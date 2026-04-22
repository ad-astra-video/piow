# Live Translation Agent API - Consumer Guide

This document explains how to consume the Live Transcription & Translation Platform's agent API endpoints. It covers authentication, making requests, handling responses, and best practices for developers building agents or integrations.

## Overview

The Live Transcription & Translation Platform provides agent-specific endpoints that allow programmatic access to:
- Agent registration and credential management
- Usage statistics and monitoring
- API key rotation
- Indirect access to transcription/translation services (via payment/subscription)

All agent endpoints are under the `/api/v1/agents/` path.

## Authentication

All agent endpoints require HMAC-SHA256 signature authentication. You must include these headers in every request:

| Header | Description | Example |
|--------|-------------|---------|
| `X-API-Key` | Your agent's API key (obtained during registration) | `ltk_live_abc123...` |
| `X-Timestamp` | Current Unix timestamp in seconds | `1712345678` |
| `X-Nonce` | Random string (UUID recommended) to prevent replay attacks | `550e8400-e29b-41d4-a716-446655440000` |
| `X-Signature` | HMAC-SHA256 signature of `(method + path + timestamp + nonce + body)` | `a1b2c3d4...` |

### Signature Generation Details

The signature is calculated as:
```
signature = HMAC-SHA256(api_secret, method + path + timestamp + nonce + body)
```

Where:
- `method`: HTTP method in uppercase (GET, POST, DELETE, etc.)
- `path`: Request path including leading slash (e.g., `/api/v1/agents/me/usage`)
- `timestamp`: Same value as the `X-Timestamp` header
- `nonce`: Same value as the `X-Nonce` header
- `body`: Request body as string (empty string for GET/DELETE, JSON string for POST/PUT)
- `api_secret`: Your agent's secret key (only shown once during registration)

**Important Security Notes:**
- Never expose `api_secret` in client-side code, public repositories, or logs
- Use a cryptographically random nonce for each request
- Keep timestamps synchronized with the server (NTP recommended)
- The API secret is only returned once during registration - store it securely

## Endpoints

### 1. Register Agent
**POST** `/api/v1/agents/register`

Registers a new agent and returns API credentials.

**Request:**
```http
POST /api/v1/agents/register
Content-Type: application/json

{
  "name": "my-transcription-agent",
  "description": "Agent for transcribing customer support calls"
}
```

**Successful Response (200):**
```json
{
  "agent_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "name": "my-transcription-agent",
  "description": "Agent for transcribing customer support calls",
  "api_key": "ltk_live_9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "api_secret": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",  // ONLY RETURNED ONCE
  "created_at": "2026-04-05T05:45:00Z",
  "message": "Agent registered successfully. Store your API key and secret securely."
}
```

**Important:** The `api_secret` is only returned once. Store it securely using a secrets manager or encrypted storage. If lost, you must create a new key.

### 2. Get Usage Statistics
**GET** `/api/v1/agents/me/usage`

Returns usage statistics for the authenticated agent.

**Required Headers:** X-API-Key, X-Timestamp, X-Nonce, X-Signature

**Successful Response (200):**
```json
{
  "agent_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "name": "my-transcription-agent",
  "usage": {
    "transcriptions_count": 142,
    "translations_count": 28,
    "total_seconds_processed": 3845.5,
    "total_cost": 2.15,
    "period": "today"
  },
  "rate_limits": {
    "requests_per_minute": 60,
    "concurrent_sessions": 5
  }
}
```

**Fields Explained:**
- `transcriptions_count`: Number of transcription requests processed today
- `translations_count`: Number of translation requests processed today
- `total_seconds_processed`: Total audio/video processing time in seconds
- `total_cost`: Estimated cost in USD for today's usage
- `period`: Time period for the statistics (today, this_month, etc.)

### 3. List API Keys
**GET** `/api/v1/agents/me/keys`

Lists API keys associated with the agent.

