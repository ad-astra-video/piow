#!/usr/bin/env python3
"""
Tests for the backend lifecycle and WebSocket workflow.

Tests application shutdown, WebSocket stream tracking,
and SSE relay cleanup on disconnect.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, '.')


class TestBackendLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test backend application lifecycle."""

    async def asyncSetUp(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        self.main = main

    async def asyncTearDown(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        from sse_relay import _active_relays
        for sid in list(_active_relays.keys()):
            relay = _active_relays.pop(sid)
            await relay.stop()

    @patch('main.stop_all_relays', new_callable=AsyncMock)
    async def test_shutdown_app_closes_frontend_websockets(self, mock_stop_relays):
        """shutdown_app closes all connected frontend WebSockets."""
        mock_ws = AsyncMock()
        self.main.connected_frontends.add(mock_ws)

        await self.main.shutdown_app(AsyncMock())

        mock_ws.close.assert_awaited_once()
        self.assertFalse(self.main.connected_frontends)
        mock_stop_relays.assert_awaited_once()

    @patch('main.stop_all_relays', new_callable=AsyncMock)
    async def test_shutdown_app_clears_ws_streams(self, mock_stop_relays):
        """shutdown_app clears the ws_streams tracking dict."""
        mock_ws = AsyncMock()
        self.main.ws_streams[mock_ws] = {"stream-1", "stream-2"}

        await self.main.shutdown_app(AsyncMock())

        self.assertEqual(len(self.main.ws_streams), 0)

    @patch('main.stop_all_relays', new_callable=AsyncMock)
    async def test_shutdown_app_with_no_connections(self, mock_stop_relays):
        """shutdown_app handles empty state gracefully."""
        await self.main.shutdown_app(AsyncMock())
        # Should not raise
        self.assertFalse(self.main.connected_frontends)
        self.assertEqual(len(self.main.ws_streams), 0)


class TestWebSocketStreamTracking(unittest.IsolatedAsyncioTestCase):
    """Test ws_streams tracking on WebSocket connect/disconnect."""

    async def asyncSetUp(self):
        import main
        main.connected_frontends.clear()
        main.ws_streams.clear()
        self.main = main

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
    async def test_cleanup_removes_ws_from_single_stream(self, mock_get_relay, mock_stop_relay):
        """_cleanup_ws_streams removes a WebSocket from its subscribed relay."""
        mock_relay = MagicMock()
        mock_relay.has_clients = False
        mock_relay.remove_client = MagicMock()
        mock_get_relay.return_value = mock_relay

        mock_ws = AsyncMock()
        self.main.ws_streams[mock_ws] = {"stream-1"}

        await self.main._cleanup_ws_streams(mock_ws)

        mock_relay.remove_client.assert_called_once_with(mock_ws)
        mock_stop_relay.assert_called_once_with("stream-1")
        self.assertNotIn(mock_ws, self.main.ws_streams)

    @patch('main.stop_relay', new_callable=AsyncMock)
    @patch('main.get_relay')
    async def test_cleanup_removes_ws_from_multiple_streams(self, mock_get_relay, mock_stop_relay):
        """_cleanup_ws_streams removes a WebSocket from multiple relays."""
        relay1 = MagicMock()
        relay1.has_clients = True
        relay1.remove_client = MagicMock()
        relay2 = MagicMock()
        relay2.has_clients = False
        relay2.remove_client = MagicMock()

        def get_relay_fn(sid):
            return relay1 if sid == "s1" else relay2

        mock_get_relay.side_effect = get_relay_fn

        mock_ws = AsyncMock()
        self.main.ws_streams[mock_ws] = {"s1", "s2"}

        await self.main._cleanup_ws_streams(mock_ws)

        relay1.remove_client.assert_called_once_with(mock_ws)
        relay2.remove_client.assert_called_once_with(mock_ws)
        # Only relay2 (no clients) should be stopped
        mock_stop_relay.assert_called_once_with("s2")

    async def test_cleanup_ws_with_no_streams(self):
        """_cleanup_ws_streams is a no-op when ws has no streams."""
        mock_ws = AsyncMock()
        await self.main._cleanup_ws_streams(mock_ws)
        self.assertNotIn(mock_ws, self.main.ws_streams)


if __name__ == '__main__':
    unittest.main()