#!/usr/bin/env python3
"""
Authentication Module
Handles both agent authentication (HMAC-SHA256) and Supabase user authentication.
Supports SIWE (Sign-In with Ethereum) via Supabase Web3 auth — the frontend
authenticates directly with Supabase, and the backend validates the resulting JWT.

Architecture:
  - Authentication is enforced by DEFAULT via `auth_middleware` (aiohttp middleware).
  - All endpoints require authentication unless explicitly opted out with `@no_auth`.
  - `@require_user_auth` and `@require_agent_auth` are MARKER decorators that tell
    the middleware which auth type to enforce (they do NOT wrap the handler).
  - `@no_auth` is a MARKER decorator that opts out of auth, rate limiting,
    usage tracking, and payment validation entirely.
  - Rate limiting and usage tracking are handled by the middleware for authenticated routes.
  - Payment decorators (`@x402_or_subscription`, etc.) check `_no_auth` and skip if set.
"""

import time
import hmac
import hashlib
import json
import logging
from typing import Optional, Dict, Any, Tuple
from aiohttp import web

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limit configuration
# ---------------------------------------------------------------------------
RATE_LIMIT_PER_MINUTE = 60


# ---------------------------------------------------------------------------
# Marker decorators
# ---------------------------------------------------------------------------

def no_auth(handler):
    """Marker decorator: opts a route out of authentication, rate limiting,
    usage tracking, and payment validation.

    Must be the innermost decorator (closest to the function definition) so
    that outer decorators (e.g. @x402_or_subscription) can see the _no_auth
    attribute via functools.wraps propagation.

    Usage::

        @no_auth
        async def health_check(request):
            ...
    """
    handler._no_auth = True
    return handler


def require_user_auth(handler):
    """Marker decorator: require Supabase user (JWT) authentication for this endpoint.

    The middleware reads ``handler._auth_type = 'user'`` and only accepts
    Supabase JWT tokens — agent HMAC auth will be rejected.

    Usage::

        @require_user_auth
        async def get_subscription(request):
            ...
    """
    handler._auth_type = 'user'
    return handler


def require_agent_auth(handler):
    """Marker decorator: require agent HMAC-SHA256 authentication for this endpoint.

    The middleware reads ``handler._auth_type = 'agent'`` and only accepts
    agent API-key + signature auth — Supabase JWT tokens will be rejected.

    Usage::

        @require_agent_auth
        async def agent_get_usage(request):
            ...
    """
    handler._auth_type = 'agent'
    return handler


# ---------------------------------------------------------------------------
# Verification functions (used by middleware and directly where needed)
# ---------------------------------------------------------------------------

