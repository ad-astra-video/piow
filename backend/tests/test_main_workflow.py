import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np

import sys
sys.path.insert(0, '.')

# We'll test the components individually to avoid import issues
# Test the VLLM client which we already have working tests for
# Test the AudioProcessorTrack logic
# Test the WHIP handler logic where we can

class TestAudioProcessorTest(unittest.IsolatedAsyncioTestCase):
    """Test the AudioProcessorTrack functionality."""

    def setUp(self):
        # Import inside the method to avoid issues
        from main import AudioProcessorTrack
        self.AudioProcessorTrack = AudioProcessorTrack

    async def test_audio_processor_track(self):
        """Test the AudioProcessorTrack functionality."""
        # Create a mock track
        mock_track = AsyncMock()
        mock_frame = MagicMock()
        # Create a simple int16 array for testing
        mock_audio_data = np.array([1, 2, 3], dtype=np.int16)
        mock_frame.to_ndarray.return_value = mock_audio_data
        mock_track.recv.return_value = mock_frame
        
        # Create a mock VLLM client
        mock_vllm_client = AsyncMock()
        mock_vllm_client.is_connected = True
        
        # Create the AudioProcessorTrack
        processor_track = self.AudioProcessorTrack(mock_track, mock_vllm_client)
        
        # Call recv
        frame = await processor_track.recv()
        
        # Verify that the track's recv was called
        mock_track.recv.assert_called_once()
        
        # Verify that send_audio was called on the VLLM client
        mock_vllm_client.send_audio.assert_called_once()
        
        # Verify that a frame was returned (the original frame)
        self.assertEqual(frame, mock_frame)

    def test_audio_frame_to_pcm16_handles_channels_first_stereo(self):
        from main import audio_frame_to_pcm16, VLLM_SAMPLE_RATE

        mock_frame = MagicMock()
        mock_frame.sample_rate = 48000
        mock_frame.to_ndarray.return_value = np.array(
            [[1000, 2000, 3000, 4000], [1000, 2000, 3000, 4000]],
            dtype=np.int16,
        )

        pcm16, original_shape, sample_rate = audio_frame_to_pcm16(mock_frame)

        self.assertEqual(original_shape, (2, 4))
        self.assertEqual(sample_rate, 48000)
        self.assertEqual(pcm16.dtype, np.int16)
        self.assertGreater(len(pcm16), 0)


class TestWHIPHandlerLogic(unittest.IsolatedAsyncioTestCase):
    """Test the WHIP handler logic where we can."""

    def setUp(self):
        # Import inside the method to avoid issues
        import main
        from main import WHIPHandler
        main.connected_frontends.clear()
        main.pcs.clear()
        main.vllm_clients.clear()
        main.relays.clear()
        main.peer_contexts.clear()
        main.whip_handler.pcs.clear()
        self.main = main
        self.WHIPHandler = WHIPHandler

    @patch('main.RTCPeerConnection')
    @patch('main.MediaRelay')
    async def test_whip_handler_success(self, mock_media_relay, mock_peer_connection):
        """Test the WHIP handler workflow with successful connection."""
        # Setup mocks
        mock_pc = AsyncMock()
        mock_pc.connectionState = "new"
        mock_peer_connection.return_value = mock_pc
        mock_media_relay.return_value = AsyncMock()
        
        # Mock the createAnswer and setLocalDescription methods
        mock_answer = MagicMock()
        mock_answer.sdp = "mock sdp answer"
        mock_pc.createAnswer.return_value = mock_answer
        mock_pc.localDescription = mock_answer
        
        # Mock the pc.on method to properly handle the decorator
        # When on(event) is called, it returns a decorator function
        mock_pc.on = MagicMock(side_effect=lambda event: lambda f: f)
        
        # Create a mock request object
        mock_request = AsyncMock()
        mock_request.text.return_value = "mock sdp offer"
        
        # Create handler instance and call the whip method
        handler = self.WHIPHandler()
        response = await handler.whip(mock_request)
        
        # Verify that the PeerConnection was created
        mock_peer_connection.assert_called_once()
        
        # Verify that setRemoteDescription was called with the offer
        mock_pc.setRemoteDescription.assert_called_once()
        offer = mock_pc.setRemoteDescription.await_args.args[0]
        self.assertEqual(offer.sdp, "mock sdp offer")
        self.assertEqual(offer.type, "offer")
        
        # Verify that createAnswer was called
        mock_pc.createAnswer.assert_called_once()
        
        # Verify that setLocalDescription was called with the answer
        mock_pc.setLocalDescription.assert_called_once_with(mock_answer)
        
        # Verify that the response contains the answer SDP
        # The response.text should be the SDP string
        self.assertEqual(response.text, mock_answer.sdp)
        self.assertEqual(response.content_type, "application/sdp")

    async def test_cleanup_peer_connection_cancels_tasks_and_closes_resources(self):
        mock_pc = AsyncMock()
        mock_pc.connectionState = "connected"
        context = self.main.create_peer_context(mock_pc, id(mock_pc))

        blocker = asyncio.Event()
        task = self.main.track_task(context, blocker.wait(), name="test-blocker")
        mock_vllm_client = AsyncMock()
        self.main.vllm_clients[id(mock_pc)] = mock_vllm_client
        self.main.pcs.add(mock_pc)
        self.main.whip_handler.pcs.add(mock_pc)

        await self.main.cleanup_peer_connection(mock_pc, reason="test")

        self.assertTrue(task.cancelled())
        mock_vllm_client.close.assert_awaited_once()
        mock_pc.close.assert_awaited_once()
        self.assertNotIn(mock_pc, self.main.peer_contexts)
        self.assertNotIn(id(mock_pc), self.main.vllm_clients)


if __name__ == '__main__':
    unittest.main()