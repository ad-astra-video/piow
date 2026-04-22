#!/usr/bin/env python3
"""
Test suite for authentication module.

Tests the refactored auth system which supports:
  - Supabase user auth (JWT validation) for email, Google, Twitter, and Web3/SIWE
  - Agent auth (HMAC-SHA256 signed requests)
  - Marker decorators: @require_user_auth, @require_agent_auth, @no_auth
  - auth_middleware: enforces auth by default, applies rate limiting and usage tracking
  - @no_auth opts out of auth, rate limiting, usage tracking, and payment validation
"""

import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import (
    verify_supabase_user,
    verify_agent_request,
    require_user_auth,
    require_agent_auth,
    no_auth,
    auth_middleware,
    _check_rate_limit,
    _record_usage,
    auth_me_handler,
)


class FakeUser:
    """Mock Supabase user object."""
    def __init__(self, id="user-123", email="test@example.com", identities=None,
                 app_metadata=None, user_metadata=None, created_at="2024-01-01T00:00:00Z"):
        self.id = id
        self.email = email
        self.identities = identities or []
        self.app_metadata = app_metadata or {}
        self.user_metadata = user_metadata or {}
        self.created_at = created_at


class TestVerifySupabaseUser(unittest.TestCase):
    """Tests for verify_supabase_user()."""

    @patch('auth.supabase')
    def test_valid_bearer_token(self, mock_supabase):
        """Test successful JWT validation via Authorization header."""
        mock_user = FakeUser()
        mock_response = MagicMock()
        mock_response.user = mock_user
        mock_supabase.auth.get_user.return_value = mock_response

        request = MagicMock()
        request.headers.get.return_value = "Bearer valid-jwt-token"
        request.cookies.get.return_value = None

        verified, result = verify_supabase_user(request)
        self.assertTrue(verified)
        self.assertEqual(result, mock_user)
        self.assertEqual(request.user, mock_user)

    @patch('auth.supabase')
    def test_valid_cookie_token(self, mock_supabase):
        """Test JWT validation via Supabase session cookie."""
        mock_user = FakeUser()
        mock_response = MagicMock()
        mock_response.user = mock_user
        mock_supabase.auth.get_user.return_value = mock_response

        request = MagicMock()
        request.headers.get.return_value = None
        request.cookies.get.side_effect = lambda k: "cookie-token" if k in ("sb:token", "supabase-auth-token") else None

        verified, result = verify_supabase_user(request)
        self.assertTrue(verified)

    def test_missing_token(self):
        """Test that missing token returns 401."""
        request = MagicMock()
        request.headers.get.return_value = None
        request.cookies.get.return_value = None

        verified, result = verify_supabase_user(request)
        self.assertFalse(verified)
        # result is an aiohttp web.Response with status 401
        self.assertEqual(result.status, 401)

    @patch('auth.supabase')
    def test_invalid_token(self, mock_supabase):
        """Test that invalid/expired token returns 401."""
        mock_supabase.auth.get_user.side_effect = Exception("Invalid token")

        request = MagicMock()
        request.headers.get.return_value = "Bearer invalid-token"
        request.cookies.get.return_value = None

        verified, result = verify_supabase_user(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)

    @patch('auth.supabase')
    def test_null_user_response(self, mock_supabase):
        """Test that null user in response returns 401."""
        mock_response = MagicMock()
        mock_response.user = None
        mock_supabase.auth.get_user.return_value = mock_response

        request = MagicMock()
        request.headers.get.return_value = "Bearer some-token"
        request.cookies.get.return_value = None

        verified, result = verify_supabase_user(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)