**Response:**
```json
{
  "agent_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "keys": [
    {
      "key_id": "primary",
      "name": "Primary Key",
      "created_at": "2026-04-05T05:45:00Z",
      "last_used": "2026-04-05T05:40:00Z",
      "is_active": true
    }
  ]
}
```

### 4. Create New API Key
**POST** `/api/v1/agents/me/keys`

Generates a new API key for the agent. This replaces the existing key - use with caution as it immediately invalidates the previous key.

**Response:**
```json
{
  "agent_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "api_key": "ltk_live_newkey123...",
  "api_secret": "newsecret456...",  // ONLY RETURNED ONCE
  "message": "New API key created successfully. Store it securely."
}
```

### 5. Revoke API Key / Deactivate Agent
**DELETE** `/api/v1/agents/me/keys`

Deactivates the agent (current implementation treats this as key revocation by deactivating the agent account).

**Response:**
```json
{
  "agent_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "message": "Agent deactivated successfully"
}
```

## Accessing Transcription & Translation Services

While agent management is under `/api/v1/agents/`, the core transcription and translation services are accessed via different endpoints. Agent authentication is **not** used for these service endpoints - instead, they use either:

1. **x402 v2 Crypto Payments** (per-request micro-payments)
2. **Stripe Subscriptions** (recurring access)

### Service Endpoints Overview

| Method | Endpoint | Description | Payment Required |
|--------|----------|-------------|------------------|
| POST | `/api/v1/transcribe/file` | Upload audio file for transcription | ✅ |
| POST | `/api/v1/transcribe/url` | Transcribe audio from URL | ✅ |
| POST | `/api/v1/transcribe/stream` | Start real-time transcription streaming | 🔒 Subscription only |
| POST | `/api/v1/translate` | Translate text | ✅ |
| POST | `/api/v1/translate/transcription` | Translate existing transcription | ✅ |
| GET | `/api/v1/transcriptions` | List user's transcriptions | 🔒 Subscription only |
| GET | `/api/v1/transcriptions/{id}` | Get transcription by ID | 🔒 Subscription only |
| DELETE | `/api/v1/transcriptions/{id}` | Delete subscription | 🔒 Subscription only |
| GET | `/api/v1/languages` | Get supported languages | ❌ No payment needed |

### Payment Methods Explained

#### Option 1: x402 v2 Payments (Pay-per-Request)
- Ideal for infrequent or variable usage
- Each request requires a micro-payment (typically fractions of a cent)
- No account or subscription needed
- Uses USDC stablecoin on Base, Ethereum, or Solana

**Flow:**
1. Make request without payment → receive 402 Payment Required
2. Response includes payment details in `PAYMENT-REQUIRED` header
3. Pay via Coinbase facilitator using provided instructions
4. Retry original request with `PAYMENT-SIGNATURE` header

#### Option 2: Subscriptions (Recurring Access)
- Ideal for regular, predictable usage
- Requires active Stripe subscription
- Tiers: Free (limited), Starter, Professional, Enterprise
- Higher tiers provide higher rate limits and features

**Verification:** The backend checks subscription status via Stripe webhook integration.

### Making Requests to Service Endpoints

Agents access service endpoints using the **same authentication methods as regular users** - either by:
1. Including valid x402 payment signatures (`PAYMENT-SIGNATURE` header)
2. Having an active subscription (checked via cookies/session or separate auth)

**Note:** Agent credentials (`X-API-Key` etc.) are **only** for the `/api/v1/agents/*` endpoints. For transcription/translation, use payment or subscription auth.

## Rate Limits & Quotas

### Agent Management Endpoints
- **60 requests per minute** per agent (across all `/api/v1/agents/*` endpoints)
- **5 concurrent sessions** (applies to streaming/long-running operations)
- Exceeding limits returns HTTP 429 (Too Many Requests)

### Service Endpoints (Transcription/Translation)
Limits depend on authentication method:

