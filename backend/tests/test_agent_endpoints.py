#!/usr/bin/env python3
"""
Test suite for agent API endpoints
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from transcribe import (
    agent_register,
    agent_get_usage,
    agent_list_keys,
    agent_create_key,
    agent_revoke_key,
    verify_agent_request
)

class TestAgentEndpoints:
    """Test class for agent endpoints"""
    
    @pytest.fixture
    def mock_request(self):
        """Create a mock request object"""
        request = MagicMock()
        request.headers = {}
        request.json = AsyncMock()
        return request
    
    @pytest.fixture
    def mock_supabase(self):
        """Mock supabase client"""
        with patch('transcribe.supabase') as mock_supabase:
            yield mock_supabase
    
    @pytest.mark.asyncio
    async def test_agent_register_success(self, mock_request, mock_supabase):
        """Test successful agent registration"""
        # Setup
        mock_request.json.return_value = {
            "name": "test-agent",
            "description": "A test agent"
        }
        
        mock_response_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "description": "A test agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response_data
        
        # Execute
        result = await agent_register(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["agent_id"] == "test-agent-id"
        assert response_json["name"] == "test-agent"
        assert "api_key" in response_json
        assert "api_secret" in response_json
        
        # Verify supabase calls
        mock_supabase.table.assert_called_with("agents")
        mock_supabase.table.return_value.insert.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_agent_register_missing_name(self, mock_request, mock_supabase):
        """Test agent registration with missing name (should use default)"""
        # Setup
        mock_request.json.return_value = {
            "description": "A test agent"
            # No name provided
        }
        
        mock_response_data = [{
            "id": "test-agent-id",
            "name": "unnamed-agent",  # Should default to this
            "description": "A test agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response_data
        
        # Execute
        result = await agent_register(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["name"] == "unnamed-agent"
    
    @pytest.mark.asyncio
    async def test_agent_register_database_error(self, mock_request, mock_supabase):
        """Test agent registration when database fails"""
        # Setup
        mock_request.json.return_value = {
            "name": "test-agent",
            "description": "A test agent"
        }
        
        mock_supabase.table.return_value.insert.return_value.execute.return_value = []
        
        # Execute
        result = await agent_register(mock_request)
        
        # Verify
        assert result.status_code == 500
        response_json = json.loads(result.body)
        assert "error" in response_json
        assert "Failed to create agent record" in response_json["error"]
    
    @pytest.mark.asyncio
    async def test_agent_get_usage_success(self, mock_request, mock_supabase):
        """Test successful agent usage retrieval"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "ltk_testkey123",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        
        mock_agent_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_agent_data
        
        # Execute
        result = await agent_get_usage(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["agent_id"] == "test-agent-id"
        assert response_json["name"] == "test-agent"
        assert "usage" in response_json
        assert "rate_limits" in response_json
    
    @pytest.mark.asyncio
    async def test_agent_get_usage_invalid_key(self, mock_request, mock_supabase):
        """Test agent usage with invalid API key"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "invalid_key",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = []
        
        # Execute
        result = await agent_get_usage(mock_request)
        
        # Verify
        assert result.status_code == 401
        response_json = json.loads(result.body)
        assert "error" in response_json
        assert "Invalid API key" in response_json["error"]
    
    @pytest.mark.asyncio
    async def test_agent_list_keys_success(self, mock_request, mock_supabase):
        """Test successful agent key listing"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "ltk_testkey123",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        
        mock_agent_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True,
            "last_used_at": "2024-01-01T01:00:00Z"
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_agent_data
        
        # Execute
        result = await agent_list_keys(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["agent_id"] == "test-agent-id"
        assert len(response_json["keys"]) == 1
        assert response_json["keys"][0]["key_id"] == "primary"
        assert response_json["keys"][0]["is_active"] == True
    
    @pytest.mark.asyncio
    async def test_agent_create_key_success(self, mock_request, mock_supabase):
        """Test successful agent key creation"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "ltk_oldkey123",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        
        # Mock the agent lookup (current key)
        mock_agent_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "api_key": "ltk_oldkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        
        # Mock the update response
        mock_update_data = [{
            "id": "test-agent-id",
            "api_key": "ltk_newkey456",
            "api_secret": "newsecret456",
            "last_used_at": "2024-01-01T02:00:00Z"
        }]
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_agent_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = mock_update_data
        
        # Execute
        result = await agent_create_key(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["agent_id"] == "test-agent-id"
        assert response_json["api_key"] == "ltk_newkey456"
        assert response_json["api_secret"] == "newsecret456"
        assert "message" in response_json
    
    @pytest.mark.asyncio
    async def test_agent_revoke_key_primary_key_error(self, mock_request, mock_supabase):
        """Test that revoking primary key returns error"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "ltk_testkey123",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        mock_request.json.return_value = {
            "key_id": "primary"
        }
        
        mock_agent_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_agent_data
        
        # Execute
        result = await agent_revoke_key(mock_request)
        
        # Verify
        assert result.status_code == 400
        response_json = json.loads(result.body)
        assert "error" in response_json
        assert "Cannot revoke primary key" in response_json["error"]
    
    @pytest.mark.asyncio
    async def test_agent_revoke_key_success(self, mock_request, mock_supabase):
        """Test successful agent key revocation (non-primary)"""
        # Setup
        mock_request.headers = {
            "X-API-Key": "ltk_testkey123",
            "X-Timestamp": str(int(os.time())),
            "X-Nonce": "testnonce123",
            "X-Signature": "testsig123"
        }
        mock_request.json.return_value = {
            "key_id": "secondary"
        }
        
        mock_agent_data = [{
            "id": "test-agent-id",
            "name": "test-agent",
            "api_key": "ltk_testkey123",
            "api_secret": "testsecret123",
            "created_at": "2024-01-01T00:00:00Z",
            "is_active": True
        }]
        
        mock_update_data = [{
            "id": "test-agent-id",
            "is_active": False,
            "revoked_at": "2024-01-01T02:00:00Z"
        }]
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_agent_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = mock_update_data
        
        # Execute
        result = await agent_revoke_key(mock_request)
        
        # Verify
        assert result.status_code == 200
        response_json = json.loads(result.body)
        assert response_json["agent_id"] == "test-agent-id"
        assert "message" in response_json
        assert "deactivated" in response_json["message"].lower()

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