class TestVerifyAgentRequest(unittest.TestCase):
    """Tests for verify_agent_request()."""

    def test_missing_headers(self):
        """Test that missing required headers returns 401."""
        request = MagicMock()
        request.headers.get.return_value = None

        verified, result = verify_agent_request(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)

    def test_expired_timestamp(self):
        """Test that expired timestamp returns 401."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: {
            'X-API-Key': 'test-key',
            'X-Timestamp': str(int(time.time()) - 600),  # 10 minutes ago
            'X-Nonce': 'random-nonce',
            'X-Signature': 'some-signature',
        }.get(h)

        verified, result = verify_agent_request(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)

    @patch('auth.supabase')
    def test_invalid_api_key(self, mock_supabase):
        """Test that invalid API key returns 401."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        request = MagicMock()
        request.headers.get.side_effect = lambda h: {
            'X-API-Key': 'invalid-key',
            'X-Timestamp': str(int(time.time())),
            'X-Nonce': 'random-nonce',
            'X-Signature': 'some-signature',
        }.get(h)

        verified, result = verify_agent_request(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)

    @patch('auth.supabase')
    def test_deactivated_agent(self, mock_supabase):
        """Test that deactivated agent returns 401."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{'id': 'agent-1', 'api_key': 'test-key', 'is_active': False}]
        )

        request = MagicMock()
        request.headers.get.side_effect = lambda h: {
            'X-API-Key': 'test-key',
            'X-Timestamp': str(int(time.time())),
            'X-Nonce': 'random-nonce',
            'X-Signature': 'some-signature',
        }.get(h)
        request.method = 'GET'
        request.path = '/api/v1/transcribe/file'

        verified, result = verify_agent_request(request)
        self.assertFalse(verified)
        self.assertEqual(result.status, 401)


class TestAuthMeHandler(unittest.TestCase):
    """Tests for the /api/v1/auth/me endpoint."""

    def test_me_returns_user_info_with_middleware(self):
        """Test that /auth/me returns user info when middleware has set request['user']."""
        mock_user = FakeUser(
            id="user-123",
            email="test@example.com",
            identities=[
                {
                    'provider': 'google',
                    'identity_data': {
                        'full_name': 'Test User',
                        'avatar_url': 'https://example.com/avatar.png',
                        'email': 'test@example.com',
                    },
                },
                {
                    'provider': 'web3',
                    'identity_data': {
                        'wallet_address': '0x742d35Cc6634C0532925a3b8D4C0532950532950',
                    },
                },
            ],
        )

        request = MagicMock()
        request.get.return_value = mock_user  # Middleware sets request['user']

        import asyncio
        response = asyncio.get_event_loop().run_until_complete(auth_me_handler(request))

        self.assertEqual(response.status, 200)

    def test_me_returns_401_when_no_user(self):
        """Test that /auth/me returns 401 when middleware hasn't set user (safety net)."""
        request = MagicMock()
        request.get.return_value = None  # No user set by middleware

        import asyncio
        response = asyncio.get_event_loop().run_until_complete(auth_me_handler(request))

        self.assertEqual(response.status, 401)


class TestMarkerDecorators(unittest.TestCase):
    """Tests for marker decorators: @no_auth, @require_user_auth, @require_agent_auth."""

    def test_no_auth_sets_flag(self):
        """Test that @no_auth sets _no_auth attribute on handler."""
        @no_auth
        async def handler(request):
            return MagicMock(status=200)

        self.assertTrue(getattr(handler, '_no_auth', False))

    def test_no_auth_returns_same_function(self):
        """Test that @no_auth returns the handler unchanged (marker only)."""
        async def handler(request):
            return MagicMock(status=200)

        original_id = id(handler)
        result = no_auth(handler)
        # Marker decorator returns the same function object
        self.assertEqual(id(result), original_id)

    def test_require_user_auth_sets_flag(self):
        """Test that @require_user_auth sets _auth_type = 'user' on handler."""
        @require_user_auth
        async def handler(request):
            return MagicMock(status=200)

        self.assertEqual(getattr(handler, '_auth_type', None), 'user')

    def test_require_user_auth_returns_same_function(self):
        """Test that @require_user_auth returns the handler unchanged (marker only)."""
        async def handler(request):
            return MagicMock(status=200)

        original_id = id(handler)
        result = require_user_auth(handler)
        self.assertEqual(id(result), original_id)

    def test_require_agent_auth_sets_flag(self):
        """Test that @require_agent_auth sets _auth_type = 'agent' on handler."""
        @require_agent_auth
        async def handler(request):
            return MagicMock(status=200)

        self.assertEqual(getattr(handler, '_auth_type', None), 'agent')

    def test_require_agent_auth_returns_same_function(self):
        """Test that @require_agent_auth returns the handler unchanged (marker only)."""
        async def handler(request):
            return MagicMock(status=200)

        original_id = id(handler)
        result = require_agent_auth(handler)
        self.assertEqual(id(result), original_id)

    def test_no_auth_flag_propagates_through_functools_wraps(self):
        """Test that _no_auth flag survives functools.wraps in outer decorators."""
        import functools

        @no_auth
        async def handler(request):
            return MagicMock(status=200)

        # Simulate an outer decorator that uses functools.wraps
        def outer_decorator(handler):
            @functools.wraps(handler)
            async def wrapper(request):
                return await handler(request)
            return wrapper

        wrapped = outer_decorator(handler)
        # functools.wraps copies __dict__, so _no_auth should propagate
        self.assertTrue(getattr(wrapped, '_no_auth', False))

    def test_default_handler_has_no_auth_type(self):
        """Test that a plain handler without markers has no _auth_type (defaults to 'any')."""
        async def handler(request):
            return MagicMock(status=200)

        self.assertIsNone(getattr(handler, '_auth_type', None))
        self.assertFalse(getattr(handler, '_no_auth', False))


