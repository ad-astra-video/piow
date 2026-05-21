#!/usr/bin/env python3
"""Tests for SSE relay analysis schema persistence."""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sse_relay import SSERelay


class TestSSERelaySchemaPersistence(unittest.IsolatedAsyncioTestCase):
    async def test_persist_analysis_schema_to_db_success(self):
        session_store = AsyncMock()
        session_store.get_stream_session = AsyncMock(return_value={
            "stream_settings": {
                "analysis": {
                    "enabled": True,
                    "type": "multimodal",
                    "audio_chunk_seconds": 5.0,
                    "video_chunk_seconds": 5.0,
                    "max_tokens": 512,
                    "video_fps": 3,
                    "prompt": "Test prompt",
                }
            }
        })
        session_store.update_stream_analysis_config = AsyncMock()

        relay = SSERelay("http://worker/data", "stream-123", session_store=session_store)
        schema = {"type": "object", "title": "TestSchema"}
        await relay._persist_analysis_schema_to_db(schema)

        session_store.get_stream_session.assert_awaited_once_with("stream-123")
        session_store.update_stream_analysis_config.assert_awaited_once_with(
            stream_id="stream-123",
            analysis_enabled=True,
            analysis_mode="multimodal",
            analysis_audio_chunk_seconds=5.0,
            analysis_video_chunk_seconds=5.0,
            analysis_max_tokens=512,
            analysis_video_fps=3,
            analysis_prompt="Test prompt",
            analysis_response_format={"type": "json_object", "schema": schema},
        )

    async def test_persist_analysis_schema_to_db_no_session_store(self):
        relay = SSERelay("http://worker/data", "stream-123", session_store=None)
        schema = {"type": "object"}
        # Should not raise
        await relay._persist_analysis_schema_to_db(schema)

    async def test_persist_analysis_schema_to_db_session_not_found(self):
        session_store = AsyncMock()
        session_store.get_stream_session = AsyncMock(return_value=None)
        session_store.update_stream_analysis_config = AsyncMock()

        relay = SSERelay("http://worker/data", "stream-123", session_store=session_store)
        schema = {"type": "object"}
        await relay._persist_analysis_schema_to_db(schema)

        session_store.get_stream_session.assert_awaited_once_with("stream-123")
        session_store.update_stream_analysis_config.assert_not_called()

    async def test_persist_analysis_schema_to_db_uses_defaults(self):
        session_store = AsyncMock()
        session_store.get_stream_session = AsyncMock(return_value={
            "stream_settings": {}
        })
        session_store.update_stream_analysis_config = AsyncMock()

        relay = SSERelay("http://worker/data", "stream-123", session_store=session_store)
        schema = {"type": "object"}
        await relay._persist_analysis_schema_to_db(schema)

        session_store.update_stream_analysis_config.assert_awaited_once_with(
            stream_id="stream-123",
            analysis_enabled=True,
            analysis_mode="multimodal",
            analysis_audio_chunk_seconds=10.0,
            analysis_video_chunk_seconds=10.0,
            analysis_max_tokens=1024,
            analysis_video_fps=3,
            analysis_prompt=None,
            analysis_response_format={"type": "json_object", "schema": schema},
        )

    async def test_handle_event_triggers_schema_persistence(self):
        session_store = AsyncMock()
        session_store.get_stream_session = AsyncMock(return_value={
            "stream_settings": {"analysis": {"enabled": True}}
        })
        session_store.update_stream_analysis_config = AsyncMock()

        relay = SSERelay("http://worker/data", "stream-123", session_store=session_store)
        relay.clients = set()

        event = {
            "event": "message",
            "data": {
                "type": "analysis_response_format",
                "schema": {"type": "object", "title": "Generated"},
                "mode": "multimodal",
            },
        }
        await relay._handle_event(event)

        # Wait for the background task to complete
        await asyncio.sleep(0.1)

        session_store.update_stream_analysis_config.assert_awaited_once()
        call_kwargs = session_store.update_stream_analysis_config.call_args.kwargs
        self.assertEqual(call_kwargs["stream_id"], "stream-123")
        self.assertEqual(call_kwargs["analysis_response_format"]["schema"]["title"], "Generated")

    async def test_handle_event_no_persistence_when_schema_is_none(self):
        session_store = AsyncMock()
        session_store.update_stream_analysis_config = AsyncMock()

        relay = SSERelay("http://worker/data", "stream-123", session_store=session_store)
        relay.clients = set()

        event = {
            "event": "message",
            "data": {
                "type": "analysis_response_format",
                "schema": None,
                "error": "Generation failed",
            },
        }
        await relay._handle_event(event)

        await asyncio.sleep(0.1)
        session_store.update_stream_analysis_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
