# Payments Implementation Plan

**Date:** 2026-04-17
**Status:** Planning (Revised)
**Priority:** P1
**Revised:** 2026-04-17 — Addresses gaps from cross-reference analysis (see `payments-gap-analysis.md`)

---

## 1. Executive Summary

The payments infrastructure has foundational modules in place (`stripe.py`, `x402.py`, `payment_strategy.py`) but they are **not wired into the application**. No API endpoints enforce payment, no billing routes exist for users, and no database tables track x402 payments. This plan details every piece needed to make the payment system fully functional, incorporating fixes for all gaps identified in the cross-reference analysis against `TECHNICAL_SPECIFICATION.md`, `X402_PAYMENTS.md`, and the current codebase.

### Key Revisions from Gap Analysis

| Gap | Severity | Fix |
|-----|----------|-----|
| x402_payments table missing X402 spec columns | CRITICAL | Added `resource_url`, `scheme`, `payment_payload`, `verification_result`, `verified_at`; aligned column names |
| agent_usage table schema conflict with X402 spec | CRITICAL | Add crypto payment columns to existing table instead of replacing |
| Missing RLS policies on all tables | CRITICAL | Added RLS policies to migration |
| No payment enforcement on WHIP/streaming endpoints | CRITICAL | Added payment decorators to WHIP and stream session endpoints |
| Wrong Base Sepolia USDC address in x402.py | CRITICAL | Fixed to `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| x402 header encoding mismatch (Base64 vs plain JSON) | CRITICAL | Aligned with X402 spec: Base64-encoded headers |
| subscription_required reads `request['user_id']` (never set) | CRITICAL | Fixed to use `request['user']` and `request['agent']` from auth middleware |
| Redundant subscription_tier on users table | MAJOR | Removed; use subscriptions table as single source of truth |
| subscriptions table missing trial_start/trial_end | MAJOR | Added columns to migration |
| Facilitator downtime: plan says "allow through" but code blocks | MAJOR | Implemented graceful fallback with logging |
| Subscription check fails open on DB errors | MAJOR | Changed to fail-closed |
| No webhook idempotency | MAJOR | Added event ID deduplication |
| x402 payment lifecycle tracking | MAJOR | Track pending → verified → settled/failed |
| Supabase service_role key needed | MAJOR | Added to env config and supabase_client |
| Async/sync mismatch in Supabase calls | MAJOR | Wrapped in asyncio.to_thread() |
| Missing Polygon/Ethereum/Solana Devnet networks | MINOR | Added all 6 networks from X402 spec |
| Missing CDP facilitator support | MINOR | Added CDP_API_KEY env vars |
| Missing Python/frontend package deps | MINOR | Added to requirements.txt and package.json sections |

---

## 2. Current State (What Exists)

### 2.1 `backend/payments/stripe.py` — `StripePaymentService` ✅

| Method | Status | Notes |
|--------|--------|-------|
| `__init__()` | ✅ | Loads config from env vars, initializes `StripeClient` |
| `create_stripe_customer()` | ✅ | Creates/reuses customers with idempotency keys |
| `create_subscription()` | ✅ | Creates subscriptions with trial periods |
| `cancel_subscription()` | ✅ | Cancels subscriptions |
| `update_subscription()` | ✅ | Changes tier with proration |
| `verify_webhook_signature()` | ✅ | Verifies Stripe webhook signatures |
| `handle_stripe_webhook()` | ⚠️ | Method exists but **no route registered**; event handling is partial |
| `get_subscription_price_id()` | ✅ | **Already implemented** in codebase (was listed as ❌ in prior version) |
| `get_tier_level()` | ✅ | Returns numeric tier hierarchy |
| `_get_tier_from_price_id()` | ✅ | Reverse lookup from price ID to tier |
| `create_checkout_session()` | ❌ | **Not implemented** — needed for user-facing checkout |
| `get_stripe_service()` | ⚠️ | **Exists but raises ValueError** if keys missing — needs graceful fallback |
| `subscription_required()` | ⚠️ | **Bug: reads `request.get('user_id')`** which is never set by auth middleware |

### 2.2 `backend/payments/x402.py` ✅

| Function | Status | Notes |
|----------|--------|-------|
| `build_x402_payment_required()` | ⚠️ | Returns plain JSON — **should be Base64-encoded per X402 spec** |
| `verify_x402_payment()` | ✅ | Verifies with Coinbase facilitator |
| `settle_x402_payment()` | ✅ | Settles after service delivery |
| `x402_required()` decorator | ⚠️ | No payment amount validation, no deadline check, no replay protection |
| `PRICING` config | ✅ | `transcribe_cpu=$0.01`, `transcribe_gpu=$0.05`, `translate=$0.001` |
| `SUPPORTED_NETWORKS` | ⚠️ | Only 3 networks — **missing Polygon, Ethereum, Solana Devnet**; **wrong USDC address for Base Sepolia** |

### 2.3 `backend/payments/payment_strategy.py` ✅

| Decorator | Status | Notes |
|-----------|--------|-------|
| `payment_required()` | ⚠️ | Creates new decorator chain on every request (inefficient); no facilitator fallback |
| `x402_or_subscription()` | ✅ | Convenience wrapper |
| `subscription_only()` | ✅ | Requires active subscription only |
| `x402_only()` | ✅ | Requires x402 payment only |

### 2.4 Database Tables

| Table | Status | Notes |
|-------|--------|-------|
| `subscriptions` | ✅ | Exists in init migration — **missing `trial_start`/`trial_end` columns** |
| `transactions` | ✅ | Exists in init migration |
| `payment_methods` | ❌ | **Not migrated** — spec §13.6 |
| `x402_payments` | ❌ | **Not migrated** — needs X402 spec-aligned schema |
| `agent_usage` | ✅ | Exists — **needs crypto payment columns added** |

### 2.5 Agent Subscription Endpoints ⚠️

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /api/v1/agents/subscription` | ✅ | Returns agent's current tier |
| `POST /api/v1/agents/subscription` | ⚠️ | x402 returns 402 with **placeholder amount**; Stripe returns 501 |
| `DELETE /api/v1/agents/subscription` | ✅ | Sets tier to free |
| `POST /api/v1/agents/subscription/reactivate` | ✅ | Reactivates |

