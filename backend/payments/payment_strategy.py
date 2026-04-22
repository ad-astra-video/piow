#!/usr/bin/env python3
"""
Unified Payment Strategy
Handles both x402 v2 crypto payments and Stripe subscription payments.
Allows endpoints to accept either payment method.

Protocol Compliance (X402 v2):
- PAYMENT-REQUIRED header: Base64-encoded JSON
- PAYMENT-SIGNATURE header: Base64-encoded JSON (decoded via x402.decode_payment_signature)
- PAYMENT-RESPONSE header: Base64-encoded JSON (built via x402.build_payment_response_header)
- Payment amount validation against expected service price
- Payment deadline validation
- Facilitator downtime fallback to subscription check
- x402 payment lifecycle tracking (pending → verified → settled/failed)
"""

import asyncio
import functools
import json
import logging
from typing import Dict, Any, Optional, Callable

from aiohttp import web

# Import payment modules
from .x402 import (
    x402_required as x402_decorator,
    build_x402_payment_required,
    decode_payment_signature,
    verify_x402_payment,
    settle_x402_payment,
    validate_payment_amount,
    validate_payment_deadline,
    build_payment_response_header,
)
from .stripe import subscription_required as stripe_decorator

logger = logging.getLogger(__name__)


def payment_required(service_type: str = 'transcribe_cpu',
                     require_subscription: bool = False,
                     subscription_tier: str = 'starter'):
    """
    Unified payment decorator that accepts either x402 payment OR active subscription.

    FAILS CLOSED: If both x402 and subscription checks fail, access is denied.
    FACILITATOR FALLBACK: If x402 facilitator is unreachable, falls back to subscription check.

    Args:
        service_type: Type of service for x402 pricing ('transcribe_cpu', 'transcribe_gpu', 'translate')
        require_subscription: If True, requires subscription (doesn't accept x402 alone)
        subscription_tier: Minimum subscription tier required when subscription is required

    Returns:
        Decorator function
    """
    def decorator(handler: Callable) -> Callable:
        # If handler is marked @no_auth, skip payment validation entirely
        if getattr(handler, '_no_auth', False):
            return handler

        @functools.wraps(handler)
        async def wrapper(request):
            # If subscription is required, use subscription_required decorator
            if require_subscription:
                return await stripe_decorator(subscription_tier)(handler)(request)

            # Check for PAYMENT-SIGNATURE header (x402 payment attempt)
            payment_signature_header = request.headers.get('PAYMENT-SIGNATURE')

            if payment_signature_header:
                # Decode payment signature (Base64 per X402 v2 spec, with fallback)
                try:
                    payment_data = decode_payment_signature(payment_signature_header)
                except ValueError as e:
                    return web.json_response(
                        {'error': f'Invalid payment signature format: {e}'},
                        status=400,
                    )

                # Validate payment amount matches expected price
                if not validate_payment_amount(payment_data, service_type):
                    logger.warning(f"Payment amount mismatch for {service_type}")
                    return web.json_response(
                        {'error': 'Payment amount does not match expected price'},
                        status=402,
                    )

                # Validate payment deadline
                if not validate_payment_deadline(payment_data):
                    logger.warning("Payment deadline expired")
                    return web.json_response(
                        {'error': 'Payment deadline expired'},
                        status=402,
                    )

                # Verify x402 payment with facilitator
                try:
                    verification_result = await verify_x402_payment(payment_signature_header)
                except Exception as e:
                    # FACILITATOR FALLBACK: If facilitator is unreachable,
                    # log and fall back to subscription check
                    logger.error(f"x402 facilitator unreachable, falling back to subscription: {e}")
                    return await _check_subscription_or_deny(
                        request, handler, subscription_tier, service_type
                    )

                if verification_result.get('valid'):
                    # Record payment as verified (lifecycle tracking)
                    await _record_x402_payment(request, payment_data, service_type, 'verified', verification_result)

                    # Payment valid, proceed with handler
                    try:
                        response = await handler(request)

                        # After successful service, settle payment
                        if response.status == 200:
                            try:
                                settlement = await settle_x402_payment(payment_signature_header)
                                # Update payment status to settled
                                await _update_x402_payment_status(payment_data, 'settled', settlement)
                                # Add settlement proof to response headers (Base64-encoded per X402 v2 spec)
                                response.headers['PAYMENT-RESPONSE'] = build_payment_response_header(settlement)
                            except Exception as e:
                                logger.error(f"Payment settlement failed: {e}")
                                # Update payment status to failed
                                await _update_x402_payment_status(payment_data, 'failed', {'error': str(e)})
                                # Don't fail the request if settlement fails, just log it

                        return response
                    except Exception as e:
                        logger.error(f"Handler failed: {e}")
                        raise
                else:
                    # Invalid x402 payment, fall back to subscription check
                    logger.info("Invalid x402 payment, checking subscription...")
                    return await _check_subscription_or_deny(
                        request, handler, subscription_tier, service_type
                    )

            # No x402 payment header — check for active subscription
            return await _check_subscription_or_deny(
                request, handler, subscription_tier, service_type
            )

        return wrapper
    return decorator


