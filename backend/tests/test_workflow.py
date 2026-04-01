import asyncio
import unittest
from unittest.mock import AsyncMock

import sys
sys.path.insert(0, '.')


class TestBackendLifecycle(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import main
        main.connected_frontends.clear()
        main.pcs.clear()
        main.vllm_clients.clear()
        main.relays.clear()
        main.peer_contexts.clear()
        main.whip_handler.pcs.clear()
        self.main = main

    async def test_shutdown_app_closes_frontend_websockets(self):
        mock_ws = AsyncMock()
        self.main.connected_frontends.add(mock_ws)

        await self.main.shutdown_app(AsyncMock())

        mock_ws.close.assert_awaited_once()
        self.assertFalse(self.main.connected_frontends)

    async def test_track_task_removes_completed_task(self):
        mock_pc = AsyncMock()
        context = self.main.create_peer_context(mock_pc, id(mock_pc))

        task = self.main.track_task(context, asyncio.sleep(0), name="completed-task")
        await task

        self.assertNotIn(task, context["tasks"])


if __name__ == '__main__':
    unittest.main()