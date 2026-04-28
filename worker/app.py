#!/usr/bin/env python3
"""
PyTrickle-based Worker for Live Translation Platform.

Uses StreamProcessor as the main entrypoint with decorator-based handlers.
Provides:
  - Batch endpoints: /transcribe, /translate, /process/request/transcribe, /process/request/translate
  - Streaming via pytrickle: /stream/start, /stream/stop, /health, /version, etc.
  - Audio frames forwarded to VLLM realtime websocket.
"""

import os
import sys
import uuid
import json
import secrets
import time
import asyncio
import tempfile
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List

import av
import aiohttp
import numpy as np
import urllib3

from pytrickle import StreamProcessor, VideoFrame, AudioFrame
from pytrickle.decorators import (
    audio_handler,
    video_handler,
    on_stream_start,
    on_stream_stop,
    model_loader,
)

# ---------------------------------------------------------------------------
# Ensure worker package is importable
# ---------------------------------------------------------------------------
WORKER_DIR = Path(__file__).parent.resolve()
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from granite_transcriber import Granite4Transcriber
from vllm_client import VLLMRealtimeClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("WORKER_PORT", "9935"))
WS_URL = os.environ.get("VLLM_WS_URL", "ws://localhost:8080/v1/realtime")
VLLM_SOURCE_LANG = os.environ.get("VLLM_SOURCE_LANG", "en")
VLLM_TARGET_LANG = os.environ.get("VLLM_TARGET_LANG", "es")

# ---------------------------------------------------------------------------
# Orchestrator Registration Configuration
# ---------------------------------------------------------------------------
ORCH_SERVICE_ADDR = os.environ.get("ORCH_SERVICE_ADDR", "")
ORCH_SECRET = os.environ.get("ORCH_SECRET", "")
CAPABILITY_NAME = os.environ.get("CAPABILITY_NAME", "")
if not CAPABILITY_NAME:
    raise RuntimeError("CAPABILITY_NAME environment variable is required")
CAPABILITY_URL = os.environ.get("CAPABILITY_URL", f"https://localhost:{PORT}")
CAPABILITY_DESCRIPTION = os.environ.get("CAPABILITY_DESCRIPTION", "")
if not CAPABILITY_DESCRIPTION:
    raise RuntimeError("CAPABILITY_DESCRIPTION environment variable is required")
CAPABILITY_CAPACITY = int(os.environ.get("CAPABILITY_CAPACITY", "1"))
CAPABILITY_PRICE_PER_UNIT = int(os.environ.get("CAPABILITY_PRICE_PER_UNIT", "0"))
CAPABILITY_PRICE_SCALING = int(os.environ.get("CAPABILITY_PRICE_SCALING", "1"))
REGISTRATION_ENABLED = os.environ.get("REGISTRATION_ENABLED", "true").lower() in ("true", "1", "yes")
REGISTRATION_INTERVAL = int(os.environ.get("REGISTRATION_INTERVAL", "60"))  # seconds

# Randomly generated 16-character token for this worker instance
WORKER_TOKEN = secrets.token_hex(8)
logger.info("Generated worker token: %s", WORKER_TOKEN)

# Suppress urllib3 InsecureRequestWarning (we use verify=False for orchestrator)
urllib3.disable_warnings()

# Runtime mutable state for capacity/price
_current_capacity = CAPABILITY_CAPACITY
_current_price_per_unit = CAPABILITY_PRICE_PER_UNIT

# ---------------------------------------------------------------------------
# Component singletons
# ---------------------------------------------------------------------------
granite_transcriber = Granite4Transcriber()
vllm_client: Optional[VLLMRealtimeClient] = None
processor: Optional[StreamProcessor] = None

# In-memory DB for batch jobs
transcriptions_db: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# Orchestrator Registration
# =============================================================================
def _build_registration_payload() -> Dict[str, Any]:
    """Build the registration request payload with current capacity/price."""
    return {
        "url": CAPABILITY_URL,
        "name": CAPABILITY_NAME,
        "description": CAPABILITY_DESCRIPTION,
        "capacity": _current_capacity,
        "price_per_unit": _current_price_per_unit,
        "price_scaling": CAPABILITY_PRICE_SCALING,
        "token": WORKER_TOKEN,
    }


