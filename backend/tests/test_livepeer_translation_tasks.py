#!/usr/bin/env python3
"""Tests for synchronous Livepeer translation provider behavior."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compute_providers.livepeer.livepeer import LivepeerComputeProvider


class _MockResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _MockSession:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None, timeout=None):
        return self._response


class TestLivepeerTranslationProvider(unittest.IsolatedAsyncioTestCase):
    async def test_create_translation_job_returns_provider_payload(self):
        provider = LivepeerComputeProvider({"name": "livepeer", "gpu_runner_url": "http://worker:9935", "enabled": True})
        response_payload = {
            "job_id": "job-123",
            "status": "completed",
            "original_text": "Hello",
            "translated_text": "Hola",
            "source_language": "en",
            "target_language": "es",
            "token_count": 12,
            "model": "granite-4.0-1b",
            "hardware": "cpu",
        }

        with patch("compute_providers.livepeer.livepeer.aiohttp.ClientSession", return_value=_MockSession(_MockResponse(200, response_payload))):
            result = await provider.create_translation_job("Hello", "en", "es")

        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["translated_text"], "Hola")
        self.assertEqual(result["source_language"], "en")
        self.assertEqual(result["target_language"], "es")
        self.assertEqual(result["provider"], "livepeer")

    async def test_create_translation_job_raises_on_non_200(self):
        provider = LivepeerComputeProvider({"name": "livepeer", "gpu_runner_url": "http://worker:9935", "enabled": True})

        with patch("compute_providers.livepeer.livepeer.aiohttp.ClientSession", return_value=_MockSession(_MockResponse(502, {"error": "upstream failure"}))):
            with self.assertRaises(Exception) as cm:
                await provider.create_translation_job("Hello", "en", "es")

        self.assertIn("HTTP 502", str(cm.exception))
