#!/usr/bin/env python3
"""
Tests for the main backend workflow.

Tests WebSocket handler message routing, SSE relay integration,
session-based stream management, and application shutdown.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, '.')

from aiohttp import web


class TestWebSocketHandlerStartStream(unittest.IsolatedAsyncioTestCase):
    """Test the _handle_start_stream helper function."""

    async def asyncSetUp(self):
        import main
        self.main = main
        # Clear global state
        main.connected_frontends.clear()
        main.ws_streams.clear()

    async def asyncTearDown(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        from sse_relay import _active_relays
        for sid in list(_active_relays.keys()):
            relay = _active_relays.pop(sid)
            await relay.stop()

    @patch('main.get_or_create_relay', new_callable=AsyncMock)
    @patch('main.session_store')
    async def test_start_stream_success(self, mock_session_store, mock_get_relay):
        """start_stream looks up session, creates relay, subscribes ws."""
        import main

        # Mock session store (async methods need AsyncMock)
        mock_session_store.get_stream_session = AsyncMock(return_value={
            "id": "stream-1",
            "provider_session": {
                "data_url": "http://worker:9935/stream/data",
                "whip_url": "http://worker:9935/whip",
            }
        })

        # Mock relay
        mock_relay = MagicMock()
        mock_relay.add_client = MagicMock()
        mock_relay.has_clients = True
        mock_get_relay.return_value = mock_relay

        # Mock WebSocket
        mock_ws = AsyncMock()

        await main._handle_start_stream(mock_ws, "stream-1")

        mock_session_store.get_stream_session.assert_called_once_with("stream-1")
        mock_get_relay.assert_called_once_with("stream-1", "http://worker:9935/stream/data")
        mock_relay.add_client.assert_called_once_with(mock_ws)
        mock_ws.send_json.assert_called()
        # Check the status message
        sent_msg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sent_msg["type"], "status")
        self.assertIn("stream-1", sent_msg["text"])

    @patch('main.session_store')
    async def test_start_stream_session_not_found(self, mock_session_store):
        """start_stream sends error when session not found."""
        import main

        mock_session_store.get_stream_session = AsyncMock(return_value=None)
        mock_ws = AsyncMock()

        await main._handle_start_stream(mock_ws, "nonexistent")

        mock_ws.send_json.assert_called_once()
        sent_msg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sent_msg["type"], "error")
        self.assertIn("not found", sent_msg["text"])

    @patch('main.session_store')
    async def test_start_stream_no_data_url(self, mock_session_store):
        """start_stream sends error when data_url is missing."""
        import main

        mock_session_store.get_stream_session = AsyncMock(return_value={
            "id": "stream-2",
            "provider_session": {
                "whip_url": "http://worker:9935/whip",
                # No data_url
            }
        })
        mock_ws = AsyncMock()

        await main._handle_start_stream(mock_ws, "stream-2")

        mock_ws.send_json.assert_called_once()
        sent_msg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sent_msg["type"], "error")
        self.assertIn("No data_url", sent_msg["text"])

    @patch('main.get_or_create_relay', new_callable=AsyncMock)
    @patch('main.session_store')
    async def test_start_stream_relay_creation_failure(self, mock_session_store, mock_get_relay):
        """start_stream sends error when relay creation fails."""
        import main

        mock_session_store.get_stream_session = AsyncMock(return_value={
            "id": "stream-3",
            "provider_session": {
                "data_url": "http://worker:9935/stream/data",
            }
        })
        mock_get_relay.side_effect = Exception("Connection refused")

        mock_ws = AsyncMock()

        await main._handle_start_stream(mock_ws, "stream-3")

        mock_ws.send_json.assert_called_once()
        sent_msg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sent_msg["type"], "error")
        self.assertIn("Failed to connect", sent_msg["text"])


class TestWebSocketHandlerStopStream(unittest.IsolatedAsyncioTestCase):
    """Test the _handle_stop_stream helper function."""

    async def asyncSetUp(self):
        import main
        self.main = main
        main.connected_frontends.clear()
        main.ws_streams.clear()

    async def asyncTearDown(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        from sse_relay import _active_relays
        for sid in list(_active_relays.keys()):
            relay = _active_relays.pop(sid)
            await relay.stop()

    @patch('main.stop_relay', new_callable=AsyncMock)
    @patch('main.get_relay')
    async def test_stop_stream_with_clients_remaining(self, mock_get_relay, mock_stop_relay):
        """stop_stream removes client but doesn't stop relay if other clients exist."""
        import main

        mock_relay = MagicMock()
        mock_relay.has_clients = True  # Still has other clients
        mock_relay.remove_client = MagicMock()
        mock_get_relay.return_value = mock_relay

        mock_ws = AsyncMock()
        main.ws_streams[mock_ws] = {"stream-1"}

        await main._handle_stop_stream(mock_ws, "stream-1")

        mock_relay.remove_client.assert_called_once_with(mock_ws)
        # Relay should NOT be stopped since it still has clients
        mock_stop_relay.assert_not_called()
        mock_ws.send_json.assert_called()

    @patch('main.stop_relay', new_callable=AsyncMock)
    @patch('main.get_relay')
    async def test_stop_stream_no_clients_remaining(self, mock_get_relay, mock_stop_relay):
        """stop_stream stops relay when no clients remain."""
        import main

        mock_relay = MagicMock()
        mock_relay.has_clients = False  # No more clients
        mock_relay.remove_client = MagicMock()
        mock_get_relay.return_value = mock_relay

        mock_ws = AsyncMock()
        main.ws_streams[mock_ws] = {"stream-1"}

        await main._handle_stop_stream(mock_ws, "stream-1")

        mock_relay.remove_client.assert_called_once_with(mock_ws)
        # Relay should be stopped since no clients remain
        mock_stop_relay.assert_called_once_with("stream-1")

    @patch('main.get_relay')
    async def test_stop_stream_no_relay(self, mock_get_relay):
        """stop_stream sends status when no relay exists for stream."""
        import main

        mock_get_relay.return_value = None
        mock_ws = AsyncMock()

        await main._handle_stop_stream(mock_ws, "nonexistent")

        mock_ws.send_json.assert_called_once()
        sent_msg = mock_ws.send_json.call_args[0][0]
        self.assertEqual(sent_msg["type"], "status")
        self.assertIn("No active relay", sent_msg["text"])


