#!/usr/bin/env python3
"""
Runpod compute provider for the Live Translation Platform.
Handles Runpod endpoint creation and communication with Runpod API.
"""

import json
import logging
import os
from typing import Dict, Any, Optional
import aiohttp

from ..base_provider import BaseComputeProvider

logger = logging.getLogger(__name__)


class RunpodComputeProvider(BaseComputeProvider):
    """Runpod compute provider implementation."""

    def __init__(self, provider_config: Dict[str, Any]):
        """
        Initialize the Runpod compute provider.

        Args:
            provider_config: Dictionary containing provider configuration
                           (should include api_key and endpoint_id)
        """
        super().__init__(provider_config)

        # Configuration - Runpod needs API key and endpoint ID
        self.api_key = provider_config.get('api_key') or os.environ.get("RUNPOD_API_KEY")
        self.endpoint_id = provider_config.get('endpoint_id') or os.environ.get("RUNPOD_ENDPOINT_ID")
        self.base_url = "https://api.runpod.ai/v2"

        if not self.api_key:
            logger.warning("Runpod API key not provided")
        if not self.endpoint_id:
            logger.warning("Runpod endpoint ID not provided")

    async def get_whip_url(self, session_id: str, **kwargs) -> str:
        """
        Get WHIP ingestion URL for a streaming session.
        For Runpod, WHIP would typically be handled by the worker endpoint.

        Args:
            session_id: Unique session identifier
            **kwargs: Additional parameters (language, model, etc.)

        Returns:
            WHIP URL for the client to connect to
        """
        # In a real implementation, this would call the Runpod endpoint
        # to get a WHIP URL for the session
        # For now, we'll return a placeholder that indicates the worker should handle this
        return f"{self.base_url}/endpoint/{self.endpoint_id}/process/stream/{session_id}/whip"

    async def get_websocket_url(self, session_id: str, **kwargs) -> str:
        """
        Get WebSocket URL for real-time communication.

        Args:
            session_id: Unique session identifier
            **kwargs: Additional parameters

        Returns:
            WebSocket URL for client communication
        """
        # For Runpod, WebSocket communication would be handled by the worker endpoint
        return f"{self.base_url}/endpoint/{self.endpoint_id}/ws"

    async def create_transcription_job(
        self,
        audio_url: str,
        language: str = "en",
        format: str = "json",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create and execute a transcription job via the Runpod Serverless API.

        Submits the job to the Runpod endpoint and polls for completion.

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

        if not self.api_key or not self.endpoint_id:
            raise Exception("Runpod API key and endpoint ID are required")

        request_body = {
            "input": {
                "audio_url": audio_url,
                "language": language,
                "format": format,
                **kwargs
            }
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}/endpoint/{self.endpoint_id}/run"

        logger.info(f"Runpod: Submitting transcription job to {url}")

        # Submit the job
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                url,
                json=request_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status not in (200, 201):
                    error_text = await response.text()
                    logger.error(f"Runpod transcription submit failed: {response.status} - {error_text[:500]}")
                    raise Exception(
                        f"Runpod transcription job submit failed: HTTP {response.status} - {error_text[:500]}"
                    )

                submit_result = await response.json()
                job_id = submit_result.get("id") or submit_result.get("job_id")
                logger.info(f"Runpod transcription job submitted: job_id={job_id}")

            # Poll for completion (Runpod serverless uses /run/{id}/status)
            if job_id:
                result = await self._poll_job_status(http_session, job_id, headers)
            else:
                # Some Runpod endpoints run synchronously and return results immediately
                result = submit_result

        # Normalize the response to a consistent format
        # Runpod returns results in output field
        output = result.get("output", result)

        return {
            "job_id": job_id or str(uuid.uuid4()),
            "status": "completed" if result.get("status") in ("COMPLETED", "completed", None) else result.get("status", "completed"),
            "text": output.get("text", ""),
            "language": output.get("language", language),
            "duration": output.get("duration"),
            "segments": output.get("segments"),
            "word_count": output.get("word_count"),
            "model": output.get("model", "granite-4.0-1b"),
            "hardware": output.get("hardware", "gpu"),
            "provider": "runpod",
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
        Create and execute a translation job via the Runpod Serverless API.

        Submits the job to the Runpod endpoint and polls for completion.

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

        if not self.api_key or not self.endpoint_id:
            raise Exception("Runpod API key and endpoint ID are required")

        request_body = {
            "input": {
                "text": text,
                "source_language": source_language,
                "target_language": target_language,
                **kwargs
            }
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}/endpoint/{self.endpoint_id}/run"

        logger.info(f"Runpod: Submitting translation job to {url}")

        # Submit the job
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                url,
                json=request_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status not in (200, 201):
                    error_text = await response.text()
                    logger.error(f"Runpod translation submit failed: {response.status} - {error_text[:500]}")
                    raise Exception(
                        f"Runpod translation job submit failed: HTTP {response.status} - {error_text[:500]}"
                    )

                submit_result = await response.json()
                job_id = submit_result.get("id") or submit_result.get("job_id")
                logger.info(f"Runpod translation job submitted: job_id={job_id}")

            # Poll for completion
            if job_id:
                result = await self._poll_job_status(http_session, job_id, headers)
            else:
                result = submit_result

        # Normalize the response
        output = result.get("output", result)

        return {
            "job_id": job_id or str(uuid.uuid4()),
            "status": "completed" if result.get("status") in ("COMPLETED", "completed", None) else result.get("status", "completed"),
            "original_text": output.get("original_text", text),
            "translated_text": output.get("translated_text", ""),
            "source_language": output.get("source_language", source_language),
            "target_language": output.get("target_language", target_language),
            "token_count": output.get("token_count"),
            "model": output.get("model", "granite-4.0-1b"),
            "hardware": output.get("hardware", "gpu"),
            "provider": "runpod",
            "raw_response": result,
        }

    async def _poll_job_status(
        self,
        http_session: aiohttp.ClientSession,
        job_id: str,
        headers: Dict[str, str],
        max_wait_seconds: int = 600,
        poll_interval: float = 2.0
    ) -> Dict[str, Any]:
        """
        Poll Runpod job status until completion or timeout.

        Args:
            http_session: Active aiohttp client session
            job_id: Runpod job ID to poll
            headers: Authorization headers
            max_wait_seconds: Maximum time to wait for completion
            poll_interval: Seconds between poll requests

        Returns:
            Final job result dictionary

        Raises:
            Exception: If the job fails or times out
        """
        import asyncio

        status_url = f"{self.base_url}/endpoint/{self.endpoint_id}/run/{job_id}/status"
        elapsed = 0.0

        while elapsed < max_wait_seconds:
            async with http_session.get(
                status_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(f"Runpod status poll failed: {response.status} - {error_text[:200]}")
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    continue

                status_result = await response.json()
                status = status_result.get("status", "").upper()

                if status == "COMPLETED":
                    logger.info(f"Runpod job {job_id} completed")
                    return status_result

                elif status in ("FAILED", "CANCELLED", "ERROR"):
                    error_msg = status_result.get("error", status_result.get("output", "Unknown error"))
                    logger.error(f"Runpod job {job_id} failed: {error_msg}")
                    raise Exception(f"Runpod job {job_id} failed: {error_msg}")

                elif status in ("IN_QUEUE", "IN_PROGRESS", "RUNNING"):
                    logger.debug(f"Runpod job {job_id} status: {status}, waiting...")
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                else:
                    logger.warning(f"Runpod job {job_id} unknown status: {status}")
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

        raise Exception(f"Runpod job {job_id} timed out after {max_wait_seconds}s")

    async def create_streaming_session(
        self,
        session_id: str,
        language: str = "en",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a streaming session for real-time transcription/translation.

        Args:
            session_id: Unique session identifier
            language: Language code for transcription
            **kwargs: Additional parameters

        Returns:
            Dictionary containing headers and session information for the provider
        """
        # Build Runpod request payload for streaming
        runpod_payload = {
            "input": {
                "stream_id": session_id,
                "language": language,
                "model": "voxtral-realtime",  # or whatever model you're using
                **kwargs
            }
        }

        runpod_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # Build request body
        request_body = {
            "stream_id": session_id,
            "params": json.dumps({
                "language": language,
                "model": "voxtral-realtime"
            })
        }

        return {
            "runpod_headers": runpod_headers,
            "request_body": request_body,
            "endpoint_id": self.endpoint_id,
            "provider": "runpod"
        }

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the Runpod compute provider.

        Returns:
            Dictionary with health status information
        """
        try:
            # Try to check the endpoint health via Runpod API
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/endpoint/{self.endpoint_id}/health",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        return {
                            "status": "healthy",
                            "provider": "runpod",
                            "endpoint_id": self.endpoint_id,
                            "response_time": "ok"
                        }
                    else:
                        return {
                            "status": "unhealthy",
                            "provider": "runpod",
                            "endpoint_id": self.endpoint_id,
                            "error": f"HTTP {response.status}"
                        }
        except Exception as e:
            logger.error(f"Runpod health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "runpod",
                "endpoint_id": self.endpoint_id,
                "error": str(e)
            }

    # Utility method for building Runpod headers if needed elsewhere
    def build_runpod_headers(self, additional_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Build Runpod headers for API requests.

        Args:
            additional_headers: Optional additional headers to include

        Returns:
            Dictionary of headers for Runpod API requests
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        if additional_headers:
            headers.update(additional_headers)

        return headers