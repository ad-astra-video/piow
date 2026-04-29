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

        if isinstance(results, list):
            for entry in results:
                words.extend(self._normalize_alignment_results(entry))
            return words

        if isinstance(results, dict):
            token = str(results.get("text") or results.get("word") or "").strip()
            start = results.get("start")
            if start is None:
                start = results.get("start_time")
            end = results.get("end")
            if end is None:
                end = results.get("end_time")
            if token and start is not None and end is not None:
                words.append({"word": token, "start": float(start), "end": float(end)})
            return words

        token = str(getattr(results, "text", "") or "").strip()
        start = getattr(results, "start_time", None)
        end = getattr(results, "end_time", None)
        if token and start is not None and end is not None:
            words.append({"word": token, "start": float(start), "end": float(end)})
        return words