class TestCleanupWsStreams(unittest.IsolatedAsyncioTestCase):
    """Test the _cleanup_ws_streams helper function."""

    async def asyncSetUp(self):
        import main
        self.main = main
        main.connected_frontends.clear()
        main.ws_streams.clear()

    async def asyncTearDown(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        from sse_relay import _active_relays
        for sid in list(_active_relays.keys()):
            relay = _active_relays.pop(sid)
            await relay.stop()

    @patch('main.stop_relay', new_callable=AsyncMock)
    @patch('main.get_relay')
    async def test_cleanup_removes_ws_from_all_relays(self, mock_get_relay, mock_stop_relay):
        """cleanup removes ws from all subscribed relays."""
        import main

        mock_relay1 = MagicMock()
        mock_relay1.has_clients = True
        mock_relay2 = MagicMock()
        mock_relay2.has_clients = False  # This one should be stopped

        def get_relay_side_effect(sid):
            if sid == "s1":
                return mock_relay1
            if sid == "s2":
                return mock_relay2
            return None

        mock_get_relay.side_effect = get_relay_side_effect

        mock_ws = AsyncMock()
        main.ws_streams[mock_ws] = {"s1", "s2"}

        await main._cleanup_ws_streams(mock_ws)

        mock_relay1.remove_client.assert_called_once_with(mock_ws)
        mock_relay2.remove_client.assert_called_once_with(mock_ws)
        # Only relay2 should be stopped (no clients left)
        mock_stop_relay.assert_called_once_with("s2")
        # ws_streams entry should be removed
        self.assertNotIn(mock_ws, main.ws_streams)

    async def test_cleanup_no_streams(self):
        """cleanup with no streams is a no-op."""
        import main

        mock_ws = AsyncMock()
        # No streams for this ws
        await main._cleanup_ws_streams(mock_ws)
        # Should not raise


class TestShutdownApp(unittest.IsolatedAsyncioTestCase):
    """Test the shutdown_app function."""

    async def asyncSetUp(self):
        import main
        self.main = main
        main.connected_frontends.clear()
        main.ws_streams.clear()

    async def asyncTearDown(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()

    @patch('main.stop_all_relays', new_callable=AsyncMock)
    async def test_shutdown_closes_websockets(self, mock_stop_relays):
        """shutdown_app closes all frontend WebSocket connections."""
        import main

        mock_ws = AsyncMock()
        main.connected_frontends.add(mock_ws)

        await main.shutdown_app(AsyncMock())

        mock_ws.close.assert_awaited_once()
        self.assertFalse(main.connected_frontends)
        mock_stop_relays.assert_awaited_once()

    @patch('main.stop_all_relays', new_callable=AsyncMock)
    async def test_shutdown_clears_ws_streams(self, mock_stop_relays):
        """shutdown_app clears the ws_streams tracking dict."""
        import main

        mock_ws = AsyncMock()
        main.ws_streams[mock_ws] = {"s1"}

        await main.shutdown_app(AsyncMock())

        self.assertEqual(len(main.ws_streams), 0)


class TestHealthCheck(unittest.IsolatedAsyncioTestCase):
    """Test the health_check endpoint includes SSE relay count."""

    async def test_health_includes_sse_relays(self):
        import main
        from sse_relay import get_active_relay_count

        mock_request = MagicMock()
        response = await main.health_check(mock_request)
        body = json.loads(response.text)
        self.assertIn("sse_relays", body["services"])
        self.assertIsInstance(body["services"]["sse_relays"], int)


if __name__ == '__main__':
    unittest.main()