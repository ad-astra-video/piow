#!/usr/bin/env python3
"""
PyTrickle-based Worker for Live Translation Platform.

Uses StreamProcessor as the main entrypoint with decorator-based handlers.
Provides streaming-only behavior for live transcription, sentence translation
updates, and orchestrator registration.
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
    param_updater,
)

# ---------------------------------------------------------------------------
# Ensure worker package is importable
# ---------------------------------------------------------------------------
WORKER_DIR = Path(__file__).parent.resolve()
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from gemma_client import GemmaClient
from gemma_prompts import get_analysis_prompt
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
CAPABILITY_URL = os.environ.get("CAPABILITY_URL", f"https://localhost:{PORT}")
# Per-capability pricing and capacity
LIVE_CAPABILITY_CAPACITY = int(os.environ.get("LIVE_CAPABILITY_CAPACITY", "1"))
LIVE_CAPABILITY_PRICE_PER_UNIT = int(os.environ.get("LIVE_CAPABILITY_PRICE_PER_UNIT", "0"))
LIVE_CAPABILITY_PRICE_SCALING = int(os.environ.get("LIVE_CAPABILITY_PRICE_SCALING", "1"))
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

# ---------------------------------------------------------------------------
# Component singletons
# ---------------------------------------------------------------------------
gemma_translator = GemmaClient()
vllm_client: Optional[VLLMRealtimeClient] = None
processor: Any = None

# Legacy batch-job storage kept only so the disabled helper functions still parse.
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
    global _live_capacity
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
        _live_capacity = new_capacity
        logger.info("Capacity updated to %d", new_capacity)

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "live_capacity": _live_capacity,
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
    global _live_price_per_unit
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
        _live_price_per_unit = new_price
        logger.info("Price per unit updated to %d", new_price)

        # Re-register immediately if registration is enabled
        if REGISTRATION_ENABLED and ORCH_SERVICE_ADDR:
            _register_to_orchestrator()

        return aiohttp.web.json_response({
            "live_price_per_unit": _live_price_per_unit,
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
            "model": result.get("model", "gemma-4-e4b"),
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
        "model": result.get("model", "gemma-4-e4b"),
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
        "model": result.get("model", "gemma-4-e4b"),
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
    logger.info("Received transcription request, but batch transcription is disabled")
    return aiohttp.web.json_response({"error": "Batch transcription is no longer supported"}, status=410)



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
    logger.info("Received translation request, but batch translation is disabled")
    return aiohttp.web.json_response({"error": "Batch translation is no longer supported"}, status=410)


async def health_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Extended health check with worker-specific info."""
    return aiohttp.web.json_response({
        "status": "healthy",
        "service": CAPABILITY_NAME,
        "version": "2.0.0",
        "gemma_translation": {
            "configured": gemma_translator.is_configured,
            "base_url": gemma_translator.base_url,
            "model": gemma_translator.model,
        },
        "vllm_client": {
            "connected": vllm_client.is_connected if vllm_client else False,
        },
        "stored_transcriptions": 0,
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
    # Cycle the VLLM WebSocket connection every 15 minutes to avoid
    # long-lived connection issues (stale state, memory growth, etc.).
    _CONNECTION_MAX_AGE_SECONDS: int = 15 * 60
    def __init__(self) -> None:
        self.analysis_enabled: bool = False
        self.analysis_mode: str = "multimodal"
        self.analysis_audio_chunk_seconds: float = 10.0
        self.analysis_video_chunk_seconds: float = 10.0
        self.analysis_max_tokens: int = 1024
        self.live_transcription_enabled: bool = True
        self.analysis_prompt: str = self._default_analysis_prompt(self.analysis_mode)
        self.analysis_prompt_custom: bool = False
        self.analysis_response_format: Optional[Dict[str, Any]] = None
        self._analysis_pending_text: str = ""
        self._analysis_pending_audio: bytes = b""
        self._analysis_audio_samples_total: int = 0
        self._transcription_audio_samples_sent: int = 0
        self._analysis_last_run_ts_ms: int = 0
        self._analysis_last_seen_ts_ms: int = 0
        self._analysis_audio_request_count: int = 0
        self._stream_started_monotonic_s: Optional[float] = None

    def _default_analysis_prompt(self, mode: str) -> str:
        return get_analysis_prompt(mode)

    def _resolve_analysis_timestamp_ms(self, timestamp_ms: Optional[int]) -> int:
        """Resolve a stable non-negative timestamp for emitted analysis events."""
        if isinstance(timestamp_ms, (int, float)):
            return max(int(timestamp_ms), 0)

        if self._analysis_last_seen_ts_ms > 0:
            return int(self._analysis_last_seen_ts_ms)
        if self._analysis_last_run_ts_ms > 0:
            return int(self._analysis_last_run_ts_ms)

        if isinstance(self._stream_started_monotonic_s, (int, float)):
            elapsed_ms = int((time.monotonic() - float(self._stream_started_monotonic_s)) * 1000)
            return max(elapsed_ms, 0)

        return 0

    def _transcription_sent_timestamp_ms(self) -> int:
        """Return transcription timeline time derived from sent PCM16 samples."""
        return int((self._transcription_audio_samples_sent * 1000) / 16000)

    def _mark_transcription_audio_sent(self, audio_pcm16: bytes) -> None:
        """Advance transcription timeline by the number of sent PCM16 samples."""
        if not audio_pcm16:
            return
        self._transcription_audio_samples_sent += len(audio_pcm16) // 2

    def _parse_structured_analysis_text(self, analysis_text: str) -> Optional[Any]:
        """Parse structured JSON output from analysis text, including fenced JSON blocks."""
        if not analysis_text:
            return None

        candidate = analysis_text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                candidate = "\n".join(lines[1:-1]).strip()
                if candidate.lower().startswith("json"):
                    candidate = candidate[4:].strip()

        parsed = self._try_parse_json_candidate(candidate)
        if parsed is not None:
            return parsed

        extracted_candidate = self._extract_leading_json_candidate(candidate)
        if extracted_candidate and extracted_candidate != candidate:
            parsed = self._try_parse_json_candidate(extracted_candidate)
            if parsed is not None:
                return parsed

        repaired_candidate = self._repair_truncated_json(candidate)
        if repaired_candidate and repaired_candidate != candidate:
            parsed = self._try_parse_json_candidate(repaired_candidate)
            if parsed is not None:
                return parsed

        if extracted_candidate:
            repaired_extracted = self._repair_truncated_json(extracted_candidate)
            if repaired_extracted and repaired_extracted != extracted_candidate:
                return self._try_parse_json_candidate(repaired_extracted)

        return None

    @staticmethod
    def _try_parse_json_candidate(candidate: str) -> Optional[Any]:
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_leading_json_candidate(text: str) -> Optional[str]:
        if not text:
            return None

        stripped = text.lstrip()
        decoder = json.JSONDecoder()
        try:
            _, end = decoder.raw_decode(stripped)
            return stripped[:end]
        except json.JSONDecodeError:
            pass

        brace_idx = stripped.find("{")
        bracket_idx = stripped.find("[")
        candidates = [idx for idx in (brace_idx, bracket_idx) if idx >= 0]
        if not candidates:
            return None
        start = min(candidates)
        return stripped[start:].strip()

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[str]:
        if not text:
            return None

        stack: list[str] = []
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in "[{":
                stack.append(ch)
            elif ch in "]}":
                if stack:
                    opener = stack[-1]
                    if (opener == "[" and ch == "]") or (opener == "{" and ch == "}"):
                        stack.pop()

        if in_string:
            # If truncation happened mid-string, don't guess missing content.
            return None

        if not stack:
            return text

        closers = []
        while stack:
            opener = stack.pop()
            closers.append("]" if opener == "[" else "}")
        return text + "".join(closers)

    def _coerce_signal_data(self, analysis_text: str) -> Any:
        """Return structured signal data, or an error envelope when JSON parsing fails."""
        structured_data = self._parse_structured_analysis_text(analysis_text)
        if structured_data is not None:
            return structured_data
        return {
            "_parse_error": "invalid_json",
            "_raw_text": analysis_text,
        }

    @staticmethod
    def _is_placeholder_signal_timestamp(value: Any) -> bool:
        if not isinstance(value, str):
            return True
        normalized = value.strip()
        return normalized in {"", "0", "0:00", "00:00", "00:0", "0:0"}

    @staticmethod
    def _format_signal_timestamp(ms: int) -> str:
        safe_ms = max(int(ms or 0), 0)
        total_seconds = safe_ms // 1000
        hh = total_seconds // 3600
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def _normalize_signal_item_timestamps(self, signal_data: Any, fallback_timestamp_ms: int) -> Any:
        """Replace placeholder per-item timestamps with the resolved event timestamp."""
        if not isinstance(signal_data, dict):
            return signal_data

        items = signal_data.get("items")
        if not isinstance(items, list):
            return signal_data

        normalized_items = []
        replaced_any = False
        replacement_ts = self._format_signal_timestamp(fallback_timestamp_ms)
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            if self._is_placeholder_signal_timestamp(normalized_item.get("timestamp")):
                normalized_item["timestamp"] = replacement_ts
                replaced_any = True
            normalized_items.append(normalized_item)

        if not replaced_any:
            return signal_data

        normalized_signal = dict(signal_data)
        normalized_signal["items"] = normalized_items
        return normalized_signal

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
        self._analysis_pending_text = ""
        self._analysis_pending_audio = b""
        self._analysis_audio_samples_total = 0
        self._transcription_audio_samples_sent = 0
        self._analysis_last_run_ts_ms = 0
        self._analysis_last_seen_ts_ms = 0
        self._stream_started_monotonic_s = time.monotonic()
        self._apply_analysis_params(params)

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

        async def _on_transcription(message: Any, is_final: bool = False, **kw) -> None:
            """Forward vLLM events to the pytrickle data channel.

            For transcription.delta, inject timestamp_ms from the sent-audio
            sample clock to keep transcription and analysis on one timeline.
            """
            if processor is None:
                return

            if not self.live_transcription_enabled:
                return

            if isinstance(message, dict):
                msg_type = message.get("type")
                if msg_type == "transcription.delta":
                    # Only forward deltas with text.
                    # Empty deltas are heartbeat signals from vLLM for timing.
                    delta = message.get("delta", "")
                    if not delta:
                        return
                    timestamp_ms = self._transcription_sent_timestamp_ms()
                    message["timestamp_ms"] = timestamp_ms
                payload = json.dumps(message)
                await processor.send_data(payload)
                if msg_type == "transcription.delta":
                    self._queue_live_analysis(
                        message.get("delta", ""),
                        message.get("timestamp_ms", self._transcription_sent_timestamp_ms()),
                        is_final=False,
                    )
                return

            text = message if isinstance(message, str) else str(message)
            if not text or not text.strip():
                return
            timestamp_ms = self._transcription_sent_timestamp_ms()
            payload = json.dumps({
                "type": "transcription",
                "text": text,
                "is_final": is_final,
                "timestamp_ms": timestamp_ms,
            })
            logger.info(
                f"Sending transcription on data channel: is_final={is_final}, len={len(text)}"
            )
            await processor.send_data(payload)
            self._queue_live_analysis(text.strip(), timestamp_ms, is_final=is_final)

        vllm_client.set_text_callback(_on_transcription)

        if self.live_transcription_enabled:
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
                if self._should_use_audio_direct_analysis():
                    self._queue_live_audio_analysis(audio_bytes)

                if self.live_transcription_enabled and vllm_client and vllm_client.is_connected:
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
                        self._mark_transcription_audio_sent(chunk)

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
                self._mark_transcription_audio_sent(remaining_audio)
            except Exception as exc:
                logger.warning(f"VLLM send remaining audio after reconnect: {exc}")

    @param_updater
    async def update_params(self, params: Dict[str, Any]) -> None:
        """Handle mid-stream parameter updates delivered via the stream update route."""
        self._apply_analysis_params(params)

        sentence = params.get("translate_sentence")
        if not isinstance(sentence, str) or not sentence.strip() or processor is None:
            return

        source_lang = params.get("source_language", "en")
        target_lang = params.get("target_language", "es")
        asyncio.create_task(self._translate_sentence_async(sentence.strip(), source_lang, target_lang))

    def _apply_analysis_params(self, params: Dict[str, Any]) -> None:
        """Apply analysis settings from stream start/update params."""
        if not isinstance(params, dict):
            return

        mode_changed = False
        analysis_enabled = params.get("analysis_enabled")
        if analysis_enabled is not None:
            self.analysis_enabled = bool(analysis_enabled)

        live_transcription_enabled = params.get("live_transcription_enabled")
        if live_transcription_enabled is not None:
            self.live_transcription_enabled = bool(live_transcription_enabled)

        analysis_mode = params.get("analysis_mode")
        if isinstance(analysis_mode, str) and analysis_mode in {"multimodal", "audio_only", "video_only"}:
            mode_changed = self.analysis_mode != analysis_mode
            self.analysis_mode = analysis_mode

        analysis_audio_chunk_seconds = params.get("analysis_audio_chunk_seconds")
        if analysis_audio_chunk_seconds is not None:
            try:
                chunk_seconds = float(analysis_audio_chunk_seconds)
                if chunk_seconds > 0:
                    self.analysis_audio_chunk_seconds = chunk_seconds
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid analysis_audio_chunk_seconds: %s", analysis_audio_chunk_seconds)

        analysis_video_chunk_seconds = params.get("analysis_video_chunk_seconds")
        if analysis_video_chunk_seconds is not None:
            try:
                chunk_seconds = float(analysis_video_chunk_seconds)
                if chunk_seconds > 0:
                    self.analysis_video_chunk_seconds = chunk_seconds
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid analysis_video_chunk_seconds: %s", analysis_video_chunk_seconds)

        analysis_max_tokens = params.get("analysis_max_tokens")
        if analysis_max_tokens is not None:
            try:
                max_tokens = int(analysis_max_tokens)
                if max_tokens > 0:
                    self.analysis_max_tokens = max_tokens
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid analysis_max_tokens: %s", analysis_max_tokens)

        analysis_prompt = params.get("analysis_prompt")
        if analysis_prompt is not None:
            prompt_text = str(analysis_prompt).strip()
            if prompt_text:
                self.analysis_prompt = prompt_text
                self.analysis_prompt_custom = True
            else:
                self.analysis_prompt = self._default_analysis_prompt(self.analysis_mode)
                self.analysis_prompt_custom = False
        elif mode_changed and not self.analysis_prompt_custom:
            self.analysis_prompt = self._default_analysis_prompt(self.analysis_mode)

        analysis_response_format = params.get("analysis_response_format")
        if analysis_response_format is not None:
            if isinstance(analysis_response_format, dict) and analysis_response_format:
                self.analysis_response_format = analysis_response_format
            else:
                self.analysis_response_format = None

    def _queue_live_analysis(self, text: str, timestamp_ms: int, is_final: bool) -> None:
        """Schedule analysis only on elapsed chunk windows for stable cadence."""
        if not self.analysis_enabled or not self.live_transcription_enabled:
            return

        candidate = (text or "").strip()
        if not candidate:
            return

        self._analysis_last_seen_ts_ms = int(timestamp_ms)

        if self._analysis_pending_text:
            self._analysis_pending_text = f"{self._analysis_pending_text} {candidate}".strip()
        else:
            self._analysis_pending_text = candidate

        chunk_seconds = self.analysis_video_chunk_seconds if self.analysis_mode == "video_only" else self.analysis_audio_chunk_seconds
        chunk_ms = max(int(chunk_seconds * 1000), 250)
        elapsed_ms = max(0, int(timestamp_ms) - int(self._analysis_last_run_ts_ms or 0))
        should_run = elapsed_ms >= chunk_ms

        if not should_run:
            return

        prompt_text = self._analysis_pending_text.strip()
        if not prompt_text:
            return

        self._analysis_pending_text = ""
        self._analysis_last_run_ts_ms = int(timestamp_ms)
        asyncio.create_task(self._run_live_analysis_async(prompt_text, int(timestamp_ms)))

    def _should_use_audio_direct_analysis(self) -> bool:
        """Audio-direct analysis runs only when transcription is off and mode uses audio."""
        if not self.analysis_enabled or self.live_transcription_enabled:
            return False
        return self.analysis_mode in {"audio_only", "multimodal"}

    def _queue_live_audio_analysis(self, audio_pcm16: bytes, is_final: bool = False) -> None:
        """Schedule audio-direct analysis on elapsed audio windows or final flush."""
        if not self._should_use_audio_direct_analysis():
            return

        if not audio_pcm16 and not is_final:
            return

        if audio_pcm16:
            self._analysis_pending_audio += audio_pcm16
            self._analysis_audio_samples_total += len(audio_pcm16) // 2

        if not self._analysis_pending_audio:
            return

        timestamp_ms = int((self._analysis_audio_samples_total * 1000) / 16000)
        chunk_ms = max(int(self.analysis_audio_chunk_seconds * 1000), 250)
        elapsed_ms = max(0, timestamp_ms - int(self._analysis_last_run_ts_ms or 0))
        should_run = is_final or elapsed_ms >= chunk_ms
        if not should_run:
            return

        audio_chunk = self._analysis_pending_audio
        self._analysis_pending_audio = b""
        self._analysis_last_run_ts_ms = int(timestamp_ms)
        logger.info(
            "Audio analysis trigger: mode=%s final=%s chunk_bytes=%d chunk_ms=%d elapsed_ms=%d timestamp_ms=%d",
            self.analysis_mode,
            is_final,
            len(audio_chunk),
            chunk_ms,
            elapsed_ms,
            timestamp_ms,
        )
        asyncio.create_task(self._run_live_audio_analysis_async(audio_chunk, int(timestamp_ms)))

    async def _run_live_analysis_async(self, text: str, timestamp_ms: Optional[int] = None) -> None:
        """Call Gemma with the current analysis prompt and emit analysis events."""
        if not self.analysis_enabled or not text or processor is None:
            return

        try:
            result = await gemma_translator.analyze(
                text=text,
                prompt=self.analysis_prompt,
                mode=self.analysis_mode,
                max_tokens=self.analysis_max_tokens,
                response_format=self.analysis_response_format,
            )
            analysis_text = ""
            suppressed = False
            if isinstance(result, dict):
                analysis_text = (result.get("analysis_text") or "").strip()
                suppressed = bool(result.get("suppressed"))

            if suppressed:
                logger.debug(
                    "Live analysis suppressed for stream output: mode=%s reason=%s",
                    self.analysis_mode,
                    result.get("suppression_reason") if isinstance(result, dict) else "unknown",
                )
                return

            if analysis_text and processor is not None:
                resolved_timestamp_ms = self._resolve_analysis_timestamp_ms(timestamp_ms)
                if self.analysis_response_format:
                    signal_data = self._coerce_signal_data(analysis_text)
                    if isinstance(signal_data, dict) and signal_data.get("_parse_error"):
                        payload = {
                            "type": "analysis.error",
                            "mode": self.analysis_mode,
                            "error": "Analysis response was not valid JSON",
                            "parse_error": signal_data.get("_parse_error"),
                            "raw_text": signal_data.get("_raw_text"),
                            "timestamp_ms": resolved_timestamp_ms,
                        }
                    else:
                        signal_data = self._normalize_signal_item_timestamps(signal_data, resolved_timestamp_ms)
                        payload = {
                            "type": "analysis.signal",
                            "mode": self.analysis_mode,
                            "data": signal_data,
                            "timestamp_ms": resolved_timestamp_ms,
                        }
                else:
                    payload = {
                        "type": "analysis.done",
                        "mode": self.analysis_mode,
                        "analysis_source": "video",
                        "text": analysis_text,
                        "timestamp_ms": resolved_timestamp_ms,
                    }
                await processor.send_data(json.dumps(payload))
            elif processor is not None and isinstance(result, dict) and result.get("error"):
                await processor.send_data(json.dumps({
                    "type": "analysis.error",
                    "error": result.get("error"),
                    "mode": self.analysis_mode,
                }))
        except Exception as exc:
            logger.error("Live analysis failed: %s", exc)
            if processor is not None:
                await processor.send_data(json.dumps({
                    "type": "analysis.error",
                    "error": str(exc),
                    "mode": self.analysis_mode,
                }))

    async def _run_live_audio_analysis_async(self, audio_pcm16: bytes, timestamp_ms: Optional[int] = None) -> None:
        """Call Gemma with buffered PCM16 audio and emit analysis events."""
        if not self.analysis_enabled or not audio_pcm16 or processor is None:
            return

        self._analysis_audio_request_count += 1
        request_id = self._analysis_audio_request_count
        chunk_duration_ms = int((len(audio_pcm16) // 2) * 1000 / 16000)
        logger.info(
            "Audio analysis request #%d: mode=%s bytes=%d duration_ms=%d timestamp_ms=%s prompt_len=%d",
            request_id,
            self.analysis_mode,
            len(audio_pcm16),
            chunk_duration_ms,
            str(timestamp_ms),
            len(self.analysis_prompt or ""),
        )

        try:
            result = await gemma_translator.analyze_audio(
                audio_pcm16=audio_pcm16,
                sample_rate_hz=16000,
                prompt=self.analysis_prompt,
                mode=self.analysis_mode,
                max_tokens=self.analysis_max_tokens,
                response_format=self.analysis_response_format,
            )
            analysis_text = ""
            suppressed = False
            error_text = ""
            if isinstance(result, dict):
                analysis_text = (result.get("analysis_text") or "").strip()
                suppressed = bool(result.get("suppressed"))
                error_text = str(result.get("error") or "").strip()
            logger.info(
                "Audio analysis response #%d: text_len=%d suppressed=%s error=%s keys=%s",
                request_id,
                len(analysis_text),
                suppressed,
                bool(error_text),
                sorted(list(result.keys())) if isinstance(result, dict) else "non-dict",
            )

            if suppressed:
                logger.info(
                    "Audio-direct analysis suppressed #%d: mode=%s reason=%s",
                    request_id,
                    self.analysis_mode,
                    result.get("suppression_reason") if isinstance(result, dict) else "unknown",
                )
                return

            if analysis_text and processor is not None:
                resolved_timestamp_ms = self._resolve_analysis_timestamp_ms(timestamp_ms)
                if self.analysis_response_format:
                    signal_data = self._coerce_signal_data(analysis_text)
                    if isinstance(signal_data, dict) and signal_data.get("_parse_error"):
                        payload = {
                            "type": "analysis.error",
                            "mode": self.analysis_mode,
                            "error": "Analysis response was not valid JSON",
                            "parse_error": signal_data.get("_parse_error"),
                            "raw_text": signal_data.get("_raw_text"),
                            "timestamp_ms": resolved_timestamp_ms,
                        }
                    else:
                        signal_data = self._normalize_signal_item_timestamps(signal_data, resolved_timestamp_ms)
                        payload = {
                            "type": "analysis.signal",
                            "mode": self.analysis_mode,
                            "data": signal_data,
                            "timestamp_ms": resolved_timestamp_ms,
                        }
                else:
                    payload = {
                        "type": "analysis.done",
                        "mode": self.analysis_mode,
                        "analysis_source": "audio",
                        "text": analysis_text,
                        "timestamp_ms": resolved_timestamp_ms,
                    }
                await processor.send_data(json.dumps(payload))
                logger.info(
                    "Audio analysis emitted #%d: text_len=%d timestamp_ms=%s",
                    request_id,
                    len(analysis_text),
                    str(resolved_timestamp_ms),
                )
            elif processor is not None and isinstance(result, dict) and result.get("error"):
                logger.warning(
                    "Audio analysis error #%d: %s",
                    request_id,
                    result.get("error"),
                )
                await processor.send_data(json.dumps({
                    "type": "analysis.error",
                    "error": result.get("error"),
                    "mode": self.analysis_mode,
                }))
            else:
                logger.warning(
                    "Audio analysis empty result #%d: no text, no suppression, no error",
                    request_id,
                )
        except Exception as exc:
            logger.error("Audio-direct live analysis failed: %s", exc)
            if processor is not None:
                await processor.send_data(json.dumps({
                    "type": "analysis.error",
                    "error": str(exc),
                    "mode": self.analysis_mode,
                }))

    async def _translate_sentence_async(
        self,
        sentence: str,
        source_lang: str,
        target_lang: str,
    ) -> None:
        """Translate a sentence and emit the result over the data channel."""
        try:
            translation_prompt = (
                f"Translate the following text from {source_lang} to {target_lang}. "
                "Return only the translated text with no explanation, notes, or formatting.\n\n"
                f"{sentence}"
            )
            result = await gemma_translator.translate(
                sentence,
                source_lang,
                target_lang,
                prompt=translation_prompt,
            )
            translated_text = ""
            if isinstance(result, dict):
                translated_text = (result.get("translated_text") or "").strip()

            if translated_text and processor is not None:
                await processor.send_data(json.dumps({
                    "type": "translation",
                    "text": translated_text,
                    "original": sentence,
                    "source_language": source_lang,
                    "target_language": target_lang,
                }))
                logger.info(
                    "Translation sent: original='%s...' translated='%s...'",
                    sentence[:40],
                    translated_text[:40],
                )
            elif processor is not None and isinstance(result, dict) and result.get("error"):
                await processor.send_data(json.dumps({
                    "type": "translation.error",
                    "error": result.get("error"),
                    "original": sentence,
                }))
        except Exception as exc:
            logger.error("Sentence translation failed: %s", exc)
            if processor is not None:
                await processor.send_data(json.dumps({
                    "type": "translation.error",
                    "error": str(exc),
                    "original": sentence,
                }))

    async def _flush_analysis_on_stop(self) -> None:
        """Flush pending analysis buffers before stream shutdown."""
        if self.analysis_enabled and self.live_transcription_enabled and self._analysis_pending_text:
            prompt_text = self._analysis_pending_text.strip()
            self._analysis_pending_text = ""
            if prompt_text:
                timestamp_ms = self._resolve_analysis_timestamp_ms(None)
                self._analysis_last_run_ts_ms = timestamp_ms
                await self._run_live_analysis_async(prompt_text, timestamp_ms)

        if self._should_use_audio_direct_analysis() and self._analysis_pending_audio:
            audio_chunk = self._analysis_pending_audio
            self._analysis_pending_audio = b""
            audio_timestamp_ms = int((self._analysis_audio_samples_total * 1000) / 16000)
            timestamp_ms = self._resolve_analysis_timestamp_ms(audio_timestamp_ms)
            self._analysis_last_run_ts_ms = timestamp_ms
            await self._run_live_audio_analysis_async(audio_chunk, timestamp_ms)

    @on_stream_stop
    async def on_stop(self) -> None:
        """Called when a trickle stream stops. Closes the per-stream VLLM
        realtime websocket so the next stream gets a fresh session."""
        global vllm_client
        logger.info("Stream stopped")

        await self._flush_analysis_on_stop()

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
    """Create and run the StreamProcessor with custom worker routes."""
    global processor
    handlers = LiveTranscriptionWorker()
    processor = StreamProcessor.from_handlers(
        handlers,
        name=CAPABILITY_NAME,
        port=PORT,
        host=HOST,
        enable_default_routes=True,
        ssl=True,
        send_data_interval=0.1,  # 100ms — smoother transcript streaming
    )
    # Register custom worker routes directly on the aiohttp app router
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