#### x402 Payments:
- Effectively unlimited (each request pays for itself)
- Practical limit: how fast you can send and confirm payments
- Recommended: batch requests where possible

#### Subscriptions:
- **Free Tier:** Very limited (e.g., 5 transcriptions/day)
- **Starter:** 100 transcriptions/month, 5 concurrent streams
- **Professional:** 1000 transcriptions/month, 20 concurrent streams
- **Enterprise:** Custom limits, priority support

**Note:** These are examples - check your subscription details for exact limits.

## Error Handling & Status Codes

Your integration should handle these common HTTP status codes:

| Code | Name | Description | Action |
|------|------|-------------|--------|
| 200 | OK | Success | Process response |
| 400 | Bad Request | Missing/invalid parameters | Fix request and retry |
| 401 | Unauthorized | Invalid/missing auth | Check credentials/signature |
| 402 | Payment Required | x402 payment needed | Initiate payment flow |
| 403 | Forbidden | Subscription required/invalid tier | Check subscription status |
| 404 | Not Found | Resource doesn't exist | Verify ID/endpoint |
| 429 | Too Many Requests | Rate limit exceeded | Wait and retry with backoff |
| 500 | Internal Server Error | Server problem | Retry after delay |
| 502 | Bad Gateway | Worker service unavailable | Retry after delay |
| 503 | Service Unavailable | Temporary overload | Retry after delay with backoff |

### Handling 402 Payment Required

When you receive a 402 response:
1. Extract payment requirements from the `PAYMENT-REQUIRED` header (base64-encoded JSON)
2. Decode: `JSON.parse(atob(paymentRequiredHeader))`
3. Follow the x402 v2 protocol to pay via the Coinbase facilitator
4. Once payment is complete, retry the original request with the `PAYMENT-SIGNATURE` header

**Example Payment Required Header:**
```
PAYMENT-REQUIRED: eyJ4NDAyVmVyc2lvbiI6MiwiYWNjZXB0cyI6W3sibmV0d29yayI6ImVpcD..."
```

### Handling 429 Too Many Requests

When you receive a 429 response:
1. Check for `Retry-After` header (seconds to wait)
2. If absent, use exponential backoff (start with 1s, double each attempt)
3. Do not retry immediately - this can worsen the situation
4. Consider upgrading subscription for higher limits if this happens frequently

## Best Practices

### Security
- **Store Secrets Securely:** Use environment variables, secrets managers (AWS Secrets Manager, HashiCorp Vault), or encrypted config files
- **Rotate Keys Regularly:** Generate new API keys every 90 days or sooner
- **Use Least Privilege:** Only request permissions you need
- **Validate TLS:** Always verify HTTPS certificates in production
- **Nonce Management:** Use cryptographically random nonces (UUID v4 recommended) and never reuse

### Performance & Reliability
- **Connection Pooling:** Reuse HTTP connections where possible
- **Timeouts:** Set reasonable timeouts (10-30s for most requests, longer for file uploads)
- **Retry Logic:** Implement exponential backoff with jitter for 5xx and 429 errors
- **Caching:** Cache infrequently changing data (like language lists)
- **Batch Processing:** Where appropriate, batch multiple items into single requests
- **Async Processing:** For long-running operations, use webhooks or polling instead of blocking connections

### Monitoring & Observability
- **Track Usage:** Periodically call `/api/v1/agents/me/usage` to monitor consumption
- **Set Alerts:** Create alerts when approaching subscription limits
- **Log Metadata:** Log request IDs, timestamps, and outcome (but never secrets)
- **Health Checks:** Use `/api/v1/transcribe/health` to check service availability
- **Distributed Tracing:** Add trace IDs to requests if your system supports it

### Error Recovery
- **Idempotency:** Design operations to be idempotent where possible (use nonce as idempotency key)
- **Dead Letter Queues:** For failed requests that require manual intervention
- **Circuit Breaker:** Temporarily stop requests if error rate exceeds threshold
- **Fallbacks:** Have alternative processing paths for critical workflows

