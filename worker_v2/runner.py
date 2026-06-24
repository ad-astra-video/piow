#!/usr/bin/env python3
"""
Live Translation Worker V2 — Livepeer Live Runner.

Replaces the pytrickle StreamProcessor server with an aiohttp app that uses
the livepeer-gateway SDK for trickle channel lifecycle (create_trickle_channels,
MediaOutput, MediaPublish, TricklePublisher).

All transcription, translation, and analysis logic is preserved from worker V1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import av
import aiohttp
import numpy as np
import urllib3

from aiohttp import web

from livepeer_gateway.live_runner import (
    register_runner,
    LiveRunnerRegistration,
    create_trickle_channels,
    LiveRunnerTrickleChannel,
    LiveRunnerTrickleChannelRequest,
)
from livepeer_gateway.media_output import MediaOutput, LagPolicy
from livepeer_gateway.media_publish import MediaPublish, MediaPublishConfig, VideoOutputConfig
from livepeer_gateway.trickle_publisher import TricklePublisher

# ---------------------------------------------------------------------------
# Ensure worker package is importable
# ---------------------------------------------------------------------------
WORKER_DIR = Path(__file__).parent.resolve()
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from gemma_client import GemmaClient
from gemma_prompts import get_analysis_prompt, get_analysis_prompt_with_schema
from vllm_client import VLLMRealtimeClient, warmup_transcription

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("live-translation-runner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = int(os.environ.get("WORKER_PORT", "8000"))
WS_URL = os.environ.get("VLLM_WS_URL", "ws://localhost:8080/v1/realtime")
VLLM_SOURCE_LANG = os.environ.get("VLLM_SOURCE_LANG", "en")
VLLM_TARGET_LANG = os.environ.get("VLLM_TARGET_LANG", "es")

# Live-runner registration config
LIVERUNNER_ORCHESTRATOR = os.environ.get("LIVERUNNER_ORCHESTRATOR", "http://localhost:8935")
LIVERUNNER_SECRET = os.environ.get("LIVERUNNER_SECRET", "")
LIVERUNNER_URL = os.environ.get("LIVERUNNER_URL", f"http://localhost:{DEFAULT_PORT}")
LIVERUNNER_APP_ID = os.environ.get("LIVERUNNER_APP_ID", "livepeer/live-transcription")
LIVERUNNER_PRICE = int(os.environ.get("LIVERUNNER_PRICE", "0"))
LIVERUNNER_PIXELS = int(os.environ.get("LIVERUNNER_PIXELS", "1"))
LIVERUNNER_CAPACITY = int(os.environ.get("LIVERUNNER_CAPACITY", "1"))
LIVERUNNER_MODE = os.environ.get("LIVERUNNER_MODE", "persistent")

# Randomly generated 16-character token for this worker instance
WORKER_TOKEN = secrets.token_hex(8)
logger.info("Generated worker token: %s", WORKER_TOKEN)

# Suppress urllib3 warnings
urllib3.disable_warnings()

# ---------------------------------------------------------------------------
# Component singletons
# ---------------------------------------------------------------------------
gemma_translator = GemmaClient()
vllm_client: Optional[VLLMRealtimeClient] = None

# ---------------------------------------------------------------------------
# Lifecycle state
# ---------------------------------------------------------------------------
STATE = {"state": "building", "error": None, "version": "2.0.0"}

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    session_id: str
    in_url: str
    out_url: str
    data_url: str
    output: MediaOutput
    publisher: MediaPublish
    data_publisher: TricklePublisher
    worker: "LiveTranscriptionWorker"
    params: Dict[str, Any]

    def to_json(self) -> Dict[str, Any]:
        return {
            "session": self.session_id,
            "in": self.in_url,
            "out": self.out_url,
            "data": self.data_url,
            "params": self.params,
        }


session: Optional[SessionState] = None
registration: Optional[LiveRunnerRegistration] = None


# =============================================================================
# Live Translation Worker (logic ported from V1, pytrickle decorators removed)
# =============================================================================
class LiveTranscriptionWorker:
    """Port of V1 decorator-based handlers to explicit methods."""

    _audio_frame_count: int = 0
    _resampler: av.AudioResampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    # 80ms @ 16kHz s16 mono = 16000 * 0.08 * 2 bytes = 2560 bytes
    _SEND_CHUNK_BYTES: int = 2560
    _audio_buffer: bytes = b""
    # Cycle the VLLM WebSocket connection every 15 minutes
    _CONNECTION_MAX_AGE_SECONDS: int = 15 * 60

    def __init__(self, send_data_callback: Any) -> None:
        self.send_data_callback = send_data_callback
        self.analysis_enabled: bool = False
        self.analysis_mode: str = "multimodal"
        self.analysis_audio_chunk_seconds: float = 10.0
        self.analysis_video_chunk_seconds: float = 10.0
        self.analysis_max_tokens: int = 1024
        self.live_transcription_enabled: bool = True
        self.analysis_prompt: str = self._default_analysis_prompt(self.analysis_mode)
        self.analysis_prompt_custom: bool = False
        self.analysis_response_format: Optional[Dict[str, Any]] = None
        self.auto_generate_schema: bool = True
        self._analysis_prompt_with_schema: Optional[str] = None
        self._analysis_pending_text: str = ""
        self._analysis_pending_audio: bytes = b""
        self._analysis_audio_samples_total: int = 0
        self._transcription_audio_samples_sent: int = 0
        self._last_transcription_timestamp_ms: int = 0
        self._analysis_last_run_ts_ms: int = 0
        self._analysis_last_seen_ts_ms: int = 0
        self._analysis_audio_request_count: int = 0
        self._stream_started_monotonic_s: Optional[float] = None

    def _default_analysis_prompt(self, mode: str) -> str:
        return get_analysis_prompt(mode)

    def _build_analysis_prompt(self) -> None:
        if self.analysis_response_format and self.analysis_prompt:
            self._analysis_prompt_with_schema = get_analysis_prompt_with_schema(
                self.analysis_prompt,
                self.analysis_response_format,
            )
        else:
            self._analysis_prompt_with_schema = None

    def _resolve_analysis_timestamp_ms(self, timestamp_ms: Optional[int]) -> int:
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
        return int((self._transcription_audio_samples_sent * 1000) / 16000)

    def _mark_transcription_audio_sent(self, audio_pcm16: bytes) -> None:
        if not audio_pcm16:
            return
        self._transcription_audio_samples_sent += len(audio_pcm16) // 2

    def _resolve_transcription_timestamp_ms(self, text: str, is_final: bool) -> int:
        current_timestamp_ms = self._transcription_sent_timestamp_ms()
        normalized_text = (text or "").strip()
        has_word_content = any(char.isalnum() for char in normalized_text)
        if is_final or has_word_content or self._last_transcription_timestamp_ms <= 0:
            self._last_transcription_timestamp_ms = current_timestamp_ms
            return current_timestamp_ms
        return self._last_transcription_timestamp_ms

    # ------------------------------------------------------------------
    # JSON parsing helpers
    # ------------------------------------------------------------------
    def _parse_structured_analysis_text(self, analysis_text: str) -> Optional[Any]:
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
            return None
        if not stack:
            return text
        closers = []
        while stack:
            opener = stack.pop()
            closers.append("]" if opener == "[" else "}")
        return text + "".join(closers)

    def _coerce_signal_data(self, analysis_text: str) -> Any:
        structured_data = self._parse_structured_analysis_text(analysis_text)
        if structured_data is not None:
            return structured_data
        return {"_parse_error": "invalid_json", "_raw_text": analysis_text}

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

    # ------------------------------------------------------------------
    # Stream lifecycle
    # ------------------------------------------------------------------
    async def on_start(self, params: Dict[str, Any]) -> None:
        global vllm_client
        logger.info("Stream started with params: %s", params)

        LiveTranscriptionWorker._audio_frame_count = 0
        LiveTranscriptionWorker._audio_buffer = b""
        self._analysis_pending_text = ""
        self._analysis_pending_audio = b""
        self._analysis_audio_samples_total = 0
        self._transcription_audio_samples_sent = 0
        self._last_transcription_timestamp_ms = 0
        self._analysis_last_run_ts_ms = 0
        self._analysis_last_seen_ts_ms = 0
        self._stream_started_monotonic_s = time.monotonic()
        self._apply_analysis_params(params)

        if self.analysis_enabled and self.auto_generate_schema and self.analysis_response_format is None and self.analysis_prompt:
            await self._generate_and_emit_schema()

        if vllm_client is not None:
            try:
                await vllm_client.close()
            except Exception as exc:
                logger.warning("Error closing stale VLLM client: %s", exc)
            vllm_client = None

        vllm_client = VLLMRealtimeClient(
            ws_url=WS_URL,
            source_lang=VLLM_SOURCE_LANG,
            target_lang=VLLM_TARGET_LANG,
        )

        async def _on_transcription(message: Any, is_final: bool = False, **kw) -> None:
            if not self.live_transcription_enabled:
                return
            if isinstance(message, dict):
                msg_type = message.get("type")
                if msg_type == "transcription.delta":
                    delta = message.get("delta", "")
                    if not delta:
                        return
                    timestamp_ms = self._resolve_transcription_timestamp_ms(delta, is_final=is_final)
                    message["timestamp_ms"] = timestamp_ms
                payload = json.dumps(message)
                await self.send_data_callback(payload)
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
            timestamp_ms = self._resolve_transcription_timestamp_ms(text, is_final=is_final)
            payload = json.dumps({
                "type": "transcription",
                "text": text,
                "is_final": is_final,
                "timestamp_ms": timestamp_ms,
            })
            logger.info("Sending transcription: is_final=%s len=%d", is_final, len(text))
            await self.send_data_callback(payload)
            self._queue_live_analysis(text.strip(), timestamp_ms, is_final=is_final)

        vllm_client.set_text_callback(_on_transcription)

        if self.live_transcription_enabled:
            try:
                await vllm_client.connect()
                logger.info("VLLM client connected successfully")
            except Exception as exc:
                logger.warning("Could not connect to VLLM on stream start: %s", exc)

    async def handle_video(self, frame: av.VideoFrame) -> av.VideoFrame:
        """Pass video frames through unchanged."""
        return frame

    async def handle_audio(self, frame: av.AudioFrame) -> None:
        """Process audio frames: resample and forward to VLLM realtime websocket."""
        LiveTranscriptionWorker._audio_frame_count += 1
        try:
            samples = frame.to_ndarray()  # shape depends on format
            # Log sample rate periodically
            if LiveTranscriptionWorker._audio_frame_count % 100 == 1:
                logger.info(
                    "handle_audio: frame=%d, sample_rate=%sHz, shape=%s, dtype=%s",
                    LiveTranscriptionWorker._audio_frame_count,
                    getattr(frame, 'sample_rate', 'unknown'),
                    samples.shape,
                    samples.dtype,
                )
            # Resample to 16kHz mono PCM16 using PyAV
            n_channels = samples.shape[0] if samples.ndim > 1 else 1
            layout = 'stereo' if n_channels == 2 else 'mono'
            av_frame = av.AudioFrame.from_ndarray(
                samples if samples.ndim > 1 else samples[np.newaxis, :],
                format='fltp',
                layout=layout,
            )
            av_frame.sample_rate = getattr(frame, 'sample_rate', 48000)
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
                                "send_audio: chunk bytes=%d, pcm16_samples=%d, ~%.1fms, rms=%.1f, peak=%d",
                                len(chunk), n_samples, n_samples / 16000 * 1000, rms, peak,
                            )
                        await vllm_client.send_audio(chunk)
                        self._mark_transcription_audio_sent(chunk)

                        if vllm_client.connection_age() >= LiveTranscriptionWorker._CONNECTION_MAX_AGE_SECONDS:
                            remaining = LiveTranscriptionWorker._audio_buffer
                            LiveTranscriptionWorker._audio_buffer = b""
                            await self._cycle_vllm_connection(remaining)
        except Exception as exc:
            logger.warning("VLLM send_audio error: %s", exc)

    async def _cycle_vllm_connection(self, remaining_audio: bytes) -> None:
        global vllm_client
        if vllm_client and vllm_client.is_connected:
            try:
                await vllm_client.commit_audio()
            except Exception as exc:
                logger.warning("VLLM commit before reconnect failed: %s", exc)
        if not vllm_client:
            logger.warning("No VLLM client to reconnect")
            LiveTranscriptionWorker._audio_buffer = remaining_audio
            return
        ok = await vllm_client.async_reconnect()
        if not ok:
            logger.error("VLLM reconnection failed; remaining audio will be dropped")
            return
        logger.info("VLLM connection cycled successfully")
        if remaining_audio:
            try:
                await vllm_client.send_audio(remaining_audio)
                self._mark_transcription_audio_sent(remaining_audio)
            except Exception as exc:
                logger.warning("VLLM send remaining audio after reconnect: %s", exc)

    async def update_params(self, params: Dict[str, Any]) -> None:
        """Handle mid-stream parameter updates."""
        self._apply_analysis_params(params)
        if params.get("generate_analysis_schema"):
            await self._generate_and_emit_schema()

        sentence = params.get("translate_sentence")
        if not isinstance(sentence, str) or not sentence.strip():
            return
        source_lang = params.get("source_language", "en")
        target_lang = params.get("target_language", "es")
        asyncio.create_task(self._translate_sentence_async(sentence.strip(), source_lang, target_lang))

    def _apply_analysis_params(self, params: Dict[str, Any]) -> None:
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

        auto_generate_schema = params.get("auto_generate_schema")
        if auto_generate_schema is not None:
            self.auto_generate_schema = bool(auto_generate_schema)

        self._build_analysis_prompt()

    async def _generate_and_emit_schema(self) -> None:
        if not self.analysis_enabled or not self.analysis_prompt:
            return
        logger.info("Generating analysis schema: mode=%s prompt_len=%d", self.analysis_mode, len(self.analysis_prompt))
        result = await gemma_translator.generate_analysis_schema(
            analysis_prompt=self.analysis_prompt,
            mode=self.analysis_mode,
            max_tokens=2048,
        )
        if isinstance(result, dict) and result.get("error"):
            logger.warning("Schema generation failed: %s", result.get("error"))
            await self.send_data_callback(json.dumps({
                "type": "analysis_response_format",
                "schema": None,
                "error": result.get("error"),
                "mode": self.analysis_mode,
            }))
            return

        schema = result.get("schema") if isinstance(result, dict) else None
        if not isinstance(schema, dict):
            logger.warning("Schema generation returned no valid schema")
            return

        self.analysis_response_format = {"type": "json_object", "schema": schema}
        self._build_analysis_prompt()
        logger.info("Schema generated successfully: keys=%s", sorted(schema.keys()))
        await self.send_data_callback(json.dumps({
            "type": "analysis_response_format",
            "schema": schema,
            "mode": self.analysis_mode,
        }))

    def _queue_live_analysis(self, text: str, timestamp_ms: int, is_final: bool) -> None:
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
        if elapsed_ms < chunk_ms:
            return

        prompt_text = self._analysis_pending_text.strip()
        if not prompt_text:
            return

        self._analysis_pending_text = ""
        self._analysis_last_run_ts_ms = int(timestamp_ms)
        asyncio.create_task(self._run_live_analysis_async(prompt_text, int(timestamp_ms)))

    def _should_use_audio_direct_analysis(self) -> bool:
        if not self.analysis_enabled or self.live_transcription_enabled:
            return False
        return self.analysis_mode in {"audio_only", "multimodal"}

    def _queue_live_audio_analysis(self, audio_pcm16: bytes, is_final: bool = False) -> None:
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
            self.analysis_mode, is_final, len(audio_chunk), chunk_ms, elapsed_ms, timestamp_ms,
        )
        asyncio.create_task(self._run_live_audio_analysis_async(audio_chunk, int(timestamp_ms)))

    async def _run_live_analysis_async(self, text: str, timestamp_ms: Optional[int] = None) -> None:
        if not self.analysis_enabled or not text:
            return
        try:
            result = await gemma_translator.analyze(
                text=text,
                prompt=self._analysis_prompt_with_schema or self.analysis_prompt,
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
                logger.debug("Live analysis suppressed: mode=%s reason=%s", self.analysis_mode,
                             result.get("suppression_reason") if isinstance(result, dict) else "unknown")
                return
            if analysis_text:
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
                await self.send_data_callback(json.dumps(payload))
            elif isinstance(result, dict) and result.get("error"):
                await self.send_data_callback(json.dumps({
                    "type": "analysis.error",
                    "error": result.get("error"),
                    "mode": self.analysis_mode,
                }))
        except Exception as exc:
            logger.error("Live analysis failed: %s", exc)
            await self.send_data_callback(json.dumps({
                "type": "analysis.error",
                "error": str(exc),
                "mode": self.analysis_mode,
            }))

    async def _run_live_audio_analysis_async(self, audio_pcm16: bytes, timestamp_ms: Optional[int] = None) -> None:
        if not self.analysis_enabled or not audio_pcm16:
            return
        self._analysis_audio_request_count += 1
        request_id = self._analysis_audio_request_count
        chunk_duration_ms = int((len(audio_pcm16) // 2) * 1000 / 16000)
        logger.info(
            "Audio analysis request #%d: mode=%s bytes=%d duration_ms=%d timestamp_ms=%s prompt_len=%d",
            request_id, self.analysis_mode, len(audio_pcm16), chunk_duration_ms,
            str(timestamp_ms), len(self.analysis_prompt or ""),
        )
        try:
            result = await gemma_translator.analyze_audio(
                audio_pcm16=audio_pcm16,
                sample_rate_hz=16000,
                prompt=self._analysis_prompt_with_schema or self.analysis_prompt,
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
                request_id, len(analysis_text), suppressed, bool(error_text),
                sorted(list(result.keys())) if isinstance(result, dict) else "non-dict",
            )
            if suppressed:
                logger.info("Audio-direct analysis suppressed #%d: mode=%s reason=%s",
                            request_id, self.analysis_mode,
                            result.get("suppression_reason") if isinstance(result, dict) else "unknown")
                return
            if analysis_text:
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
                await self.send_data_callback(json.dumps(payload))
                logger.info("Audio analysis emitted #%d: text_len=%d timestamp_ms=%s",
                            request_id, len(analysis_text), str(resolved_timestamp_ms))
            elif isinstance(result, dict) and result.get("error"):
                logger.warning("Audio analysis error #%d: %s", request_id, result.get("error"))
                await self.send_data_callback(json.dumps({
                    "type": "analysis.error",
                    "error": result.get("error"),
                    "mode": self.analysis_mode,
                }))
            else:
                logger.warning("Audio analysis empty result #%d: no text, no suppression, no error", request_id)
        except Exception as exc:
            logger.error("Audio-direct live analysis failed: %s", exc)
            await self.send_data_callback(json.dumps({
                "type": "analysis.error",
                "error": str(exc),
                "mode": self.analysis_mode,
            }))

    async def _translate_sentence_async(self, sentence: str, source_lang: str, target_lang: str) -> None:
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
            if translated_text:
                await self.send_data_callback(json.dumps({
                    "type": "translation",
                    "text": translated_text,
                    "original": sentence,
                    "source_language": source_lang,
                    "target_language": target_lang,
                }))
                logger.info("Translation sent: original='%s...' translated='%s...'",
                            sentence[:40], translated_text[:40])
            elif isinstance(result, dict) and result.get("error"):
                await self.send_data_callback(json.dumps({
                    "type": "translation.error",
                    "error": result.get("error"),
                    "original": sentence,
                }))
        except Exception as exc:
            logger.error("Sentence translation failed: %s", exc)
            await self.send_data_callback(json.dumps({
                "type": "translation.error",
                "error": str(exc),
                "original": sentence,
            }))

    async def _flush_analysis_on_stop(self) -> None:
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

    async def on_stop(self) -> None:
        global vllm_client
        logger.info("Stream stopped")
        await self._flush_analysis_on_stop()
        if vllm_client is not None:
            try:
                await vllm_client.close()
            except Exception as exc:
                logger.warning("Error closing VLLM client on stream stop: %s", exc)
            vllm_client = None


# =============================================================================
# Warm-up
# =============================================================================
async def _warmup() -> None:
    try:
        logger.info("Warming up: loading models and running test transcription")
        warmup_audio = Path(__file__).parent / "test.wav"
        if warmup_audio.exists():
            await warmup_transcription(ws_url=WS_URL, audio_path=str(warmup_audio))
        else:
            logger.warning("Warmup skipped: test.wav not found at %s", warmup_audio)
        STATE["state"] = "ready"
        logger.info("Warmup complete; /health now returns 200")
    except Exception as exc:
        STATE.update(state="error", error=f"{type(exc).__name__}: {exc}")
        logger.error("Warmup FAILED: %s", exc, exc_info=True)
        os._exit(1)


# =============================================================================
# Session helpers
# =============================================================================
def _session_id(request: web.Request) -> str:
    sid = request.headers.get("Livepeer-Session-Id", "").strip()
    if not sid:
        raise web.HTTPBadRequest(text="missing Livepeer-Session-Id header")
    return sid


async def _close_session() -> None:
    global session
    if session is None:
        return
    current, session = session, None
    with suppress(Exception):
        await current.worker.on_stop()
    with suppress(Exception):
        await current.data_publisher.close()
    with suppress(Exception):
        await current.publisher.close()
    with suppress(Exception):
        await current.output.close()


# =============================================================================
# aiohttp handlers
# =============================================================================
async def _handle_status(request: web.Request) -> web.Response:
    health = {
        "status": STATE["state"],
        "service": LIVERUNNER_APP_ID,
        "version": "2.0.0",
        "gemma_translation": {
            "configured": gemma_translator.is_configured,
            "base_url": gemma_translator.base_url,
            "model": gemma_translator.model,
        },
        "vllm_client": {
            "connected": vllm_client.is_connected if vllm_client else False,
        },
        "timestamp": int(time.time()),
    }
    return web.json_response(health)


async def _handle_health(request: web.Request) -> web.Response:
    if STATE["state"] != "ready":
        raise web.HTTPServiceUnavailable(text=STATE["state"])
    return web.Response(text="ok")


async def _handle_stream(request: web.Request) -> web.Response:
    global session
    if STATE["state"] != "ready":
        raise web.HTTPServiceUnavailable(text=f"runner not ready ({STATE['state']})")

    session_id = _session_id(request)
    if session is not None:
        if session.session_id != session_id:
            raise web.HTTPConflict(text="runner already has an active session")
        return web.json_response(session.to_json())

    if registration is None:
        raise web.HTTPInternalServerError(text="runner not registered with orchestrator")

    channels = await registration.create_trickle_channels(
        session_id,
        [
            {"name": "in", "mime_type": "video/mp2t"},
            {"name": "out", "mime_type": "video/mp2t"},
            {"name": "data", "mime_type": "application/json"},
        ],
        session_token=request.headers.get("Livepeer-Session-Token", "").strip(),
    )
    by_name = {c["name"]: c for c in channels}
    if "in" not in by_name or "out" not in by_name or "data" not in by_name:
        raise web.HTTPInternalServerError(text="orchestrator did not return in/out/data channels")

    body = json.loads(await request.read() or "{}")
    params = body if isinstance(body, dict) else {}

    # Data publisher for JSON events
    data_pub = TricklePublisher(
        by_name["data"].get("internal_url") or by_name["data"]["url"],
        mime_type="application/json",
    )
    await data_pub.create()

    async def _send_data(payload: str) -> None:
        try:
            async with await data_pub.next() as seg:
                await seg.write(payload.encode("utf-8"))
        except Exception as exc:
            logger.warning("Data publish error: %s", exc)

    worker = LiveTranscriptionWorker(send_data_callback=_send_data)

    # Media publisher for passthrough video
    publisher = MediaPublish(
        by_name["out"].get("internal_url") or by_name["out"]["url"],
        config=MediaPublishConfig(
            tracks=[VideoOutputConfig(fps=30.0, keyframe_interval_s=0.25)],
            min_segment_wallclock_s=0.25,
        ),
    )

    async def _on_frame(decoded) -> None:
        if decoded.kind == "video":
            try:
                out_frame = await worker.handle_video(decoded.frame)
                if out_frame is not None:
                    out_frame.pts = decoded.frame.pts
                    out_frame.time_base = decoded.frame.time_base
                    await publisher.write_frame(out_frame)
            except Exception as exc:
                logger.warning("Video frame error: %s", exc)
        elif decoded.kind == "audio":
            try:
                await worker.handle_audio(decoded.frame)
            except Exception as exc:
                logger.warning("Audio frame error: %s", exc)

    output = MediaOutput(
        by_name["in"].get("internal_url") or by_name["in"]["url"],
        on_frame=_on_frame,
        max_segments=2,
        lag_policy=LagPolicy.LATEST,
    )

    session = SessionState(
        session_id=session_id,
        in_url=by_name["in"]["url"],
        out_url=by_name["out"]["url"],
        data_url=by_name["data"]["url"],
        output=output,
        publisher=publisher,
        data_publisher=data_pub,
        worker=worker,
        params=params,
    )

    for task in output.callback_tasks():
        task.add_done_callback(lambda _t: asyncio.create_task(_close_session()))

    await worker.on_start(params)
    logger.info("Started session %s", session_id)
    return web.json_response(session.to_json())


async def _handle_update(request: web.Request) -> web.Response:
    if session is None:
        raise web.HTTPNotFound(text="session not started")
    if session.session_id != _session_id(request):
        raise web.HTTPConflict(text="runner has a different active session")
    body = json.loads(await request.read() or "{}")
    await session.worker.update_params(body)
    session.params.update(body)
    return web.json_response(session.to_json())


async def _handle_stop(request: web.Request) -> web.Response:
    if session is None:
        raise web.HTTPNotFound(text="session not started")
    if session.session_id != _session_id(request):
        raise web.HTTPConflict(text="runner has a different active session")
    await _close_session()
    return web.json_response({"status": "stopped"})


# =============================================================================
# Lifecycle
# =============================================================================
async def _on_startup(app: web.Application) -> None:
    global registration
    # Warm up in the background so /status + /health serve immediately
    app["warmup"] = asyncio.create_task(_warmup())

    # Register as a live runner with the orchestrator
    if LIVERUNNER_SECRET:
        logger.info("Registering live runner with orchestrator %s", LIVERUNNER_ORCHESTRATOR)
        registration = await register_runner(
            orchestrator_url=LIVERUNNER_ORCHESTRATOR,
            secret=LIVERUNNER_SECRET,
            runner_url=LIVERUNNER_URL,
            app=LIVERUNNER_APP_ID,
            price_per_unit=LIVERUNNER_PRICE,
            pixels_per_unit=LIVERUNNER_PIXELS,
            capacity=LIVERUNNER_CAPACITY,
            mode=LIVERUNNER_MODE,
        )
        logger.info("Live runner registered: runner_id=%s", registration.runner_id)
    else:
        logger.warning("LIVERUNNER_SECRET not set; skipping live runner registration")


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("warmup")
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
    await _close_session()
    if registration is not None:
        await registration.close()


# =============================================================================
# Main
# =============================================================================
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live Translation live runner.")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    app = web.Application()
    app.router.add_get("/status", _handle_status)
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/stream", _handle_stream)
    app.router.add_post("/update", _handle_update)
    app.router.add_post("/stop", _handle_stop)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
