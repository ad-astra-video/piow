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
import base64
import binascii
import secrets
import time
import asyncio
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
from vllm_client import VLLMRealtimeClient, warmup_transcription

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("pytrickle").setLevel(logging.WARNING)

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
CAPABILITY_NAME = "live-transcription"
CAPABILITY_DESCRIPTION = "Transcribe audio to text"
CAPABILITY_NAME_2 = "transcribe-translate"
CAPABILITY_DESCRIPTION_2 = "Transcribe audio to text and translate to a target language"
CAPABILITY_URL = os.environ.get("CAPABILITY_URL", f"https://localhost:{PORT}")
# Per-capability pricing and capacity
LIVE_CAPABILITY_CAPACITY = int(os.environ.get("LIVE_CAPABILITY_CAPACITY", "1"))
LIVE_CAPABILITY_PRICE_PER_UNIT = int(os.environ.get("LIVE_CAPABILITY_PRICE_PER_UNIT", "0"))
LIVE_CAPABILITY_PRICE_SCALING = int(os.environ.get("LIVE_CAPABILITY_PRICE_SCALING", "1"))
BATCH_CAPABILITY_CAPACITY = int(os.environ.get("BATCH_CAPABILITY_CAPACITY", "1"))
BATCH_CAPABILITY_PRICE_PER_UNIT = int(os.environ.get("BATCH_CAPABILITY_PRICE_PER_UNIT", "0"))
BATCH_CAPABILITY_PRICE_SCALING = int(os.environ.get("BATCH_CAPABILITY_PRICE_SCALING", "1"))
REGISTRATION_ENABLED = os.environ.get("REGISTRATION_ENABLED", "true").lower() in ("true", "1", "yes")
REGISTRATION_INTERVAL = int(os.environ.get("REGISTRATION_INTERVAL", "60"))  # seconds

# Randomly generated 16-character token for this worker instance
WORKER_TOKEN = secrets.token_hex(8)
logger.info("Generated worker token: %s", WORKER_TOKEN)

# Suppress urllib3 InsecureRequestWarning (we use verify=False for orchestrator)
urllib3.disable_warnings()

# Runtime mutable state for capacity/price (per capability)
_live_capacity = LIVE_CAPABILITY_CAPACITY
_live_price_per_unit = LIVE_CAPABILITY_PRICE_PER_UNIT
_batch_capacity = BATCH_CAPABILITY_CAPACITY
_batch_price_per_unit = BATCH_CAPABILITY_PRICE_PER_UNIT

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
def _build_registration_payload(name: str, description: str, capacity: int, price_per_unit: int, price_scaling: int) -> Dict[str, Any]:
    """Build the registration request payload for the given capability."""
    return {
        "url": CAPABILITY_URL,
        "name": name,
        "description": description,
        "capacity": capacity,
        "price_per_unit": price_per_unit,
        "price_scaling": price_scaling,
        "token": WORKER_TOKEN,
    }


def _register_capability(name: str, description: str, capacity: int, price_per_unit: int, price_scaling: int) -> bool:
    """Perform a single registration attempt for the given capability with retries."""
    register_req = _build_registration_payload(name, description, capacity, price_per_unit, price_scaling)
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
                logger.info("Capability '%s' registered successfully", name)
                return True
            elif response.status_code == 400:
                logger.error("Orchestrator secret incorrect (HTTP 400) for capability '%s'", name)
                return False
            else:
                logger.info("Attempt %d failed for '%s': HTTP %d - %s", attempt, name, response.status_code, response.text)
        except requests.RequestException as e:
            if attempt == max_retries:
                logger.error("All retries failed for capability '%s': %s", name, e)
            else:
                logger.info("Attempt %d failed for '%s': %s", attempt, name, e)
                time.sleep(delay)
    return False


def _register_to_orchestrator() -> None:
    """Register all capabilities with the orchestrator."""
    _register_capability(CAPABILITY_NAME, CAPABILITY_DESCRIPTION, _live_capacity, _live_price_per_unit, LIVE_CAPABILITY_PRICE_SCALING)
    _register_capability(CAPABILITY_NAME_2, CAPABILITY_DESCRIPTION_2, _batch_capacity, _batch_price_per_unit, BATCH_CAPABILITY_PRICE_SCALING)


