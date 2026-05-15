#!/usr/bin/env python3
"""Gemma vLLM client for sentence translation.

This client sends request/response chat-completion calls to a dedicated Gemma
vLLM container and normalizes the result into the worker's translation event
shape.
"""

import logging
import os
import re
import time
import base64
import io
import wave
from typing import Any, Dict, Optional

import aiohttp
from gemma_prompts import get_analysis_prompt

logger = logging.getLogger(__name__)


class GemmaClient:
    """HTTP client for the dedicated Gemma translation runtime."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
        timeout_seconds: float = 60.0,
    ):
        self.base_url = (base_url or os.environ.get("GEMMA_VLLM_BASE_URL", "http://gemma-vllm:6100")).rstrip("/")
        self.model = model or os.environ.get("GEMMA_VLLM_MODEL", "cyankiwi/gemma-4-E4B-it-AWQ-INT4")
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout_seconds = float(timeout_seconds)

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def audio_analysis_supported(self) -> bool:
        """Audio analysis is enabled by default, with an explicit env opt-out."""
        raw = os.environ.get("GEMMA_AUDIO_ANALYSIS_ENABLED", "true").strip().lower()
        return raw not in ("0", "false", "no", "off")

    async def _chat_completion(self, messages: list[dict[str, Any]], response_format: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                response_text = await response.text()
                if response.status >= 400:
                    return {
                        "error": f"Gemma request failed: HTTP {response.status}",
                        "response": response_text[:500],
                    }
                try:
                    return await response.json()
                except Exception:
                    return {
                        "error": "Gemma response was not valid JSON",
                        "response": response_text[:500],
                    }

    @staticmethod
    def _extract_content(data: Any) -> str:
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                return str(message.get("content") or "").strip()
        return ""

    @staticmethod
    def _default_analysis_prompt(mode: str) -> str:
        return get_analysis_prompt(mode)

    @staticmethod
    def _is_no_update_response(text: str) -> bool:
        """Return True when model output requests suppression via NO_UPDATE contract."""
        normalized = (text or "").strip()
        if not normalized:
            return False

        # Direct contract match.
        if re.fullmatch(r"NO[_\s-]?UPDATE[\.!]?", normalized, flags=re.IGNORECASE):
            return True

        # Handle wrapped responses such as markdown code fences or quoted text.
        unwrapped = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", normalized, flags=re.DOTALL).strip()
        unwrapped = unwrapped.strip("`\"' ")
        if re.fullmatch(r"NO[_\s-]?UPDATE[\.!]?", unwrapped, flags=re.IGNORECASE):
            return True

        # Accept one-line sentences that explicitly request no update.
        compact = re.sub(r"\s+", " ", unwrapped).strip().lower()
        return compact in {
            "no update",
            "no_update",
            "no-update",
            "no update.",
            "no_update.",
            "no-update.",
        }

    @staticmethod
    def _pcm16_to_wav_bytes(audio_pcm16: bytes, sample_rate_hz: int) -> bytes:
        """Wrap raw PCM16 mono bytes into a WAV container for vLLM audio decoding."""
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate_hz))
            wav_file.writeframes(audio_pcm16)
        return wav_buffer.getvalue()

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Translate text using the Gemma vLLM chat-completions API."""
        start_time = time.time()

        if not text or not text.strip():
            return {
                "error": "Missing text",
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": self.model,
                "backend": "gemma-vllm",
            }

        if not self.is_configured:
            return {
                "error": "Gemma translation runtime is not configured",
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": self.model,
                "backend": "gemma-vllm",
            }

        system_prompt = (
			"You are a professional translation engine. Translate the user's text into "
			"natural, fluent target-language text while preserving the original meaning, "
			"tone, intent, punctuation, and line breaks. Use idiomatic expressions when "
			"appropriate and avoid word-for-word translation. Preserve incomplete "
			"sentences and conversational transcript style naturally. Return only the "
			"translated text with no explanations, labels, or markdown."
		)
        user_prompt = prompt or f"Translate from {source_lang} to {target_lang}:\n\n{text}"
        try:
            data = await self._chat_completion([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if isinstance(data, dict) and data.get("error"):
                return {
                    "error": data.get("error"),
                    "original_text": text,
                    "translated_text": "",
                    "source_language": source_lang,
                    "target_language": target_lang,
                    "model": self.model,
                    "backend": "gemma-vllm",
                    "response": data.get("response", ""),
                }

            translated_text = self._extract_content(data)

            processing_time = time.time() - start_time
            return {
                "original_text": text,
                "translated_text": translated_text,
                "source_language": source_lang,
                "target_language": target_lang,
                "processing_time": processing_time,
                "model": self.model,
                "backend": "gemma-vllm",
            }
        except Exception as exc:
            logger.exception("Gemma translation request failed")
            return {
                "error": str(exc),
                "original_text": text,
                "translated_text": "",
                "source_language": source_lang,
                "target_language": target_lang,
                "model": self.model,
                "backend": "gemma-vllm",
            }

    async def analyze(self, text: str, prompt: Optional[str] = None, mode: str = "multimodal", response_format: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run live-analysis text generation on the Gemma runtime."""
        start_time = time.time()

        if not text or not text.strip():
            return {
                "error": "Missing text",
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

        if not self.is_configured:
            return {
                "error": "Gemma runtime is not configured",
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

        default_prompt = self._default_analysis_prompt(mode)
        effective_prompt = (prompt or default_prompt).strip()
        user_prompt = (
            f"{effective_prompt}\n\n"
            f"Mode: {mode}\n"
            f"Transcript:\n{text}"
        )

        try:
            data = await self._chat_completion(
                [{"role": "user", "content": user_prompt}],
                response_format=response_format,
            )
            if isinstance(data, dict) and data.get("error"):
                return {
                    "error": data.get("error"),
                    "analysis_text": "",
                    "model": self.model,
                    "backend": "gemma-vllm",
                    "response": data.get("response", ""),
                }

            analysis_text = self._extract_content(data)
            if self._is_no_update_response(analysis_text):
                return {
                    "analysis_text": "",
                    "suppressed": True,
                    "suppression_reason": "no_update",
                    "model": self.model,
                    "backend": "gemma-vllm",
                }

            processing_time = time.time() - start_time
            return {
                "analysis_text": analysis_text,
                "model": self.model,
                "backend": "gemma-vllm",
                "processing_time": processing_time,
            }
        except Exception as exc:
            logger.exception("Gemma analysis request failed")
            return {
                "error": str(exc),
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

    async def analyze_audio(
        self,
        audio_pcm16: bytes,
        sample_rate_hz: int = 16000,
        prompt: Optional[str] = None,
        mode: str = "audio_only",
    ) -> Dict[str, Any]:
        """Run live analysis directly from PCM16 audio payloads."""
        start_time = time.time()

        if not audio_pcm16:
            return {
                "error": "Missing audio",
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

        if not self.is_configured:
            return {
                "error": "Gemma runtime is not configured",
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

        if not self.audio_analysis_supported:
            return {
                "error": "Gemma audio-direct analysis disabled via GEMMA_AUDIO_ANALYSIS_ENABLED=false.",
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }

        default_prompt = self._default_analysis_prompt(mode)
        effective_prompt = (prompt or default_prompt).strip()
        user_text = (
            f"{effective_prompt}\\n\\n"
            f"Mode: {mode}\\n"
            f"Audio format: pcm16 mono {int(sample_rate_hz)}Hz. "
            "Analyze only the provided audio chunk and report any actionable updates."
        )
        wav_bytes = self._pcm16_to_wav_bytes(audio_pcm16, sample_rate_hz)
        encoded_audio = base64.b64encode(wav_bytes).decode("ascii")
        logger.info(
            "Gemma analyze_audio request: mode=%s pcm16_bytes=%d wav_bytes=%d sample_rate_hz=%d prompt_len=%d",
            mode,
            len(audio_pcm16),
            len(wav_bytes),
            int(sample_rate_hz),
            len(effective_prompt),
        )

        try:
            data = await self._chat_completion([
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "audio_url",
                            "audio_url": {
                                "url": f"data:audio/wav;base64,{encoded_audio}",
                            },
                        },
                    ],
                },
            ])
            if isinstance(data, dict) and data.get("error"):
                logger.warning(
                    "Gemma analyze_audio upstream error: %s",
                    data.get("error"),
                )
                return {
                    "error": data.get("error"),
                    "analysis_text": "",
                    "model": self.model,
                    "backend": "gemma-vllm",
                    "response": data.get("response", ""),
                }

            analysis_text = self._extract_content(data)
            if self._is_no_update_response(analysis_text):
                logger.info("Gemma analyze_audio response: suppressed NO_UPDATE")
                return {
                    "analysis_text": "",
                    "suppressed": True,
                    "suppression_reason": "no_update",
                    "model": self.model,
                    "backend": "gemma-vllm",
                }

            processing_time = time.time() - start_time
            return {
                "analysis_text": analysis_text,
                "model": self.model,
                "backend": "gemma-vllm",
                "processing_time": processing_time,
            }
        except Exception as exc:
            logger.exception("Gemma audio analysis request failed")
            return {
                "error": str(exc),
                "analysis_text": "",
                "model": self.model,
                "backend": "gemma-vllm",
            }
