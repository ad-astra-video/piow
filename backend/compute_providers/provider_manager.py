"""Compute provider manager for managing multiple compute providers."""

from typing import Dict, Any, Optional, List
from .base_provider import BaseComputeProvider
import logging
import importlib
import os

logger = logging.getLogger(__name__)


class ComputeProviderManager:
    """Manages multiple compute providers and selects the appropriate one."""

    def __init__(self):
        self.providers: Dict[str, BaseComputeProvider] = {}
        self.default_provider: Optional[str] = None
        self.provider_definitions: List[Dict[str, Any]] = []

    def register_provider(self, name: str, provider: BaseComputeProvider, is_default: bool = False):
        """Register a compute provider.

        Args:
            name: Unique name for the provider
            provider: Compute provider instance
            is_default: Whether this should be the default provider
        """
        self.providers[name] = provider
        if is_default or self.default_provider is None:
            self.default_provider = name
        logger.info(f"Registered compute provider: {name} (default: {is_default})")

    def register_providers_from_definitions(self, definitions: List[Dict[str, Any]]):
        """
        Register multiple providers from definitions.
        
        Args:
            definitions: List of provider definitions with:
                - name: provider name
                - class_path: dotted path to provider class
                - config: configuration dict (with env var substitution)
                - is_default: whether to set as default
                - tags: optional tags for filtering
        """
        self.provider_definitions = definitions
        
        for definition in definitions:
            try:
                # Dynamically import provider class
                module_path, class_name = definition["class_path"].rsplit(".", 1)
                module = importlib.import_module(module_path)
                provider_class = getattr(module, class_name)
                
                # Substitute environment variables in config
                config = self._substitute_env_vars(definition["config"])
                
                # Create provider instance
                provider_instance = provider_class(config)
                
                # Register provider
                self.register_provider(
                    definition["name"],
                    provider_instance,
                    is_default=definition.get("is_default", False)
                )
                
            except Exception as e:
                logger.error(f"Failed to register provider {definition.get('name', 'unknown')}: {e}")

    def _substitute_env_vars(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Substitute environment variables in config values."""
        result = {}
        for key, value in config.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # Extract env var name
                env_var = value[2:-1]
                # Get from environment or keep original if not found
                result[key] = os.environ.get(env_var, value)
            else:
                result[key] = value
        return result

    def get_provider(self, name: Optional[str] = None) -> Optional[BaseComputeProvider]:
        """Get a compute provider by name.

        Args:
            name: Provider name (uses default if None)

        Returns:
            Compute provider instance or None if not found
        """
        provider_name = name or self.default_provider
        if provider_name is None:
            return None

        return self.providers.get(provider_name)

    def select_provider(self, job_type: str, requirements: Dict[str, Any] = None) -> BaseComputeProvider:
        """
        Select the best provider for a given job type and requirements.
        
        Args:
            job_type: Type of job (transcribe_stream, transcribe_batch, translate, etc.)
            requirements: Job requirements (latency, gpu_required, etc.)
            
        Returns:
            Selected compute provider instance
            
        Raises:
            Exception: If no suitable provider is found
        """
        requirements = requirements or {}
        
        # Filter enabled providers
        candidates = [
            (name, provider) for name, provider in self.providers.items()
            if provider.enabled
        ]
        
        if not candidates:
            raise Exception("No enabled compute providers available")
        
        # Filter by job type capability (simplified - in reality would check provider capabilities)
        capable_providers = []
        for name, provider in candidates:
            # For now, assume all providers can handle all job types
            # In a real implementation, each provider would advertise its capabilities
            capable_providers.append((name, provider))
        
        if not capable_providers:
            raise Exception(f"No provider supports job type: {job_type}")
        
        # Filter by health (prefer healthy providers)
        healthy_providers = []
        for name, provider in capable_providers:
            if self.is_healthy(name):
                healthy_providers.append((name, provider))
        
        # Use healthy providers if available, otherwise fall back to all capable
        providers_to_consider = healthy_providers if healthy_providers else capable_providers
        
        # Apply requirements-based filtering/scoring
        scored_providers = []
        for name, provider in providers_to_consider:
            score = self._score_provider(provider, job_type, requirements)
            scored_providers.append((score, name, provider))
        
        # Sort by score (highest first)
        scored_providers.sort(key=lambda x: x[0], reverse=True)
        
        if not scored_providers:
            raise Exception("No suitable providers found after scoring")
        
        # Return the highest scoring provider
        selected_provider = scored_providers[0][2]
        logger.info(f"Selected provider '{scored_providers[0][1]}' for job_type={job_type} with score {scored_providers[0][0]}")
        return selected_provider

    def _score_provider(self, provider: BaseComputeProvider, job_type: str, requirements: Dict[str, Any]) -> float:
        """
        Score a provider based on how well it matches the job requirements.
        
        Args:
            provider: Provider instance to score
            job_type: Type of job
            requirements: Job requirements
            
        Returns:
            Score (higher is better)
        """
        score = 0.0
        provider_info = provider.get_provider_info()
        
        # Base score for being enabled
        if provider.enabled:
            score += 10.0
        
        # Check for GPU requirement
        if requirements.get("gpu_required", False):
            # In a real implementation, check provider capabilities for GPU
            # For now, assume providers with "gpu" in name or tags have GPU
            provider_name_lower = provider.provider_name.lower()
            if "gpu" in provider_name_lower:
                score += 20.0
        
        # Check for latency requirements
        max_latency_ms = requirements.get("max_latency_ms")
        if max_latency_ms is not None:
            # Lower latency is better - give higher score for lower latency capabilities
            # This would be based on provider capabilities in a real implementation
            if max_latency_ms < 1000:  # <1 second
                score += 15.0  # Prefer low-latency providers
            elif max_latency_ms < 5000:  # <5 seconds
                score += 10.0
            else:
                score += 5.0
        
        # Prefer default provider slightly
        if provider.provider_name == self.default_provider:
            score += 5.0
            
        return score

    def list_providers(self) -> Dict[str, Dict[str, Any]]:
        """List all registered providers.

        Returns:
            Dictionary mapping provider names to their info
        """
        return {
            name: provider.get_provider_info()
            for name, provider in self.providers.items()
        }

    def is_healthy(self, name: Optional[str] = None) -> bool:
        """Check if a provider is healthy.

        Args:
            name: Provider name (uses default if None)

        Returns:
            True if provider is healthy, False otherwise
        """
        provider = self.get_provider(name)
        if provider and provider.enabled:
            # In a real implementation, this would be async
            # For now, we'll assume enabled providers are healthy
            return True
        return False

    def get_provider_definitions(self) -> List[Dict[str, Any]]:
        """Get the provider definitions used for registration."""
        return self.provider_definitions.copy()