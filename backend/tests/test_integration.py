#!/usr/bin/env python3
"""
Integration test suite for Live Transcription & Translation Platform - Backend
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import tempfile

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_client import get_supabase_client
from auth import (
    generate_nonce,
    create_siwe_message,
    verify_siwe_message,
    create_jwt_token,
    verify_jwt_token,
    authenticate_with_siwe,
    NonceStore
)

class TestIntegration(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_address = "0x742d35Cc6634C0532925a3b8D4C0532950532950"
        
    @patch.dict(os.environ, {
        'SUPABASE_URL': 'https://test.supabase.co',
        'SUPABASE_SECRET_KEY': 'test-secret-key'
    })
    @patch('supabase.create_client')
    def test_supabase_integration(self, mock_create_client):
        """Test Supabase client integration"""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        # Test getting client
        client = get_supabase_client()
        self.assertIsNotNone(client)
        
        # Test that it's a singleton
        client2 = get_supabase_client()
        self.assertIs(client, client2)
    
    def test_auth_flow_integration(self):
        """Test complete SIWE authentication flow"""
        # Generate nonce
        nonce = generate_nonce()
        self.assertIsInstance(nonce, str)
        self.assertEqual(len(nonce), 32)
        
        # Create SIWE message
        message = create_siwe_message(self.test_address, nonce)
        self.assertIn("live-translation-app wants you to sign in", message)
        self.assertIn(self.test_address, message)
        self.assertIn(nonce, message)
        
        # Test nonce store
        store = NonceStore()
        expires_at = MagicMock()
        expires_at.__gt__ = MagicMock(return_value=True)  # Not expired
        
        store.store_nonce(self.test_address, nonce, expires_at)
        nonce_data = store.get_nonce(self.test_address)
        self.assertIsNotNone(nonce_data)
        self.assertEqual(nonce_data["nonce"], nonce)
        
        # Test JWT creation and verification
        user_data = {
            "id": "test-user",
            "ethereum_address": self.test_address
        }
        token = create_jwt_token(user_data)
        self.assertIsInstance(token, str)
        
        payload = verify_jwt_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["id"], "test-user")
        
        # Test nonce consumption
        consumed = store.consume_nonce(self.test_address)
        self.assertTrue(consumed)
        
        # Second consumption should fail
        consumed_again = store.consume_nonce(self.test_address)
        self.assertFalse(consumed_again)
    
    def test_end_to_end_auth_workflow(self):
        """Test a simplified end-to-end auth workflow with mocks"""
        # 1. User generates nonce for SIWE
        nonce = generate_nonce()
        
        # 2. Create SIWE message
        message = create_siwe_message(self.test_address, nonce)
        
        # 3. Verify message format
        self.assertIn("live-translation-app wants you to sign in", message)
        
        # 4. Store nonce (simulating database)
        store = NonceStore()
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(minutes=10)
        store.store_nonce(self.test_address, nonce, expires_at)
        
        # 5. Verify nonce can be retrieved
        nonce_data = store.get_nonce(self.test_address)
        self.assertIsNotNone(nonce_data)
        self.assertEqual(nonce_data["nonce"], nonce)
        
        # 6. Create JWT for user (after successful signature verification)
        user_data = {
            "id": "user-123",
            "ethereum_address": self.test_address,
            "email": "user@example.com"
        }
        token = create_jwt_token(user_data)
        
        # 7. Verify JWT
        payload = verify_jwt_token(token)
        self.assertEqual(payload["id"], "user-123")
        self.assertEqual(payload["ethereum_address"], self.test_address)

if __name__ == '__main__':
    unittest.main()