def verify_agent_request(request) -> Tuple[bool, Any]:
    """
    Verify agent request using HMAC-SHA256 signature.
    
    Expected headers:
    - X-API-Key: The agent's API key
    - X-Timestamp: Current timestamp in seconds
    - X-Nonce: Random nonce to prevent replay attacks
    - X-Signature: HMAC-SHA256 signature of (method + path + timestamp + nonce + body)
    
    Returns:
    - (True, agent_data) if verification successful
    - (False, error_response) if verification failed
    """
    try:
        api_key = request.headers.get('X-API-Key')
        timestamp = request.headers.get('X-Timestamp')
        nonce = request.headers.get('X-Nonce')
        signature = request.headers.get('X-Signature')
        
        if not all([api_key, timestamp, nonce, signature]):
            return False, web.json_response({
                "error": "Missing required headers for agent authentication",
                "missing": [
                    h for h, v in [('X-API-Key', api_key), ('X-Timestamp', timestamp), 
                                 ('X-Nonce', nonce), ('X-Signature', signature)] if not v
                ]
            }, status=401)
        
        # Check timestamp to prevent replay attacks (allow 5 minutes clock skew)
        try:
            ts = int(timestamp)
            now = int(time.time())
            if abs(now - ts) > 300:  # 5 minutes
                return False, web.json_response({
                    "error": "Request timestamp expired or invalid",
                }, status=401)
        except ValueError:
            return False, web.json_response({
                "error": "Invalid timestamp format",
            }, status=401)
        
        # Look up agent by API key
        from supabase_client import supabase
        result = supabase.table('agents').select('*').eq('api_key', api_key).execute()
        
        if not result.data:
            return False, web.json_response({
                "error": "Invalid API key",
            }, status=401)
        
        agent = result.data[0]
        if not agent.get('is_active', False):
            return False, web.json_response({
                "error": "Agent account is deactivated",
            }, status=401)
        
        # Verify signature
        # Get request body
        body = ""
        if hasattr(request, '_body'):
            body = request._body.decode('utf-8') if request._body else ""
        elif hasattr(request, 'text'):
            # For aiohttp, we might need to read the body differently
            # This is a simplified approach - in practice, we'd need to read and then reset the body
            pass
        
        # Create the signature string: method + path + timestamp + nonce + body
        method = request.method
        path = request.path
        sig_string = f"{method}{path}{timestamp}{nonce}{body}"
        
        # Calculate expected signature
        expected_signature = hmac.new(
            api_key.encode('utf-8'),
            sig_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures (constant-time comparison)
        if not hmac.compare_digest(expected_signature, signature):
            return False, web.json_response({
                "error": "Invalid signature",
            }, status=401)
        
        # Store agent in request for later use
        request['agent'] = agent
        return True, agent
        
    except Exception as e:
        logger.error(f"Error in agent request verification: {e}")
        return False, web.json_response({
            "error": "Internal server error",
        }, status=500)


def verify_supabase_user(request) -> Tuple[bool, Any]:
    """
    Verify user request using Supabase JWT token.
    
    Expected headers:
    - Authorization: Bearer <jwt_token>
    - OR via Supabase session cookie
    
    Returns:
    - (True, user_data) if verification successful
    - (False, error_response) if verification failed
    """
    try:
        # Try to get token from Authorization header
        auth_header = request.headers.get('Authorization')
        token = None
        
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header[7:]  # Remove 'Bearer ' prefix
        
        # If no token in header, try to get from cookies (Supabase session)
        if not token:
            cookies = request.cookies
            # Supabase stores session in 'sb:token' or similar
            # This is simplified - actual implementation would depend on Supabase SDK
            token = cookies.get('sb:token') or cookies.get('supabase-auth-token')
        
        if not token:
            return False, web.json_response({
                "error": "Missing authorization token",
            }, status=401)
        
        # Verify the token with Supabase
        from supabase_client import supabase
        try:
            user_response = supabase.auth.get_user(token)
            if user_response.user:
                # Store user in request for later use
                request['user'] = user_response.user
                return True, user_response.user
            else:
                return False, web.json_response({
                    "error": "Invalid or expired token",
                }, status=401)
        except Exception as e:
            logger.error(f"Supabase auth verification failed: {e}")
            return False, web.json_response({
                "error": "Invalid or expired token",
            }, status=401)
            
    except Exception as e:
        logger.error(f"Error in Supabase user verification: {e}")
        return False, web.json_response({
            "error": "Internal server error",
        }, status=500)


# ---------------------------------------------------------------------------
# Helper functions (extracted from old decorators, used by middleware)
# ---------------------------------------------------------------------------

def _check_rate_limit(request) -> bool:
    """Check rate limit for the authenticated entity.

    Returns True if the request is allowed, False if rate-limited.
    Must be called AFTER authentication (so request['user'] or request['agent'] is set).
    """
    agent = request.get('agent')
    user = request.get('user')

    if agent:
        identifier = agent['id']
        auth_type = "agent"
    elif user:
        identifier = str(user.id) if hasattr(user, 'id') else None
        auth_type = "user"
    else:
        # No authenticated entity — should not happen when called from middleware,
        # but if it does, allow the request (middleware already enforced auth).
        return True

    if not identifier:
        return True

    try:
        one_minute_ago = time.time() - 60
        from supabase_client import supabase
        result = supabase.table('agent_usage').select('id', count='exact').eq('agent_id', identifier).gte('timestamp', one_minute_ago).execute()
        if result.count >= RATE_LIMIT_PER_MINUTE:
            return False
    except Exception as e:
        logger.error(f"Rate limit check failed (allowing request): {e}")
        # Fail open — if the DB is down, don't block requests

    return True


def _record_usage(request, response, start_time: float) -> None:
    """Record usage after request completion.

    Must be called AFTER authentication (so request['user'] or request['agent'] is set).
    Errors are logged but never raise — usage tracking must not break the request.
    """
    try:
        status_code = response.status
        success = 200 <= status_code < 300

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Determine who made the request (agent or user)
        agent = request.get('agent')
        user = request.get('user')

        if agent:
            entity_id = agent['id']
            entity_type = "agent"
            entity_name = agent.get('agent_name', 'unknown')
        elif user:
            entity_id = str(user.id) if hasattr(user, 'id') else 'unknown'
            entity_type = "user"
            entity_name = getattr(user, 'email', 'unknown') or 'unknown'
        else:
            # Should not happen when called from middleware, but handle gracefully
            entity_id = "unknown"
            entity_type = "unknown"
            entity_name = "unknown"

        # Determine endpoint and method
        endpoint = request.path
        method = request.method

        # Determine cost (simplified - in reality, this would come from the service used)
        cost_usdc_cents = 0  # Placeholder

        # Prepare metadata
        metadata = {
            "processing_time_ms": processing_time_ms,
            "user_agent": request.headers.get('User-Agent', ''),
            "referer": request.headers.get('Referer', ''),
            "auth_type": entity_type
        }

        # Insert usage record
        from supabase_client import supabase
        supabase.table('agent_usage').insert({
            "agent_id": entity_id,  # Reusing this column for both agents and users
            "endpoint": endpoint,
            "method": method,
            "success": success,
            "cost_usdc_cents": cost_usdc_cents,
            "metadata": metadata
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log usage: {e}")


# ---------------------------------------------------------------------------
# Auth middleware (enforces auth by default on ALL routes)
# ---------------------------------------------------------------------------

@web.middleware
async def auth_middleware(request, handler):
    """aiohttp middleware that enforces authentication by default.

    Flow:
      1. If handler has ``_no_auth = True``, skip all auth/rate-limit/usage logic.
      2. Determine auth type from ``handler._auth_type`` ('user', 'agent', or 'any').
      3. Perform authentication (verify JWT or HMAC).
      4. Check rate limit.
      5. Execute handler (payment decorators run here if present).
      6. Record usage in ``finally`` block.
    """
    # Step 1: @no_auth — skip everything
    if getattr(handler, '_no_auth', False):
        return await handler(request)

    # Step 2: Determine auth type from marker decorator
    auth_type = getattr(handler, '_auth_type', 'any')

    # Step 3: Perform authentication
    if auth_type == 'user':
        verified, result = verify_supabase_user(request)
    elif auth_type == 'agent':
        verified, result = verify_agent_request(request)
    else:
        # Default 'any' — try agent auth first, then user auth
        verified, result = verify_agent_request(request)
        if not verified:
            verified, result = verify_supabase_user(request)

    if not verified:
        return result  # 401 error response

    # Step 4: Rate limiting
    if not _check_rate_limit(request):
        return web.json_response({
            "error": f"Rate limit exceeded: {RATE_LIMIT_PER_MINUTE} requests per minute",
        }, status=429)

    # Step 5: Execute handler (payment decorators run inside if present)
    start_time = time.time()
    try:
        response = await handler(request)
    except Exception:
        raise

    # Step 6: Usage tracking
    _record_usage(request, response, start_time)

    return response


# ---------------------------------------------------------------------------
# Route setup
# ---------------------------------------------------------------------------

def setup_routes(app):
    """Register auth-related API routes."""
    app.router.add_get('/api/v1/auth/me', auth_me_handler)
    logger.info("Auth routes registered")


@require_user_auth
async def auth_me_handler(request):
    """
    GET /api/v1/auth/me

    Returns the current authenticated user's info.
    Requires a valid Supabase JWT in the Authorization header.

    This endpoint works with any Supabase auth method including
    SIWE (Sign-In with Ethereum), email/password, Google, and Twitter —
    the JWT is validated the same way regardless of how the user authenticated.

    Authentication is enforced by auth_middleware based on the @require_user_auth
    marker. The middleware sets request['user'] before this handler runs.
    """
    user = request.get('user')
    if not user:
        # Safety net — should never happen if middleware is correctly configured
        return web.json_response({"error": "Authentication required"}, status=401)

    identities = getattr(user, 'identities', []) or []
    user_metadata = getattr(user, 'user_metadata', {}) or {}

    # Extract identity data from all linked providers
    wallet_address = None
    avatar_url = None
    full_name = None
    providers = []

    for identity in identities:
        provider = identity.get('provider', '')
        identity_data = identity.get('identity_data', {}) or {}
        providers.append(provider)

        # Wallet address (web3/SIWE users)
        if provider == 'web3' and wallet_address is None:
            wallet_address = identity_data.get('wallet_address')

        # Avatar URL (social providers)
        if avatar_url is None:
            avatar_url = (
                identity_data.get('avatar_url')
                or identity_data.get('picture')
            )

        # Full name (social providers)
        if full_name is None:
            full_name = (
                identity_data.get('full_name')
                or identity_data.get('name')
            )

    # Fall back to user_metadata if identities didn't provide the data
    if avatar_url is None:
        avatar_url = user_metadata.get('avatar_url') or user_metadata.get('picture')
    if full_name is None:
        full_name = user_metadata.get('full_name') or user_metadata.get('name')

    return web.json_response({
        "id": str(user.id) if hasattr(user, 'id') else None,
        "email": getattr(user, 'email', None),
        "full_name": full_name,
        "avatar_url": avatar_url,
        "wallet_address": wallet_address,
        "providers": providers,
        "app_metadata": getattr(user, 'app_metadata', {}),
        "user_metadata": user_metadata,
        "created_at": str(getattr(user, 'created_at', '')),
    })