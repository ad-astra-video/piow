#!/usr/bin/env python3
"""
Billing Routes Module
Handles user-facing billing endpoints for Stripe subscriptions and usage tracking.
"""

import asyncio
import logging
import os
from typing import Any, Dict

import aiohttp.web as web

from auth import require_user_auth, no_auth
from payments.stripe import get_stripe_service

logger = logging.getLogger(__name__)


def _get_base_url():
    """Build the public base URL from DOMAIN or HOST_IP env vars."""
    domain = os.environ.get("DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    host_ip = os.environ.get("HOST_IP", "").strip()
    if host_ip:
        return f"https://{host_ip}"
    return ""


# Stripe Checkout URLs (configurable via environment)
_base = _get_base_url()
STRIPE_SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL", f"{_base}/api/v1/billing/success" if _base else "/api/v1/billing/success")
STRIPE_CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL", f"{_base}/api/v1/billing/cancel" if _base else "/api/v1/billing/cancel")


def setup_routes(app):
    """Setup billing-related routes."""
    app.router.add_post('/api/v1/billing/create-checkout-session', create_checkout_session)
    app.router.add_get('/api/v1/billing/subscription', get_subscription)
    app.router.add_post('/api/v1/billing/cancel', cancel_subscription)
    app.router.add_post('/api/v1/billing/update', update_subscription)
    app.router.add_get('/api/v1/billing/usage', get_usage)
    app.router.add_post('/api/v1/billing/webhook', stripe_webhook)
    logger.info("Billing routes registered")


@require_user_auth
async def create_checkout_session(request):
    """POST /api/v1/billing/create-checkout-session

    Create a Stripe Checkout Session for the authenticated user.
    Body: { "tier": "starter" | "pro" | "enterprise" }
    """
    user = request.get('user')
    if not user:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        data = await request.json()
        tier = data.get('tier', 'starter')

        if tier not in ('starter', 'pro', 'enterprise'):
            return web.json_response({
                'error': 'Invalid tier. Must be starter, pro, or enterprise',
            }, status=400)

        stripe_service = get_stripe_service()
        if not stripe_service:
            return web.json_response({
                'error': 'Stripe is not configured. Please use x402 payment or contact support.',
            }, status=503)

        # Get price ID for the requested tier
        price_id = stripe_service.get_subscription_price_id(tier)
        if not price_id:
            return web.json_response({
                'error': f'Price not configured for tier: {tier}',
            }, status=400)

        user_id = str(user.id) if hasattr(user, 'id') else ""
        email = getattr(user, 'email', '') or ''
        name = getattr(user, 'user_metadata', {}).get('full_name', '') if hasattr(user, 'user_metadata') else ''

        # Get or create Stripe customer
        customer = await stripe_service.create_stripe_customer(
            user_id=user_id,
            email=email,
            name=name,
        )

        # Create Checkout Session
        from supabase_client import supabase

        checkout_session = await stripe_service._client.v1.checkout.sessions.create_async(  # type: ignore
            params={
                'customer': customer.id,
                'mode': 'subscription',
                'line_items': [{'price': price_id, 'quantity': 1}],
                'success_url': STRIPE_SUCCESS_URL + '?session_id={CHECKOUT_SESSION_ID}',
                'cancel_url': STRIPE_CANCEL_URL,
                'metadata': {
                    'supabase_user_id': user_id,
                    'tier': tier,
                },
            },
        )

        return web.json_response({
            'url': checkout_session.url,
            'session_id': checkout_session.id,
        })

    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_subscription(request):
    """GET /api/v1/billing/subscription

    Get the current subscription status for the authenticated user.
    """
    user = request.get('user')
    if not user:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        from supabase_client import supabase
        user_id = str(user.id) if hasattr(user, 'id') else None

        result = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
                .select('*')
                .eq('user_id', user_id)
                .execute()
        )

        if result.data:
            sub = result.data[0]
            return web.json_response({
                'subscription': sub,
                'tier': sub.get('plan', 'free'),
                'status': sub.get('status', 'none'),
            })
        else:
            return web.json_response({
                'subscription': None,
                'tier': 'free',
                'status': 'none',
            })

    except Exception as e:
        logger.error(f"Error getting subscription: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def cancel_subscription(request):
    """POST /api/v1/billing/cancel

    Cancel the authenticated user's subscription.
    """
    user = request.get('user')
    if not user:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        from supabase_client import supabase
        user_id = str(user.id) if hasattr(user, 'id') else None

        # Get current subscription
        result = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
                .select('stripe_subscription_id')
                .eq('user_id', user_id)
                .execute()
        )

        if not result.data or not result.data[0].get('stripe_subscription_id'):
            return web.json_response({
                'error': 'No active Stripe subscription found',
            }, status=404)

        stripe_subscription_id = result.data[0]['stripe_subscription_id']

        stripe_service = get_stripe_service()
        if not stripe_service:
            return web.json_response({
                'error': 'Stripe is not configured',
            }, status=503)

        # Cancel via Stripe
        await stripe_service.cancel_subscription(
            subscription_id=stripe_subscription_id,
            user_id=user_id,
        )

        return web.json_response({
            'message': 'Subscription cancelled successfully',
            'subscription_id': stripe_subscription_id,
        })

    except Exception as e:
        logger.error(f"Error cancelling subscription: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def update_subscription(request):
    """POST /api/v1/billing/update

    Change the authenticated user's subscription tier.
    Body: { "tier": "starter" | "pro" | "enterprise" }
    """
    user = request.get('user')
    if not user:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        data = await request.json()
        tier = data.get('tier')

        if tier not in ('starter', 'pro', 'enterprise'):
            return web.json_response({
                'error': 'Invalid tier. Must be starter, pro, or enterprise',
            }, status=400)

        from supabase_client import supabase
        user_id = str(user.id) if hasattr(user, 'id') else None

        # Get current subscription
        result = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
                .select('stripe_subscription_id')
                .eq('user_id', user_id)
                .execute()
        )

        if not result.data or not result.data[0].get('stripe_subscription_id'):
            return web.json_response({
                'error': 'No active Stripe subscription found. Please create one first.',
            }, status=404)

        stripe_subscription_id = result.data[0]['stripe_subscription_id']

        stripe_service = get_stripe_service()
        if not stripe_service:
            return web.json_response({
                'error': 'Stripe is not configured',
            }, status=503)

        # Get price ID for new tier
        price_id = stripe_service.get_subscription_price_id(tier)
        if not price_id:
            return web.json_response({
                'error': f'Price not configured for tier: {tier}',
            }, status=400)

        # Update subscription via Stripe
        await stripe_service.update_subscription(
            subscription_id=stripe_subscription_id,
            price_id=price_id,
            user_id=user_id,
        )

        return web.json_response({
            'message': f'Subscription updated to {tier} tier',
            'tier': tier,
        })

    except Exception as e:
        logger.error(f"Error updating subscription: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_usage(request):
    """GET /api/v1/billing/usage

    Get usage statistics vs quota for the authenticated user.
    """
    user = request.get('user')
    if not user:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        from supabase_client import supabase
        from payments.quotas import check_quota, PLAN_LIMITS
        user_id = str(user.id) if hasattr(user, 'id') else None

        # Get user's subscription tier
        sub_result = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
                .select('plan,status')
                .eq('user_id', user_id)
                .execute()
        )

        tier = 'free'
        if sub_result.data and sub_result.data[0].get('status') in ('active', 'trialing'):
            tier = sub_result.data[0].get('plan', 'free')

        # Check quotas — transcription is a combined pool (CPU+GPU)
        transcribe_allowed, transcribe_info = await check_quota(user_id, 'transcribe_cpu', tier)
        translate_allowed, translate_info = await check_quota(user_id, 'translate', tier)

        return web.json_response({
            'tier': tier,
            'plan_limits': PLAN_LIMITS.get(tier, PLAN_LIMITS['free']),
            'usage': {
                'transcription': transcribe_info,
                'translation': translate_info,
            },
        })

    except Exception as e:
        logger.error(f"Error getting usage: {e}")
        return web.json_response({'error': str(e)}, status=500)


@no_auth
async def stripe_webhook(request):
    """POST /api/v1/billing/webhook

    Handle Stripe webhook events.
    NOTE: This endpoint opts out of authentication via @no_auth —
    it verifies Stripe signatures instead.
    """
    stripe_service = get_stripe_service()
    if not stripe_service:
        logger.warning("Stripe webhook received but Stripe is not configured")
        return web.json_response({'error': 'Stripe not configured'}, status=503)

    # Delegate to the StripePaymentService webhook handler
    # which handles signature verification, event deduplication, and routing
    return await stripe_service.handle_stripe_webhook(request)