def _unregister_capability(name: str) -> None:
    """Unregister a capability from the orchestrator (best-effort, no retries)."""
    if not ORCH_SERVICE_ADDR:
        return
    payload = {"name": name, "url": CAPABILITY_URL, "token": WORKER_TOKEN}
    headers = {
        "Authorization": ORCH_SECRET,
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            "https://" + ORCH_SERVICE_ADDR + "/capability/unregister",
            json=payload,
            headers=headers,
            timeout=5,
            verify=False,
        )
        if response.status_code == 200:
            logger.info("Capability '%s' unregistered successfully", name)
        else:
            logger.warning("Unregister '%s' returned HTTP %d: %s", name, response.status_code, response.text)
    except requests.RequestException as e:
        logger.warning("Failed to unregister capability '%s': %s", name, e)


def _unregister_from_orchestrator() -> None:
    """Unregister all capabilities from the orchestrator."""
    _unregister_capability(CAPABILITY_NAME)
    _unregister_capability(CAPABILITY_NAME_2)


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
    global _live_capacity, _batch_capacity
    try:
        body = await request.json()
        new_capacity = body.get("capacity")
        capability = body.get("capability")  # optional: "live-transcription" or "transcribe-translate"
        if new_capacity is None:
            return aiohttp.web.json_response(
                {"error": "Missing 'capacity' field"}, status=400
            )
        new_capacity = int(new_capacity)
        if new_capacity < 0:
            return aiohttp.web.json_response(
                {"error": "Capacity must be >= 0"}, status=400
            )
        if capability == CAPABILITY_NAME_2:
            _batch_capacity = new_capacity
        elif capability == CAPABILITY_NAME:
            _live_capacity = new_capacity
        else:
            _live_capacity = new_capacity
            _batch_capacity = new_capacity
        logger.info("Capacity updated to %d (capability=%s)", new_capacity, capability or "both")

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "live_capacity": _live_capacity,
            "batch_capacity": _batch_capacity,
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
    global _live_price_per_unit, _batch_price_per_unit
    try:
        body = await request.json()
        new_price = body.get("price_per_unit")
        capability = body.get("capability")  # optional: "live-transcription" or "transcribe-translate"
        if new_price is None:
            return aiohttp.web.json_response(
                {"error": "Missing 'price_per_unit' field"}, status=400
            )
        new_price = int(new_price)
        if new_price < 0:
            return aiohttp.web.json_response(
                {"error": "price_per_unit must be >= 0"}, status=400
            )
        if capability == CAPABILITY_NAME_2:
            _batch_price_per_unit = new_price
        elif capability == CAPABILITY_NAME:
            _live_price_per_unit = new_price
        else:
            _live_price_per_unit = new_price
            _batch_price_per_unit = new_price
        logger.info("Price per unit updated to %d (capability=%s)", new_price, capability or "both")

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "live_price_per_unit": _live_price_per_unit,
            "batch_price_per_unit": _batch_price_per_unit,
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
        "capabilities": [
            {
                "name": CAPABILITY_NAME,
                "description": CAPABILITY_DESCRIPTION,
                "capacity": _live_capacity,
                "price_per_unit": _live_price_per_unit,
                "price_scaling": LIVE_CAPABILITY_PRICE_SCALING,
            },
            {
                "name": CAPABILITY_NAME_2,
                "description": CAPABILITY_DESCRIPTION_2,
                "capacity": _batch_capacity,
                "price_per_unit": _batch_price_per_unit,
                "price_scaling": BATCH_CAPABILITY_PRICE_SCALING,
            },
        ],
        "capability_url": CAPABILITY_URL,
        "registration_enabled": REGISTRATION_ENABLED,
        "registration_interval_seconds": REGISTRATION_INTERVAL,
        "orchestrator_service_address": ORCH_SERVICE_ADDR,
    })


