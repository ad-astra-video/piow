import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import json
import base64

import sys
sys.path.insert(0, '.')

from vllm_client import VLLMRealtimeClient


class TestVLLMRealtimeClient(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.client = VLLMRealtimeClient(
            ws_url="ws://localhost:8001/v1/realtime",
            source_lang="en",
            target_lang="es",
            temperature=0.7,
            max_tokens=256
        )

    async def asyncTearDown(self):
        await self.client.close()

    async def _connect_client(self, mock_connect):
        mock_ws = AsyncMock()
        mock_ws.recv.return_value = json.dumps({"type": "session.created", "id": "test-session"})
        mock_ws.__aiter__.return_value = iter(())

        async def mock_connect_corr(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = mock_connect_corr
        await self.client.connect()
        return mock_ws

    async def test_connect_success(self):
        # Mock the websocket connection
        with patch('vllm_client.websockets.connect') as mock_connect:
            mock_ws = await self._connect_client(mock_connect)

            # Check that connect was called with the correct URL
            mock_connect.assert_called_once_with("ws://localhost:8001/v1/realtime", additional_headers={})
            # Check that the client is marked as connected
            self.assertTrue(self.client.is_connected)
            # Check that the websocket is set
            self.assertEqual(self.client.websocket, mock_ws)
            # Check that the websocket.send was called at least once (for config)
            self.assertTrue(mock_ws.send.called)

    async def test_connect_failure(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            mock_connect.side_effect = Exception("Connection failed")

            with self.assertRaises(Exception):
                await self.client.connect(max_retries=2, retry_delay=0)

            self.assertFalse(self.client.is_connected)

    async def test_send_audio_not_connected(self):
        # When not connected, sending audio should log a warning and return early
        with patch('vllm_client.logger') as mock_logger:
            await self.client.send_audio(b"test audio")
            mock_logger.warning.assert_called_with("VLLM not connected, dropping audio data")

    async def test_send_audio_connected(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            mock_ws = await self._connect_client(mock_connect)
            self.assertTrue(self.client.is_connected)

            # Now send audio
            test_audio = b"test audio data"
            await self.client.send_audio(test_audio)

            # Check that the websocket.send was called with the correct base64 encoded data
            expected_base64 = base64.b64encode(test_audio).decode('utf-8')
            mock_ws.send.assert_called_with(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": expected_base64
            }))

    async def test_send_audio_connected_with_commit(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            mock_ws = await self._connect_client(mock_connect)
            self.assertTrue(self.client.is_connected)

            await self.client.send_audio(b"test audio data", commit=True)

            expected_base64 = base64.b64encode(b"test audio data").decode('utf-8')
            self.assertEqual(
                mock_ws.send.await_args_list[-2].args[0],
                json.dumps({"type": "input_audio_buffer.append", "audio": expected_base64}),
            )
            self.assertEqual(
                mock_ws.send.await_args_list[-1].args[0],
                json.dumps({"type": "input_audio_buffer.commit"}),
            )

    async def test_commit_audio_not_connected(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            await self._connect_client(mock_connect)
            # Then disconnect it
            self.client.is_connected = False

            with patch('vllm_client.logger') as mock_logger:
                await self.client.commit_audio()
                mock_logger.warning.assert_called_with("VLLM not connected, cannot commit audio")

    async def test_handle_vllm_message_transcript_delta(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            await self._connect_client(mock_connect)

            # Set up a callback to capture the text
            captured_text = []
            def text_callback(text, is_final=False):
                captured_text.append((text, is_final))

            self.client.set_text_callback(text_callback)

            # Simulate a transcript delta message
            message = {
                "type": "transcription.delta",
                "delta": "Hello"
            }
            await self.client._handle_vllm_message(message)

            self.assertEqual(len(captured_text), 1)
            self.assertEqual(captured_text[0][0], "Hello")
            self.assertFalse(captured_text[0][1])  # is_final should be False

    async def test_handle_vllm_message_transcript_done(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            await self._connect_client(mock_connect)

            captured_text = []
            def text_callback(text, is_final=False, usage=None):
                captured_text.append((text, is_final))

            self.client.set_text_callback(text_callback)

            message = {
                "type": "transcription.done",
                "transcript": "Hello world"
            }
            await self.client._handle_vllm_message(message)

            self.assertEqual(len(captured_text), 1)
            self.assertEqual(captured_text[0][0], "Hello world")
            self.assertTrue(captured_text[0][1])  # is_final should be True

    async def test_handle_vllm_message_async_callback(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            await self._connect_client(mock_connect)

            callback = AsyncMock()
            self.client.set_text_callback(callback)

            await self.client._handle_vllm_message({
                "type": "transcription.done",
                "transcript": "Hello world"
            })

            callback.assert_awaited_once()

    async def test_close(self):
        with patch('vllm_client.websockets.connect') as mock_connect:
            mock_ws = await self._connect_client(mock_connect)
            self.assertTrue(self.client.is_connected)

            await self.client.close()

            self.assertFalse(self.client.is_connected)
            self.assertIsNone(self.client.websocket)
            mock_ws.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()