---

## 3. Implementation Tasks

### Phase 0: Critical Fixes (P0 — Must do before any other payment work)

#### Task 0.1: Fix x402.py Protocol Compliance

**File:** `backend/payments/x402.py`

**0.1a: Fix Base Sepolia USDC address**

```python
SUPPORTED_NETWORKS = [
    {
        'chain_id': 'eip155:84532',  # Base Sepolia
        'asset': '0x036CbD53842c5426634e7929541eC2318f3dCF7e',  # USDC on Base Sepolia (FIXED)
        'symbol': 'USDC',
    },
    {
        'chain_id': 'eip155:8453',  # Base
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
```

**0.1b: Base64-encode x402 headers per X402 v2 spec**

```python
import base64

def build_x402_payment_required(service_type: str, resource_url: str) -> str:
    """Build x402 payment required header value (Base64-encoded JSON per X402 v2 spec)."""
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

    for net in SUPPORTED_NETWORKS:
        payment_required['accepts'].append({
            'scheme': 'exact',
            'network': net['chain_id'],
            'amount': str(int(PRICING[service_type]['usdc_cents'] * 10000)),
            'asset': net['asset'],
            'payTo': PLATFORM_WALLET,
            'maxTimeoutSeconds': 300,
            'resource': str(resource_url),
            'description': f"{service_type} service - ${PRICING[service_type]['usd']} USDC",
            'mimeType': 'application/json',
        })

    # X402 v2 spec: Base64-encode the JSON payload
    return base64.b64encode(json.dumps(payment_required).encode()).decode()


def decode_payment_signature(payment_signature: str) -> Dict[str, Any]:
    """Decode Base64-encoded payment signature per X402 v2 spec."""
    try:
        return json.loads(base64.b64decode(payment_signature).decode())
    except Exception:
        # Fallback: try plain JSON for backward compatibility
        return json.loads(payment_signature)


def build_payment_response_header(settlement: Dict[str, Any]) -> str:
    """Build x402 payment response header (Base64-encoded JSON per X402 v2 spec)."""
    response = {
        'x402Version': 2,
        'transaction': settlement.get('transaction_hash', ''),
        'network': settlement.get('network', ''),
        'payer': settlement.get('payer', ''),
        'payee': PLATFORM_WALLET,
        'amount': settlement.get('amount', ''),
        'asset': settlement.get('asset', ''),
        'success': settlement.get('success', True),
    }
    return base64.b64encode(json.dumps(response).encode()).decode()
```

**0.1c: Add payment amount validation and deadline checking**

```python
def validate_payment_amount(payment_data: Dict[str, Any], expected_service_type: str) -> bool:
    """Validate that the payment amount matches the expected price for the service."""
    accepted = payment_data.get('accepted', {})
    amount = int(accepted.get('amount', 0))
    expected_cents = int(PRICING[expected_service_type]['usdc_cents'] * 10000)
    # Allow 1% tolerance for rounding
    return abs(amount - expected_cents) / max(expected_cents, 1) < 0.01


def validate_payment_deadline(payment_data: Dict[str, Any]) -> bool:
    """Validate that the payment deadline has not expired."""
    authorization = payment_data.get('payload', {}).get('authorization', {})
    deadline = authorization.get('deadline', 0)
    current_time = int(time.time())
    return current_time <= deadline
```

**0.1d: Add CDP facilitator configuration support**

```python
# Environment variables for facilitator
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")
CDP_API_KEY_ID = os.environ.get("CDP_API_KEY_ID", "")
CDP_API_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET", "")
PLATFORM_WALLET = os.environ.get("PLATFORM_WALLET", "0xYourPlatformWallet")

def _get_facilitator_headers() -> Dict[str, str]:
    """Get headers for facilitator requests, including CDP auth if configured."""
    headers = {"Content-Type": "application/json"}
    if CDP_API_KEY_ID and CDP_API_KEY_SECRET:
        headers["CDP-API-KEY-ID"] = CDP_API_KEY_ID
        headers["CDP-API-KEY-SECRET"] = CDP_API_KEY_SECRET
    return headers
```

**Dependencies:** None
**Estimated effort:** 2 hours

---

#### Task 0.2: Fix `subscription_required` Decorator Identity Bug

**File:** `backend/payments/stripe.py`

The existing decorator at line 933 reads `request.get('user_id')` which is **never set** by the auth middleware. The auth middleware sets `request['user']` (Supabase user object) and `request['agent']` (dict). Replace the entire decorator:

