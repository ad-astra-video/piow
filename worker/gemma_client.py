#!/usr/bin/env python3
"""Gemma vLLM client for sentence translation.

This client sends request/response chat-completion calls to a dedicated Gemma
vLLM container and normalizes the result into the worker's translation event
shape.
"""

import logging
import os
import time
from typing import Any, Dict, Optional

import aiohttp

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

    async def _chat_completion(self, messages: list[dict[str, str]]) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

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
        prompts = {
            "multimodal": (
                "Analyze the live conversation using both audio and video context. "
                "Summarize key actions, decisions, and risks."
            ),
            "audio_only": (
                "Analyze only the spoken audio from the live conversation. "
                "Summarize key actions, decisions, and risks."
            ),
            "video_only": (
                "Analyze only the visual video context from the live conversation. "
                "Summarize key actions, decisions, and risks."
            ),
        }
        return prompts.get(mode, prompts["multimodal"])

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

    async def analyze(self, text: str, prompt: Optional[str] = None, mode: str = "multimodal") -> Dict[str, Any]:
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
        system_prompt = "You are a real-time analyst. Return concise, actionable observations only."
        user_prompt = (
            f"Mode: {mode}\n"
            f"Instructions: {effective_prompt}\n\n"
            f"Transcript:\n{text}"
        )

        try:
            data = await self._chat_completion([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if isinstance(data, dict) and data.get("error"):
                return {
                    "error": data.get("error"),
                    "analysis_text": "",
                    "model": self.model,
                    "backend": "gemma-vllm",
                    "response": data.get("response", ""),
                }

            processing_time = time.time() - start_time
            return {
                "analysis_text": self._extract_content(data),
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
