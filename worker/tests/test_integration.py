#!/usr/bin/env python3
"""
Integration test suite for Worker components (Granite transcriber and VLLM client)
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite_transcriber import Granite4Transcriber
from vllm_client import VLLMRealtimeClient

class TestWorkerIntegration(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_address = "0x742d35Cc6634C0532925a3b8D4C0532950532950"
        
    @patch('granite_transcriber.Granite4Transcriber._load_model')
    def test_granite_transcriber_integration(self, mock_load_model):
        """Test Granite transcriber integration"""
        transcriber = Granite4Transcriber()
        
        # Test that it initializes correctly
        self.assertIsInstance(transcriber, Granite4Transcriber)
        
        # Test availability check
        is_available = transcriber.is_available()
        self.assertIsInstance(is_available, bool)
        
        # Test mock transcription when not loaded
        transcriber.is_loaded = False
        result = transcriber.transcribe("/fake/path.wav")
        self.assertIn('text', result)
        self.assertEqual(result['model'], 'granite-4.0-1b-mock')
    
    @patch('vllm_client.websockets.connect')
    def test_vllm_client_integration(self, mock_connect):
        """Test VLLM client integration"""
        mock_websocket = MagicMock()
        mock_connect.return_value = mock_websocket
        
        client = VLLMRealtimeClient(
            ws_url="ws://test.example.com/v1/realtime",
            source_lang="en",
            target_lang="es"
        )
        
        self.assertEqual(client.ws_url, "ws://test.example.com/v1/realtime")
        self.assertEqual(client.source_lang, "en")
        self.assertEqual(client.target_lang, "es")
        self.assertFalse(client.is_connected)

if __name__ == '__main__':
    unittest.main()