```python
def subscription_required(min_tier: str = 'starter'):
    """Decorator to require an active subscription at or above min_tier.
    
    FAILS CLOSED: If subscription check fails due to error, access is denied.
    """
    def decorator(handler):
        async def wrapper(request: web.Request) -> web.Response:
            service = get_stripe_service()
            
            # Try to get user or agent identity from auth middleware
            user = request.get('user')
            agent = request.get('agent')
            
            if not user and not agent:
                return web.json_response(
                    {'error': 'Authentication required'},
                    status=401,
                )
            
            # Agent authentication: check subscription_tier from agent record
            if agent:
                tier = agent.get('subscription_tier', 'free')
                if service and service.get_tier_level(tier) >= service.get_tier_level(min_tier):
                    return await handler(request)
                else:
                    return web.json_response({
                        'error': f'Subscription tier {min_tier} or higher required',
                        'current_tier': tier,
                        'required_tier': min_tier,
                    }, status=403)
            
            # User authentication: check subscription in database
            if user:
                user_id = str(user.id) if hasattr(user, 'id') else None
                if not user_id:
                    return web.json_response(
                        {'error': 'Unable to determine user identity'},
                        status=401,
                    )
                
                try:
                    result = await asyncio.to_thread(
                        lambda: supabase.table('subscriptions')
                            .select('plan,status')
                            .eq('user_id', user_id)
                            .execute()
                    )
                    
                    if result.data:
                        sub = result.data[0]
                        if sub['status'] in ('active', 'trialing'):
                            tier = sub.get('plan', 'free')
                            if service and service.get_tier_level(tier) >= service.get_tier_level(min_tier):
                                return await handler(request)
                            else:
                                return web.json_response({
                                    'error': f'Insufficient subscription tier. Required: {min_tier}, Current: {tier}',
                                    'required_tier': min_tier,
                                    'current_tier': tier,
                                }, status=403)
                    
                    # No active subscription found
                    return web.json_response({
                        'error': 'Active subscription required',
                        'required_tier': min_tier,
                    }, status=402)
                    
                except Exception as e:
                    logger.error(f"Error checking subscription (fail-closed): {e}")
                    # FAIL CLOSED: deny access on database error
                    return web.json_response({
                        'error': 'Unable to verify subscription status',
                    }, status=503)
            
            return web.json_response({'error': 'Authentication required'}, status=401)
        return wrapper
    return decorator
```

**Key changes:**
- Uses `request.get('user')` and `request.get('agent')` (matching auth middleware)
- **Fails closed** on database errors (returns 503, not allowing access)
- Uses `asyncio.to_thread()` for Supabase calls (non-blocking)
- Handles both agent and user identity paths

**Dependencies:** None
**Estimated effort:** 1 hour

---

#### Task 0.3: Fix `get_stripe_service()` to Return None Gracefully

**File:** `backend/payments/stripe.py`

The existing `get_stripe_service()` at line 907 raises `ValueError` if `STRIPE_SECRET_KEY` is missing. It should return `None` so the payment strategy can fall back to x402-only:

```python
_stripe_service: Optional[StripePaymentService] = None

def get_stripe_service() -> Optional[StripePaymentService]:
    """Get or create the Stripe payment service singleton.
    
    Returns None if STRIPE_SECRET_KEY is not configured, allowing
    the payment strategy to fall back to x402-only mode.
    """
    global _stripe_service
    if _stripe_service is None:
        api_key = os.environ.get("STRIPE_SECRET_KEY")
        if not api_key:
            logger.warning("STRIPE_SECRET_KEY not configured, Stripe payments disabled")
            return None
        try:
            _stripe_service = StripePaymentService()
        except ValueError as e:
            logger.error(f"Failed to initialize Stripe service: {e}")
            return None
    return _stripe_service
```

**Dependencies:** None
**Estimated effort:** 15 minutes

---

### Phase 1: Foundation (P0 — Required for any payment flow)

#### Task 1.1: Add Missing Database Migrations

**File:** `supabase/migrations/20260417_01_create_payment_tables.sql`

