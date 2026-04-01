#!/usr/bin/env python3
"""
Model Router
Routes transcription and translation requests to appropriate models based on requirements.
"""

import os
import logging
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class ProcessingType(Enum):
    """Types of processing requests."""
    TRANSCRIBE_BATCH = "transcribe_batch"
    TRANSCRIBE_STREAM = "transcribe_stream"
    TRANSLATE = "translate"

class HardwareType(Enum):
    """Available hardware types."""
    CPU = "cpu"
    GPU = "gpu"

class ModelRouter:
    """
    Routes requests to appropriate models based on processing type and requirements.
    """
    
    def __init__(self):
        """Initialize the model router."""
        # Import workers lazily to avoid loading models unless needed
        self._granite_transcriber = None
        self._voxtral_streamer = None
        
        # Configuration from environment
        self.vllm_url = os.environ.get("VLLM_WS_URL", "ws://localhost:6000/v1/realtime")
        self.grape_model_path = os.environ.get("GRANITE_MODEL_PATH", "models/granite-4.0-1b-speech-onnx")
        
        logger.info("ModelRouter initialized")
    
    def _get_granite_transcriber(self):
        """Lazy load Granite transcriber."""
        if self._granite_transcriber is None:
            from granite_transcriber import Granite4Transcriber
            self._granite_transcriber = Granite4Transcriber(self.grape_model_path)
        return self._granite_transcriber
    
    def _get_voxtral_streamer(self):
        """Lazy load Voxtral streamer."""
        if self._voxtral_streamer is None:
            from voxtral_streamer import VoxtralStreamer
            self._voxtral_streamer = VoxtralStreamer(self.vllm_url)
        return self._voxtral_streamer
    
    def route_transcription(self, 
                          audio_input: Any,
                          language: str = "en",
                          streaming: bool = False,
                          **kwargs) -> Dict[str, Any]:
        """
        Route transcription request to appropriate model.
        
        Args:
            audio_input: Audio data (file path for batch, audio bytes/stream for streaming)
            language: Language code
            streaming: Whether to use streaming (GPU) or batch (CPU) processing
            **kwargs: Additional parameters
            
        Returns:
            Transcription result dictionary
        """
        if streaming:
            logger.info("Routing transcription request to Voxtral (GPU streaming)")
            return self._route_to_voxtral_transcribe(audio_input, language, **kwargs)
        else:
            logger.info("Routing transcription request to Granite 4.0 (CPU batch)")
            return self._route_to_granite_transcribe(audio_input, language, **kwargs)
    
    def route_translation(self, 
                        text: str,
                        source_lang: str,
                        target_lang: str) -> Dict[str, Any]:
        """
        Route translation request to appropriate model.
        
        Translation is currently only supported on CPU using Granite 4.0.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Translation result dictionary
        """
        logger.info(f"Routing translation request ({source_lang} -> {target_lang}) to Granite 4.0 (CPU)")
        return self._route_to_granite_translate(text, source_lang, target_lang)
    
    def _route_to_granite_transcribe(self, audio_path: str, language: str, **kwargs) -> Dict[str, Any]:
        """Route to Granite 4.0 for batch transcription."""
        try:
            transcriber = self._get_granite_transcriber()
            return transcriber.transcribe(audio_path, language, **kwargs)
        except Exception as e:
            logger.error(f"Error in Granite transcription: {e}")
            return {
                "error": str(e),
                "text": "",
                "language": language,
                "model": "granite-4.0-1b-error",
                "hardware": "cpu"
            }
    
    def _route_to_voxtral_transcribe(self, audio_data: Any, language: str, **kwargs) -> Dict[str, Any]:
        """
        Route to Voxtral for streaming transcription.
        Note: This is a simplified version - real streaming would be handled differently
        through WebSocket connections.
        """
        try:
            # For now, we'll indicate that streaming requires WebSocket connection
            # In a real implementation, this would return connection info or handle the stream
            logger.info("Voxtral streaming requires WebSocket connection - returning connection info")
            
            return {
                "status": "streaming_required",
                "message": "Use WebSocket connection for real-time streaming transcription",
                "ws_url": self.vllm_url,
                "language": language,
                "model": "voxtral-realtime",
                "hardware": "gpu",
                "instructions": "Connect to WebSocket and send audio chunks for real-time transcription"
            }
        except Exception as e:
            logger.error(f"Error setting up Voxtral transcription: {e}")
            return {
                "error": str(e),
                "model": "voxtral-realtime-error",
                "hardware": "gpu"
            }
    
    def _route_to_granite_translate(self, text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """Route to Granite 4.0 for translation."""
        try:
            transcriber = self._get_granite_transcriber()
            return transcriber.translate(text, source_lang, target_lang)
        except Exception as e:
            logger.error(f"Error in Granite translation: {e}")
            return {
                "error": str(e),
                "original_text": text,
                "source_language": source_lang,
                "target_language": target_lang,
                "model": "granite-4.0-1b-error",
                "hardware": "cpu"
            }
    
    def get_available_models(self) -> Dict[str, Any]:
        """
        Get information about available models and their status.
        
        Returns:
            Dictionary with model availability information
        """
        granite_available = False
        voxtral_available = False
        
        try:
            granite = self._get_granite_transcriber()
            granite_available = granite.is_available()
        except:
            granite_available = False
            
        try:
            voxtral = self._get_voxtral_streamer()
            # For Voxtraf, we'd check if we can connect, but for now just check if class loads
            voxtral_available = True  # Assume available if class loads
        except:
            voxtral_available = False
        
        return {
            "granite_4_0_1b": {
                "available": granite_available,
                "hardware": "cpu",
                "use_cases": ["batch_transcription", "translation"],
                "model_path": self.grape_model_path
            },
            "voxtral_realtime": {
                "available": voxtral_available,
                "hardware": "gpu",
                "use_cases": ["streaming_transcription", "real_time_captions"],
                "ws_url": self.vllm_url
            }
        }
    
    def get_recommended_setup(self, 
                            use_case: str,
                            latency_requirement: str = "medium",
                            budget_constraint: str = "medium") -> Dict[str, Any]:
        """
        Get recommended model/setup for a given use case.
        
        Args:
            use_case: One of "transcription", "translation", "streaming"
            latency_requirement: "low", "medium", "high"
            budget_constraint: "low", "medium", "high"
            
        Returns:
            Recommendation dictionary
        """
        recommendations = {
            "transcription": {
                "low_latency": {
                    "recommended": "voxtral_realtime",
                    "reason": "GPU-based streaming for lowest latency",
                    "hardware": "gpu",
                    "expected_latency_ms": "<500ms"
                },
                "medium_latency": {
                    "recommended": "granite_4_0_1b",
                    "reason": "CPU-based batch processing good balance",
                    "hardware": "cpu",
                    "expected_latency_s": "1-5s"
                },
                "high_latency": {
                    "recommended": "granite_4_0_1b",
                    "reason": "CPU-based processing acceptable for high latency tolerance",
                    "hardware": "cpu",
                    "expected_latency_s": "5-10s"
                }
            },
            "translation": {
                "low_latency": {
                    "recommended": "granite_4_0_1b",
                    "reason": "Translation only available on CPU with Granite 4.0",
                    "hardware": "cpu",
                    "expected_latency_s": "<1s"
                },
                "medium_latency": {
                    "recommended": "granite_4_0_1b",
                    "reason": "Translation only available on CPU with Granite 4.0",
                    "hardware": "cpu",
                    "expected_latency_s": "1-3s"
                },
                "high_latency": {
                    "recommended": "granite_4_0_1b",
                    "reason": "Translation only available on CPU with Granite 4.0",
                    "hardware": "cpu",
                    "expected_latency_s": "3-5s"
                }
            },
            "streaming": {
                "low_latency": {
                    "recommended": "voxtral_realtime",
                    "reason": "GPU-based streaming required for real-time",
                    "hardware": "gpu",
                    "expected_latency_ms": "<500ms"
                },
                "medium_latency": {
                    "recommended": "voxtral_realtime",
                    "reason": "Streaming requires GPU-based Voxtral",
                    "hardware": "gpu",
                    "expected_latency_ms": "<500ms"
                },
                "high_latency": {
                    "recommended": "voxtral_realtime",
                    "reason": "Streaming requires GPU-based Voxtral regardless of latency tolerance",
                    "hardware": "gpu",
                    "expected_latency_ms": "<500ms"
                }
            }
        }
        
        use_case_recs = recommendations.get(use_case, {})
        latency_recs = use_case_recs.get(latency_requirement, {})
        
        if latency_recs:
            return latency_recs
        else:
            # Fallback to medium latency recommendation
            medium_recs = use_case_recs.get("medium_latency", {})
            if medium_recs:
                return medium_recs
            else:
                return {
                    "recommended": "granite_4_0_1b",
                    "reason": "Fallback recommendation",
                    "hardware": "cpu"
                }

# Factory function
def create_model_router() -> ModelRouter:
    """Factory function to create a ModelRouter instance."""
    return ModelRouter()

# Health check function
def model_router_health_check() -> Dict[str, Any]:
    """Check if model router is working correctly."""
    router = ModelRouter()
    models_info = router.get_available_models()
    
    return {
        "status": "healthy",
        "module": "model_router",
        "models_available": models_info,
        "timestamp": __import__('time').time()
    }
