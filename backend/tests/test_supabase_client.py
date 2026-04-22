#!/usr/bin/env python3
"""
Test suite for Supabase client
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestSupabaseClient(unittest.TestCase):
    
    def setUp(self):
        """Clear the singleton before each test"""
        import supabase_client
        supabase_client._supabase_client = None
    
    def tearDown(self):
        """Clear the singleton after each test"""
        import supabase_client
        supabase_client._supabase_client = None
    
    @patch.dict(os.environ, {
        'SUPABASE_URL': 'https://test.supabase.co',
        'SUPABASE_SECRET_KEY': 'test-secret-key'
    })
    @patch('supabase.create_client')
    def test_get_supabase_client_creates_client(self, mock_create_client):
        """Test that get_supabase_client creates a client when env vars are set"""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        # Import after setting environment variables
        from supabase_client import get_supabase_client
        
        client = get_supabase_client()
        
        mock_create_client.assert_called_once_with(
            'https://test.supabase.co',
            'test-secret-key'
        )
        self.assertEqual(client, mock_client)
    
    @patch.dict(os.environ, {
        'SUPABASE_URL': 'https://test.supabase.co',
        'SUPABASE_SECRET_KEY': 'test-secret-key'
    })
    def test_get_supabase_client_returns_singleton(self):
        """Test that get_supabase_client returns the same instance"""
        # Import after setting environment variables
        from supabase_client import get_supabase_client
        
        client1 = get_supabase_client()
        client2 = get_supabase_client()
        
        self.assertIs(client1, client2)
    
    @patch.dict(os.environ, {}, clear=True)
    def test_get_supabase_client_missing_env_vars(self):
        """Test that get_supabase_client raises ValueError when env vars missing"""
        # Import after clearing environment variables
        from supabase_client import get_supabase_client
        
        with self.assertRaises(ValueError) as context:
            get_supabase_client()
        
        self.assertIn("SUPABASE_URL and SUPABASE_SECRET_KEY must be set", str(context.exception))

if __name__ == '__main__':
    unittest.main()