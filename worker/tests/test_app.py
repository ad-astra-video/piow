#!/usr/bin/env python3
"""Tests for the PyTrickle-based worker app."""

import os
import sys
import json
import asyncio
import time
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
    mock_gemma = MagicMock()
    mock_gemma.translate = AsyncMock(return_value={"translated_text": "Hola mundo"})
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = mock_gemma
        await worker._translate_sentence_async("Hello world", "en", "es")
        mock_gemma.translate.assert_awaited_once()
        translate_args, translate_kwargs = mock_gemma.translate.call_args
        assert translate_args == ("Hello world", "en", "es")
        assert "Return only the translated text" in translate_kwargs["prompt"]
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


async def test_run_live_analysis_async_emits_stream_message():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.analysis_prompt = "Call out key risks"

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = MagicMock()
        worker_app.gemma_translator.analyze = AsyncMock(return_value={"analysis_text": "Potential outage risk"})
        await worker._run_live_analysis_async("We have a deploy pending", 640)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    mock_processor.send_data.assert_awaited_once()
    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload == {
        "type": "analysis.done",
        "mode": "audio_only",
        "text": "Potential outage risk",
        "timestamp_ms": 640,
    }


async def test_run_live_analysis_async_emits_elapsed_timestamp_when_missing():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker._stream_started_monotonic_s = time.monotonic() - 1.25

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = MagicMock()
        worker_app.gemma_translator.analyze = AsyncMock(return_value={"analysis_text": "Update available"})
        await worker._run_live_analysis_async("New update", None)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload["type"] == "analysis.done"
    assert payload["timestamp_ms"] >= 1000


async def test_run_live_analysis_async_suppresses_no_update_output():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = MagicMock()
        worker_app.gemma_translator.analyze = AsyncMock(return_value={
            "analysis_text": "",
            "suppressed": True,
            "suppression_reason": "no_update",
        })
        await worker._run_live_analysis_async("No clear action", 720)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    mock_processor.send_data.assert_not_awaited()


async def test_apply_analysis_params_updates_worker_state():
    worker = worker_app.LiveTranscriptionWorker()

    worker._apply_analysis_params({
        "analysis_enabled": True,
        "analysis_mode": "video_only",
        "analysis_audio_chunk_seconds": 0.75,
        "analysis_video_chunk_seconds": 5,
        "live_transcription_enabled": False,
        "analysis_prompt": "Track visual cues",
    })

    assert worker.analysis_enabled is True
    assert worker.analysis_mode == "video_only"
    assert worker.analysis_audio_chunk_seconds == 0.75
    assert worker.analysis_video_chunk_seconds == 5
    assert worker.live_transcription_enabled is False
    assert worker.analysis_prompt == "Track visual cues"


async def test_queue_live_analysis_triggers_on_chunk_window():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.live_transcription_enabled = True
    worker.analysis_audio_chunk_seconds = 0.5
    worker._run_live_analysis_async = AsyncMock()

    worker._queue_live_analysis("first partial", 200, is_final=False)
    await asyncio.sleep(0)
    worker._run_live_analysis_async.assert_not_awaited()

    worker._queue_live_analysis("second partial", 600, is_final=False)
    await asyncio.sleep(0)

    worker._run_live_analysis_async.assert_awaited_once_with(
        "first partial second partial",
        600,
    )


async def test_queue_live_analysis_does_not_bypass_chunk_window_on_final_or_punctuation():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.live_transcription_enabled = True
    worker.analysis_audio_chunk_seconds = 1.0
    worker._run_live_analysis_async = AsyncMock()

    worker._queue_live_analysis("first sentence.", 400, is_final=False)
    await asyncio.sleep(0)
    worker._run_live_analysis_async.assert_not_awaited()

    worker._queue_live_analysis("second sentence", 700, is_final=True)
    await asyncio.sleep(0)
    worker._run_live_analysis_async.assert_not_awaited()

    worker._queue_live_analysis("third sentence", 1100, is_final=False)
    await asyncio.sleep(0)

    worker._run_live_analysis_async.assert_awaited_once_with(
        "first sentence. second sentence third sentence",
        1100,
    )


async def test_queue_live_analysis_respects_video_window_and_transcription_flag():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "video_only"
    worker.analysis_video_chunk_seconds = 2
    worker.live_transcription_enabled = False
    worker._run_live_analysis_async = AsyncMock()

    worker._queue_live_analysis("visual cue", 2100, is_final=False)
    await asyncio.sleep(0)
    worker._run_live_analysis_async.assert_not_awaited()

    worker.live_transcription_enabled = True
    worker._queue_live_analysis("visual cue", 2100, is_final=False)
    await asyncio.sleep(0)
    worker._run_live_analysis_async.assert_awaited_once_with("visual cue", 2100)


async def test_queue_live_audio_analysis_triggers_without_transcription():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker.analysis_audio_chunk_seconds = 0.5
    worker._run_live_audio_analysis_async = AsyncMock()

    # 3200 bytes = 100ms @ 16kHz PCM16 mono
    worker._queue_live_audio_analysis(b"\x00" * 3200)
    await asyncio.sleep(0)
    worker._run_live_audio_analysis_async.assert_not_awaited()

    # Add 12800 bytes (400ms) for total 500ms window.
    worker._queue_live_audio_analysis(b"\x00" * 12800)
    await asyncio.sleep(0)

    worker._run_live_audio_analysis_async.assert_awaited_once()
    run_args = worker._run_live_audio_analysis_async.call_args.args
    assert len(run_args[0]) == 16000
    assert run_args[1] == 500


