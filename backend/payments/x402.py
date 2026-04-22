#!/usr/bin/env python3
"""
x402 v2 Payment Implementation
Handles creation, verification, and settlement of x402 payments for the platform.

Protocol Compliance (X402 v2):
- PAYMENT-REQUIRED header: Base64-encoded JSON
- PAYMENT-SIGNATURE header: Base64-encoded JSON
- PAYMENT-RESPONSE header: Base64-encoded JSON
- Payment amount validation against expected service price
- Payment deadline validation (maxTimeoutSeconds)
- All 6 networks from X402 spec supported
- CDP facilitator authentication supported
"""

import asyncio
import base64
import json
import logging
import os
import time
from typing import Dict, Any, Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

# Configuration - these should come from environment variables
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")
CDP_API_KEY_ID = os.environ.get("CDP_API_KEY_ID", "")
CDP_API_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET", "")
PLATFORM_WALLET = os.environ.get("PLATFORM_WALLET", "0xYourPlatformWallet")  # Replace with actual wallet
SOLANA_WALLET = os.environ.get("SOLANA_WALLET", "")  # Solana wallet address for Solana payments

# Supported networks for x402 payments (aligned with X402_PAYMENTS.md spec)
SUPPORTED_NETWORKS = [
    {
        'chain_id': 'eip155:84532',  # Base Sepolia (testnet)
        'asset': '0x036CbD53842c5426634e7929541eC2318f3dCF7e',  # USDC on Base Sepolia
        'symbol': 'USDC',
    },
    {
        'chain_id': 'eip155:8453',  # Base Mainnet
        'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',  # USDC on Base
        'symbol': 'USDC',
    },
    {
        'chain_id': 'eip155:137',  # Polygon
        'asset': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',  # USDC on Polygon
        'symbol': 'USDC',
    },
    {
        'chain_id': 'eip155:1',  # Ethereum Mainnet
        'asset': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC on Ethereum
        'symbol': 'USDC',
    },
    {
        'chain_id': 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp',  # Solana Mainnet
        'asset': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC on Solana
        'symbol': 'USDC',
    },
    {
        'chain_id': 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1',  # Solana Devnet
        'asset': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC on Solana Devnet
        'symbol': 'USDC',
    },
]

# Pricing configuration (in USDC cents)
PRICING = {
    'transcribe_cpu': {'usd': 0.01, 'usdc_cents': 1},
    'transcribe_gpu': {'usd': 0.05, 'usdc_cents': 5},
    'translate': {'usd': 0.001, 'usdc_cents': 0.1},
    # Subscription tier pricing for x402 (monthly, in USDC cents)
    'subscription_starter': {'usd': 15.00, 'usdc_cents': 1500},
    'subscription_pro': {'usd': 39.00, 'usdc_cents': 3900},
    'subscription_enterprise': {'usd': 99.00, 'usdc_cents': 9900},
}


def _get_facilitator_headers() -> Dict[str, str]:
    """Get headers for facilitator requests, including CDP auth if configured."""
    headers = {"Content-Type": "application/json"}
    if CDP_API_KEY_ID and CDP_API_KEY_SECRET:
        headers["CDP-API-KEY-ID"] = CDP_API_KEY_ID
        headers["CDP-API-KEY-SECRET"] = CDP_API_KEY_SECRET
    return headers


def _get_pay_to_address(network: str) -> str:
    """Get the pay-to wallet address for a given network.

    Solana networks use SOLANA_WALLET if configured, otherwise PLATFORM_WALLET.
    EVM networks always use PLATFORM_WALLET.
    """
    if network.startswith('solana:') and SOLANA_WALLET:
        return SOLANA_WALLET
    return PLATFORM_WALLET


def build_x402_payment_required(service_type: str, resource_url: str) -> str:
    """Build x402 payment required header value (Base64-encoded JSON per X402 v2 spec).

    Args:
        service_type: Type of service ('transcribe_cpu', 'transcribe_gpu', 'translate',
                      'subscription_starter', 'subscription_pro', 'subscription_enterprise')
        resource_url: The URL that requires payment

    Returns:
        Base64-encoded JSON string for PAYMENT-REQUIRED header

    Raises:
        ValueError: If service_type is unknown
    """
    if service_type not in PRICING:
        raise ValueError(f"Unknown service type: {service_type}")

    payment_required = {
        'x402Version': 2,
        'accepts': [],
        'resource': {
            'url': resource_url,
            'description': f'{service_type} service',
            'mimeType': 'application/json',
        }
    }

    # Add payment options for each supported network
    for net in SUPPORTED_NETWORKS:
        pay_to = _get_pay_to_address(net['chain_id'])
        payment_required['accepts'].append({
            'scheme': 'exact',
            'network': net['chain_id'],
            'amount': str(int(PRICING[service_type]['usdc_cents'] * 10000)),  # 6 decimals for USDC
            'asset': net['asset'],
            'payTo': pay_to,
            'maxTimeoutSeconds': 300,
            'resource': str(resource_url),
            'description': f"{service_type} service - ${PRICING[service_type]['usd']} USDC",
            'mimeType': 'application/json',
        })

    # X402 v2 spec: Base64-encode the JSON payload
    return base64.b64encode(json.dumps(payment_required).encode()).decode()