## Example Implementation (Python)

Here's a complete example showing how to interact with the agent API:

```python
import hashlib
import hmac
import time
import uuid
import requests
import json
from typing import Dict, Any, Optional

class LiveTranslationAgentClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.api_key: Optional[str] = None
        self.api_secret: Optional[str] = None
    
    def _generate_signature(self, method: str, path: str, timestamp: str, nonce: str, body: str = "") -> str:
        """Generate HMAC-SHA256 signature for authentication."""
        message = method + path + timestamp + nonce + body
        return hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def _make_authenticated_request(self, method: str, path: str, body: Optional[Dict] = None) -> requests.Response:
        """Make an authenticated request to agent endpoints."""
        if not self.api_key or not self.api_secret:
            raise ValueError("API credentials not set. Call register_agent() or set_credentials() first.")
        
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        body_str = json.dumps(body) if body else ""
        
        signature = self._generate_signature(
            method, path, timestamp, nonce, body_str
        )
        
        headers = {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{path}"
        
        if method.upper() == "GET":
            return requests.get(url, headers=headers, params=body)
        elif method.upper() == "POST":
            return requests.post(url, headers=headers, data=body_str)
        elif method.upper() == "DELETE":
            return requests.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
    
    def register_agent(self, name: str, description: str = "") -> Dict[str, Any]:
        """Register a new agent and set credentials."""
        response = requests.post(
            f"{self.base_url}/api/v1/agents/register",
            json={"name": name, "description": description}
        )
        response.raise_for_status()
        data = response.json()
        
        # Store credentials for future use
        self.api_key = data["api_key"]
        self.api_secret = data["api_secret"]
        
        return data
    
    def set_credentials(self, api_key: str, api_secret: str):
        """Set existing credentials."""
        self.api_key = api_key
        self.api_secret = api_secret
    
    def get_usage(self) -> Dict[str, Any]:
        """Get usage statistics for the authenticated agent."""
        response = self._make_authenticated_request("GET", "/api/v1/agents/me/usage")
        response.raise_for_status()
        return response.json()
    
    def list_keys(self) -> Dict[str, Any]:
        """List API keys for the agent."""
        response = self._make_authenticated_request("GET", "/api/v1/agents/me/keys")
        response.raise_for_status()
        return response.json()
    
    def create_new_key(self) -> Dict[str, Any]:
        """Create a new API key (replaces existing)."""
        response = self._make_authenticated_request("POST", "/api/v1/agents/me/keys")
        response.raise_for_status()
        return response.json()
    
    def revoke_key(self) -> Dict[str, Any]:
        """Revoke API key (deactivates agent)."""
        response = self._make_authenticated_request("DELETE", "/api/v1/agents/me/keys")
        response.raise_for_status()
        return response.json()

# Example usage
if __name__ == "__main__":
    # Initialize client
    client = LiveTranslationAgentClient(base_url="http://localhost:8000")
    
    # Register a new agent (or set credentials if already registered)
    print("Registering new agent...")
    reg_result = client.register_agent(
        name="SupportCallTranscriber",
        description="Automatically transcribes and analyzes customer support calls"
    )
    
    print(f"Agent ID: {reg_result['agent_id']}")
    print(f"API Key: {reg_result['api_key']}")
    print(f"API Secret: {reg_result['api_secret']}  # STORE THIS SECURELY!")
    
    # Now use the agent
    print("\nGetting usage statistics...")
    usage = client.get_usage()
    print(f"Today's transcriptions: {usage['usage']['transcriptions_count']}")
    print(f"Today's translations: {usage['usage']['translations_count']}")
    
    # List keys
    print("\nListing API keys...")
    keys = client.list_keys()
    print(f"Key count: {len(keys['keys'])}")
    
    # Create new key (example - uncomment to use)
    # print("\nCreating new API key...")
    # new_key = client.create_new_key()
    # print(f"New API Key: {new_key['api_key']}")
    # print(f"New API Secret: {new_key['api_secret']}  # STORE THIS SECURELY!")
```