class TestAuthMiddleware(unittest.IsolatedAsyncioTestCase):
    """Tests for auth_middleware behavior."""

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_allows_no_auth_handler(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that @no_auth handlers skip auth, rate limiting, and usage tracking."""
        mock_rate_limit.return_value = True

        @no_auth
        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()
        request.headers.get.return_value = None  # No auth headers

        response = await auth_middleware(request, handler)

        # Handler should be called directly, no auth verification
        mock_agent_auth.assert_not_called()
        mock_user_auth.assert_not_called()
        mock_rate_limit.assert_not_called()
        mock_record_usage.assert_not_called()
        self.assertEqual(response.status, 200)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_default_auth_tries_agent_then_user(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that default auth (no marker) tries agent auth first, then user auth."""
        mock_agent_auth.return_value = (False, MagicMock(status=401))
        mock_user_auth.return_value = (True, FakeUser())
        mock_rate_limit.return_value = True

        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        # Agent auth should be tried first
        mock_agent_auth.assert_called_once()
        # User auth should be tried as fallback
        mock_user_auth.assert_called_once()
        self.assertEqual(response.status, 200)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_default_auth_returns_401_when_both_fail(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that default auth returns 401 when both agent and user auth fail."""
        from aiohttp import web
        mock_agent_auth.return_value = (False, web.json_response({"error": "Agent auth failed"}, status=401))
        mock_user_auth.return_value = (False, web.json_response({"error": "User auth failed"}, status=401))

        async def handler(request):
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        self.assertEqual(response.status, 401)
        mock_rate_limit.assert_not_called()
        mock_record_usage.assert_not_called()

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_require_user_auth_rejects_agent(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that @require_user_auth only accepts user auth, not agent auth."""
        from aiohttp import web

        @require_user_auth
        async def handler(request):
            return web.json_response({"status": "ok"})

        request = MagicMock()
        # Only agent auth headers present
        mock_user_auth.return_value = (False, web.json_response({"error": "Missing authorization token"}, status=401))

        response = await auth_middleware(request, handler)

        # Should only call user auth, not agent auth
        mock_agent_auth.assert_not_called()
        mock_user_auth.assert_called_once()
        self.assertEqual(response.status, 401)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_require_agent_auth_rejects_user(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that @require_agent_auth only accepts agent auth, not user auth."""
        from aiohttp import web

        @require_agent_auth
        async def handler(request):
            return web.json_response({"status": "ok"})

        request = MagicMock()
        mock_agent_auth.return_value = (False, web.json_response({"error": "Missing headers"}, status=401))

        response = await auth_middleware(request, handler)

        # Should only call agent auth, not user auth
        mock_agent_auth.assert_called_once()
        mock_user_auth.assert_not_called()
        self.assertEqual(response.status, 401)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_applies_rate_limiting(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that middleware applies rate limiting for authenticated requests."""
        mock_agent_auth.return_value = (True, {'id': 'agent-1', 'is_active': True, 'agent_name': 'test'})
        mock_rate_limit.return_value = True

        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        mock_rate_limit.assert_called_once()
        self.assertEqual(response.status, 200)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_returns_429_when_rate_limited(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that middleware returns 429 when rate limit is exceeded."""
        mock_agent_auth.return_value = (True, {'id': 'agent-1', 'is_active': True, 'agent_name': 'test'})
        mock_rate_limit.return_value = False  # Rate limited

        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        self.assertEqual(response.status, 429)
        mock_record_usage.assert_not_called()

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_records_usage(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that middleware records usage after successful request."""
        mock_agent_auth.return_value = (True, {'id': 'agent-1', 'is_active': True, 'agent_name': 'test'})
        mock_rate_limit.return_value = True

        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        mock_record_usage.assert_called_once()
        self.assertEqual(response.status, 200)

    @patch('auth._record_usage')
    @patch('auth._check_rate_limit')
    @patch('auth.verify_supabase_user')
    @patch('auth.verify_agent_request')
    async def test_middleware_skips_rate_limit_and_usage_for_no_auth(self, mock_agent_auth, mock_user_auth, mock_rate_limit, mock_record_usage):
        """Test that @no_auth skips rate limiting and usage tracking entirely."""
        @no_auth
        async def handler(request):
            from aiohttp import web
            return web.json_response({"status": "ok"})

        request = MagicMock()

        response = await auth_middleware(request, handler)

        mock_agent_auth.assert_not_called()
        mock_user_auth.assert_not_called()
        mock_rate_limit.assert_not_called()
        mock_record_usage.assert_not_called()
        self.assertEqual(response.status, 200)


class TestCheckRateLimit(unittest.TestCase):
    """Tests for _check_rate_limit helper function."""

    @patch('auth.supabase')
    def test_rate_limit_allows_under_limit(self, mock_supabase):
        """Test that requests under the limit are allowed."""
        mock_result = MagicMock()
        mock_result.count = 10  # Under 60 limit
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_result

        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {'agent': {'id': 'agent-1'}, 'user': None}.get(k, d)

        result = _check_rate_limit(request)
        self.assertTrue(result)

    @patch('auth.supabase')
    def test_rate_limit_blocks_over_limit(self, mock_supabase):
        """Test that requests over the limit are blocked."""
        mock_result = MagicMock()
        mock_result.count = 65  # Over 60 limit
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_result

        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {'agent': {'id': 'agent-1'}, 'user': None}.get(k, d)

        result = _check_rate_limit(request)
        self.assertFalse(result)

    def test_rate_limit_allows_when_no_auth_entity(self):
        """Test that rate limiting allows when no auth entity (shouldn't happen in middleware flow)."""
        request = MagicMock()
        request.get.return_value = None

        result = _check_rate_limit(request)
        self.assertTrue(result)

    @patch('auth.supabase')
    def test_rate_limit_fails_open_on_db_error(self, mock_supabase):
        """Test that rate limiting fails open (allows) when DB is down."""
        mock_supabase.table.side_effect = Exception("DB connection failed")

        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {'agent': {'id': 'agent-1'}, 'user': None}.get(k, d)

        result = _check_rate_limit(request)
        self.assertTrue(result)  # Fail open


class TestRecordUsage(unittest.TestCase):
    """Tests for _record_usage helper function."""

    @patch('auth.supabase')
    def test_record_usage_with_agent(self, mock_supabase):
        """Test that usage is recorded correctly for agent requests."""
        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {
            'agent': {'id': 'agent-1', 'agent_name': 'test-agent'},
            'user': None,
        }.get(k, d)
        request.path = '/api/v1/transcribe/file'
        request.method = 'POST'
        request.headers.get.return_value = 'test-agent/1.0'

        response = MagicMock()
        response.status = 200

        _record_usage(request, response, time.time() - 0.1)

        mock_supabase.table.return_value.insert.assert_called_once()
        call_args = mock_supabase.table.return_value.insert.call_args[0][0]
        self.assertEqual(call_args['agent_id'], 'agent-1')
        self.assertEqual(call_args['endpoint'], '/api/v1/transcribe/file')
        self.assertEqual(call_args['method'], 'POST')
        self.assertTrue(call_args['success'])

    @patch('auth.supabase')
    def test_record_usage_with_user(self, mock_supabase):
        """Test that usage is recorded correctly for user requests."""
        mock_user = FakeUser(id="user-456", email="user@example.com")

        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {
            'agent': None,
            'user': mock_user,
        }.get(k, d)
        request.path = '/api/v1/translate/text'
        request.method = 'POST'
        request.headers.get.return_value = 'Mozilla/5.0'

        response = MagicMock()
        response.status = 200

        _record_usage(request, response, time.time() - 0.05)

        mock_supabase.table.return_value.insert.assert_called_once()
        call_args = mock_supabase.table.return_value.insert.call_args[0][0]
        self.assertEqual(call_args['agent_id'], 'user-456')
        self.assertTrue(call_args['success'])

    @patch('auth.supabase')
    def test_record_usage_handles_db_error(self, mock_supabase):
        """Test that usage recording doesn't raise on DB error."""
        mock_supabase.table.side_effect = Exception("DB error")

        request = MagicMock()
        request.get.side_effect = lambda k, d=None: {
            'agent': {'id': 'agent-1', 'agent_name': 'test'},
            'user': None,
        }.get(k, d)
        request.path = '/api/v1/test'
        request.method = 'GET'
        request.headers.get.return_value = ''

        response = MagicMock()
        response.status = 200

        # Should not raise
        _record_usage(request, response, time.time())

    def test_record_usage_records_failure_status(self):
        """Test that usage recording marks non-2xx as failure."""
        with patch('auth.supabase') as mock_supabase:
            request = MagicMock()
            request.get.side_effect = lambda k, d=None: {
                'agent': {'id': 'agent-1', 'agent_name': 'test'},
                'user': None,
            }.get(k, d)
            request.path = '/api/v1/test'
            request.method = 'POST'
            request.headers.get.return_value = ''

            response = MagicMock()
            response.status = 500

            _record_usage(request, response, time.time())

            call_args = mock_supabase.table.return_value.insert.call_args[0][0]
            self.assertFalse(call_args['success'])


if __name__ == '__main__':
    unittest.main()