def build_x402_payment_required_json(service_type: str, resource_url: str) -> Dict[str, Any]:
    """Build x402 payment required as a plain JSON dict (for backward compatibility and logging).

    Args:
        service_type: Type of service
        resource_url: The URL that requires payment

    Returns:
        Payment required object as a Python dict
    """
    header_value = build_x402_payment_required(service_type, resource_url)
    return json.loads(base64.b64decode(header_value).decode())


def decode_payment_signature(payment_signature: str) -> Dict[str, Any]:
    """Decode Base64-encoded payment signature per X402 v2 spec.

    Falls back to plain JSON for backward compatibility with clients
    that don't use Base64 encoding.

    Args:
        payment_signature: The payment signature from PAYMENT-SIGNATURE header

    Returns:
        Decoded payment signature as a Python dict

    Raises:
        ValueError: If the signature cannot be decoded
    """
    try:
        # Try Base64 decoding first (X402 v2 spec)
        return json.loads(base64.b64decode(payment_signature).decode())
    except Exception:
        try:
            # Fallback: try plain JSON for backward compatibility
            return json.loads(payment_signature)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid payment signature format: {e}")


def build_payment_response_header(settlement: Dict[str, Any]) -> str:
    """Build x402 payment response header (Base64-encoded JSON per X402 v2 spec).

    The PAYMENT-RESPONSE header format follows the X402 v2 specification:
    {
        "x402Version": 2,
        "transaction": "<tx_hash>",
        "network": "<chain_id>",
        "payer": "<payer_address>",
        "payee": "<payee_address>",
        "amount": "<amount>",
        "asset": "<asset_address>",
        "success": true/false
    }

    Args:
        settlement: Settlement result from the facilitator

    Returns:
        Base64-encoded JSON string for PAYMENT-RESPONSE header
    """
    response = {
        'x402Version': 2,
        'transaction': settlement.get('transaction_hash', settlement.get('transaction', '')),
        'network': settlement.get('network', ''),
        'payer': settlement.get('payer', ''),
        'payee': settlement.get('payee', PLATFORM_WALLET),
        'amount': settlement.get('amount', ''),
        'asset': settlement.get('asset', ''),
        'success': settlement.get('success', True),
    }
    return base64.b64encode(json.dumps(response).encode()).decode()


def validate_payment_amount(payment_data: Dict[str, Any], expected_service_type: str) -> bool:
    """Validate that the payment amount matches the expected price for the service.

    This prevents clients from sending a payment for a cheaper service type
    while accessing a more expensive one.

    Args:
        payment_data: Decoded payment signature data
        expected_service_type: The service type the endpoint requires

    Returns:
        True if the payment amount is valid (within 1% tolerance)
    """
    if expected_service_type not in PRICING:
        logger.warning(f"Unknown service type for validation: {expected_service_type}")
        return False

    accepted = payment_data.get('accepted', {})
    amount = int(accepted.get('amount', 0))
    expected_cents = int(PRICING[expected_service_type]['usdc_cents'] * 10000)

    if expected_cents == 0:
        return amount == 0

    # Allow 1% tolerance for rounding
    return abs(amount - expected_cents) / expected_cents < 0.01


def validate_payment_deadline(payment_data: Dict[str, Any]) -> bool:
    """Validate that the payment deadline has not expired.

    Args:
        payment_data: Decoded payment signature data

    Returns:
        True if the payment is within the deadline window
    """
    authorization = payment_data.get('payload', {}).get('authorization', {})
    deadline = authorization.get('deadline', 0)

    if deadline == 0:
        # No deadline specified — allow (some payment methods don't use deadlines)
        return True

    current_time = int(time.time())
    return current_time <= deadline


async def verify_x402_payment(payment_signature: str) -> Dict[str, Any]:
    """Verify x402 payment with the facilitator.

    Supports both the x402.org testnet facilitator and the CDP facilitator
    (which requires API key authentication).

    Args:
        payment_signature: The payment signature from PAYMENT-SIGNATURE header
                          (may be Base64-encoded or plain JSON)

    Returns:
        Verification result from facilitator with 'valid' key

    Raises:
        aiohttp.ClientError: If verification request fails
    """
    # Decode the payment signature (handles both Base64 and plain JSON)
    try:
        payment_data = decode_payment_signature(payment_signature)
    except ValueError as e:
        raise aiohttp.ClientError(f"Invalid payment signature format: {e}")

    headers = _get_facilitator_headers()

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f'{FACILITATOR_URL}/verify',
                json=payment_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"Payment verification failed with status {response.status}",
                        headers=response.headers
                    )
                return await response.json()
        except aiohttp.ClientError:
            raise
        except Exception as e:
            raise aiohttp.ClientError(f"Payment verification failed: {e}")


