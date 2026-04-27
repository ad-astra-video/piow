-- ============================================================
-- Retire legacy agent_usage table
-- Date: 2026-04-27
--
-- This migration assumes api_usage exists.
-- It performs a final idempotent sync for agent rows, then drops
-- the legacy table now that all reads/writes are on api_usage.
-- ============================================================

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

DROP TABLE IF EXISTS agent_usage;