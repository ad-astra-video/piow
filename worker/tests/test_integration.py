#!/usr/bin/env python3
"""Integration test suite for Worker components."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add the worker directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemma_client import GemmaClient
from vllm_client import VLLMRealtimeClient

class TestWorkerIntegration(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_address = "0x742d35Cc6634C0532925a3b8D4C0532950532950"
        
    def test_gemma_client_integration(self):
        """Test Gemma client integration"""
        client = GemmaClient(base_url="http://gemma-vllm:6100")

        self.assertIsInstance(client, GemmaClient)
        self.assertTrue(client.is_configured)
        self.assertEqual(client.base_url, "http://gemma-vllm:6100")
    
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