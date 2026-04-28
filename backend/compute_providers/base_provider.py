"""
Base compute provider interface for the Live Translation Platform.
All compute providers must implement this interface to provide consistent
information to the backend.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TypedDict
import logging

logger = logging.getLogger(__name__)


class StreamSessionData(TypedDict, total=False):
    """
    Standardized structure for streaming session data returned by providers.
    
    Attributes:
        provider: Name of the compute provider
        provider_stream_id: Provider's internal stream ID
        whip_url: WHIP ingestion URL for WebRTC ingest
        whep_url: WHEP egress URL for WebRTC egress
        rtmp_url: RTMP ingestion URL
        rtmp_output_url: RTMP egress URLs (comma-separated)
        data_url: SSE connection URL for real-time data
        update_url: URL to send stream updates
        status_url: URL to get stream status
        stop_url: URL to stop the stream
        metadata: Additional provider-specific data
    """
    provider: str
    provider_stream_id: str
    whip_url: str
    whep_url: str
    rtmp_url: str
    rtmp_output_url: str
    data_url: str
    update_url: str
    status_url: str
    stop_url: str
    metadata: Dict[str, Any]


class BaseComputeProvider(ABC):
    """Base class for all compute providers."""
    
    def __init__(self, provider_config: Dict[str, Any]):
        """
        Initialize the compute provider with configuration.
        
        Args:
            provider_config: Dictionary containing provider configuration
                           (URLs, API keys, capabilities, etc.)
        """
        self.provider_config = provider_config
        self.provider_name = provider_config.get('name', 'unknown')
        self.enabled = provider_config.get('enabled', True)
    
    @abstractmethod
    async def create_transcription_job(
        self,
        audio_url: str,
        language: str = "en",
        format: str = "json",
        punctuation_pass: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create and execute a transcription job.
        
        Submits the job to the compute provider and waits for the result.
        
        Args:
            audio_url: URL to audio file
            language: Language code for transcription
            format: Response format
            **kwargs: Additional parameters (e.g., audio_data for base64-encoded content)
            
        Returns:
            Dictionary containing the transcription result:
                - job_id: Unique job identifier
                - status: "completed" or "failed"
                - text: Transcribed text
                - language: Detected/specified language
                - duration: Audio duration in seconds (optional)
                - segments: Time-stamped segments (optional)
                - word_count: Word count (optional)
                - model: Model used (optional)
                - hardware: Hardware used (optional)
                - provider: Provider name
                - raw_response: Original provider response (optional)
                
        Raises:
            Exception: If the job submission fails or the provider returns an error
        """
        pass
    
    @abstractmethod
    async def create_translation_job(
        self,
        text: str,
        source_language: str,
        target_language: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create and execute a translation job.
        
        Submits the job to the compute provider and waits for the result.
        
        Args:
            text: Text to translate
            source_language: Source language code
            target_language: Target language code
            **kwargs: Additional parameters
            
        Returns:
            Dictionary containing the translation result:
                - job_id: Unique job identifier
                - status: "completed" or "failed"
                - original_text: Original text
                - translated_text: Translated text
                - source_language: Source language code
                - target_language: Target language code
                - token_count: Token count (optional)
                - model: Model used (optional)
                - hardware: Hardware used (optional)
                - provider: Provider name
                - raw_response: Original provider response (optional)
                
        Raises:
            Exception: If the job submission fails or the provider returns an error
        """
        pass
    
    @abstractmethod
    async def create_streaming_session(
        self,
        session_id: str,
        language: str = "en",
        **kwargs
    ) -> StreamSessionData:
        """
        Create a streaming session by negotiating with the compute provider.
        
        This method should:
        1. Make an HTTP request to the provider's session start endpoint
        2. Receive the provider's response with stream URLs
        3. Return the session data including whip_url, data_url, update_url, stop_url
        
        Args:
            session_id: Unique session identifier
            language: Language code for transcription
            **kwargs: Additional parameters (model, etc.)
            
        Returns:
            StreamSessionData containing:
                - provider: Name of the compute provider
                - provider_stream_id: Provider's internal stream ID
                - whip_url: WHIP ingestion URL for client
                - data_url: SSE connection URL for real-time data
                - update_url: URL to send stream updates
                - stop_url: URL to stop the stream
                - metadata: Additional provider-specific data
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the compute provider.
        
        Returns:
            Dictionary with health status information
        """
        pass
    
    def get_provider_info(self) -> Dict[str, Any]:
        """
        Get basic information about this provider.
        
        Returns:
            Dictionary with provider name, capabilities, and status
        """
        return {
            'name': self.provider_name,
            'enabled': self.enabled,
            'type': self.__class__.__name__,
            'config_keys': list(self.provider_config.keys())
        }


