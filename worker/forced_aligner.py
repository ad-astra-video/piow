#!/usr/bin/env python3
"""Qwen forced aligner wrapper for windowed text timestamp extraction."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "it": "Italian",
    "ru": "Russian",
    "zh": "Chinese",
    "yue": "Cantonese",
}


class ForcedAlignerService:
    """Loads and executes Qwen3-ForcedAligner for word-level timestamps."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or os.environ.get(
            "QWEN_ALIGNER_MODEL_PATH", "Qwen/Qwen3-ForcedAligner-0.6B"
        )
        self.model: Any = None
        self.is_loaded = False
        self._load_error: Optional[str] = None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def is_available(self) -> bool:
        return self._load_error is None or self.is_loaded

    def load(self) -> None:
        """Load the forced aligner model lazily."""
        if self.is_loaded and self.model is not None:
            return

        try:
            import torch
            from qwen_asr import Qwen3ForcedAligner

            if torch.cuda.is_available():
                dtype = torch.bfloat16
                device_map = "cuda:0"
            else:
                dtype = torch.float32
                device_map = "cpu"

            self.model = Qwen3ForcedAligner.from_pretrained(
                self.model_path,
                dtype=dtype,
                device_map=device_map,
            )
            self.is_loaded = True
            self._load_error = None
            logger.info("Forced aligner loaded: model=%s device=%s", self.model_path, device_map)
        except Exception as exc:
            self._load_error = str(exc)
            logger.warning("Forced aligner unavailable: %s", exc)
            raise

    def align(
        self,
        pcm16_bytes: bytes,
        sample_rate: int,
        text: str,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return normalized word-level timestamps for a text/audio pair."""
        clean_text = (text or "").strip()
        if not clean_text:
            return []
        if not pcm16_bytes:
            return []

        if not self.is_loaded or self.model is None:
            self.load()

        audio = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        lang_name = self._normalize_language_name(language)

        results = self.model.align(
            audio=(audio, sample_rate),
            text=clean_text,
            language=lang_name,
        )
        return self._normalize_alignment_results(results)

    def _normalize_language_name(self, language: Optional[str]) -> Optional[str]:
        if language is None:
            return None
        code = str(language).strip().lower().replace("_", "-").split("-", 1)[0]
        return LANGUAGE_NAMES.get(code)

    def _normalize_alignment_results(self, results: Any) -> List[Dict[str, Any]]:
        words: List[Dict[str, Any]] = []
        seen: set = set()

        def _first_non_none(values: List[Any]) -> Any:
            for value in values:
                if value is not None:
                    return value
            return None

        def _extract_token(obj: Any) -> str:
            if isinstance(obj, dict):
                value = obj.get("text") or obj.get("word") or obj.get("token")
                return str(value or "").strip()
            value = (
                getattr(obj, "text", None)
                or getattr(obj, "word", None)
                or getattr(obj, "token", None)
            )
            return str(value or "").strip()

        def _extract_start(obj: Any) -> Any:
            if isinstance(obj, dict):
                return _first_non_none(
                    [
                        obj.get("start"),
                        obj.get("start_time"),
                        obj.get("startTime"),
                        obj.get("start_ms"),
                    ]
                )
            return _first_non_none(
                [
                    getattr(obj, "start", None),
                    getattr(obj, "start_time", None),
                    getattr(obj, "startTime", None),
                    getattr(obj, "start_ms", None),
                ]
            )

        def _extract_end(obj: Any) -> Any:
            if isinstance(obj, dict):
                return _first_non_none(
                    [
                        obj.get("end"),
                        obj.get("end_time"),
                        obj.get("endTime"),
                        obj.get("end_ms"),
                    ]
                )
            return _first_non_none(
                [
                    getattr(obj, "end", None),
                    getattr(obj, "end_time", None),
                    getattr(obj, "endTime", None),
                    getattr(obj, "end_ms", None),
                ]
            )

        def _append_if_word(obj: Any) -> None:
            token = _extract_token(obj)
            start = _extract_start(obj)
            end = _extract_end(obj)
            if not token or start is None or end is None:
                return
            try:
                normalized = {"word": token, "start": float(start), "end": float(end)}
                key = (normalized["word"], normalized["start"], normalized["end"])
                if key in seen:
                    return
                seen.add(key)
                words.append(normalized)
            except (TypeError, ValueError):
                return

        def _collect(obj: Any) -> None:
            if obj is None:
                return

            if isinstance(obj, (list, tuple, set)):
                for item in obj:
                    _collect(item)
                return

            if isinstance(obj, dict):
                _append_if_word(obj)
                for value in obj.values():
                    if isinstance(value, (dict, list, tuple, set)) or hasattr(value, "__dict__"):
                        _collect(value)
                return

            _append_if_word(obj)

            for attr in (
                "time_stamps",
                "timestamps",
                "words",
                "items",
                "alignment",
                "alignments",
                "results",
                "segments",
            ):
                child = getattr(obj, attr, None)
                if child is not None:
                    _collect(child)

            payload = getattr(obj, "__dict__", None)
            if isinstance(payload, dict):
                for value in payload.values():
                    if isinstance(value, (dict, list, tuple, set)) or hasattr(value, "__dict__"):
                        _collect(value)

        _collect(results)
        return words
