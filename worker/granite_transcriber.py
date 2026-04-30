#!/usr/bin/env python3
"""
Granite-Speech-4.1-2B-Plus transcriber.

Single speech-LLM that supports three prompt-controlled modes:
  - Plain ASR
  - Speaker-attributed ASR (SAA, ``[Speaker N]:`` tags)
  - Word-level timestamps (``[T:N]`` centisecond tags, mod 1000)

Replaces the previous Granite-4.0-1b-speech + Qwen3-ForcedAligner pipeline.
"""

import io
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import librosa
import numpy as np
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
import av

logger = logging.getLogger(__name__)

WORKER_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIRNAME = "granite-speech-4.1-2b-plus"
DEFAULT_MODEL_ID = "ibm-granite/granite-speech-4.1-2b-plus"
PUBLIC_MODEL_NAME = "granite-speech-4.1-2b-plus"

ASR_PROMPT = "<|audio|> can you transcribe the speech into a written format?"
SAA_PROMPT = (
    "<|audio|> Speaker attribution: Transcribe and denote who is speaking "
    "by adding [Speaker 1]: and [Speaker 2]: tags before speaker turns."
)
TIMESTAMP_PROMPT = (
    "<|audio|> Timestamps: Transcribe the speech. After each word, add a "
    "timestamp tag showing the end time in centiseconds, "
    "e.g. hello [T:45] world [T:82]"
)
SYSTEM_PROMPT = (
    "Knowledge Cutoff Date: April 2024.\n"
    "Today's Date: December 19, 2024.\n"
    "You are Granite, developed by IBM. You are a helpful AI assistant"
)

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}

_TIMESTAMP_TAG_RE = re.compile(r"\[T:(\d+)\]")
_SPEAKER_TAG_RE = re.compile(r"\[Speaker \d+\]:")
_SPEAKER_SPLIT_RE = re.compile(r"(\[Speaker (\d+)\]:)")


@dataclass(frozen=True)
class _IncrementalWindow:
    end_sample: int
    audio: np.ndarray


def _normalise_language_code(language: Optional[str]) -> str:
    if not language:
        return ""
    return str(language).strip().lower().replace("_", "-").split("-", 1)[0]


def _resolve_model_path(model_path: Optional[str] = None) -> Path:
    """Resolve the preferred local Granite model path across local and container layouts."""
    if model_path:
        return Path(model_path)

    env_model_path = os.environ.get("GRANITE_MODEL_PATH")
    if env_model_path:
        return Path(env_model_path)

    candidate_paths = [
        WORKER_DIR / "models" / DEFAULT_MODEL_DIRNAME,
        Path("/models") / DEFAULT_MODEL_DIRNAME,
        WORKER_DIR.parent / "models" / DEFAULT_MODEL_DIRNAME,
    ]

    for candidate in candidate_paths:
        if (candidate / "config.json").exists():
            return candidate

    return candidate_paths[0]


def _parse_word_timestamps(text: str) -> List[Dict[str, Any]]:
    """Parse ``word [T:N]`` sequences into ``[{word, end}]``.

    The tag value ``N`` is in centiseconds modulo 1000 (10s rollover); we
    unwrap by adding 10s whenever the next end-time would go backwards.
    Silence tokens (``_``) are skipped.
    """
    if not text:
        return []

    parts = _TIMESTAMP_TAG_RE.split(text)
    # parts is [word, ts, word, ts, ..., trailing]
    words: List[Dict[str, Any]] = []
    last_end = 0.0
    offset = 0.0
    for word, ts in zip(parts[0::2], parts[1::2]):
        token = word.strip()
        try:
            ts_val = float(ts) / 100.0
        except (TypeError, ValueError):
            continue
        end_time = ts_val + offset
        while end_time < last_end:
            offset += 10.0
            end_time = ts_val + offset
        last_end = end_time
        if not token or token == "_":
            continue
        words.append({"word": token, "end": round(end_time, 3)})
    return words


