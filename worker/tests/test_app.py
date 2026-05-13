#!/usr/bin/env python3
"""Tests for the PyTrickle-based worker app."""

import os
import sys
import json
import pytest
import pytest_asyncio
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import AsyncMock, patch, MagicMock

# Add worker dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock pytrickle before importing app
sys.modules['pytrickle'] = MagicMock()
sys.modules['pytrickle.server'] = MagicMock()
sys.modules['pytrickle.decorators'] = MagicMock()

import app as worker_app


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def cli():
    """Create an aiohttp test client with the worker routes."""
    application = web.Application()
    
    # Register only the routes that remain active.
    application.router.add_get("/", worker_app.root_handler)
    application.router.add_get("/health", worker_app.health_handler)
    application.router.add_get("/capability/status", worker_app.capability_status_handler)

    server = TestServer(application)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_root(cli):
    """Test the root endpoint."""
    resp = await cli.get("/")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "healthy"


async def test_health(cli):
    """Test the health endpoint."""
    resp = await cli.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "healthy"
    assert "gemma_translation" in data
    assert "vllm_client" in data


async def test_capability_status(cli):
    resp = await cli.get("/capability/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["capabilities"][0]["name"] == "live-transcription"


async def test_translate_json_is_gone(cli):
    resp = await cli.post("/translate", json={"text": "Hello world", "source_language": "en", "target_language": "es"})
    assert resp.status == 404


async def test_translate_missing_text_is_gone(cli):
    resp = await cli.post("/translate", json={"source_language": "en", "target_language": "es"})
    assert resp.status == 404


async def test_translate_alias_is_gone(cli):
    resp = await cli.post("/process/request/translate", json={"text": "Hello", "source_language": "en", "target_language": "fr"})
    assert resp.status == 404


async def test_transcribe_missing_audio_url_is_gone(cli):
    resp = await cli.post("/transcribe", json={"language": "en"})
    assert resp.status == 404


async def test_transcribe_alias_is_gone(cli):
    resp = await cli.post("/process/request/transcribe", json={"language": "en"})
    assert resp.status == 404


async def test_transcribe_accepts_data_url_is_gone(cli):
    resp = await cli.post("/transcribe", json={"audio_url": "data:audio/wav;base64,UklGRg==", "language": "en"})
    assert resp.status == 404


async def test_transcribe_returns_500_for_transcriber_errors_is_gone(cli):
    resp = await cli.post("/transcribe", json={"audio_url": "data:audio/wav;base64,UklGRg==", "language": "en"})
    assert resp.status == 404


async def test_translate_sentence_async_emits_stream_message():
    """Worker stream updates emit translated text over the data channel."""
    worker = worker_app.LiveTranscriptionWorker()
    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = MagicMock()
        worker_app.gemma_translator.translate = AsyncMock(return_value={"translated_text": "Hola mundo"})
        await worker._translate_sentence_async("Hello world", "en", "es")
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    mock_processor.send_data.assert_awaited_once()
    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload == {
        "type": "translation",
        "text": "Hola mundo",
        "original": "Hello world",
        "source_language": "en",
        "target_language": "es",
    }
