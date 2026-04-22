#!/usr/bin/env python3
"""
Agent Endpoints Module
Handles all agent-specific API endpoints.
"""

import aiohttp.web as web
import logging
import os
import time
import json
import secrets
import string
from typing import Dict, Any

logger = logging.getLogger(__name__)

from auth import no_auth, require_agent_auth

# These will be imported when needed to avoid circular imports
# from supabase_client import supabase


def setup_routes(app):
    """Setup agent-related routes."""
    # Agent registration and management
    app.router.add_post('/api/v1/agents/register', agent_register)
    app.router.add_get('/api/v1/agents/usage', agent_get_usage)
    app.router.add_get('/api/v1/agents/keys', agent_list_keys)
    app.router.add_post('/api/v1/agents/keys', agent_create_key)
    app.router.add_delete('/api/v1/agents/keys', agent_revoke_key)
    app.router.add_get('/api/v1/agents/subscription', agent_get_subscription)
    app.router.add_post('/api/v1/agents/subscription', agent_create_subscription)
    app.router.add_delete('/api/v1/agents/subscription', agent_delete_subscription)
    app.router.add_post('/api/v1/agents/subscription/reactivate', agent_reactivate_subscription)


@no_auth
async def agent_register(request):
    """Register a new agent."""
    logger.info("Received agent registration request")

    try:
        data = await request.json()

        # Validate required fields
        required_fields = ['agent_name', 'contact_email']
        for field in required_fields:
            if field not in data:
                return web.json_response({
                    "error": f"Missing required field: {field}"
                }, status=400)

        agent_name = data['agent_name']
        contact_email = data['contact_email']

        # Generate API key and secret
        # Generate a secure API key
        alphabet = string.ascii_letters + string.digits
        api_key = ''.join(secrets.choice(alphabet) for _ in range(32))
        api_secret = ''.join(secrets.choice(alphabet) for _ in range(64))

        # Import supabase here to avoid circular imports
        from supabase_client import supabase

        # Insert agent into database
        result = supabase.table('agents').insert({
            'agent_name': agent_name,
            'contact_email': contact_email,
            'api_key': api_key,
            'api_secret': api_secret,
            'subscription_tier': 'free',  # Default tier
            'is_active': True
        }).execute()

        if not result.data:
            raise Exception("Failed to create agent")

        agent = result.data[0]

        return web.json_response({
            "agent_id": agent['id'],
            "agent_name": agent['agent_name'],
            "api_key": agent['api_key'],
            "api_secret": agent['api_secret'],  # Only returned once!
            "subscription_tier": agent['subscription_tier'],
            "message": "Agent registered successfully. Store your API key and secret securely."
        })

    except Exception as e:
        logger.error(f"Error registering agent: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_get_usage(request):
    """Get usage statistics for the authenticated agent."""
    logger.info("Received agent usage request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        # Get usage statistics from the database
        # We'll get today's usage and total usage
        today_start = int(time.time()) - (int(time.time()) % 86400)  # Start of today in UTC

        # Today's usage
        today_result = supabase.table('agent_usage').select(
            'endpoint', 'method', 'success', 'cost_usdc_cents'
        ).eq('agent_id', agent_id).gte('timestamp', today_start).execute()

        # Total usage
        total_result = supabase.table('agent_usage').select(
            'endpoint', 'method', 'success', 'cost_usdc_cents'
        ).eq('agent_id', agent_id).execute()

        # Calculate statistics
        today_data = today_result.data if hasattr(today_result, 'data') else today_result
        total_data = total_result.data if hasattr(total_result, 'data') else total_result

        # Process today's usage
        today_transcriptions = 0
        today_translations = 0
        today_cost = 0
        today_success = 0
        today_total = len(today_data)

        for record in today_data:
            if record.get('success'):
                today_success += 1
            if 'transcribe' in record.get('endpoint', ''):
                today_transcriptions += 1
            elif 'translate' in record.get('endpoint', ''):
                today_translations += 1
            today_cost += record.get('cost_usdc_cents', 0)

        # Process total usage
        total_transcriptions = 0
        total_translations = 0
        total_cost = 0
        total_success = 0
        total_total = len(total_data)

        for record in total_data:
            if record.get('success'):
                total_success += 1
            if 'transcribe' in record.get('endpoint', ''):
                total_transcriptions += 1
            elif 'translate' in record.get('endpoint', ''):
                total_translations += 1
            total_cost += record.get('cost_usdc_cents', 0)

        return web.json_response({
            "agent_id": agent_id,
            "subscription_tier": agent.get('subscription_tier', 'free'),
            "usage": {
                "today": {
                    "transcriptions": today_transcriptions,
                    "translations": today_translations,
                    "total_requests": today_total,
                    "successful_requests": today_success,
                    "failed_requests": today_total - today_success,
                    "total_cost_usdc": today_cost / 100.0,  # Convert cents to dollars
                    "total_cost_usdc_cents": today_cost
                },
                "total": {
                    "transcriptions": total_transcriptions,
                    "translations": total_translations,
                    "total_requests": total_total,
                    "successful_requests": total_success,
                    "failed_requests": total_total - total_success,
                    "total_cost_usdc": total_cost / 100.0,  # Convert cents to dollars
                    "total_cost_usdc_cents": total_cost
                }
            }
        })

    except Exception as e:
        logger.error(f"Error getting agent usage: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_list_keys(request):
    """List API keys for the agent."""
    logger.info("Received agent list keys request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        # For now, we only have the primary key
        # In a full implementation, we would have a separate keys table
        return web.json_response({
            "agent_id": agent_id,
            "keys": [
                {
                    "key_id": "primary",
                    "api_key": agent['api_key'],
                    "created_at": agent.get('created_at'),
                    "last_used_at": agent.get('last_used_at'),
                    "is_active": agent.get('is_active', True)
                }
            ]
        })

    except Exception as e:
        logger.error(f"Error listing agent keys: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_create_key(request):
    """Create a new API key for the agent."""
    logger.info("Received agent create key request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        # Generate new API key
        alphabet = string.ascii_letters + string.digits
        new_api_key = ''.join(secrets.choice(alphabet) for _ in range(32))
        new_api_secret = ''.join(secrets.choice(alphabet) for _ in range(64))

        # Update the agent with new key (in a full implementation, we'd store multiple keys)
        # For simplicity, we'll update the main key
        import datetime
        update_result = supabase.table('agents').update({
            'api_key': new_api_key,
            'api_secret': new_api_secret,
            'last_used_at': datetime.datetime.utcnow().isoformat()
        }).eq('id', agent_id).execute()

        if not update_result.data:
            raise Exception("Failed to update agent key")

        return web.json_response({
            "agent_id": agent_id,
            "api_key": new_api_key,
            "api_secret": new_api_secret,  # Only returned once!
            "message": "New API key created successfully. Store it securely."
        })

    except Exception as e:
        logger.error(f"Error creating agent key: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_revoke_key(request):
    """Revoke/delete an API key for the agent."""
    logger.info("Received agent revoke key request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        data = await request.json()
        key_id = data.get('key_id', 'primary')

        # For security, we don't actually allow revoking the only key
        # In a full implementation with multiple keys, you would delete the specific key
        # For now, we'll deactivate the agent if trying to revoke primary key
        if key_id == 'primary':
            return web.json_response({
                "error": "Cannot revoke primary key. Create a new key first, then revoke the old one.",
                "status": "error"
            }, status=400)

        # Deactivate agent (simplified approach)
        import datetime
        update_result = supabase.table('agents').update({
            "is_active": False,
            "revoked_at": datetime.datetime.utcnow().isoformat()
        }).eq('id', agent_id).execute()

        if not update_result.data:
            raise Exception("Failed to revoke agent key")

        return web.json_response({
            "agent_id": agent_id,
            "message": "Agent deactivated successfully"
        })

    except Exception as e:
        logger.error(f"Error revoking agent key: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


# Subscription management endpoints for agents
@require_agent_auth
async def agent_get_subscription(request):
    """Get current subscription status for the agent."""
    logger.info("Received agent get subscription request")

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')

    try:
        return web.json_response({
            "agent_id": agent['id'],
            "subscription_tier": agent.get('subscription_tier', 'free'),
            "is_active": agent.get('is_active', True),
            "message": "Subscription status retrieved successfully"
        })

    except Exception as e:
        logger.error(f"Error getting agent subscription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_create_subscription(request):
    """Create a subscription for the agent (x402 or Stripe)."""
    logger.info("Received agent create subscription request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        data = await request.json()

        # Validate required fields
        payment_method = data.get('payment_method')  # 'x402' or 'stripe'
        tier = data.get('tier', 'starter')  # free, starter, pro, enterprise

        if not payment_method:
            return web.json_response({
                "error": "Missing required field: payment_method"
            }, status=400)

        if payment_method not in ['x402', 'stripe']:
            return web.json_response({
                "error": "Invalid payment_method. Must be 'x402' or 'stripe'"
            }, status=400)

        if tier not in ['free', 'starter', 'pro', 'enterprise']:
            return web.json_response({
                "error": "Invalid tier. Must be 'free', 'starter', 'pro', or 'enterprise'"
            }, status=400)

        # If free tier, just update the subscription tier
        if tier == 'free':
            update_result = supabase.table('agents').update({
                'subscription_tier': 'free'
            }).eq('id', agent_id).execute()

            if not update_result.data:
                raise Exception("Failed to update subscription tier")

            return web.json_response({
                "agent_id": agent_id,
                "subscription_tier": "free",
                "message": "Subscription set to free tier successfully"
            })

        # For paid tiers, we need to process payment
        if payment_method == 'x402':
            # Return 402 with payment requirements for x402
            # Use proper pricing from x402 module
            from payments.x402 import build_x402_payment_required, PRICING

            subscription_service_type = f"subscription_{tier}"

            # Check if we have pricing for this subscription tier
            if subscription_service_type not in PRICING:
                return web.json_response({
                    "error": f"x402 payment not available for tier: {tier}",
                    "status": "error"
                }, status=400)

            # Build x402 payment requirement (Base64-encoded per X402 v2 spec)
            payment_required = build_x402_payment_required(
                service_type=subscription_service_type,
                resource_url=str(request.url)
            )

            return web.json_response({
                "error": "Payment required",
                "payment_required": True,
                "payment_method": "x402",
                "subscription_tier": tier,
                "message": "Please provide x402 payment for subscription"
            }, status=402, headers={
                'PAYMENT-REQUIRED': payment_required
            })

        elif payment_method == 'stripe':
            # Process Stripe payment via checkout session
            try:
                # Import here to avoid circular imports
                from payments.stripe import get_stripe_service

                # Get the Stripe service
                stripe_service = get_stripe_service()

                if not stripe_service:
                    return web.json_response({
                        "error": "Stripe is not configured. Please use x402 or contact support.",
                        "status": "error",
                        "payment_method": "stripe",
                        "tier": tier
                    }, status=503)

                # Get the price ID for the requested tier
                price_id = stripe_service.get_subscription_price_id(tier)

                if not price_id:
                    return web.json_response({
                        "error": f"Invalid subscription tier: {tier}. Price not configured.",
                        "status": "error"
                    }, status=400)

                # Create a Stripe customer for the agent
                agent_id = agent['id']
                contact_email = agent.get('contact_email', '')
                agent_name = agent.get('agent_name', '')

                customer = await stripe_service.create_stripe_customer(
                    user_id=agent_id,
                    email=contact_email,
                    name=agent_name,
                )

                # Create Checkout Session
                import os
                success_url = os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:5173/billing/success")
                cancel_url = os.environ.get("STRIPE_CANCEL_URL", "http://localhost:5173/billing/cancel")

                checkout_session = await stripe_service._client.v1.checkout.sessions.create_async(  # type: ignore
                    params={
                        'customer': customer.id,
                        'mode': 'subscription',
                        'line_items': [{'price': price_id, 'quantity': 1}],
                        'success_url': success_url + '?session_id={CHECKOUT_SESSION_ID}',
                        'cancel_url': cancel_url,
                        'metadata': {
                            'supabase_user_id': agent_id,
                            'agent_subscription': 'true',
                            'tier': tier,
                        },
                    },
                )

                return web.json_response({
                    "url": checkout_session.url,
                    "session_id": checkout_session.id,
                    "payment_method": "stripe",
                    "tier": tier,
                    "message": "Redirect to Stripe Checkout to complete subscription"
                })

            except Exception as e:
                logger.error(f"Error creating Stripe checkout session: {e}")
                return web.json_response({
                    "error": str(e),
                    "status": "error"
                }, status=500)

    except Exception as e:
        logger.error(f"Error creating agent subscription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_delete_subscription(request):
    """Delete/cancel subscription for the agent."""
    logger.info("Received agent delete subscription request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        # Update subscription tier to free (cancel subscription)
        update_result = supabase.table('agents').update({
            'subscription_tier': 'free'
        }).eq('id', agent_id).execute()

        if not update_result.data:
            raise Exception("Failed to update subscription tier")

        return web.json_response({
            "agent_id": agent_id,
            "subscription_tier": "free",
            "message": "Subscription cancelled successfully. Tier changed to free."
        })

    except Exception as e:
        logger.error(f"Error deleting agent subscription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@require_agent_auth
async def agent_reactivate_subscription(request):
    """Reactivate a cancelled subscription for the agent."""
    logger.info("Received agent reactivate subscription request")

    # Import here to avoid circular imports
    from supabase_client import supabase

    # Agent is already verified and set in request by the decorator
    agent = request.get('agent')
    agent_id = agent['id']

    try:
        data = await request.json()
        tier = data.get('tier', 'starter')

        if tier not in ['starter', 'pro', 'enterprise']:
            return web.json_response({
                "error": "Invalid tier for reactivation. Must be 'starter', 'pro', or 'enterprise'"
            }, status=400)

        # For reactivation, we'll treat it as a new subscription creation
        # In a full implementation, we'd check if there was a previous subscription
        # and reactivate it with the same payment method

        # Update to the requested tier (payment would be processed separately)
        update_result = supabase.table('agents').update({
            'subscription_tier': tier
        }).eq('id', agent_id).execute()

        if not update_result.data:
            raise Exception("Failed to update subscription tier")

        return web.json_response({
            "agent_id": agent_id,
            "subscription_tier": tier,
            "message": f"Subscription reactivated to {tier} tier. Please complete payment to activate."
        })

    except Exception as e:
        logger.error(f"Error reactivating agent subscription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)