```sql
-- =============================================
-- Payment tables migration
-- Addresses gaps from cross-reference analysis:
-- - x402_payments aligned with X402_PAYMENTS.md spec
-- - payment_methods for user billing
-- - agent_usage crypto payment columns
-- - subscriptions trial columns
-- - RLS policies on all new tables
-- =============================================

-- Payment methods linked to users
CREATE TABLE IF NOT EXISTS payment_methods (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('stripe', 'crypto')),
  stripe_payment_method_id TEXT UNIQUE,
  stripe_customer_id TEXT,
  wallet_address TEXT,
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_methods_user_id ON payment_methods(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_methods_stripe_customer ON payment_methods(stripe_customer_id);

-- x402 payments table (aligned with X402_PAYMENTS.md spec)
CREATE TABLE IF NOT EXISTS x402_payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_wallet TEXT NOT NULL,                          -- X402 spec: agent_wallet
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,  -- Plan addition: link to user
  agent_id UUID REFERENCES agents(id) ON DELETE SET NULL, -- Plan addition: link to agent
  resource_url TEXT NOT NULL,                          -- X402 spec: resource_url
  amount NUMERIC NOT NULL,                             -- X402 spec: amount (NUMERIC, not INTEGER)
  asset TEXT NOT NULL,                                 -- X402 spec: asset
  network TEXT NOT NULL,                               -- X402 spec: network
  scheme TEXT NOT NULL DEFAULT 'exact',                -- X402 spec: scheme
  service_type TEXT NOT NULL CHECK (service_type IN ('transcribe_cpu', 'transcribe_gpu', 'translate', 'subscription_starter', 'subscription_pro', 'subscription_enterprise')),
  transaction_hash TEXT,                               -- X402 spec: transaction_hash
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'settled', 'failed')),
  payment_payload JSONB,                               -- X402 spec: payment_payload
  verification_result JSONB,                           -- X402 spec: verification_result
  settlement_result JSONB,                             -- X402 spec: settlement_result
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  verified_at TIMESTAMPTZ,                             -- X402 spec: verified_at
  settled_at TIMESTAMPTZ                               -- X402 spec: settled_at
);

CREATE INDEX IF NOT EXISTS idx_x402_payments_agent_wallet ON x402_payments(agent_wallet);
CREATE INDEX IF NOT EXISTS idx_x402_payments_resource_url ON x402_payments(resource_url);
CREATE INDEX IF NOT EXISTS idx_x402_payments_transaction_hash ON x402_payments(transaction_hash);
CREATE INDEX IF NOT EXISTS idx_x402_payments_status ON x402_payments(status);
CREATE INDEX IF NOT EXISTS idx_x402_payments_created ON x402_payments(created_at);
CREATE INDEX IF NOT EXISTS idx_x402_payments_user_id ON x402_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_x402_payments_agent_id ON x402_payments(agent_id);

-- Add crypto payment columns to existing agent_usage table
-- (merges X402 spec schema with existing migration schema)
ALTER TABLE agent_usage ADD COLUMN IF NOT EXISTS agent_wallet TEXT;
ALTER TABLE agent_usage ADD COLUMN IF NOT EXISTS amount_paid NUMERIC;
ALTER TABLE agent_usage ADD COLUMN IF NOT EXISTS asset TEXT;
ALTER TABLE agent_usage ADD COLUMN IF NOT EXISTS transaction_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_usage_agent_wallet ON agent_usage(agent_wallet);

-- Add trial columns to subscriptions table (needed by Stripe webhook handler)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_start TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_end TIMESTAMPTZ;

-- =============================================
-- Row Level Security (RLS) Policies
-- =============================================

-- Enable RLS on all new tables
ALTER TABLE payment_methods ENABLE ROW LEVEL SECURITY;
ALTER TABLE x402_payments ENABLE ROW LEVEL SECURITY;

-- Service role can manage all payment_methods
CREATE POLICY "Service role can manage payment_methods" ON payment_methods
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- Users can view their own payment methods
CREATE POLICY "Users can view own payment_methods" ON payment_methods
  FOR SELECT USING (user_id = auth.uid());

-- Service role can manage all x402_payments
CREATE POLICY "Service role can manage x402_payments" ON x402_payments
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- Agents can view their own payments (by wallet address)
CREATE POLICY "Agents can view own x402_payments" ON x402_payments
  FOR SELECT USING (agent_wallet = auth.jwt()->>'wallet_address');

-- Users can view their own x402_payments
CREATE POLICY "Users can view own x402_payments" ON x402_payments
  FOR SELECT USING (user_id = auth.uid());

-- Enable RLS on existing tables that lack it
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;

-- Service role can manage subscriptions and transactions
CREATE POLICY "Service role can manage subscriptions" ON subscriptions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Service role can manage transactions" ON transactions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- Users can view their own subscriptions
CREATE POLICY "Users can view own subscriptions" ON subscriptions
  FOR SELECT USING (user_id = auth.uid());

-- Users can view their own transactions
CREATE POLICY "Users can view own transactions" ON transactions
  FOR SELECT USING (user_id = auth.uid());
```

**Key changes from prior version:**
- `x402_payments` now includes all X402_PAYMENTS.md spec columns (`resource_url`, `scheme`, `payment_payload`, `verification_result`, `settlement_result`, `verified_at`)
- Column names aligned with X402 spec (`agent_wallet` instead of `wallet_address`, `amount` as NUMERIC instead of `amount_cents` as INTEGER)
- Added `service_type` CHECK constraint includes subscription tiers
- Added crypto payment columns to existing `agent_usage` table instead of replacing it
- Added `trial_start`/`trial_end` to `subscriptions` table
- Added RLS policies on all new and existing payment-related tables
- Removed redundant `subscription_tier` column on `users` table (subscriptions table is source of truth)

**Dependencies:** None
**Estimated effort:** 1 hour

---

#### Task 1.2: Wire Payment Decorators into API Endpoints

**Files:** `backend/transcribe.py`, `backend/translate.py`, `backend/sessions.py`

Add `@x402_or_subscription()` decorator to all service endpoints. The decorator must be placed **after** `@require_auth` (so user/agent is identified first) but **before** `@check_rate_limit` and `@track_usage`:

```python
# In transcribe.py
from payments.payment_strategy import x402_or_subscription

@require_auth
@x402_or_subscription(service_type='transcribe_cpu')
@check_rate_limit
@track_usage
async def transcribe_file(request):
    ...

@require_auth
@x402_or_subscription(service_type='transcribe_cpu')
@check_rate_limit
@track_usage
async def transcribe_url(request):
    ...

@require_auth
@x402_or_subscription(service_type='transcribe_gpu')
@check_rate_limit
@track_usage
async def transcribe_stream(request):
    ...

# CRITICAL: WHIP endpoint must also enforce payment — it's the most expensive GPU operation
@require_auth
@x402_or_subscription(service_type='transcribe_gpu')
@check_rate_limit
@track_usage
async def whip_proxy(request):
    ...

# In translate.py
from payments.payment_strategy import x402_or_subscription

@require_auth
@x402_or_subscription(service_type='translate')
@check_rate_limit
@track_usage
async def translate_text(request):
    ...

@require_auth
@x402_or_subscription(service_type='translate')
@check_rate_limit
@track_usage
async def translate_transcription(request):
    ...
```