# =============================================================================
# Batch job helpers
# =============================================================================
def _normalize_transcription_result(result: Dict[str, Any], job_id: str, language: str) -> Dict[str, Any]:
    """Ensure a consistent response shape for transcription jobs."""
    if result.get("error"):
        return {
            "job_id": job_id,
            "status": "failed",
            "error": result.get("error"),
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "duration": result.get("duration"),
            "segments": result.get("segments"),
            "words": result.get("words"),
            "speakers": result.get("speakers"),
            "word_count": result.get("word_count"),
            "model": result.get("model", "granite-speech-4.1-2b-plus"),
            "hardware": result.get("hardware", "cpu"),
            "provider": "worker",
            "raw_response": result,
        }

    return {
        "job_id": job_id,
        "status": "completed",
        "text": result.get("text", ""),
        "language": result.get("language", language),
        "duration": result.get("duration"),
        "segments": result.get("segments"),
        "words": result.get("words"),
        "speakers": result.get("speakers"),
        "word_count": result.get("word_count"),
        "model": result.get("model", "granite-speech-4.1-2b-plus"),
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
        "model": result.get("model", "granite-speech-4.1-2b-plus"),
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
            uploaded_name = "audio.wav"
            language = "en"
            fmt = "json"
            with_speakers = False
            with_word_timestamps = False
            source_language = None
            target_language = None

            while field is not None:
                if field.filename and not file_data:
                    uploaded_name = field.filename or "audio.wav"
                    file_data = await field.read()
                elif field.name == "language":
                    language = (await field.read()).decode("utf-8", "replace").strip() or "en"
                elif field.name == "format":
                    fmt = (await field.read()).decode("utf-8", "replace").strip() or "json"
                elif field.name == "with_speakers":
                    val = (await field.read()).decode("utf-8", "replace").strip().lower()
                    with_speakers = val in ("1", "true", "yes")
                elif field.name == "with_word_timestamps":
                    val = (await field.read()).decode("utf-8", "replace").strip().lower()
                    with_word_timestamps = val in ("1", "true", "yes")
                elif field.name == "source_language":
                    source_language = (await field.read()).decode("utf-8", "replace").strip() or None
                elif field.name == "target_language":
                    target_language = (await field.read()).decode("utf-8", "replace").strip() or None
                field = await reader.next()

            if not file_data:
                return aiohttp.web.json_response(
                    {"error": "Missing file in multipart upload"}, status=400
                )

            if len(file_data) == 0:
                return aiohttp.web.json_response(
                    {"error": "Uploaded file is empty"}, status=400
                )

            # Preserve the original extension so libavformat can probe correctly.
            suffix = os.path.splitext(uploaded_name)[1] or ".wav"
            logger.info(
                "Received upload: name=%s size=%d bytes suffix=%s",
                uploaded_name, len(file_data), suffix,
            )

            result = granite_transcriber.transcribe(file_data, language, with_speakers=with_speakers, with_word_timestamps=with_word_timestamps, source_language=source_language, target_language=target_language)

        else:
            body = await request.json()
            audio_url = body.get("audio_url")
            language = body.get("language", "en")
            with_speakers = bool(body.get("with_speakers", False))
            with_word_timestamps = bool(body.get("with_word_timestamps", False))
            source_language = body.get("source_language")
            target_language = body.get("target_language")

            if not audio_url:
                return aiohttp.web.json_response(
                    {"error": "Missing audio_url"}, status=400
                )

            if isinstance(audio_url, str) and audio_url.startswith("data:"):
                marker = ";base64,"
                if marker not in audio_url:
                    return aiohttp.web.json_response(
                        {"error": "Invalid data URL payload (expected base64)"},
                        status=400,
                    )

                try:
                    payload = audio_url.split(marker, 1)[1]
                    audio_bytes = base64.b64decode(payload, validate=True)
                except (ValueError, binascii.Error):
                    return aiohttp.web.json_response(
                        {"error": "Invalid base64 audio payload"},
                        status=400,
                    )

                result = granite_transcriber.transcribe(audio_bytes, language, with_speakers=with_speakers, with_word_timestamps=with_word_timestamps, source_language=source_language, target_language=target_language)
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(audio_url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            return aiohttp.web.json_response(
                                {"error": f"Failed to download audio: HTTP {resp.status}"},
                                status=502,
                            )
                        audio_bytes = await resp.read()

                result = granite_transcriber.transcribe(audio_bytes, language, with_speakers=with_speakers, with_word_timestamps=with_word_timestamps, source_language=source_language, target_language=target_language)

        normalized = _normalize_transcription_result(result, job_id, language)
        transcriptions_db[job_id] = normalized
        if result.get("error"):
            return aiohttp.web.json_response(normalized, status=500)
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
    # 320ms @ 16kHz s16 mono = 16000 * 0.32 * 2 bytes = 10240 bytes
    _SEND_CHUNK_BYTES: int = 10240
    _audio_buffer: bytes = b""
    # Cycle the VLLM WebSocket connection every 15 minutes to avoid
    # long-lived connection issues (stale state, memory growth, etc.).
    _CONNECTION_MAX_AGE_SECONDS: int = 15 * 60

    @model_loader
    async def load(self, **kwargs: dict) -> None:
        """Called once at worker startup. The VLLM websocket connection is
        established per-stream in ``on_start`` so each stream gets a fresh
        realtime session."""
        logger.info("Initializing Live Translation handlers")
        warmup_audio = Path(__file__).parent / "test.wav"
        if warmup_audio.exists():
            await warmup_transcription(ws_url=WS_URL, audio_path=str(warmup_audio))
        else:
            logger.warning("VLLM warmup skipped: test.wav not found at %s", warmup_audio)

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

        # Voxtral generates one token every 80ms.  Count each
        # transcription.delta to derive elapsed generation time.
        _delta_count = 0

        async def _on_transcription(message: Any, is_final: bool = False, **kw) -> None:
            """Forward vLLM events to the pytrickle data channel.

            For transcription.delta, inject timestamp_ms (80ms per delta) on
            the worker side — the vLLM server no longer patches this.
            """
            nonlocal _delta_count
            if processor is None:
                return

            if isinstance(message, dict):
                msg_type = message.get("type")
                if msg_type == "transcription.delta":
                    _delta_count += 1
                    # Use the delta count for timestamp tracking, but only
                    # forward to the data channel when there's actual text.
                    # Empty deltas are heartbeat signals from vLLM for timing.
                    delta = message.get("delta", "")
                    if not delta:
                        return
                    message["timestamp_ms"] = _delta_count * 80
                payload = json.dumps(message)
                await processor.send_data(payload)
                return

            text = message if isinstance(message, str) else str(message)
            if not text or not text.strip():
                return
            _delta_count += 1
            payload = json.dumps({
                "type": "transcription",
                "text": text,
                "is_final": is_final,
                "timestamp_ms": _delta_count * 80,
            })
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

                        # Cycle VLLM WebSocket connection every 15 minutes.
                        # Flush remaining buffered audio before reconnecting so the
                        # next buffer is sent on the new connection.
                        if vllm_client.connection_age() >= LiveTranscriptionWorker._CONNECTION_MAX_AGE_SECONDS:
                            remaining = LiveTranscriptionWorker._audio_buffer
                            LiveTranscriptionWorker._audio_buffer = b""
                            await self._cycle_vllm_connection(remaining)
            except Exception as exc:
                logger.warning(f"VLLM send_audio error: {exc}")
        return [frame]

    async def _cycle_vllm_connection(self, remaining_audio: bytes) -> None:
        """Close the current VLLM WebSocket, reconnect, and flush any
        remaining buffered audio on the new connection.

        Called from ``handle_audio`` when the connection age exceeds
        ``_CONNECTION_MAX_AGE_SECONDS``.
        """
        global vllm_client

        # Commit any audio buffered on the old connection before closing
        if vllm_client and vllm_client.is_connected:
            try:
                await vllm_client.commit_audio()
            except Exception as exc:
                logger.warning(f"VLLM commit before reconnect failed: {exc}")

        # Reconnect
        if not vllm_client:
            logger.warning("No VLLM client to reconnect")
            # Just put the remaining audio back
            LiveTranscriptionWorker._audio_buffer = remaining_audio
            return

        ok = await vllm_client.async_reconnect()
        if not ok:
            logger.error("VLLM reconnection failed; remaining audio will be dropped")
            return

        logger.info(
            "VLLM connection cycled successfully (age reset to 0s)"
        )

        # Send any remaining buffered audio on the new connection
        if remaining_audio:
            try:
                await vllm_client.send_audio(remaining_audio)
            except Exception as exc:
                logger.warning(f"VLLM send remaining audio after reconnect: {exc}")

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
    """Cleanup VLLM connection on server shutdown and unregister capabilities."""
    global vllm_client
    logger.info("Worker shutting down")
    if vllm_client:
        await vllm_client.close()
        vllm_client = None
    if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
        logger.info("Unregistering capabilities from orchestrator")
        _unregister_from_orchestrator()


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

    # Raise the body-size limit to 300 MB so large audio data-URLs are accepted.
    # aiohttp enforces client_max_size at read() time; patching the app attr is
    # the only way to change it after the Application object already exists.
    # 300 MB covers ~2 hours of 16 kHz mono audio after base64 encoding (~4/3 overhead).
    _MAX_BODY = int(os.environ.get("WORKER_MAX_BODY_SIZE", str(300 * 1024 * 1024)))
    processor.server.app._client_max_size = _MAX_BODY
    logger.info("aiohttp client_max_size set to %d bytes", _MAX_BODY)

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
