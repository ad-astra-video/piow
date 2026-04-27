-- ============================================================
-- Unified API usage tracking for both user and agent identities
-- Date: 2026-04-27
--
-- Migration strategy:
-- 1) Create new api_usage table with explicit actor columns.
-- 2) Backfill historical rows from legacy agent_usage.
-- 3) Keep legacy table for compatibility during rollout.
-- ============================================================

CREATE TABLE IF NOT EXISTS api_usage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_type TEXT NOT NULL CHECK (actor_type IN ('agent', 'user')),
  agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  endpoint TEXT NOT NULL,
  method TEXT NOT NULL,
  "timestamp" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  success BOOLEAN NOT NULL,
  cost_usdc_cents INTEGER,
  subscription_tier TEXT,
  metadata JSONB,
  agent_wallet TEXT,
  amount_paid NUMERIC,
  asset TEXT,
  transaction_hash TEXT,
  CONSTRAINT api_usage_actor_identity_check CHECK (
    (actor_type = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL)
    OR
    (actor_type = 'user' AND user_id IS NOT NULL AND agent_id IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_api_usage_agent_time
  ON api_usage (actor_type, agent_id, "timestamp")
  WHERE actor_type = 'agent';

CREATE INDEX IF NOT EXISTS idx_api_usage_user_time
  ON api_usage (actor_type, user_id, "timestamp")
  WHERE actor_type = 'user';

CREATE INDEX IF NOT EXISTS idx_api_usage_endpoint
  ON api_usage (endpoint);

INSERT INTO api_usage (
  id,
  actor_type,
  agent_id,
  user_id,
  endpoint,
  method,
  "timestamp",
  success,
  cost_usdc_cents,
  subscription_tier,
  metadata,
  agent_wallet,
  amount_paid,
  asset,
  transaction_hash
)
SELECT
  au.id,
  'agent' AS actor_type,
  au.agent_id,
  NULL::UUID AS user_id,
  au.endpoint,
  au.method,
  au."timestamp",
  au.success,
  au.cost_usdc_cents,
  au.subscription_tier,
  au.metadata,
  au.agent_wallet,
  au.amount_paid,
  au.asset,
  au.transaction_hash
FROM agent_usage au
WHERE au.agent_id IS NOT NULL
ON CONFLICT (id) DO NOTHING;