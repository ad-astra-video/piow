#!/usr/bin/env python3
"""
Livepeer compute provider for the Live Translation Platform.
Handles Livepeer header creation and communication with Livepeer orchestrator.
"""

import asyncio
import base64
import json
import logging
import os
import time
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
        gpu_runner_url = provider_config.get('gpu_runner_url') or os.environ.get(
            "GPU_RUNNER_URL", "http://localhost:9935"
        )
        
        # Ensure URL has a scheme (https for remote, http for localhost)
        self.GPU_RUNNER_URL = self._normalize_url(gpu_runner_url)
    
    def _normalize_url(self, url: str) -> str:
        """
        Normalize GPU runner URL to include scheme.
        
        For localhost/127.0.0.1, use http://
        For remote hosts, use https://
        
        Args:
            url: URL that may or may not have a scheme
            
        Returns:
            Normalized URL with scheme
        """
        if not url:
            return "http://localhost:9935"
        
        url = url.strip()
        
        # If already has scheme, return as-is
        if url.startswith(("http://", "https://")):
            return url
        
        # For localhost/127.0.0.1, use http
        if url.startswith(("localhost", "127.0.0.1", "0.0.0.0")):
            return f"http://{url}"
        
        # For remote URLs, use https
        return f"https://{url}"

    async def create_transcription_job(
        self,
        audio_url: str,
        language: str = "en",
        format: str = "json",
        punctuation_pass: bool = False,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
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
            "punctuation_pass": punctuation_pass,
            **kwargs,
        }
        if source_language:
            request_body["source_language"] = source_language
        if target_language:
            request_body["target_language"] = target_language

        livepeer_header = self.build_livepeer_batch_header(
            request={"job": "transcribe"},
            parameters={},
            capability=kwargs.get("capability", "transcribe-translate"),
            timeout_seconds=kwargs.get("timeout_seconds", 300),
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

                result = await response.json(content_type=None)
                if not isinstance(result, dict):
                    raise Exception(
                        f"Livepeer transcription returned invalid response type: {type(result).__name__}"
                    )
                if not result:
                    raise Exception("Livepeer transcription returned an empty response payload")
                logger.info(f"Livepeer transcription completed: job_id={result.get('job_id', 'unknown')}")

        # Normalize the response to a consistent format
        return {
            "job_id": result.get("job_id", str(uuid.uuid4())),
            "status": result.get("status", "completed"),
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "duration": result.get("duration"),
            "segments": result.get("segments"),
            "words": result.get("words"),
            "speakers": result.get("speakers"),
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

        livepeer_header = self.build_livepeer_batch_header(
            request={"job": "translate"},
            parameters={},
            capability=kwargs.get("capability", "transcribe-translate"),
            timeout_seconds=kwargs.get("timeout_seconds", 60),
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

                result = await response.json(content_type=None)
                if not isinstance(result, dict):
                    raise Exception(
                        f"Livepeer translation returned invalid response type: {type(result).__name__}"
                    )
                if not result:
                    raise Exception("Livepeer translation returned an empty response payload")
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
        
        Implements the Livepeer BYOC AI Stream API specification with required:
        - Livepeer header with request, parameters, capability, and timeout_seconds
        - StartRequest body with stream_id, stream_name, and params
        
        The GPU runner returns a response containing:
        {
            "stream_id": "...",
            "whip_url": "...",
            "whep_url": "...",
            "rtmp_url": "...",
            "rtmp_output_url": "...",
            "data_url": "...",  # SSE connection
            "update_url": "...",
            "status_url": "...",
            "stop_url": "..."
        }

        Args:
            session_id: Unique session identifier
            language: Language code for transcription
            **kwargs: Additional parameters (model, capability, timeout_seconds, 
                     enable_video_ingress, enable_video_egress, enable_data_output, etc.)

        Returns:
            StreamSessionData containing:
                - provider: "livepeer"
                - provider_stream_id: Provider's internal stream ID
                - whip_url: WHIP ingestion URL for client
                - whep_url: WHEP egress URL for client
                - rtmp_url: RTMP ingestion URL
                - rtmp_output_url: RTMP egress URLs
                - data_url: SSE connection URL for real-time data
                - update_url: URL to send stream updates
                - status_url: URL to get stream status
                - stop_url: URL to stop the stream
                - metadata: Full provider response
        """
        model = kwargs.get("model", "voxtral-realtime")
        target_language = kwargs.get("target_language")
        capability = kwargs.get("capability", "transcribe-translate" if target_language else "live-transcription")
        timeout_seconds = kwargs.get("timeout_seconds", 120)
        stream_name = "live-transcribe"
        stream_request_id = kwargs.get("stream_request_id")
        rtmp_output = kwargs.get("rtmp_output")
        
        # JobRequestDetails - required fields for stream initialization
        request_details = {
            "stream_id": session_id
        }
        
        # JobParameters - controls video/data ingress/egress
        job_parameters = {
            "enable_video_ingress": kwargs.get("enable_video_ingress", True),
            "enable_video_egress": kwargs.get("enable_video_egress", False),
            "enable_data_output": kwargs.get("enable_data_output", True)
        }
        
        # Add orchestrator filters if provided
        if "orchestrators" in kwargs:
            job_parameters["orchestrators"] = kwargs["orchestrators"]
        
        # Build Livepeer header (must be JSON-encoded strings inside the header)
        livepeer_header_payload = {
            "request": json.dumps(request_details),
            "parameters": json.dumps(job_parameters),
            "capability": capability,
            "timeout_seconds": timeout_seconds
        }
        
        # Base64 encode the Livepeer header
        livepeer_header = base64.b64encode(
            json.dumps(livepeer_header_payload).encode()
        ).decode()
        
        # Build worker params
        worker_params = {
            "language": language,
            "model": model,
            "live_transcription_enabled": bool(kwargs.get("live_transcription_enabled", True)),
            "live_translation_enabled": bool(kwargs.get("live_translation_enabled", False)),
            "analysis_enabled": bool(kwargs.get("analysis_enabled", False)),
            "analysis_mode": kwargs.get("analysis_mode", "multimodal"),
            "analysis_audio_chunk_seconds": float(kwargs.get("analysis_audio_chunk_seconds", 10.0)),
            "analysis_video_chunk_seconds": float(kwargs.get("analysis_video_chunk_seconds", 10.0)),
            "analysis_video_fps": int(kwargs.get("analysis_video_fps", 3)),
        }
        analysis_prompt = kwargs.get("analysis_prompt")
        if analysis_prompt is not None:
            worker_params["analysis_prompt"] = str(analysis_prompt)
        analysis_response_format = kwargs.get("analysis_response_format")
        if analysis_response_format is not None:
            worker_params["analysis_response_format"] = analysis_response_format
        source_language = kwargs.get("source_language")
        target_language = kwargs.get("target_language")
        if source_language:
            worker_params["source_language"] = source_language
        if target_language:
            worker_params["target_language"] = target_language

        # Build StartRequest body
        start_request = {
            "stream_id": session_id,
            "stream_name": stream_name,
            "params": json.dumps(worker_params)
        }
        
        # Add optional rtmp_output if provided
        if rtmp_output:
            start_request["rtmp_output"] = rtmp_output
        
        start_url = f"{self.GPU_RUNNER_URL}/process/stream/start"
        request_started_at = time.perf_counter()
        
        logger.info(
            "Livepeer stream start request: request_id=%s url=%s session_id=%s language=%s model=%s capability=%s timeout_seconds=%s payload=%s",
            stream_request_id,
            start_url,
            session_id,
            language,
            model,
            capability,
            timeout_seconds,
            start_request,
        )
        
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    start_url,
                    json=start_request,
                    headers={
                        "Content-Type": "application/json",
                        "Livepeer": livepeer_header
                    },
                    timeout=aiohttp.ClientTimeout(total=300)
                ) as response:
                    elapsed_ms = round((time.perf_counter() - request_started_at) * 1000, 2)
                    
                    # Always log response details for debugging
                    logger.info(
                        "Livepeer stream start response received: request_id=%s url=%s session_id=%s http_status=%s elapsed_ms=%s content_type=%s",
                        stream_request_id,
                        start_url,
                        session_id,
                        response.status,
                        elapsed_ms,
                        response.headers.get("content-type", "unknown"),
                    )
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            "Livepeer stream start failed: request_id=%s url=%s session_id=%s http_status=%s elapsed_ms=%s request_headers=%s request_payload=%s response_headers=%s response_body=%s",
                            stream_request_id,
                            start_url,
                            session_id,
                            response.status,
                            elapsed_ms,
                            dict(response.request_info.headers) if hasattr(response, 'request_info') else "n/a",
                            start_request,
                            dict(response.headers),
                            error_text[:1000],
                        )
                        raise Exception(f"HTTP {response.status}: {error_text[:500]}")
                    
                    provider_data = await response.json(content_type=None)
                    if not isinstance(provider_data, dict):
                        raise Exception(
                            f"Livepeer stream start returned invalid response type: {type(provider_data).__name__}"
                        )
                    if not provider_data:
                        raise Exception("Livepeer stream start returned an empty response payload")
                    logger.info(
                        "Livepeer stream start success: request_id=%s url=%s session_id=%s http_status=%s elapsed_ms=%s response_keys=%s whip_url=%s data_url=%s",
                        stream_request_id,
                        start_url,
                        session_id,
                        response.status,
                        elapsed_ms,
                        sorted(list(provider_data.keys())),
                        provider_data.get("whip_url", "NOT_PROVIDED"),
                        provider_data.get("data_url", "NOT_PROVIDED"),
                    )
                    
                    # Return the full provider response in standardized format
                    return {
                        "provider": self.provider_name,
                        "provider_stream_id": provider_data.get("stream_id", session_id),
                        "whip_url": provider_data.get("whip_url", ""),
                        "whep_url": provider_data.get("whep_url", ""),
                        "rtmp_url": provider_data.get("rtmp_url", ""),
                        "rtmp_output_url": provider_data.get("rtmp_output_url", ""),
                        "data_url": provider_data.get("data_url", ""),
                        "update_url": provider_data.get("update_url", ""),
                        "status_url": provider_data.get("status_url", ""),
                        "stop_url": provider_data.get("stop_url", ""),
                        "metadata": provider_data  # Store entire response for future use
                    }
        except asyncio.TimeoutError as e:
            logger.error(
                "Livepeer stream start timeout: request_id=%s url=%s session_id=%s elapsed_ms=%s error=%s",
                stream_request_id,
                start_url,
                session_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                str(e),
            )
            raise Exception(f"Livepeer timeout: {str(e)}")
        except aiohttp.ClientError as e:
            logger.error(
                "Livepeer stream start connection error: request_id=%s url=%s session_id=%s elapsed_ms=%s error_type=%s error=%s",
                stream_request_id,
                start_url,
                session_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                type(e).__name__,
                str(e),
            )
            raise Exception(f"Livepeer connection error: {type(e).__name__}: {str(e)}")
        except Exception as e:
            logger.error(
                "Livepeer stream start unexpected error: request_id=%s url=%s session_id=%s elapsed_ms=%s error_type=%s error=%s",
                stream_request_id,
                start_url,
                session_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                type(e).__name__,
                str(e),
            )
            raise

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

    async def update_streaming_session(
        self,
        provider_stream_id: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Update stream parameters via POST /process/stream/{streamId}/update.
        
        Sends updated parameters to the running stream. The Livepeer header must
        include the stream_id in the request field.

        Args:
            provider_stream_id: The provider's stream ID to update
            params: Dictionary of parameters to update (passed to pipeline worker)
            **kwargs: Additional options (timeout_seconds, capability, etc.)

        Returns:
            Dictionary with update status and response

        Raises:
            Exception: If the update fails
        """
        timeout_seconds = kwargs.get("timeout_seconds", 15)
        capability = kwargs.get("capability", "video-analysis")
        stream_request_id = kwargs.get("stream_request_id")
        
        # Build Livepeer header for update request
        request_details = {
            "stream_id": provider_stream_id
        }
        
        livepeer_header_payload = {
            "request": json.dumps(request_details),
            "parameters": "{}",
            "capability": capability,
            "timeout_seconds": timeout_seconds
        }
        
        livepeer_header = base64.b64encode(
            json.dumps(livepeer_header_payload).encode()
        ).decode()
        
        update_url = f"{self.GPU_RUNNER_URL}/process/stream/{provider_stream_id}/update"
        request_body = params or {}
        request_started_at = time.perf_counter()
        
        logger.info(
            "Livepeer stream update request: request_id=%s url=%s stream_id=%s params=%s",
            stream_request_id,
            update_url,
            provider_stream_id,
            request_body,
        )
        
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    update_url,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        "Livepeer": livepeer_header
                    },
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    elapsed_ms = round((time.perf_counter() - request_started_at) * 1000, 2)
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            "Livepeer stream update failed: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s response_headers=%s response_body=%s",
                            stream_request_id,
                            update_url,
                            provider_stream_id,
                            response.status,
                            elapsed_ms,
                            dict(response.headers),
                            error_text[:500],
                        )
                        raise Exception(f"HTTP {response.status}: {error_text[:300]}")
                    
                    logger.info(
                        "Livepeer stream update success: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s",
                        stream_request_id,
                        update_url,
                        provider_stream_id,
                        response.status,
                        elapsed_ms,
                    )
                    
                    return {
                        "status": "updated",
                        "stream_id": provider_stream_id
                    }
        except asyncio.TimeoutError as e:
            logger.error(
                "Livepeer stream update timeout: request_id=%s url=%s stream_id=%s elapsed_ms=%s error=%s",
                stream_request_id,
                update_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                str(e),
            )
            raise Exception(f"Livepeer timeout: {str(e)}")
        except aiohttp.ClientError as e:
            logger.error(
                "Livepeer stream update connection error: request_id=%s url=%s stream_id=%s elapsed_ms=%s error_type=%s error=%s",
                stream_request_id,
                update_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                type(e).__name__,
                str(e),
            )
            raise Exception(f"Livepeer connection error: {type(e).__name__}: {str(e)}")

    async def get_stream_status(
        self,
        provider_stream_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get stream status via GET /process/stream/{streamId}/status.
        
        Retrieves current stream status including orchestrator and ingest metrics.

        Args:
            provider_stream_id: The provider's stream ID
            **kwargs: Additional options

        Returns:
            Dictionary with stream status information

        Raises:
            Exception: If the status request fails
        """
        stream_request_id = kwargs.get("stream_request_id")
        status_url = f"{self.GPU_RUNNER_URL}/process/stream/{provider_stream_id}/status"
        request_started_at = time.perf_counter()
        
        logger.info(
            "Livepeer stream status request: request_id=%s url=%s stream_id=%s",
            stream_request_id,
            status_url,
            provider_stream_id,
        )
        
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.get(
                    status_url,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    elapsed_ms = round((time.perf_counter() - request_started_at) * 1000, 2)
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            "Livepeer stream status failed: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s response_headers=%s response_body=%s",
                            stream_request_id,
                            status_url,
                            provider_stream_id,
                            response.status,
                            elapsed_ms,
                            dict(response.headers),
                            error_text[:500],
                        )
                        raise Exception(f"HTTP {response.status}: {error_text[:300]}")
                    
                    status_data = await response.json(content_type=None)
                    logger.info(
                        "Livepeer stream status success: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s status_data_keys=%s",
                        stream_request_id,
                        status_url,
                        provider_stream_id,
                        response.status,
                        elapsed_ms,
                        sorted(list(status_data.keys())),
                    )
                    
                    return status_data
        except asyncio.TimeoutError as e:
            logger.error(
                "Livepeer stream status timeout: request_id=%s url=%s stream_id=%s elapsed_ms=%s error=%s",
                stream_request_id,
                status_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                str(e),
            )
            raise Exception(f"Livepeer timeout: {str(e)}")
        except aiohttp.ClientError as e:
            logger.error(
                "Livepeer stream status connection error: request_id=%s url=%s stream_id=%s elapsed_ms=%s error_type=%s error=%s",
                stream_request_id,
                status_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                type(e).__name__,
                str(e),
            )
            raise Exception(f"Livepeer connection error: {type(e).__name__}: {str(e)}")

    async def stop_streaming_session(
        self,
        provider_stream_id: str,
        stop_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Stop a streaming session via POST /process/stream/{streamId}/stop.
        
        Stops and cleans up the running stream.

        Args:
            provider_stream_id: The provider's stream ID to stop
            stop_data: Optional data to pass to the pipeline worker
            **kwargs: Additional options

        Returns:
            Dictionary with stop status (HTTP 204 returns empty content)

        Raises:
            Exception: If the stop request fails
        """
        stream_request_id = kwargs.get("stream_request_id")
        stop_url = f"{self.GPU_RUNNER_URL}/process/stream/{provider_stream_id}/stop"
        request_body = stop_data or {}
        request_started_at = time.perf_counter()
        
        logger.info(
            "Livepeer stream stop request: request_id=%s url=%s stream_id=%s",
            stream_request_id,
            stop_url,
            provider_stream_id,
        )
        
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    stop_url,
                    json=request_body,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    elapsed_ms = round((time.perf_counter() - request_started_at) * 1000, 2)
                    
                    # 204 No Content is the expected success response
                    if response.status not in (200, 204):
                        error_text = await response.text()
                        logger.error(
                            "Livepeer stream stop failed: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s response_headers=%s response_body=%s",
                            stream_request_id,
                            stop_url,
                            provider_stream_id,
                            response.status,
                            elapsed_ms,
                            dict(response.headers),
                            error_text[:500],
                        )
                        raise Exception(f"HTTP {response.status}: {error_text[:300]}")
                    
                    logger.info(
                        "Livepeer stream stop success: request_id=%s url=%s stream_id=%s http_status=%s elapsed_ms=%s",
                        stream_request_id,
                        stop_url,
                        provider_stream_id,
                        response.status,
                        elapsed_ms,
                    )
                    
                    return {
                        "status": "stopped",
                        "stream_id": provider_stream_id
                    }
        except asyncio.TimeoutError as e:
            logger.error(
                "Livepeer stream stop timeout: request_id=%s url=%s stream_id=%s elapsed_ms=%s error=%s",
                stream_request_id,
                stop_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                str(e),
            )
            raise Exception(f"Livepeer timeout: {str(e)}")
        except aiohttp.ClientError as e:
            logger.error(
                "Livepeer stream stop connection error: request_id=%s url=%s stream_id=%s elapsed_ms=%s error_type=%s error=%s",
                stream_request_id,
                stop_url,
                provider_stream_id,
                round((time.perf_counter() - request_started_at) * 1000, 2),
                type(e).__name__,
                str(e),
            )
            raise Exception(f"Livepeer connection error: {type(e).__name__}: {str(e)}")

    def build_livepeer_batch_header(
        self,
        request: Dict[str, Any],
        parameters: Dict[str, Any],
        capability: str,
        timeout_seconds: int = 60,
    ) -> str:
        """
        Build a Livepeer header for BYOC batch request endpoints.

        The payload shape matches the documented format:
        {
            "request": "<json-string>",
            "parameters": "<json-string>",
            "capability": "<capability-name>",
            "timeout_seconds": <seconds>
        }
        """
        livepeer_payload = {
            "request": json.dumps(request),
            "parameters": json.dumps(parameters),
            "capability": capability,
            "timeout_seconds": timeout_seconds,
        }

        return base64.b64encode(json.dumps(livepeer_payload).encode()).decode()