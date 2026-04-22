#!/usr/bin/env python3
"""
Test suite for VLLM client
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import json
import base64

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vllm_client import VLLMRealtimeClient, TextCallback, AudioCallback

class TestVLLMRealtimeClient(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.ws_url = "ws://test.example.com/v1/realtime"
        self.source_lang = "en"
        self.target_lang = "es"
        
    @patch('vllm_client.websockets.connect')
    def test_init(self, mock_connect):
        """Test client initialization"""
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        self.assertEqual(client.ws_url, self.ws_url)
        self.assertEqual(client.source_lang, self.source_lang)
        self.assertEqual(client.target_lang, self.target_lang)
        self.assertFalse(client.is_connected)
        self.assertIsNone(client.websocket)
        self.assertIsNone(client.audio_callback)
        self.assertIsNone(client.text_callback)
    
    def test_init_invalid_params(self):
        """Test initialization with invalid parameters"""
        # Test invalid ws_url
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(ws_url="", source_lang="en", target_lang="es")
        
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(ws_url=None, source_lang="en", target_lang="es")
            
        # Test invalid source_lang
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(ws_url=self.ws_url, source_lang="", target_lang="es")
            
        # Test invalid target_lang
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(ws_url=self.ws_url, source_lang="en", target_lang="")
            
        # Test invalid temperature
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(
                ws_url=self.ws_url,
                source_lang="en",
                target_lang="es",
                temperature=-0.1
            )
            
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(
                ws_url=self.ws_url,
                source_lang="en",
                target_lang="es",
                temperature=2.1
            )
            
        # Test invalid max_tokens
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(
                ws_url=self.ws_url,
                source_lang="en",
                target_lang="es",
                max_tokens=0
            )
            
        with self.assertRaises(ValueError):
            VLLMRealtimeClient(
                ws_url=self.ws_url,
                source_lang="en",
                target_lang="es",
                max_tokens=-1
            )
    
    @patch('vllm_client.websockets.connect')
    @patch('asyncio.create_task')
    def test_connect_success(self, mock_create_task, mock_connect):
        """Test successful connection"""
        # Setup mocks
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket
        
        # Mock the initial session.created message
        mock_websocket.recv.side_effect = [
            json.dumps({"type": "session.created", "id": "test-session-id"}),
            json.dumps({"type": "session.updated"})  # for session.update response
        ]
        
        # Create client and connect
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        # Run the connect method
        asyncio.run(client.connect())
        
        # Assertions
        mock_connect.assert_called_once_with(self.ws_url, additional_headers={})
        self.assertTrue(client.is_connected)
        self.assertEqual(client.websocket, mock_websocket)
        mock_create_task.assert_called_once()  # Listener task should be created
        
        # Check that session.update was sent with correct model
        calls = mock_websocket.send.call_args_list
        session_update_sent = any(
            '"type":"session.update"' in str(call) and 
            '"model":"mistralai/Voxtral-Mini-4B-Realtime-2602"' in str(call)
            for call in calls
        )
        self.assertTrue(session_update_sent)
        
        # Check that initial commit was sent
        initial_commit_sent = any(
            '"type":"input_audio_buffer.commit"' in str(call)
            for call in calls
        )
        self.assertTrue(initial_commit_sent)
    
    @patch('vllm_client.websockets.connect')
    def test_connect_failure_retry(self, mock_connect):
        """Test connection failure with retry logic"""
        # Make connect fail twice then succeed
        mock_connect.side_effect = [
            Exception("Connection failed"),
            Exception("Connection failed"),
            AsyncMock()  # Success on third attempt
        ]
        
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        # This should raise an exception after max retries
        with self.assertRaises(Exception) as context:
            asyncio.run(client.connect(max_retries=2, retry_delay=0.01))
        
        self.assertIn("Failed to connect to VLLM after 2 attempts", str(context.exception))
        self.assertEqual(mock_connect.call_count, 2)
    
    @patch('vllm_client.websockets.connect')
    def test_send_audio_not_connected(self, mock_connect):
        """Test sending audio when not connected"""
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket
        
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        # Don't connect, leave is_connected as False
        
        # This should not raise an exception, just log warning
        asyncio.run(client.send_audio(b"test audio data"))
        
        # Should not have called send on websocket
        mock_websocket.send.assert_not_called()
    
    @patch('vllm_client.websockets.connect')
    def test_send_audio_success(self, mock_connect):
        """Test sending audio data successfully"""
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket
        
        # Setup connection mocks
        mock_websocket.recv.side_effect = [
            json.dumps({"type": "session.created", "id": "test"}),
            json.dumps({"type": "session.updated"})
        ]
        
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        # Connect the client
        asyncio.run(client.connect())
        
        # Send audio
        test_audio = b"test audio data"
        asyncio.run(client.send_audio(test_audio))
        
        # Check that audio was sent as base64
        expected_base64 = base64.b64encode(test_audio).decode('utf-8')
        mock_websocket.send.assert_called_with(
            json.dumps({
                "type": "input_audio_buffer.append",
                "audio": expected_base64
            })
        )
    
    @patch('vllm_client.websockets.connect')
    def test_commit_audio(self, mock_connect):
        """Test committing audio buffer"""
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket
        
        # Setup connection mocks
        mock_websocket.recv.side_effect = [
            json.dumps({"type": "session.created", "id": "test"}),
            json.dumps({"type": "session.updated"})
        ]
        
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        # Connect the client
        asyncio.run(client.connect())
        
        # Commit audio
        asyncio.run(client.commit_audio())
        
        # Check that commit was sent
        mock_websocket.send.assert_called_with(
            json.dumps({"type": "input_audio_buffer.commit"})
        )
    
    @patch('vllm_client.websockets.connect')
    def test_close(self, mock_connect):
        """Test closing the connection"""
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket
        mock_listener_task = AsyncMock()
        
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        client.websocket = mock_websocket
        client.is_connected = True
        client.listener_task = mock_listener_task
        
        # Close the client
        asyncio.run(client.close())
        
        # Check that listener task was cancelled
        mock_listener_task.cancel.assert_called_once()
        
        # Check that websocket was closed
        mock_websocket.close.assert_called_once()
        
        # Check that state was reset
        self.assertFalse(client.is_connected)
        self.assertIsNone(client.websocket)
        self.assertIsNone(client.listener_task)
    
    def test_set_callbacks(self):
        """Test setting callbacks"""
        client = VLLMRealtimeClient(
            ws_url=self.ws_url,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )
        
        # Test audio callback
        audio_callback = MagicMock()
        client.set_audio_callback(audio_callback)
        self.assertEqual(client.audio_callback, audio_callback)
        
        # Test text callback
        text_callback = MagicMock()
        client.set_text_callback(text_callback)
        self.assertEqual(client.text_callback, text_callback)

if __name__ == '__main__':
    unittest.main()