async def _check_subscription_or_deny(request, handler, subscription_tier, service_type):
    """Check subscription status. If no subscription, return 402 with x402 payment option."""
    from .stripe import subscription_required

    try:
        return await subscription_required(subscription_tier)(handler)(request)
    except web.HTTPException as e:
        # Subscription check returned an error (402, 403, etc.)
        if e.status in (402, 403):
            # Return 402 with x402 payment requirements as fallback option
            try:
                payment_required = build_x402_payment_required(
                    service_type=service_type,
                    resource_url=str(request.url)
                )
                return web.json_response(
                    {'error': 'Payment required', 'subscription_required': True},
                    status=402,
                    headers={'PAYMENT-REQUIRED': payment_required}
                )
            except Exception as e2:
                logger.error(f"Error building payment required response: {e2}")
                raise e  # Re-raise original subscription error
        raise
    except Exception as e:
        logger.error(f"Subscription check failed (fail-closed): {e}")
        return web.json_response(
            {'error': 'Unable to verify payment status'},
            status=503,
        )


async def _record_x402_payment(request, payment_data, service_type, status, result_data):
    """Record x402 payment in database with lifecycle tracking.

    Lifecycle: pending → verified → settled/failed
    This function is called at the 'verified' stage.
    """
    try:
        from supabase_client import supabase

        user = request.get('user')
        agent = request.get('agent')
        accepted = payment_data.get('accepted', {})
        authorization = payment_data.get('payload', {}).get('authorization', {})

        record = {
            'agent_wallet': authorization.get('from', 'unknown'),
            'user_id': str(user.id) if user and hasattr(user, 'id') else None,
            'agent_id': str(agent.get('id')) if agent else None,
            'resource_url': payment_data.get('resource', {}).get('url', str(request.url)),
            'amount': float(accepted.get('amount', 0)) / 1_000_000,  # Convert from 6-decimal USDC
            'asset': accepted.get('asset', 'unknown'),
            'network': accepted.get('network', 'unknown'),
            'scheme': accepted.get('scheme', 'exact'),
            'service_type': service_type,
            'status': status,
            'payment_payload': payment_data,
        }

        if status == 'verified':
            record['verification_result'] = result_data
            record['verified_at'] = 'now()'
        elif status == 'settled':
            record['settlement_result'] = result_data
            record['settled_at'] = 'now()'

        await asyncio.to_thread(
            lambda: supabase.table('x402_payments').insert(record).execute()
        )
    except Exception as e:
        logger.error(f"Failed to record x402 payment: {e}")


async def _update_x402_payment_status(payment_data, status, result_data):
    """Update x402 payment status in database.

    Called after settlement succeeds (→ settled) or fails (→ failed).
    """
    try:
        from supabase_client import supabase

        authorization = payment_data.get('payload', {}).get('authorization', {})
        wallet = authorization.get('from', 'unknown')
        resource_url = payment_data.get('resource', {}).get('url', '')

        update_data = {'status': status}
        if status == 'settled':
            update_data['settlement_result'] = result_data
            update_data['settled_at'] = 'now()'
            update_data['transaction_hash'] = result_data.get('transaction_hash')
        elif status == 'failed':
            update_data['settlement_result'] = result_data

        # Find the most recent pending/verified payment for this wallet+resource
        await asyncio.to_thread(
            lambda: supabase.table('x402_payments')
                .update(update_data)
                .eq('agent_wallet', wallet)
                .eq('resource_url', resource_url)
                .in_('status', ['pending', 'verified'])
                .order('created_at', desc=True)
                .limit(1)
                .execute()
        )
    except Exception as e:
        logger.error(f"Failed to update x402 payment status: {e}")


# Convenience decorators for common use cases
def x402_or_subscription(service_type: str = 'transcribe_cpu',
                         subscription_tier: str = 'starter'):
    """
    Decorator that accepts either x402 payment OR active subscription.
    """
    return payment_required(service_type=service_type,
                            require_subscription=False,
                            subscription_tier=subscription_tier)


def subscription_only(subscription_tier: str = 'starter'):
    """
    Decorator that requires active subscription only (doesn't accept x402).
    """
    return payment_required(require_subscription=True,
                            subscription_tier=subscription_tier)


def x402_only(service_type: str = 'transcribe_cpu'):
    """
    Decorator that requires x402 payment only (doesn't accept subscription).
    """
    return x402_decorator(service_type)