**Important:** The `x402_or_subscription` decorator needs to be updated to properly extract the user/agent identity from `request['user']` or `request['agent']` (set by `require_auth`) to check subscription status. See Task 0.2 for the corrected `subscription_required` implementation.

**Dependencies:** Task 0.2 (fixed subscription_required), Task 0.3 (graceful get_stripe_service), Task 1.1 (payment_methods table)
**Estimated effort:** 2 hours

---

#### Task 1.3: Update `payment_strategy.py` — Fix Decorator Composition and Add Fallback

**File:** `backend/payments/payment_strategy.py`

Refactor to avoid creating new decorator chains on every request, and implement facilitator downtime fallback:

```python
def payment_required(service_type: str = 'transcribe_cpu', 
                    require_subscription: bool = False,
                    subscription_tier: str = 'starter'):
    """
    Unified payment decorator that accepts either x402 payment OR active subscription.
    
    FAILS CLOSED: If both x402 and subscription checks fail, access is denied.
    FACILITATOR FALLBACK: If x402 facilitator is unreachable, falls back to subscription check.
    """
    def decorator(handler: Callable) -> Callable:
        async def wrapper(request):
            # If subscription is required, use subscription_required decorator
            if require_subscription:
                return await subscription_only(subscription_tier)(handler)(request)
            
            # Check for PAYMENT-SIGNATURE header (x402 payment attempt)
            payment_signature_header = request.headers.get('PAYMENT-SIGNATURE')
            
            if payment_signature_header:
                # Decode payment signature (Base64 per X402 v2 spec, with fallback)
                try:
                    from .x402 import decode_payment_signature, verify_x402_payment, settle_x402_payment
                    from .x402 import validate_payment_amount, validate_payment_deadline
                    
                    payment_data = decode_payment_signature(payment_signature_header)
                    
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
                                    from .x402 import build_payment_response_header
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
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid payment signature format: {e}")
                    return web.json_response(
                        {'error': 'Invalid payment signature format'},
                        status=400,
                    )
                except Exception as e:
                    logger.error(f"x402 payment processing failed: {e}")
                    # Fall back to subscription check
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
                from .x402 import build_x402_payment_required
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
    """Record x402 payment in database with lifecycle tracking."""
    try:
        from supabase_client import supabase
        import asyncio
        
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
    """Update x402 payment status in database."""
    try:
        from supabase_client import supabase
        import asyncio
        
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
```

**Dependencies:** Task 0.1 (x402 protocol fixes), Task 0.2 (subscription_required fix), Task 1.1 (x402_payments table)
**Estimated effort:** 3 hours

---

### Phase 2: User-Facing Billing (P1 — Required for users to pay)

#### Task 2.1: Create Billing Routes Module

**File:** `backend/billing.py` (new file)

```python
# Routes to implement:
POST /api/v1/billing/create-checkout-session   # Create Stripe Checkout Session
GET  /api/v1/billing/subscription               # Get current subscription status
POST /api/v1/billing/cancel                     # Cancel subscription
POST /api/v1/billing/update                     # Change subscription tier
GET  /api/v1/billing/usage                      # Get usage vs quota
POST /api/v1/billing/webhook                    # Stripe webhook handler (NO @require_auth)
```

**Auth requirements:**

| Endpoint | Auth | Notes |
|----------|------|-------|
| `create-checkout-session` | `@require_user_auth` | Only authenticated users |
| `subscription` (GET) | `@require_user_auth` | Only authenticated users |
| `cancel` | `@require_user_auth` | Only authenticated users |
| `update` | `@require_user_auth` | Only authenticated users |
| `usage` | `@require_user_auth` | Only authenticated users |
| `webhook` | **None** (Stripe signature) | Must NOT use `@require_auth` |

**`create_checkout_session` implementation:**
1. Get authenticated user from `request['user']`
2. Get or create Stripe customer via `StripePaymentService.create_stripe_customer()`
3. Look up price_id from `STRIPE_PRICE_STARTER/PRO/ENTERPRISE` env vars
4. Create Checkout Session with success/cancel URLs (configurable via env vars)
5. Return `{ url: checkout_session.url, session_id: checkout_session.id }`

**`webhook` implementation:**
1. Verify signature via `StripePaymentService.verify_webhook_signature()`
2. **Check for duplicate events** using Stripe event ID (idempotency):
   ```python
   # Deduplicate webhook events
   event_id = event.id
   existing = await asyncio.to_thread(
       lambda: supabase.table('stripe_events').select('id').eq('stripe_event_id', event_id).execute()
   )
   if existing.data:
       logger.info(f"Duplicate webhook event ignored: {event_id}")
       return web.json_response({'status': 'duplicate_ignored'})
   await asyncio.to_thread(
       lambda: supabase.table('stripe_events').insert({'stripe_event_id': event_id, 'event_type': event.type}).execute()
   )
   ```
3. Route events:
   - `checkout.session.completed` → Create/update subscription in `subscriptions` table
   - `customer.subscription.updated` → Update subscription status/plan
   - `customer.subscription.deleted` → Mark subscription as canceled
   - `invoice.payment_succeeded` → Record in `transactions` table
   - `invoice.payment_failed` → Log warning, potentially suspend
4. Return 200 OK for all valid webhooks