## Testing Your Implementation

### 1. Registration Test
```bash
curl -X POST http://localhost:8000/api/v1/agents/register \
  -H "Content-Type: application/json" \
  -d '{"name":"test-agent","description":"Test agent"}'
```
Verify you receive both `api_key` and `api_secret`.

### 2. Authentication Test
```bash
# Get current timestamp
TIMESTAMP=$(date +%s)
# Generate random nonce
NONCE=$(uuidgen)
# Create empty body for GET request
BODY=""
# Generate signature (replace with your actual secret)
SIGNATURE=$(echo -n "GET/api/v1/agents/me/usage${TIMESTAMP}${NONCE}${BODY}" | \
  openssl dgst -sha256 -hmac "YOUR_API_SECRET_HERE" | awk '{print $2}')

curl -X GET "http://localhost:8000/api/v1/agents/me/usage" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "X-Timestamp: ${TIMESTAMP}" \
  -H "X-Nonce: ${NONCE}" \
  -H "X-Signature: ${SIGNATURE}"
```

### 3. Rate Limit Test
Make 61 requests within 60 seconds - the 61st should return 429.

### 4. Usage Verification
Check the database directly:
```sql
-- After making requests as an agent
SELECT * FROM agent_usage WHERE agent_id = 'your-agent-id-here' ORDER BY timestamp DESC LIMIT 5;
```

## Troubleshooting Guide

### Common Authentication Issues
| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| 401 Invalid signature | Timestamp skew >5 min | Sync system clock with NTP |
| 401 Missing headers | Forgetting one of X-* headers | Verify all 4 headers present |
| 401 Invalid API key | Wrong key or typo | Double-check API key value |
| 401 Signature mismatch | Body not included in signature | Include body even if empty ("") |

### Rate Limiting (429)
- **Symptom:** Receiving 429 responses
- **Solution:** 
  1. Implement client-side throttling (max 60 req/min)
  2. Use exponential backoff for retries
  3. Consider upgrading subscription for higher limits
  4. Check if multiple instances are sharing credentials

### Payment Issues (402)
- **Symptom:** Receiving 402 when expecting service
- **Solution:**
  1. Verify you're handling payment flow correctly
  2. Check if subscription has lapsed (for subscription-only endpoints)
  3. Ensure you're using correct payment network/asset

### Service Unavailable (502/503)
- **Symptom:** Receiving 502/503 errors
- **Solution:**
  1. Implement retry with exponential backoff
  2. Check service status page if available
  3. Consider implementing circuit breaker pattern

## Next Steps

After mastering the agent API:
1. **Explore Service Endpoints:** Learn how to access transcription/translation via x402 or subscription
2. **Build Workflows:** Create agent pipelines that combine multiple service calls
3. **Implement Webhooks:** Set up webhook endpoints for asynchronous notifications
4. **Add Usage Optimization:** Build logic to minimize costs based on usage patterns
5. **Contribute:** Consider building open-source tools or extensions that enhance the platform

## Reference Materials

- **Full API Specification:** `docs/TECHNICAL_SPECIFICATION.md` in the project repository
- **x402 v2 Protocol:** https://x402.org/
- **HMAC-SHA256:** RFC 2104, https://en.wikipedia.org/wiki/Hash-based_message_authentication_code
- **Coinbase Facilitator:** Used for x402 payment settlement in this platform
- **Stripe Subscription Docs:** https://stripe.com/docs/billing/subscriptions

---
*This guide enables developers to securely and efficiently build agents and integrations with the Live Transcription & Translation Platform while respecting authentication requirements, rate limits, and payment systems.*