async def test_run_live_audio_analysis_async_emits_stream_message():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker.analysis_prompt = "Track decision changes"

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    try:
        worker_app.gemma_translator = MagicMock()
        worker_app.gemma_translator.analyze_audio = AsyncMock(return_value={"analysis_text": "Decision reversed"})
        await worker._run_live_audio_analysis_async(b"\x00\x00\x01\x01", 320)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    mock_processor.send_data.assert_awaited_once()
    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload == {
        "type": "analysis.done",
        "mode": "audio_only",
        "text": "Decision reversed",
        "timestamp_ms": 320,
    }


async def test_run_live_audio_analysis_async_emits_signal_when_schema_set():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker.analysis_prompt = "Track decision changes"
    worker.analysis_response_format = {
        "type": "json_object",
        "schema": {"category": {"type": "string"}},
    }

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    gemma_mock = MagicMock()
    gemma_mock.analyze_audio = AsyncMock(return_value={"analysis_text": '{"category":"Action"}'})
    try:
        worker_app.gemma_translator = gemma_mock
        await worker._run_live_audio_analysis_async(b"\x00\x00\x01\x01", 320)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    gemma_mock.analyze_audio.assert_awaited_once()
    assert gemma_mock.analyze_audio.call_args.kwargs["response_format"] == worker.analysis_response_format

    mock_processor.send_data.assert_awaited_once()
    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload == {
        "type": "analysis.signal",
        "mode": "audio_only",
        "data": {"category": "Action"},
        "timestamp_ms": 320,
    }


async def test_run_live_audio_analysis_async_normalizes_placeholder_item_timestamp():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker.analysis_prompt = "Track decision changes"
    worker.analysis_response_format = {
        "type": "json_object",
        "schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array"}
            }
        },
    }

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    gemma_mock = MagicMock()
    gemma_mock.analyze_audio = AsyncMock(return_value={
        "analysis_text": json.dumps({
            "items": [
                {
                    "timestamp": "0:00",
                    "category": "Action",
                    "item": "Do the thing",
                    "priority": None,
                }
            ]
        })
    })
    try:
        worker_app.gemma_translator = gemma_mock
        await worker._run_live_audio_analysis_async(b"\x00\x00\x01\x01", 40400)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload["type"] == "analysis.signal"
    assert payload["timestamp_ms"] == 40400
    assert payload["data"]["items"][0]["timestamp"] == "00:00:40"


async def test_run_live_audio_analysis_async_emits_analysis_error_when_schema_json_invalid():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker.analysis_prompt = "Track decision changes"
    worker.analysis_response_format = {
        "type": "json_object",
        "schema": {"category": {"type": "string"}},
    }

    mock_processor = MagicMock()
    mock_processor.send_data = AsyncMock()

    original_processor = worker_app.processor
    original_gemma = worker_app.gemma_translator
    worker_app.processor = mock_processor
    gemma_mock = MagicMock()
    gemma_mock.analyze_audio = AsyncMock(return_value={"analysis_text": '{"category":"Risk"'})
    try:
        worker_app.gemma_translator = gemma_mock
        await worker._run_live_audio_analysis_async(b"\x00\x00\x01\x01", 320)
    finally:
        worker_app.processor = original_processor
        worker_app.gemma_translator = original_gemma

    mock_processor.send_data.assert_awaited_once()
    payload = json.loads(mock_processor.send_data.call_args[0][0])
    assert payload["type"] == "analysis.error"
    assert payload["mode"] == "audio_only"
    assert payload["parse_error"] == "invalid_json"
    assert payload["raw_text"] == '{"category":"Risk"'
    assert payload["timestamp_ms"] == 320


async def test_queue_live_audio_analysis_flushes_on_final():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.analysis_mode = "audio_only"
    worker.live_transcription_enabled = False
    worker._analysis_pending_audio = b"\x00\x00\x01\x01"
    worker._analysis_audio_samples_total = 2
    worker._run_live_audio_analysis_async = AsyncMock()

    worker._queue_live_audio_analysis(b"", is_final=True)
    await asyncio.sleep(0)

    worker._run_live_audio_analysis_async.assert_awaited_once_with(b"\x00\x00\x01\x01", 0)


async def test_on_stop_flush_uses_elapsed_timestamp_when_none_seen():
    worker = worker_app.LiveTranscriptionWorker()
    worker.analysis_enabled = True
    worker.live_transcription_enabled = True
    worker._analysis_pending_text = "tail text"
    worker._stream_started_monotonic_s = time.monotonic() - 1.5
    worker._run_live_analysis_async = AsyncMock()

    await worker._flush_analysis_on_stop()

    worker._run_live_analysis_async.assert_awaited_once()
    call_args = worker._run_live_analysis_async.call_args.args
    assert call_args[0] == "tail text"
    assert call_args[1] >= 1000