**Success/cancel URL configuration:**
```python
STRIPE_SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:5173/billing/success")
STRIPE_CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL", "http://localhost:5173/billing/cancel")
```

**Dependencies:** Task 0.3 (get_stripe_service), Task 1.1 (payment_methods table)
**Estimated effort:** 5 hours

---

#### Task 2.2: Register Billing Routes in `main.py`

**File:** `backend/main.py`

```python
from billing import setup_routes as setup_billing_routes
# ...
setup_billing_routes(app)
```

**Dependencies:** Task 2.1
**Estimated effort:** 15 minutes

---

#### Task 2.3: Fix Agent Stripe Subscription (Remove 501)

**File:** `backend/agents.py`

Currently `agent_create_subscription` returns 501 for Stripe. Update to:
1. Call `get_stripe_service()`
2. If Stripe is configured, create a checkout session
3. Return the checkout URL to the agent
4. If Stripe is not configured, return appropriate error

Also fix the x402 subscription pricing — replace placeholder amount with tier-specific pricing:

```python
SUBSCRIPTION_PRICING = {
    'starter': {'usdc_cents': 1500, 'usd': 15.00},    # $15/month
    'pro': {'usdc_cents': 3900, 'usd': 39.00},         # $39/month
    'enterprise': {'usdc_cents': 9900, 'usd': 99.00},  # $99/month
}
```

**Dependencies:** Task 0.3 (get_stripe_service)
**Estimated effort:** 1.5 hours

---

### Phase 3: Quota Enforcement (P1 — Required to limit free tier usage)

#### Task 3.1: Create Quota Checking Module

**File:** `backend/payments/quotas.py` (new file)

```python
# Plan prices (USD per month)
PLAN_PRICES = {
    'free': 0,
    'starter': 15,
    'pro': 39,
    'enterprise': 99,
}

# Plan limits (rolling 30-day window)
# transcription_minutes: combined CPU+GPU transcription pool
PLAN_LIMITS = {
    'free': {
        'transcription_minutes': 1800,       # 1 hr/day
        'translation_characters': 5000,
        'queue_delay': True,
        'priority': 'low',
        'watermark': True,
    },
    'starter': {
        'transcription_minutes': 5400,       # 3 hr/day
        'translation_characters': 100000,
        'queue_delay': False,
        'priority': 'normal',
        'watermark': False,
    },
    'pro': {
        'transcription_minutes': 14400,      # 8 hr/day
        'translation_characters': -1,        # unlimited
        'queue_delay': False,
        'priority': 'high',
        'watermark': False,
    },
    'enterprise': {
        'transcription_minutes': -1,         # unlimited (24 hr/day)
        'translation_characters': -1,        # unlimited
        'queue_delay': False,
        'priority': 'highest',
        'watermark': False,
    },
}

async def check_quota(user_id: str, service_type: str, tier: str = 'free') -> tuple[bool, dict]:
    """
    Check if user has remaining quota for the given service type.
    
    Returns:
        (allowed: bool, quota_info: dict with remaining, limit, used)
    """
    import asyncio
    from supabase_client import supabase
    
    limits = PLAN_LIMITS.get(tier, PLAN_LIMITS['free'])
    
    # Map service_type to quota key and usage table
    # Both transcribe_cpu and transcribe_gpu draw from the same transcription_minutes pool
    quota_mapping = {
        'transcribe_cpu': ('transcription_minutes', 'transcription_usage', 'duration_seconds'),
        'transcribe_gpu': ('transcription_minutes', 'transcription_usage', 'duration_seconds'),
        'translate': ('translation_characters', 'translation_usage', 'characters_translated'),
    }
    
    if service_type not in quota_mapping:
        return True, {'remaining': -1, 'limit': -1, 'used': 0}
    
    quota_key, table, column = quota_mapping[service_type]
    limit = limits[quota_key]
    
    # Unlimited quota
    if limit == -1:
        return True, {'remaining': -1, 'limit': -1, 'used': 0, 'unlimited': True}
    
    # Query usage for the last 30 days
    thirty_days_ago = int(time.time()) - (30 * 24 * 60 * 60)
    
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table(table)
                .select(column)
                .eq('user_id', user_id)
                .gte('created_at', thirty_days_ago)
                .execute()
        )
        
        used_raw = sum(row.get(column, 0) or 0 for row in (result.data or []))
        
        # Convert to quota units
        if 'minutes' in quota_key:
            used = used_raw / 60  # seconds to minutes
        else:
            used = used_raw  # already in characters
        
        remaining = max(0, limit - used)
        allowed = used < limit
        
        return allowed, {
            'remaining': remaining,
            'limit': limit,
            'used': used,
            'unlimited': False,
        }
    except Exception as e:
        logger.error(f"Error checking quota: {e}")
        # Fail closed: deny access if quota check fails
        return False, {'remaining': 0, 'limit': limit, 'used': 0, 'error': str(e)}
```

**Dependencies:** Task 1.1 (subscription_tier accessible via subscriptions table)
**Estimated effort:** 2 hours

---

#### Task 3.2: Integrate Quota Check into Payment Strategy

**File:** `backend/payments/payment_strategy.py`

Update `payment_required()` to check quota before processing:
1. If user has active subscription → check quota → if exceeded, return 429 with x402 option
2. If no subscription → require x402 payment (pay-per-request bypasses quotas)