def _register_to_orchestrator() -> bool:
    """Perform a single registration attempt with retries."""
    register_req = _build_registration_payload()
    headers = {
        "Authorization": ORCH_SECRET,
        "Content-Type": "application/json",
    }
    max_retries = 10
    delay = 2  # seconds
    logger.info("Registering capability: %s", json.dumps(register_req))
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                "https://" + ORCH_SERVICE_ADDR + "/capability/register",
                json=register_req,
                headers=headers,
                timeout=5,
                verify=False,
            )
            if response.status_code == 200:
                logger.info("Capability registered successfully")
                return True
            elif response.status_code == 400:
                logger.error("Orchestrator secret incorrect (HTTP 400)")
                return False
            else:
                logger.info("Attempt %d failed: HTTP %d - %s", attempt, response.status_code, response.text)
        except requests.RequestException as e:
            if attempt == max_retries:
                logger.error("All retries failed: %s", e)
            else:
                logger.info("Attempt %d failed: %s", attempt, e)
                time.sleep(delay)
    return False


async def registration_background_task():
    """Background task that periodically re-registers the worker with the orchestrator."""
    if not REGISTRATION_ENABLED:
        logger.info("Orchestrator registration disabled (REGISTRATION_ENABLED=false)")
        return

    # Initial registration
    _register_to_orchestrator()

    # Periodic re-registration
    while True:
        await asyncio.sleep(REGISTRATION_INTERVAL)
        logger.info("Re-registering with orchestrator (interval: %ds)", REGISTRATION_INTERVAL)
        _register_to_orchestrator()


# =============================================================================
# Registration management routes
# =============================================================================
async def update_capacity_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """
    POST /capability/capacity

    Update the worker's capacity and re-register with the orchestrator.

    Body:
      { "capacity": 5 }
    """
    global _current_capacity
    try:
        body = await request.json()
        new_capacity = body.get("capacity")
        if new_capacity is None:
            return aiohttp.web.json_response(
                {"error": "Missing 'capacity' field"}, status=400
            )
        new_capacity = int(new_capacity)
        if new_capacity < 0:
            return aiohttp.web.json_response(
                {"error": "Capacity must be >= 0"}, status=400
            )
        _current_capacity = new_capacity
        logger.info("Capacity updated to %d", new_capacity)

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "capacity": _current_capacity,
            "message": "Capacity updated successfully",
        })
    except Exception as exc:
        logger.exception("Error updating capacity")
        return aiohttp.web.json_response({"error": str(exc)}, status=500)


async def update_price_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """
    POST /capability/price

    Update the worker's price per unit and re-register with the orchestrator.

    Body:
      { "price_per_unit": 1.5 }
    """
    global _current_price_per_unit
    try:
        body = await request.json()
        new_price = body.get("price_per_unit")
        if new_price is None:
            return aiohttp.web.json_response(
                {"error": "Missing 'price_per_unit' field"}, status=400
            )
        new_price = int(new_price)
        if new_price < 0:
            return aiohttp.web.json_response(
                {"error": "price_per_unit must be >= 0"}, status=400
            )
        _current_price_per_unit = new_price
        logger.info("Price per unit updated to %d", new_price)

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "price_per_unit": _current_price_per_unit,
            "message": "Price updated successfully",
        })
    except Exception as exc:
        logger.exception("Error updating price")
        return aiohttp.web.json_response({"error": str(exc)}, status=500)


async def capability_status_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """
    GET /capability/status

    Return the current registration state.
    """
    return aiohttp.web.json_response({
        "capability_name": CAPABILITY_NAME,
        "capability_url": CAPABILITY_URL,
        "description": CAPABILITY_DESCRIPTION,
        "capacity": _current_capacity,
        "price_per_unit": _current_price_per_unit,
        "price_scaling": CAPABILITY_PRICE_SCALING,
        "registration_enabled": REGISTRATION_ENABLED,
        "registration_interval_seconds": REGISTRATION_INTERVAL,
        "orchestrator_service_address": ORCH_SERVICE_ADDR,
    })