def _segments_from_speakers(
    text: str, words: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Parse ``[Speaker N]:`` tagged transcript into ordered segments.

    When ``words`` (from a parallel timestamp-mode pass) is provided, segment
    ``start`` and ``end`` are derived by positionally consuming that word list.
    Otherwise ``start``/``end`` are ``None``.
    """
    if not text:
        return []

    pieces = _SPEAKER_SPLIT_RE.split(text)
    # Layout: [pre, tag, num, body, tag, num, body, ...]
    # Drop any leading text before the first speaker tag.
    if not pieces or len(pieces) < 4:
        return []

    segments: List[Dict[str, Any]] = []
    word_cursor = 0
    word_count = len(words) if words else 0

    # Walk triples (tag, num, body) starting at index 1.
    for i in range(1, len(pieces), 3):
        if i + 2 >= len(pieces):
            break
        try:
            speaker_num = int(pieces[i + 1])
        except (TypeError, ValueError):
            continue
        body = (pieces[i + 2] or "").strip()
        if not body:
            continue
        segment: Dict[str, Any] = {"speaker": speaker_num, "text": body}
        if words and word_cursor < word_count:
            tokens_in_body = [t for t in body.split() if t]
            n_tokens = len(tokens_in_body)
            start_idx = word_cursor
            end_idx = min(word_cursor + n_tokens, word_count) - 1
            if end_idx >= start_idx:
                start_word = words[start_idx]
                end_word = words[end_idx]
                # ``end`` is the timestamp; approximate ``start`` as previous
                # word's end (or 0 for the first word).
                if start_idx == 0:
                    seg_start = 0.0
                else:
                    seg_start = float(words[start_idx - 1].get("end", 0.0))
                segment["start"] = round(seg_start, 3)
                segment["end"] = float(end_word.get("end", seg_start))
            word_cursor += n_tokens
        segments.append(segment)
    return segments


class Granite4Transcriber:
    """Granite-Speech-4.1-2B-Plus transcriber with ASR / SAA / timestamp modes."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = _resolve_model_path(model_path)
        self.model_ref = str(self.model_path) if self.model_path.exists() else DEFAULT_MODEL_ID
        self.sample_rate = 16000
        self.max_new_tokens = int(os.environ.get("GRANITE_MAX_NEW_TOKENS", "10000"))
        self.incremental_window_seconds = max(
            5.0,
            float(os.environ.get("GRANITE_INCREMENTAL_WINDOW_SECONDS", "30")),
        )
        self.incremental_min_duration_seconds = max(
            0.0,
            float(os.environ.get("GRANITE_INCREMENTAL_MIN_DURATION_SECONDS", "45")),
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor: Any = None
        self.tokenizer: Any = None
        self.model: Any = None
        self.is_loaded = False
        self.active_backend = "unavailable"
        self.load_error: Optional[str] = None

        self._load_model()

    @property
    def hardware(self) -> str:
        return "gpu" if self.device == "cuda" else "cpu"

    def _select_torch_dtype(self, torch_module: Any) -> Any:
        if self.device == "cuda" and hasattr(torch_module, "bfloat16"):
            return torch_module.bfloat16
        return torch_module.float32

    def _load_model(self):
        """Load the Granite processor and model via transformers."""
        try:
            logger.info("Loading Granite-Speech-4.1-2B-Plus model from %s", self.model_ref)

            self.processor = AutoProcessor.from_pretrained(self.model_ref)
            self.tokenizer = self.processor.tokenizer
            self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_ref,
                dtype=self._select_torch_dtype(torch),
            )
            self.model.to(self.device)
            self.model.eval()

            self.is_loaded = True
            self.active_backend = "transformers"
            self.load_error = None
            logger.info("Granite-Speech-4.1-2B-Plus runtime initialized successfully on %s", self.device)
        except Exception as exc:
            logger.exception("Failed to load Granite-Speech-4.1-2B-Plus model")
            self.is_loaded = False
            self.active_backend = "unavailable"
            self.load_error = str(exc)

    def is_available(self) -> bool:
        return self.is_loaded

    def transcribe(
        self,
        audio_source: "str | bytes",
        language: str = "en",
        with_speakers: bool = False,
        with_word_timestamps: bool = False,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe audio. Optionally include per-speaker segments and word-level timestamps."""
        start_time = time.time()

        if not self.is_loaded:
            logger.error("Granite-Speech-4.1 not available for transcription: %s", self.load_error or "unknown error")
            return {
                "error": self.load_error or "Granite transcription backend is unavailable",
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }

        try:
            label = f"<{len(audio_source)} bytes>" if isinstance(audio_source, (bytes, bytearray)) else audio_source
            logger.info(
                "Transcribing %s with Granite-Speech-4.1 (language=%s, speakers=%s, timestamps=%s)",
                label, language, with_speakers, with_word_timestamps,
            )

            audio = self._decode_audio_to_array(audio_source)
            duration = len(audio) / self.sample_rate

            asr_text = ""
            words: List[Dict[str, Any]] = []
            speaker_text = ""
            speakers: List[Dict[str, Any]] = []

            translation_requested = (
                source_language and target_language
                and source_language.lower() != target_language.lower()
            )

            if with_word_timestamps and not translation_requested:
                ts_raw = self._generate_mode_text(mode="timestamps", audio=audio)
                words = _parse_word_timestamps(ts_raw)
                asr_text = " ".join(w["word"] for w in words)
            else:
                asr_text = self._generate_mode_text(
                    mode="asr",
                    audio=audio,
                    source_language=source_language,
                    target_language=target_language,
                )

            if with_speakers and not translation_requested:
                speaker_text = self._generate_mode_text(mode="speakers", audio=audio)
                speakers = _segments_from_speakers(speaker_text, words if with_word_timestamps else None)

            # Build transcript: prefer speaker-tagged text when available.
            output_text = speaker_text.strip() if speakers else asr_text.strip()

            # Build segments: prefer speaker segments if present; otherwise
            # derive from word timestamps; otherwise a single whole-clip seg.
            if speakers and any(s.get("end") is not None for s in speakers):
                segments = [
                    {
                        "start": s.get("start", 0.0) or 0.0,
                        "end": s.get("end", duration) or duration,
                        "text": s["text"],
                        "speaker": s["speaker"],
                    }
                    for s in speakers
                ]
            elif words:
                segments = [{"start": 0.0, "end": words[-1]["end"], "text": asr_text}]
            else:
                segments = [{"start": 0.0, "end": duration, "text": output_text}]

            processing_time = time.time() - start_time
            result: Dict[str, Any] = {
                "text": output_text,
                "segments": segments,
                "language": language,
                "duration": duration,
                "processing_time": processing_time,
                "real_time_factor": processing_time / duration if duration > 0 else 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }
            if with_word_timestamps:
                result["words"] = words
            if with_speakers:
                result["speakers"] = speakers

            logger.info(
                "Transcription completed in %.2fs (RTF: %.2f)",
                processing_time,
                result["real_time_factor"],
            )
            return result
        except Exception as exc:
            logger.exception("Error during Granite transcription")
            return {
                "error": str(exc),
                "text": "",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }

    def translate(self, text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """Translate text using Granite's LLM backbone (text-only, no audio token)."""
        start_time = time.time()

        if not self.is_loaded:
            logger.error("Granite-Speech-4.1 not available for translation: %s", self.load_error or "unknown error")
            return {
                "error": self.load_error or "Granite translation backend is unavailable",
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }

        try:
            logger.info("Translating from %s to %s with Granite-Speech-4.1", source_lang, target_lang)

            chat = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Translate the following text from {source_lang} to {target_lang}:\n\n{text}",
                },
            ]
            prompt = self.tokenizer.apply_chat_template(
                chat,
                tokenize=False,
                add_generation_prompt=True,
            )

            with torch.inference_mode():
                model_inputs = self.tokenizer(prompt, return_tensors="pt")
                model_inputs = {name: tensor.to(self.device) for name, tensor in model_inputs.items()}
                model_outputs = self.model.generate(
                    **model_inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    num_beams=1,
                )

            num_input_tokens = model_inputs["input_ids"].shape[-1]
            new_tokens = model_outputs[:, num_input_tokens:]
            decoded = self.tokenizer.batch_decode(
                new_tokens,
                add_special_tokens=False,
                skip_special_tokens=True,
            )
            translated_text = decoded[0].strip() if decoded else ""

            processing_time = time.time() - start_time
            logger.info("Translation completed in %.2fs", processing_time)

            return {
                "original_text": text,
                "translated_text": translated_text,
                "source_language": source_lang,
                "target_language": target_lang,
                "processing_time": processing_time,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }
        except Exception as exc:
            logger.exception("Error during Granite translation")
            return {
                "error": str(exc),
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": PUBLIC_MODEL_NAME,
                "hardware": self.hardware,
            }

    def _run_generation(self, prompt: str, audio: np.ndarray) -> str:
        with torch.inference_mode():
            model_inputs = self.processor(
                prompt,
                audio,
                sampling_rate=self.sample_rate,
                return_tensors="pt",
            )
            model_inputs = {name: tensor.to(self.device) for name, tensor in model_inputs.items()}
            #retry generation if OOM using offloaded kv cache
            try:
                model_outputs = self.model.generate(
                    **model_inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    num_beams=1,
                )
            except torch.OutOfMemoryError as e:
                if self.device == "cuda":
                    torch.cuda.empty_cache()
                model_outputs = self.model.generate(
                    **model_inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    num_beams=1,
                    cache_implementation="offloaded",
                )

        num_input_tokens = model_inputs["input_ids"].shape[-1]
        new_tokens = model_outputs[:, num_input_tokens:]
        decoded = self.tokenizer.batch_decode(
            new_tokens,
            add_special_tokens=False,
            skip_special_tokens=True,
        )
        return decoded[0].strip() if decoded else ""

    def _generate_mode_text(
        self,
        mode: str,
        audio: np.ndarray,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> str:
        if self._should_use_incremental_decoding(mode, audio, source_language, target_language):
            return self._run_incremental_generation(
                mode=mode,
                audio=audio,
                source_language=source_language,
                target_language=target_language,
            )

        prompt = self._build_prompt(
            mode=mode,
            source_language=source_language,
            target_language=target_language,
        )
        return self._run_generation(prompt, audio)

    def _should_use_incremental_decoding(
        self,
        mode: str,
        audio: np.ndarray,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> bool:
        if mode not in {"asr", "speakers", "timestamps"}:
            return False
        if (
            source_language
            and target_language
            and source_language.lower() != target_language.lower()
        ):
            return False
        duration = len(audio) / self.sample_rate if self.sample_rate > 0 else 0.0
        return duration >= self.incremental_min_duration_seconds

    def _run_incremental_generation(
        self,
        mode: str,
        audio: np.ndarray,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
    ) -> str:
        merged_text = ""
        used_incremental = False

        for window in self._iter_incremental_windows(audio):
            used_incremental = True
            prompt = self._build_prompt(
                mode=mode,
                source_language=source_language,
                target_language=target_language,
                prefix_text=merged_text or None,
            )
            try:
                new_text = self._run_generation(prompt, window.audio)
            except Exception:
                if not merged_text:
                    raise
                logger.warning(
                    "Incremental Granite decode failed for mode=%s at %.2fs; falling back to single pass",
                    mode,
                    window.end_sample / self.sample_rate,
                    exc_info=True,
                )
                return self._run_generation(
                    self._build_prompt(
                        mode=mode,
                        source_language=source_language,
                        target_language=target_language,
                    ),
                    audio,
                )
            merged_text = self._merge_incremental_text(merged_text, new_text)

        if used_incremental and merged_text:
            return merged_text

        prompt = self._build_prompt(
            mode=mode,
            source_language=source_language,
            target_language=target_language,
        )
        return self._run_generation(prompt, audio)

    def _iter_incremental_windows(self, audio: np.ndarray) -> Iterator[_IncrementalWindow]:
        total_samples = len(audio)
        if total_samples <= 0:
            return

        window_samples = max(1, int(self.incremental_window_seconds * self.sample_rate))
        for end_sample in range(window_samples, total_samples, window_samples):
            yield _IncrementalWindow(end_sample=end_sample, audio=audio[:end_sample])
        yield _IncrementalWindow(end_sample=total_samples, audio=audio)

    def _merge_incremental_text(self, previous_text: str, current_text: str) -> str:
        previous = previous_text.strip()
        current = current_text.strip()

        if not previous:
            return current
        if not current:
            return previous
        if current == previous:
            return previous
        if current.startswith(previous):
            suffix = current[len(previous):].strip()
            return previous if not suffix else f"{previous} {suffix}".strip()
        if previous.endswith(current):
            return previous

        overlap = self._find_text_overlap(previous, current)
        if overlap > 0:
            suffix = current[overlap:].strip()
            return previous if not suffix else f"{previous} {suffix}".strip()

        return f"{previous} {current}".strip()

    def _find_text_overlap(self, previous_text: str, current_text: str) -> int:
        previous = previous_text.strip()
        current = current_text.strip()
        if not previous or not current:
            return 0

        max_overlap = min(len(previous), len(current))
        for overlap in range(max_overlap, 0, -1):
            if previous[-overlap:] == current[:overlap]:
                return overlap
        return 0

    def _build_prompt(
        self,
        mode: str = "asr",
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
        prefix_text: Optional[str] = None,
    ) -> str:
        if mode == "speakers":
            content = SAA_PROMPT
        elif mode == "timestamps":
            content = TIMESTAMP_PROMPT
        elif source_language and target_language and source_language.lower() != target_language.lower():
            src_name = LANGUAGE_NAMES.get(_normalise_language_code(source_language), source_language)
            tgt_name = LANGUAGE_NAMES.get(_normalise_language_code(target_language), target_language)
            content = f"<|audio|>translate from {src_name} to {tgt_name}"
        else:
            content = ASR_PROMPT

        chat = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        extra = {"prefix_text": prefix_text} if prefix_text is not None else {}
        return self.tokenizer.apply_chat_template(
            chat,
            tokenize=False,
            add_generation_prompt=True,
            **extra,
        )

    def _decode_audio_to_array(self, source: "str | bytes | io.IOBase") -> np.ndarray:
        """Decode any audio format to a 16 kHz mono float32 numpy array."""
        resampler = av.AudioResampler(
            format="fltp",
            layout="mono",
            rate=self.sample_rate,
        )

        if isinstance(source, (bytes, bytearray)):
            av_input = io.BytesIO(source)
            label = f"<{len(source)} bytes>"
        elif isinstance(source, io.IOBase):
            av_input = source
            label = repr(source)
        else:
            av_input = source
            label = source

        file_size = len(source) if isinstance(source, (bytes, bytearray)) else (
            os.path.getsize(source) if isinstance(source, str) and os.path.exists(source) else -1
        )
        logger.debug("Decoding '%s' (%d bytes) with PyAV", label, file_size)

        chunks: list[np.ndarray] = []
        try:
            with av.open(av_input) as container:
                for frame in container.decode(audio=0):
                    for out_frame in resampler.resample(frame):
                        chunks.append(out_frame.to_ndarray()[0])
            for out_frame in resampler.resample(None):
                chunks.append(out_frame.to_ndarray()[0])
        except Exception as av_exc:
            logger.warning(
                "PyAV failed on '%s' (%d bytes): %s; falling back to librosa",
                label,
                file_size,
                av_exc,
            )
            try:
                if isinstance(source, (bytes, bytearray)):
                    audio_data, _ = librosa.load(io.BytesIO(source), sr=self.sample_rate, mono=True)
                else:
                    audio_data, _ = librosa.load(source, sr=self.sample_rate, mono=True)
                return audio_data.astype(np.float32)
            except Exception as librosa_exc:
                raise RuntimeError(
                    f"All decoders failed for '{label}': PyAV={av_exc!r}, librosa={librosa_exc!r}"
                ) from librosa_exc

        if not chunks:
            raise RuntimeError(f"No audio frames decoded from '{label}'")

        audio_data = np.concatenate(chunks).astype(np.float32)
        logger.debug("Decoded '%s' -> %d samples @ %d Hz", label, len(audio_data), self.sample_rate)
        return audio_data


def create_granite_transcriber(model_path: Optional[str] = None) -> Granite4Transcriber:
    """Factory function to create a Granite4Transcriber instance."""
    return Granite4Transcriber(model_path)


def granite_health_check() -> Dict[str, Any]:
    """Check if Granite transcriber is working correctly."""
    transcriber = Granite4Transcriber()
    return {
        "status": "healthy" if transcriber.is_available() else "degraded",
        "module": "granite_transcriber",
        "model_loaded": transcriber.is_available(),
        "model_path": str(transcriber.model_path),
        "model_ref": transcriber.model_ref,
        "active_backend": transcriber.active_backend,
        "load_error": transcriber.load_error,
        "timestamp": time.time(),
    }