```python
async def check_subscription_quota(request, tier: str, service_type: str) -> tuple[bool, Optional[web.Response]]:
    """Check if subscription tier has remaining quota. If exceeded, offer x402 fallback."""
    from payments.quotas import check_quota
    
    user = request.get('user')
    agent = request.get('agent')
    entity_id = str(user.id) if user and hasattr(user, 'id') else str(agent.get('id')) if agent else None
    
    if not entity_id:
        return False, web.json_response({'error': 'Unable to determine identity'}, status=401)
    
    allowed, quota_info = await check_quota(entity_id, service_type, tier)
    if not allowed:
        # Quota exceeded — offer x402 as fallback
        try:
            from .x402 import build_x402_payment_required
            payment_required = build_x402_payment_required(
                service_type=service_type,
                resource_url=str(request.url)
            )
            return False, web.json_response({
                'error': 'Quota exceeded',
                'quota': quota_info,
                'x402_available': True,
            }, status=429, headers={'PAYMENT-REQUIRED': payment_required})
        except Exception:
            return False, web.json_response({
                'error': 'Quota exceeded',
                'quota': quota_info,
            }, status=429)
    
    return True, None
```

**Dependencies:** Task 3.1
**Estimated effort:** 1.5 hours

---

### Phase 4: x402 Payment Recording (P2)

#### Task 4.1: x402 Payment Lifecycle Tracking

**File:** `backend/payments/payment_strategy.py`

This is now integrated into the updated `payment_required` decorator (Task 1.3) via `_record_x402_payment` and `_update_x402_payment_status`. The lifecycle is:

1. **Pending** → When payment signature is first received (before verification)
2. **Verified** → After facilitator verification succeeds
3. **Settled** → After service delivery and successful settlement
4. **Failed** → If settlement fails after service delivery

No separate task needed — covered by Task 1.3.

**Dependencies:** Task 1.1 (x402_payments table), Task 1.3 (payment_strategy refactor)
**Estimated effort:** Included in Task 1.3

---

### Phase 5: Frontend Payment UI (P2)

#### Task 5.1: Subscription Plan Selection Component

**File:** `frontend/src/components/SubscriptionPlans.jsx` (new)

- Display 4 plan tiers (Free, Starter, Pro, Enterprise) with features and pricing
- "Subscribe" button calls `POST /api/v1/billing/create-checkout-session`
- Redirect to Stripe Checkout URL
- Requires routing library (e.g., react-router-dom)

**Dependencies:** Task 2.1, react-router-dom package
**Estimated effort:** 3 hours

#### Task 5.2: Billing Management Page

**File:** `frontend/src/components/BillingPage.jsx` (new)

- Show current plan and usage
- Cancel/update subscription buttons
- Usage bars (CPU minutes, GPU minutes, translation characters used vs limit)
- Payment history

**Dependencies:** Task 2.1, Task 3.1
**Estimated effort:** 4 hours

#### Task 5.3: Stripe Checkout Success/Cancel Handlers

**File:** `frontend/src/App.jsx` (add routes)

- `/billing/success` — Show confirmation, refresh subscription status
- `/billing/cancel` — Show cancellation message

**Dependencies:** Task 2.1
**Estimated effort:** 1 hour

#### Task 5.4: x402 Payment Flow in Frontend

**File:** `frontend/src/lib/x402.js` (new)

- Intercept 402 responses
- Decode Base64-encoded `PAYMENT-REQUIRED` header (per X402 v2 spec)
- Connect wallet (MetaMask, Coinbase Wallet, etc.)
- Sign payment with wallet using EIP-3009
- Encode payment as Base64 `PAYMENT-SIGNATURE` header
- Retry request with `PAYMENT-SIGNATURE` header
- Decode Base64 `PAYMENT-RESPONSE` header from successful response

**Required packages:** `ethers` or `viem` + `wagmi` for wallet connection

**Dependencies:** Task 0.1 (Base64 encoding)
**Estimated effort:** 5 hours

---

## 4. Environment Variables Needed

```env
# Stripe (required for subscription payments)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PUBLIC_KEY=pk_test_...
STRIPE_PRICE_STARTER=price_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_ENTERPRISE=price_...
STRIPE_API_VERSION=2024-12-18.acacia
STRIPE_MAX_RETRIES=2
STRIPE_SUCCESS_URL=http://localhost:5173/billing/success
STRIPE_CANCEL_URL=http://localhost:5173/billing/cancel

# x402 (required for crypto payments)
FACILITATOR_URL=https://x402.org/facilitator
PLATFORM_WALLET=0xYourPlatformWallet
# CDP facilitator (recommended for production)
CDP_API_KEY_ID=
CDP_API_KEY_SECRET=
# Solana wallet for Solana x402 payments
SOLANA_WALLET=

# Supabase (secret key required for server-side operations, bypasses RLS)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-secret-key
SUPABASE_PUBLISHABLE_KEY=your-publishable-key

# Frontend
VITE_STRIPE_PUBLIC_KEY=pk_test_...
```

---

## 5. Python Package Dependencies

Add to `backend/requirements.txt`:

```
stripe>=7.0.0
```

Note: `aiohttp` is already present. `asyncio` is stdlib. No additional packages needed for x402 (uses `aiohttp` for facilitator calls).

---

## 6. Frontend Package Dependencies

Add to `frontend/package.json`:

```json
{
  "dependencies": {
    "react-router-dom": "^6.0.0",
    "@stripe/stripe-js": "^2.0.0",
    "ethers": "^6.0.0"
  }
}
```

---

## 7. Testing Checklist

### Unit Tests