# =============================================================================
# Batch job helpers
# =============================================================================
def _normalize_transcription_result(result: Dict[str, Any], job_id: str, language: str) -> Dict[str, Any]:
    """Ensure a consistent response shape for transcription jobs."""
    return {
        "job_id": job_id,
        "status": "completed",
        "text": result.get("text", ""),
        "language": result.get("language", language),
        "duration": result.get("duration"),
        "segments": result.get("segments"),
        "word_count": result.get("word_count"),
        "model": result.get("model", "granite-4.0-1b"),
        "hardware": result.get("hardware", "cpu"),
        "provider": "worker",
        "raw_response": result,
    }


def _normalize_translation_result(
    result: Dict[str, Any], job_id: str, text: str, source_lang: str, target_lang: str
) -> Dict[str, Any]:
    """Ensure a consistent response shape for translation jobs."""
    return {
        "job_id": job_id,
        "status": "completed",
        "original_text": result.get("original_text", text),
        "translated_text": result.get("translated_text", ""),
        "source_language": result.get("source_language", source_lang),
        "target_language": result.get("target_language", target_lang),
        "token_count": result.get("token_count"),
        "model": result.get("model", "granite-4.0-1b"),
        "hardware": result.get("hardware", "cpu"),
        "provider": "worker",
        "raw_response": result,
    }


