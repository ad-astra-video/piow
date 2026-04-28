#!/usr/bin/env python3
"""
Tests for the PyTrickle-based worker app (batch endpoints).
"""

import os
import sys
import json
import pytest
import aiohttp
from aiohttp import web
from unittest.mock import patch, MagicMock

# Add worker dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock pytrickle before importing app
sys.modules['pytrickle'] = MagicMock()
sys.modules['pytrickle.server'] = MagicMock()

import app as worker_app


@pytest.fixture
def cli(event_loop, aiohttp_client):
    """Create an aiohttp test client with the worker routes."""
    application = web.Application()
    
    # Register the batch routes manually
    application.router.add_get("/", worker_app.root_handler)
    application.router.add_get("/health", worker_app.health_handler)
    application.router.add_post("/transcribe", worker_app.transcribe_handler)
    application.router.add_post("/process/request/transcribe", worker_app.transcribe_handler)
    application.router.add_post("/translate", worker_app.translate_handler)
    application.router.add_post("/process/request/translate", worker_app.translate_handler)
    
    return event_loop.run_until_complete(aiohttp_client(application))


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
    assert "granite_transcriber" in data
    assert "vllm_client" in data


async def test_translate_json(cli):
    """Test POST /translate with JSON body."""
    payload = {
        "text": "Hello world",
        "source_language": "en",
        "target_language": "es"
    }
    resp = await cli.post("/translate", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "completed"
    assert "translated_text" in data
    assert data["source_language"] == "en"
    assert data["target_language"] == "es"


async def test_translate_missing_text(cli):
    """Test POST /translate without text returns 400."""
    payload = {"source_language": "en", "target_language": "es"}
    resp = await cli.post("/translate", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


async def test_translate_alias(cli):
    """Test POST /process/request/translate alias."""
    payload = {
        "text": "Hello",
        "source_language": "en",
        "target_language": "fr"
    }
    resp = await cli.post("/process/request/translate", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "completed"


async def test_transcribe_missing_audio_url(cli):
    """Test POST /transcribe without audio_url returns 400."""
    payload = {"language": "en"}
    resp = await cli.post("/transcribe", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


async def test_transcribe_alias(cli):
    """Test POST /process/request/transcribe alias returns 400 (no audio_url)."""
    payload = {"language": "en"}
    resp = await cli.post("/process/request/transcribe", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


async def test_transcribe_accepts_data_url(cli):
    """Test POST /transcribe with base64 data URL payload."""
    data_url = "data:audio/wav;base64,UklGRg=="

    with patch.object(worker_app.granite_transcriber, "transcribe", return_value={"text": "ok", "language": "en"}):
        resp = await cli.post("/transcribe", json={"audio_url": data_url, "language": "en"})

    assert resp.status == 200
    payload = await resp.json()
    assert payload["status"] == "completed"
    assert payload["text"] == "ok"
