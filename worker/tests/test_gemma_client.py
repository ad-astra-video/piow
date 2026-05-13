#!/usr/bin/env python3
"""Tests for the Gemma translation client."""

import os
import sys
import unittest
import base64
from unittest.mock import AsyncMock, patch

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemma_client import GemmaClient


class TestGemmaClient(unittest.IsolatedAsyncioTestCase):
    @patch.object(GemmaClient, "_chat_completion", new_callable=AsyncMock)
    async def test_translate_uses_explicit_prompt_and_returns_text_only(self, mock_chat_completion):
        client = GemmaClient(base_url="http://example.com", model="test-model")
        mock_chat_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Hola mundo",
                    }
                }
            ]
        }

        result = await client.translate(
            "Hello world",
            "en",
            "es",
            prompt="Translate the following text from en to es. Return only the translated text.\n\nHello world",
        )

        mock_chat_completion.assert_awaited_once()
        messages = mock_chat_completion.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Return only the translated text", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Return only the translated text", messages[1]["content"])
        self.assertEqual(result["translated_text"], "Hola mundo")
        self.assertEqual(result["original_text"], "Hello world")
        self.assertEqual(result["source_language"], "en")
        self.assertEqual(result["target_language"], "es")

    @patch.object(GemmaClient, "_chat_completion", new_callable=AsyncMock)
    async def test_analyze_suppresses_no_update_contract(self, mock_chat_completion):
        client = GemmaClient(base_url="http://example.com", model="test-model")
        mock_chat_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "NO_UPDATE",
                    }
                }
            ]
        }

        result = await client.analyze("brief filler transcript", mode="audio_only")

        self.assertEqual(result["analysis_text"], "")
        self.assertTrue(result["suppressed"])
        self.assertEqual(result["suppression_reason"], "no_update")

    @patch.object(GemmaClient, "_chat_completion", new_callable=AsyncMock)
    async def test_analyze_audio_sends_multimodal_payload(self, mock_chat_completion):
        client = GemmaClient(base_url="http://example.com", model="test-model")
        mock_chat_completion.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Speaker agreed on rollback plan",
                    }
                }
            ]
        }

        result = await client.analyze_audio(
            audio_pcm16=b"\x01\x02\x03\x04",
            sample_rate_hz=16000,
            mode="audio_only",
            prompt="Call out critical decisions",
        )

        self.assertEqual(result["analysis_text"], "Speaker agreed on rollback plan")
        mock_chat_completion.assert_awaited_once()
        messages = mock_chat_completion.call_args.args[0]
        self.assertEqual(messages[1]["role"], "user")
        user_content = messages[1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[1]["type"], "audio_url")
        audio_url = user_content[1]["audio_url"]["url"]
        self.assertTrue(audio_url.startswith("data:audio/wav;base64,"))
        wav_bytes = base64.b64decode(audio_url.split(",", 1)[1])
        self.assertTrue(wav_bytes.startswith(b"RIFF"))

    @patch.dict(os.environ, {"GEMMA_AUDIO_ANALYSIS_ENABLED": "false"}, clear=False)
    async def test_analyze_audio_fails_fast_when_explicitly_disabled(self):
        client = GemmaClient(base_url="http://example.com", model="test-model")
        result = await client.analyze_audio(audio_pcm16=b"\x01\x02")
        self.assertIn("disabled via GEMMA_AUDIO_ANALYSIS_ENABLED=false", result["error"])