# =============================================================================
# aiohttp route handlers (batch endpoints)
# =============================================================================
async def transcribe_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """
    POST /transcribe  (and alias /process/request/transcribe)

    Supports:
      - multipart/form-data with file + optional language/format
      - JSON with audio_url + optional language/format
    """
    logger.info("Received transcription request")
    job_id = str(uuid.uuid4())

    try:
        content_type = request.headers.get("Content-Type", "")

        if content_type.startswith("multipart/form-data"):
            reader = await request.multipart()
            field = await reader.next()

            file_data = None
            language = "en"
            fmt = "json"

            while field is not None:
                if field.filename and not file_data:
                    file_data = await field.read()
                elif field.name == "language":
                    language = (await field.read()).decode("utf-8", "replace").strip() or "en"
                elif field.name == "format":
                    fmt = (await field.read()).decode("utf-8", "replace").strip() or "json"
                field = await reader.next()

            if not file_data:
                return aiohttp.web.json_response(
                    {"error": "Missing file in multipart upload"}, status=400
                )

            suffix = ".wav"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            try:
                result = granite_transcriber.transcribe(tmp_path, language)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        else:
            body = await request.json()
            audio_url = body.get("audio_url")
            language = body.get("language", "en")

            if not audio_url:
                return aiohttp.web.json_response(
                    {"error": "Missing audio_url"}, status=400
                )

            tmp_path = tempfile.mktemp(suffix=".wav")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(audio_url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            return aiohttp.web.json_response(
                                {"error": f"Failed to download audio: HTTP {resp.status}"},
                                status=502,
                            )
                        with open(tmp_path, "wb") as f:
                            f.write(await resp.read())

                result = granite_transcriber.transcribe(tmp_path, language)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        normalized = _normalize_transcription_result(result, job_id, language)
        transcriptions_db[job_id] = normalized
        return aiohttp.web.json_response(normalized)

    except Exception as exc:
        logger.exception("Transcription error")
        return aiohttp.web.json_response({"error": str(exc)}, status=500)


async def translate_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """
    POST /translate  (and alias /process/request/translate)

    Expects JSON body:
      {
        "text": "...",
        "source_language": "en",
        "target_language": "es"
      }
    """
    logger.info("Received translation request")
    job_id = str(uuid.uuid4())

    try:
        body = await request.json()
        text = body.get("text")
        source_lang = body.get("source_language", "en")
        target_lang = body.get("target_language", "es")

        if not text:
            return aiohttp.web.json_response(
                {"error": "Missing text parameter"}, status=400
            )

        result = granite_transcriber.translate(text, source_lang, target_lang)
        normalized = _normalize_translation_result(result, job_id, text, source_lang, target_lang)
        return aiohttp.web.json_response(normalized)

    except Exception as exc:
        logger.exception("Translation error")
        return aiohttp.web.json_response({"error": str(exc)}, status=500)


async def health_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Extended health check with worker-specific info."""
    return aiohttp.web.json_response({
        "status": "healthy",
        "service": CAPABILITY_NAME,
        "version": "2.0.0",
        "granite_transcriber": {
            "available": granite_transcriber.is_available(),
            "loaded": granite_transcriber.is_loaded,
        },
        "vllm_client": {
            "connected": vllm_client.is_connected if vllm_client else False,
        },
        "stored_transcriptions": len(transcriptions_db),
        "timestamp": int(time.time()),
    })


async def root_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Root endpoint."""
    return aiohttp.web.json_response({
        "message": "Live Translation Worker (PyTrickle) is running",
        "status": "healthy",
    })


CUSTOM_ROUTES = [
    ("GET", "/", root_handler),
    ("GET", "/health", health_handler),
    ("POST", "/transcribe", transcribe_handler),
    ("POST", "/process/request/transcribe", transcribe_handler),
    ("POST", "/translate", translate_handler),
    ("POST", "/process/request/translate", translate_handler),
    # Capability / registration management routes
    ("GET", "/capability/status", capability_status_handler),
    ("POST", "/capability/capacity", update_capacity_handler),
    ("POST", "/capability/price", update_price_handler),
]


# =============================================================================
# StreamProcessor handlers (decorator-based)
# =============================================================================
class LiveTranscriptionWorker:
    """Decorator-based handlers for pytrickle StreamProcessor."""

    _audio_frame_count: int = 0
    _resampler: av.AudioResampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    # 80ms @ 16kHz s16 mono = 16000 * 0.08 * 2 bytes = 2560 bytes
    _SEND_CHUNK_BYTES: int = 2560
    _audio_buffer: bytes = b""

    @model_loader
    async def load(self, **kwargs: dict) -> None:
        """Called once at worker startup. The VLLM websocket connection is
        established per-stream in ``on_start`` so each stream gets a fresh
        realtime session."""
        logger.info("Initializing Live Translation handlers")

    @on_stream_start
    async def on_start(self, params: Dict[str, Any]) -> None:
        """Called when a trickle stream starts. Opens a fresh VLLM realtime
        websocket session for this stream."""
        global vllm_client
        logger.info(f"Stream started with params: {params}")

        # Reset per-stream audio buffering state
        LiveTranscriptionWorker._audio_frame_count = 0
        LiveTranscriptionWorker._audio_buffer = b""

        # Close any stale client from a previous stream just in case
        if vllm_client is not None:
            try:
                await vllm_client.close()
            except Exception as exc:
                logger.warning(f"Error closing stale VLLM client: {exc}")
            vllm_client = None

        vllm_client = VLLMRealtimeClient(
            ws_url=WS_URL,
            source_lang=VLLM_SOURCE_LANG,
            target_lang=VLLM_TARGET_LANG,
        )

        async def _on_transcription(text: str, is_final: bool = False, **kw) -> None:
            """Forward vLLM transcription results back through the pytrickle data channel."""
            if processor is None:
                return
            if not text or not text.strip():
                return
            payload = json.dumps({"type": "transcription", "text": text, "is_final": is_final})
            logger.info(
                f"Sending transcription on data channel: is_final={is_final}, len={len(text)}"
            )
            await processor.send_data(payload)

        vllm_client.set_text_callback(_on_transcription)

        try:
            await vllm_client.connect()
            logger.info("VLLM client connected successfully")
        except Exception as exc:
            logger.warning(f"Could not connect to VLLM on stream start: {exc}")

    @video_handler
    async def handle_video(self, frame: VideoFrame) -> VideoFrame:
        """Pass video frames through unchanged."""
        return frame

    @audio_handler
    async def handle_audio(self, frame: AudioFrame) -> List[AudioFrame]:
        """Forward audio frames to the VLLM realtime websocket."""
        LiveTranscriptionWorker._audio_frame_count += 1
        if vllm_client and vllm_client.is_connected:
            try:
                samples = frame.samples  # shape (channels, samples), dtype float32
                # Log sample rate periodically
                if LiveTranscriptionWorker._audio_frame_count % 100 == 1:
                    logger.info(
                        f"handle_audio: frame={LiveTranscriptionWorker._audio_frame_count}, "
                        f"sample_rate={getattr(frame, 'rate', 'unknown')}Hz, "
                        f"shape={samples.shape}, dtype={samples.dtype}"
                    )
                # Resample to 16kHz mono PCM16 using PyAV
                n_channels = samples.shape[0] if samples.ndim > 1 else 1
                layout = 'stereo' if n_channels == 2 else 'mono'
                av_frame = av.AudioFrame.from_ndarray(
                    samples if samples.ndim > 1 else samples[np.newaxis, :],
                    format='fltp',
                    layout=layout,
                )
                av_frame.sample_rate = getattr(frame, 'rate', 48000)
                resampled_frames = LiveTranscriptionWorker._resampler.resample(av_frame)
                audio_bytes = b''.join(bytes(rf.planes[0]) for rf in resampled_frames)
                if audio_bytes:
                    LiveTranscriptionWorker._audio_buffer += audio_bytes
                    while len(LiveTranscriptionWorker._audio_buffer) >= LiveTranscriptionWorker._SEND_CHUNK_BYTES:
                        chunk = LiveTranscriptionWorker._audio_buffer[:LiveTranscriptionWorker._SEND_CHUNK_BYTES]
                        LiveTranscriptionWorker._audio_buffer = LiveTranscriptionWorker._audio_buffer[LiveTranscriptionWorker._SEND_CHUNK_BYTES:]
                        if LiveTranscriptionWorker._audio_frame_count % 100 == 1:
                            n_samples = len(chunk) // 2
                            pcm16 = np.frombuffer(chunk, dtype=np.int16)
                            rms = float(np.sqrt(np.mean(pcm16.astype(np.float32) ** 2)))
                            peak = int(np.abs(pcm16).max())
                            logger.info(
                                f"send_audio: chunk bytes={len(chunk)}, "
                                f"pcm16_samples={n_samples}, "
                                f"~{n_samples/16000*1000:.1f}ms of audio, "
                                f"rms={rms:.1f}, peak={peak}"
                            )
                        await vllm_client.send_audio(chunk)
            except Exception as exc:
                logger.warning(f"VLLM send_audio error: {exc}")
        return [frame]

    @on_stream_stop
    async def on_stop(self) -> None:
        """Called when a trickle stream stops. Closes the per-stream VLLM
        realtime websocket so the next stream gets a fresh session."""
        global vllm_client
        logger.info("Stream stopped")
        if vllm_client is not None:
            try:
                await vllm_client.close()
            except Exception as exc:
                logger.warning(f"Error closing VLLM client on stream stop: {exc}")
            vllm_client = None


# =============================================================================
# Startup / shutdown
# =============================================================================
async def on_shutdown(app: aiohttp.web.Application):
    """Cleanup VLLM connection on server shutdown."""
    global vllm_client
    logger.info("Worker shutting down")
    if vllm_client:
        await vllm_client.close()
        vllm_client = None


# =============================================================================
# Main entry point
# =============================================================================
async def main() -> None:
    """Create and run the StreamProcessor with custom batch routes."""
    global processor
    handlers = LiveTranscriptionWorker()
    processor = StreamProcessor.from_handlers(
        handlers,
        name=CAPABILITY_NAME,
        port=PORT,
        host=HOST,
        enable_default_routes=True,
        ssl=True,
    )
    # Register custom batch routes directly on the aiohttp app router
    # (avoids pytrickle custom_routes API incompatibility with tuples)
    for method, path, handler in CUSTOM_ROUTES:
        processor.server.app.router.add_route(method, path, handler)

    processor.server.app.on_shutdown.append(on_shutdown)

    # Start the periodic orchestrator registration background task
    if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
        logger.info("Starting orchestrator registration background task (interval: %ds)", REGISTRATION_INTERVAL)
        asyncio.create_task(registration_background_task())
    elif REGISTRATION_ENABLED and not ORCH_SERVICE_ADDR:
        logger.warning("REGISTRATION_ENABLED is true but ORCH_SERVICE_ADDR is not set. Skipping registration.")

    logger.info("Starting PyTrickle worker on %s:%s with SSL", HOST, PORT)
    await processor.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
