#!/usr/bin/env python3
"""
Tests for the SSE relay module.

Tests SSE event parsing, relay lifecycle, client management,
and the global relay registry.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, '.')

from aiohttp import web


class TestSSEEventParsing(unittest.TestCase):
    """Test SSE event parsing logic."""

    def setUp(self):
        from sse_relay import SSERelay
        self.SSERelay = SSERelay
        self.relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

    def test_parse_simple_event(self):
        """Parse a simple SSE event with event and data fields."""
        event_text = "event: transcription\ndata: {\"type\": \"transcription\", \"text\": \"hello\", \"is_final\": false}"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["event"], "transcription")
        self.assertEqual(result["data"]["type"], "transcription")
        self.assertEqual(result["data"]["text"], "hello")
        self.assertFalse(result["data"]["is_final"])

    def test_parse_event_with_id(self):
        """Parse an SSE event with an id field."""
        event_text = "event: message\ndata: {\"text\": \"world\"}\nid: 42"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "42")
        self.assertEqual(result["data"]["text"], "world")

    def test_parse_event_with_retry(self):
        """Parse an SSE event with a retry field (sets reconnect delay)."""
        event_text = "event: message\ndata: {\"text\": \"test\"}\nretry: 3000"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(self.relay._retry_delay, 3.0)

    def test_parse_multiline_data(self):
        """Parse an SSE event with multiple data lines (joined by newlines per spec)."""
        event_text = "data: line1\ndata: line2\ndata: line3"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["data"]["text"], "line1\nline2\nline3")

    def test_parse_comment_lines_ignored(self):
        """Comment lines (starting with :) are ignored."""
        event_text = ": this is a comment\ndata: {\"text\": \"hello\"}"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["data"]["text"], "hello")

    def test_parse_empty_event_returns_none(self):
        """An event with no data lines returns None."""
        event_text = "event: ping"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNone(result)

    def test_parse_non_json_data_wrapped_as_text(self):
        """Non-JSON data is wrapped as a transcription text event."""
        event_text = "data: plain text message"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["data"]["text"], "plain text message")

    def test_parse_field_with_leading_space_in_value(self):
        """Per SSE spec, a single leading space after the colon is removed."""
        event_text = "data:  {\"text\": \"spaced\"}"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["data"]["text"], "spaced")

    def test_parse_field_no_colon(self):
        """A line with no colon is treated as a field name with empty value."""
        event_text = "data"
        result = self.relay._parse_sse_event(event_text)
        # "data" with no colon means field name "data" with empty value
        # This results in an empty data line, which still counts
        self.assertIsNotNone(result)

    def test_parse_default_event_type_is_message(self):
        """If no event field is specified, default type is 'message'."""
        event_text = "data: {\"text\": \"default\"}"
        result = self.relay._parse_sse_event(event_text)
        self.assertIsNotNone(result)
        self.assertEqual(result["event"], "message")


class TestSSERelayClientManagement(unittest.TestCase):
    """Test SSERelay client add/remove/has_clients."""

    def setUp(self):
        from sse_relay import SSERelay
        self.relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

    def test_add_client(self):
        mock_ws = MagicMock()
        self.relay.add_client(mock_ws)
        self.assertIn(mock_ws, self.relay.clients)
        self.assertTrue(self.relay.has_clients)

    def test_remove_client(self):
        mock_ws = MagicMock()
        self.relay.add_client(mock_ws)
        self.relay.remove_client(mock_ws)
        self.assertNotIn(mock_ws, self.relay.clients)
        self.assertFalse(self.relay.has_clients)

    def test_remove_nonexistent_client_no_error(self):
        mock_ws = MagicMock()
        # Should not raise
        self.relay.remove_client(mock_ws)
        self.assertFalse(self.relay.has_clients)

    def test_multiple_clients(self):
        ws1 = MagicMock()
        ws2 = MagicMock()
        self.relay.add_client(ws1)
        self.relay.add_client(ws2)
        self.assertEqual(len(self.relay.clients), 2)
        self.relay.remove_client(ws1)
        self.assertEqual(len(self.relay.clients), 1)
        self.assertTrue(self.relay.has_clients)


class TestSSERelayBroadcast(unittest.IsolatedAsyncioTestCase):
    """Test SSERelay._broadcast method."""

    async def test_broadcast_sends_to_all_clients(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws1 = AsyncMock()
        ws1.closed = False
        ws2 = AsyncMock()
        ws2.closed = False

        relay.add_client(ws1)
        relay.add_client(ws2)

        message = {"type": "transcription", "text": "hello", "is_final": False}
        await relay._broadcast(message)

        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    async def test_broadcast_removes_disconnected_clients(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws_good = AsyncMock()
        ws_good.closed = False
        ws_bad = AsyncMock()
        ws_bad.closed = False
        ws_bad.send_json.side_effect = Exception("Connection closed")

        relay.add_client(ws_good)
        relay.add_client(ws_bad)

        await relay._broadcast({"type": "status", "text": "test"})

        # Bad client should be removed
        self.assertNotIn(ws_bad, relay.clients)
        self.assertIn(ws_good, relay.clients)

    async def test_broadcast_skips_closed_websockets(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws_closed = AsyncMock()
        ws_closed.closed = True

        relay.add_client(ws_closed)

        await relay._broadcast({"type": "status", "text": "test"})

        # Closed websocket should not receive message and should be removed
        ws_closed.send_json.assert_not_called()
        self.assertNotIn(ws_closed, relay.clients)

    async def test_broadcast_no_clients_no_error(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        # Should not raise
        await relay._broadcast({"type": "status", "text": "test"})


class TestSSERelayHandleEvent(unittest.IsolatedAsyncioTestCase):
    """Test SSERelay._handle_event method."""

    async def test_handle_transcription_event(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws = AsyncMock()
        ws.closed = False
        relay.add_client(ws)

        event = {
            "event": "transcription",
            "data": {"type": "transcription", "text": "hello world", "is_final": True},
            "id": None
        }
        await relay._handle_event(event)

        ws.send_json.assert_called_once_with({
            "type": "transcription", "text": "hello world", "is_final": True
        })

    async def test_handle_status_event(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws = AsyncMock()
        ws.closed = False
        relay.add_client(ws)

        event = {
            "event": "message",
            "data": {"type": "status", "text": "Connected"},
            "id": None
        }
        await relay._handle_event(event)

        ws.send_json.assert_called_once_with({"type": "status", "text": "Connected"})

    async def test_handle_untyped_dict_wrapped_as_transcription(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws = AsyncMock()
        ws.closed = False
        relay.add_client(ws)

        event = {
            "event": "message",
            "data": {"text": "some text", "is_final": False},
            "id": None
        }
        await relay._handle_event(event)

        ws.send_json.assert_called_once_with({
            "type": "transcription", "text": "some text", "is_final": False
        })

    async def test_handle_string_data_wrapped_as_partial_transcription(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws = AsyncMock()
        ws.closed = False
        relay.add_client(ws)

        event = {
            "event": "message",
            "data": "raw text data",
            "id": None
        }
        await relay._handle_event(event)

        ws.send_json.assert_called_once_with({
            "type": "transcription", "text": "raw text data", "is_final": False
        })

    async def test_handle_empty_data_skipped(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/stream/data", stream_id="test-stream")

        ws = AsyncMock()
        ws.closed = False
        relay.add_client(ws)

        event = {
            "event": "message",
            "data": None,
            "id": None
        }
        await relay._handle_event(event)

        ws.send_json.assert_not_called()


class TestSSERelayRegistry(unittest.IsolatedAsyncioTestCase):
    """Test the global relay registry functions."""

    async def asyncTearDown(self):
        from sse_relay import _active_relays
        # Clean up any relays left over from tests
        relay_ids = list(_active_relays.keys())
        for stream_id in relay_ids:
            relay = _active_relays.pop(stream_id)
            await relay.stop()

    @patch('sse_relay.SSERelay.start', new_callable=AsyncMock)
    async def test_get_or_create_relay_creates_new(self, mock_start):
        from sse_relay import get_or_create_relay, _active_relays
        relay = await get_or_create_relay("stream-1", "http://localhost:9999/data")
        self.assertIsNotNone(relay)
        self.assertEqual(relay.stream_id, "stream-1")
        self.assertEqual(relay.data_url, "http://localhost:9999/data")
        self.assertIn("stream-1", _active_relays)
        mock_start.assert_called_once()

    @patch('sse_relay.SSERelay.start', new_callable=AsyncMock)
    async def test_get_or_create_relay_returns_existing(self, mock_start):
        from sse_relay import get_or_create_relay, _active_relays
        relay1 = await get_or_create_relay("stream-2", "http://localhost:9999/data")
        relay2 = await get_or_create_relay("stream-2", "http://localhost:9999/data")
        self.assertIs(relay1, relay2)
        # start should only be called once (when created)
        mock_start.assert_called_once()

    @patch('sse_relay.SSERelay.start', new_callable=AsyncMock)
    async def test_get_or_create_relay_different_url_restarts(self, mock_start):
        from sse_relay import get_or_create_relay, _active_relays
        relay1 = await get_or_create_relay("stream-3", "http://localhost:9999/data1")
        # Request same stream_id with different data_url
        relay2 = await get_or_create_relay("stream-3", "http://localhost:9999/data2")
        self.assertIsNot(relay1, relay2)
        self.assertEqual(relay2.data_url, "http://localhost:9999/data2")
        # start called twice: once for initial, once for restart
        self.assertEqual(mock_start.call_count, 2)

    async def test_stop_relay(self):
        from sse_relay import get_or_create_relay, stop_relay, _active_relays, SSERelay

        with patch.object(SSERelay, 'start', new_callable=AsyncMock):
            await get_or_create_relay("stream-4", "http://localhost:9999/data")

        self.assertIn("stream-4", _active_relays)
        await stop_relay("stream-4")
        self.assertNotIn("stream-4", _active_relays)

    async def test_stop_nonexistent_relay_no_error(self):
        from sse_relay import stop_relay
        # Should not raise
        await stop_relay("nonexistent-stream")

    async def test_stop_all_relays(self):
        from sse_relay import get_or_create_relay, stop_all_relays, _active_relays, SSERelay

        with patch.object(SSERelay, 'start', new_callable=AsyncMock):
            await get_or_create_relay("s1", "http://localhost:9999/d1")
            await get_or_create_relay("s2", "http://localhost:9999/d2")

        self.assertTrue(len(_active_relays) >= 2)
        await stop_all_relays()
        self.assertEqual(len(_active_relays), 0)

    @patch('sse_relay.SSERelay.start', new_callable=AsyncMock)
    async def test_get_relay(self, mock_start):
        from sse_relay import get_or_create_relay, get_relay
        relay = await get_or_create_relay("stream-5", "http://localhost:9999/data")
        found = get_relay("stream-5")
        self.assertIs(found, relay)
        self.assertIsNone(get_relay("nonexistent"))

    @patch('sse_relay.SSERelay.start', new_callable=AsyncMock)
    async def test_get_active_relay_count(self, mock_start):
        from sse_relay import get_or_create_relay, get_active_relay_count, stop_relay
        self.assertEqual(get_active_relay_count(), 0)
        await get_or_create_relay("c1", "http://localhost:9999/d1")
        self.assertEqual(get_active_relay_count(), 1)
        await get_or_create_relay("c2", "http://localhost:9999/d2")
        self.assertEqual(get_active_relay_count(), 2)
        await stop_relay("c1")
        self.assertEqual(get_active_relay_count(), 1)


class TestSSERelayLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test SSERelay start/stop lifecycle."""

    async def asyncTearDown(self):
        from sse_relay import _active_relays
        relay_ids = list(_active_relays.keys())
        for stream_id in relay_ids:
            relay = _active_relays.pop(stream_id)
            await relay.stop()

    async def test_start_creates_task(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/data", stream_id="lifecycle-test")

        with patch.object(relay, '_relay_loop', new_callable=AsyncMock):
            await relay.start()
            self.assertTrue(relay._running)
            self.assertIsNotNone(relay._task)

        await relay.stop()

    async def test_stop_cancels_task(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/data", stream_id="lifecycle-test")

        with patch.object(relay, '_relay_loop', new_callable=AsyncMock):
            await relay.start()
            await relay.stop()

        self.assertFalse(relay._running)
        self.assertTrue(relay._task.done() or relay._task.cancelled())

    async def test_double_start_no_error(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/data", stream_id="lifecycle-test")

        with patch.object(relay, '_relay_loop', new_callable=AsyncMock):
            await relay.start()
            await relay.start()  # Should not raise or create duplicate task

        await relay.stop()

    async def test_stop_clears_clients(self):
        from sse_relay import SSERelay
        relay = SSERelay(data_url="http://localhost:9999/data", stream_id="lifecycle-test")

        ws = MagicMock()
        relay.add_client(ws)
        self.assertTrue(relay.has_clients)

        with patch.object(relay, '_relay_loop', new_callable=AsyncMock):
            await relay.start()
            await relay.stop()

        self.assertFalse(relay.has_clients)


if __name__ == '__main__':
    unittest.main()