#!/usr/bin/env python3
"""
Livepeer compute provider for the Live Translation Platform.
Handles Livepeer header creation and communication with Livepeer orchestrator.
"""

import base64
import json
import logging
import os
from typing import Dict, Any, Optional
import aiohttp
from aiohttp import web

from ..base_provider import BaseComputeProvider, StreamSessionData

logger = logging.getLogger(__name__)


class LivepeerComputeProvider(BaseComputeProvider):
    """Livepeer compute provider implementation."""

    def __init__(self, provider_config: Dict[str, Any]):
        """
        Initialize the Livepeer compute provider.

        Args:
            provider_config: Dictionary containing provider configuration
                           (should include GPU_RUNNER_URL)
        """
        super().__init__(provider_config)

        # Configuration - only GPU_RUNNER_URL is needed for worker communication
        self.GPU_RUNNER_URL = provider_config.get('gpu_runner_url') or os.environ.get(
            "GPU_RUNNER_URL", "http://localhost:9935"
        )

    async def get_whip_url(self, session_id: str, **kwargs) -> str:
        """
        Get WHIP ingestion URL for a streaming session.
        For Livepeer, this would typically be provided by the orchestrator/worker.

        Args:
            session_id: Unique session identifier
            **kwargs: Additional parameters (language, model, etc.)

        Returns:
            WHIP URL for the client to connect to
        """
        # In a real implementation, this would call the Livepeer orchestrator
        # to get a WHIP URL for the session
        # For now, we'll return a placeholder that indicates the worker should handle this
        return f"{self.GPU_RUNNER_URL}/process/stream/stream/start"

    async def get_websocket_url(self, session_id: str, **kwargs) -> str:
        """
        Get WebSocket URL for real-time communication.

        Args:
            session_id: Unique session identifier
            **kwargs: Additional parameters

        Returns:
            WebSocket URL for client communication
        """
        # For Livepeer, WebSocket communication might be handled differently
        # This would typically be the worker's WebSocket endpoint
        return f"{self.GPU_RUNNER_URL}/ws"

    async def create_transcription_job(
        self,
        audio_url: str,
        language: str = "en",
        format: str = "json",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create and execute a transcription job via the Livepeer BYOC AI Stream API.

        Submits the job to GPU_RUNNER_URL/process/request/transcribe with the
        Livepeer header and waits for the result.

        Args:
            audio_url: URL to audio file
            language: Language code for transcription
            format: Response format
            **kwargs: Additional parameters

        Returns:
            Dictionary containing the transcription result

        Raises:
            Exception: If the job submission fails or the provider returns an error
        """
        import uuid

        request_body = {
            "audio_url": audio_url,
            "language": language,
            "format": format,
        }

        livepeer_header = self.build_livepeer_header(
            request_body=request_body,
            capability="transcription",
            timeout_seconds=300
        )

        logger.info(f"Livepeer: Submitting transcription job to {self.GPU_RUNNER_URL}/process/request/transcribe")

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{self.GPU_RUNNER_URL}/process/request/transcribe",
                json=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Livepeer": livepeer_header,
                },
                timeout=aiohttp.ClientTimeout(total=600)  # 10 min timeout for long audio
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Livepeer transcription failed: {response.status} - {error_text[:500]}")
                    raise Exception(
                        f"Livepeer transcription job failed: HTTP {response.status} - {error_text[:500]}"
                    )

                result = await response.json()
                logger.info(f"Livepeer transcription completed: job_id={result.get('job_id', 'unknown')}")

        # Normalize the response to a consistent format
        return {
            "job_id": result.get("job_id", str(uuid.uuid4())),
            "status": result.get("status", "completed"),
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "duration": result.get("duration"),
            "segments": result.get("segments"),
            "word_count": result.get("word_count"),
            "model": result.get("model", "granite-4.0-1b"),
            "hardware": result.get("hardware", "cpu"),
            "provider": "livepeer",
            "raw_response": result,
        }

    async def create_translation_job(
        self,
        text: str,
        source_language: str,
        target_language: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create and execute a translation job via the Livepeer BYOC AI Stream API.

        Submits the job to GPU_RUNNER_URL/process/request/translate with the
        Livepeer header and waits for the result.

        Args:
            text: Text to translate
            source_language: Source language code
            target_language: Target language code
            **kwargs: Additional parameters

        Returns:
            Dictionary containing the translation result

        Raises:
            Exception: If the job submission fails or the provider returns an error
        """
        import uuid

        request_body = {
            "text": text,
            "source_language": source_language,
            "target_language": target_language,
        }

        livepeer_header = self.build_livepeer_header(
            request_body=request_body,
            capability="translation",
            timeout_seconds=60
        )

        logger.info(f"Livepeer: Submitting translation job to {self.GPU_RUNNER_URL}/process/request/translate")

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{self.GPU_RUNNER_URL}/process/request/translate",
                json=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Livepeer": livepeer_header,
                },
                timeout=aiohttp.ClientTimeout(total=120)  # 2 min timeout
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Livepeer translation failed: {response.status} - {error_text[:500]}")
                    raise Exception(
                        f"Livepeer translation job failed: HTTP {response.status} - {error_text[:500]}"
                    )

                result = await response.json()
                logger.info(f"Livepeer translation completed: job_id={result.get('job_id', 'unknown')}")

        # Normalize the response to a consistent format
        return {
            "job_id": result.get("job_id", str(uuid.uuid4())),
            "status": result.get("status", "completed"),
            "original_text": result.get("original_text", text),
            "translated_text": result.get("translated_text", ""),
            "source_language": result.get("source_language", source_language),
            "target_language": result.get("target_language", target_language),
            "token_count": result.get("token_count"),
            "model": result.get("model", "granite-4.0-1b"),
            "hardware": result.get("hardware", "cpu"),
            "provider": "livepeer",
            "raw_response": result,
        }

    async def create_streaming_session(
        self,
        session_id: str,
        language: str = "en",
        **kwargs
    ) -> StreamSessionData:
        """
        Create a streaming session by POSTing to GPU_RUNNER_URL/process/stream/start.
        
        The GPU runner returns a response containing:
        {
            "provider_stream_id": "...",
            "whip_url": "...",
            "data_url": "...",  # SSE connection
            "update_url": "...",
            "stop_url": "..."
        }

        Args:
            session_id: Unique session identifier
            language: Language code for transcription
            **kwargs: Additional parameters (model, etc.)

        Returns:
            StreamSessionData containing:
                - provider: "livepeer"
                - provider_stream_id: Provider's internal stream ID
                - whip_url: WHIP ingestion URL for client
                - data_url: SSE connection URL for real-time data
                - update_url: URL to send stream updates
                - stop_url: URL to stop the stream
                - metadata: Full provider response
        """
        model = kwargs.get("model", "voxtral-realtime")
        
        # Build request to GPU runner
        start_request = {
            "stream_id": session_id,
            "params": {
                "language": language,
                "model": model
            }
        }
        
        logger.info(f"Creating Livepeer streaming session: session_id={session_id}, language={language}, model={model}")
        
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{self.GPU_RUNNER_URL}/process/stream/start",
                json=start_request,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Livepeer session creation failed: {response.status} - {error_text}")
                    raise Exception(f"Failed to create streaming session: HTTP {response.status} - {error_text}")
                
                provider_data = await response.json()
                logger.info(f"Livepeer session created successfully: {provider_data}")
                
                # Return the full provider response in standardized format
                return {
                    "provider": "livepeer",
                    "provider_stream_id": provider_data.get("provider_stream_id", session_id),
                    "whip_url": provider_data.get("whip_url", ""),
                    "data_url": provider_data.get("data_url", ""),
                    "update_url": provider_data.get("update_url", ""),
                    "stop_url": provider_data.get("stop_url", ""),
                    "metadata": provider_data  # Store entire response for future use
                }

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the Livepeer compute provider.

        Returns:
            Dictionary with health status information
        """
        try:
            # Try to connect to the GPU runner to check if it's available
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.GPU_RUNNER_URL}/health", timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        return {
                            "status": "healthy",
                            "provider": "livepeer",
                            "gpu_runner_url": self.GPU_RUNNER_URL,
                            "response_time": "ok"
                        }
                    else:
                        return {
                            "status": "unhealthy",
                            "provider": "livepeer",
                            "gpu_runner_url": self.GPU_RUNNER_URL,
                            "error": f"HTTP {response.status}"
                        }
        except Exception as e:
            logger.error(f"Livepeer health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "livepeer",
                "gpu_runner_url": self.GPU_RUNNER_URL,
                "error": str(e)
            }

    # Keep the original utility methods for backward compatibility
    def build_livepeer_header(self, request_body: Dict[str, Any], capability: str, timeout_seconds: int = 60) -> str:
        """
        Build a Livepeer header for BYOC AI Stream API requests.

        Args:
            request_body: The request body to encode
            capability: The capability being requested
            timeout_seconds: Timeout for the request

        Returns:
            Base64-encoded Livepeer header
        """
        livepeer_payload = {
            "request": json.dumps(request_body),
            "capability": capability,
            "timeout_seconds": timeout_seconds
        }

        # Base64 encode the Livepeer header
        livepeer_header = base64.b64encode(
            json.dumps(livepeer_payload).encode()
        ).decode()

        return livepeer_header