async def settle_x402_payment(payment_signature: str) -> Dict[str, Any]:
    """Settle x402 payment with the facilitator after service completion.

    Args:
        payment_signature: The payment signature from PAYMENT-SIGNATURE header
                          (may be Base64-encoded or plain JSON)

    Returns:
        Settlement result from facilitator

    Raises:
        aiohttp.ClientError: If settlement request fails
    """
    # Decode the payment signature (handles both Base64 and plain JSON)
    try:
        payment_data = decode_payment_signature(payment_signature)
    except ValueError as e:
        raise aiohttp.ClientError(f"Invalid payment signature format: {e}")

    headers = _get_facilitator_headers()

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f'{FACILITATOR_URL}/settle',
                json=payment_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"Payment settlement failed with status {response.status}",
                        headers=response.headers
                    )
                return await response.json()
        except aiohttp.ClientError:
            raise
        except Exception as e:
            raise aiohttp.ClientError(f"Payment settlement failed: {e}")


def x402_required(service_type: str):
    """Decorator to require x402 payment for an endpoint.

    Implements the full X402 v2 protocol:
    1. Check for PAYMENT-SIGNATURE header
    2. Decode (Base64) and validate payment amount
    3. Validate payment deadline
    4. Verify with facilitator
    5. Execute handler
    6. Settle payment after successful service delivery
    7. Return PAYMENT-RESPONSE header (Base64-encoded)

    Args:
        service_type: Type of service requiring payment
    """
    def decorator(handler):
        async def wrapper(request):
            # Check for PAYMENT-SIGNATURE header
            payment_signature_header = request.headers.get('PAYMENT-SIGNATURE')

            if not payment_signature_header:
                # Return 402 with payment requirements (Base64-encoded per X402 v2 spec)
                try:
                    payment_required = build_x402_payment_required(
                        service_type=service_type,
                        resource_url=str(request.url)
                    )

                    return web.json_response(
                        {'error': 'Payment required'},
                        status=402,
                        headers={'PAYMENT-REQUIRED': payment_required}
                    )
                except Exception as e:
                    logger.error(f"Error building payment required response: {e}")
                    return web.json_response(
                        {'error': 'Internal server error'},
                        status=500
                    )

            # Decode the payment signature (Base64 per X402 v2 spec, with fallback)
            try:
                payment_data = decode_payment_signature(payment_signature_header)
            except ValueError as e:
                return web.json_response(
                    {'error': f'Invalid payment signature format: {e}'},
                    status=400
                )

            # Validate payment amount matches expected price
            if not validate_payment_amount(payment_data, service_type):
                logger.warning(f"Payment amount mismatch for {service_type}")
                return web.json_response(
                    {'error': 'Payment amount does not match expected price'},
                    status=402
                )

            # Validate payment deadline
            if not validate_payment_deadline(payment_data):
                logger.warning("Payment deadline expired")
                return web.json_response(
                    {'error': 'Payment deadline expired'},
                    status=402
                )

            # Verify payment with facilitator
            try:
                verification_result = await verify_x402_payment(payment_signature_header)

                if not verification_result.get('valid'):
                    return web.json_response(
                        {'error': 'Invalid payment'},
                        status=402
                    )

            except aiohttp.ClientError as e:
                logger.error(f"Payment verification failed (facilitator error): {e}")
                return web.json_response(
                    {'error': 'Payment verification failed'},
                    status=402
                )
            except Exception as e:
                logger.error(f"Payment verification failed (unexpected): {e}")
                return web.json_response(
                    {'error': 'Payment verification failed'},
                    status=402
                )

            # Store decoded payment info for settlement and recording
            request['x402_payment'] = payment_data
            request['x402_payment_signature'] = payment_signature_header

            # Proceed with handler
            try:
                response = await handler(request)

                # After successful service, settle payment
                if response.status == 200:
                    try:
                        settlement = await settle_x402_payment(payment_signature_header)
                        # Add settlement proof to response headers (Base64-encoded per X402 v2 spec)
                        response.headers['PAYMENT-RESPONSE'] = build_payment_response_header(settlement)
                    except Exception as e:
                        logger.error(f"Payment settlement failed: {e}")
                        # Don't fail the request if settlement fails, just log it
                        # The service was already delivered

                return response

            except Exception as e:
                logger.error(f"Handler failed: {e}")
                raise

        return wrapper
    return decorator