- [ ] `test_stripe_service.py` — Test `StripePaymentService` methods with mocked Stripe API
- [ ] `test_x402_payments.py` — Test `build_x402_payment_required`, `verify_x402_payment`, `settle_x402_payment`
- [ ] `test_x402_encoding.py` — Test Base64 encoding/decoding of x402 headers
- [ ] `test_x402_validation.py` — Test payment amount validation and deadline checking
- [ ] `test_payment_strategy.py` — Test all 4 decorators with mocked dependencies
- [ ] `test_quotas.py` — Test quota checking with various tiers and usage levels
- [ ] `test_subscription_required.py` — Test with `request['user']` and `request['agent']` (not `user_id`)
- [ ] `test_base_sepolia_address.py` — Verify correct USDC address for each network

### Integration Tests

- [ ] Test `POST /api/v1/billing/create-checkout-session` with valid user
- [ ] Test `POST /api/v1/billing/webhook` with valid Stripe signature
- [ ] Test webhook duplicate event deduplication
- [ ] Test `GET /api/v1/billing/subscription` returns correct tier
- [ ] Test `POST /api/v1/billing/cancel` cancels subscription
- [ ] Test x402 payment flow: 402 → sign → verify → settle → 200
- [ ] Test x402 payment with wrong amount → 402
- [ ] Test x402 payment with expired deadline → 402
- [ ] Test x402 facilitator unreachable → falls back to subscription check
- [ ] Test quota enforcement: free user hits limit → 429 with x402 option
- [ ] Test quota bypass: x402 payment bypasses quota check
- [ ] Test subscription check fails closed on database error → 503
- [ ] Test WHIP endpoint requires payment → 402 without subscription/x402
- [ ] Test x402 payment on Base Sepolia (correct USDC address)
- [ ] Test agent + user dual auth on same request

### Manual Tests

- [ ] Stripe Checkout flow end-to-end (test mode)
- [ ] Webhook delivery and subscription activation
- [ ] Subscription cancellation and downgrade
- [ ] x402 payment with test wallet on Base Sepolia
- [ ] Facilitator downtime: verify fallback to subscription
- [ ] Database failure during payment: verify fail-closed behavior

---

## 8. Implementation Order

| Phase | Task | Dependencies | Priority |
|-------|------|-------------|----------|
| 0 | 0.1 Fix x402.py protocol compliance | None | P0 |
| 0 | 0.2 Fix subscription_required identity bug | None | P0 |
| 0 | 0.3 Fix get_stripe_service graceful fallback | None | P0 |
| 1 | 1.1 Database migrations (with RLS, trial columns, agent_usage merge) | None | P0 |
| 1 | 1.2 Wire payment decorators into endpoints (including WHIP) | 0.1, 0.2, 0.3, 1.1 | P0 |
| 1 | 1.3 Update payment_strategy.py (composition fix, fallback, lifecycle) | 0.1, 0.2, 1.1 | P0 |
| 2 | 2.1 Billing routes module (with webhook idempotency, auth) | 0.3, 1.1 | P1 |
| 2 | 2.2 Register billing routes in main.py | 2.1 | P1 |
| 2 | 2.3 Fix agent Stripe subscription (remove 501, fix x402 pricing) | 0.3 | P1 |
| 3 | 3.1 Quota checking module | 1.1 | P1 |
| 3 | 3.2 Integrate quota into payment strategy (with x402 fallback) | 3.1 | P1 |
| 5 | 5.1 Subscription plan selection UI | 2.1 | P2 |
| 5 | 5.2 Billing management page | 2.1, 3.1 | P2 |
| 5 | 5.3 Stripe success/cancel handlers | 2.1 | P2 |
| 5 | 5.4 x402 payment flow in frontend (Base64, wallet) | 0.1 | P2 |

---

## 9. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Stripe API changes | Medium | Pin `stripe` package version; use `STRIPE_API_VERSION` env var |
| x402 facilitator downtime | High | **Graceful fallback** — if facilitator unreachable, fall back to subscription check; log the event for monitoring |
| Missing Stripe env vars | Low | `get_stripe_service()` returns `None`; endpoints fall back to x402-only or return clear error |
| Webhook signature verification failure | Medium | Log detailed error; implement retry logic in Stripe dashboard |
| Webhook duplicate delivery | Medium | **Event ID deduplication** — check `stripe_events` table before processing |
| Webhook out-of-order delivery | Medium | **Timestamp validation** — compare event timestamps before applying state changes |
| Race condition on subscription creation | Low | Use idempotency keys on all Stripe API calls; DB upsert with `on_conflict` |
| Free tier abuse | Medium | Rate limiting (60 req/min); quota enforcement adds hard limits; x402 as fallback |
| Database failure during payment recording | High | **Fail-closed** for subscription checks; **log and continue** for x402 recording (service already delivered); implement reconciliation job |
| Wrong USDC address on testnet | High | **Fixed** — Base Sepolia now uses `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| x402 header encoding mismatch | High | **Fixed** — Aligned with X402 v2 spec (Base64-encoded headers) |
| Subscription check fails open | High | **Fixed** — Now fails closed (returns 503 on database error) |
| RLS policies missing | High | **Fixed** — Added RLS policies to all new and existing payment tables |
| Supabase calls blocking event loop | Medium | **Fixed** — Wrapped in `asyncio.to_thread()` |
| WHIP endpoint unprotected | High | **Fixed** — Added `@x402_or_subscription` decorator to WHIP endpoint |