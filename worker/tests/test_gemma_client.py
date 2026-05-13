#!/usr/bin/env python3
"""Tests for the Gemma translation client."""

import os
import sys
import unittest
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
