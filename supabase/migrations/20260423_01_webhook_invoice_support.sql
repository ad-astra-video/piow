-- ============================================================
-- Migration: Webhook Invoice Support
-- Date: 2026-04-23
--
-- Adds stripe_invoice_id to transactions table to support
-- recording subscription invoice payments from Stripe webhooks.
-- Also adds index on subscriptions.stripe_subscription_id for
-- faster webhook lookups.
-- ============================================================

-- Make stripe_payment_id nullable so we can record invoice-based
-- transactions where payment_intent may not be immediately available
ALTER TABLE transactions ALTER COLUMN stripe_payment_id DROP NOT NULL;

-- Add stripe_invoice_id for tracking invoice payments
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS stripe_invoice_id TEXT;

-- Add stripe_subscription_id for linking transactions to subscriptions
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;

-- Unique constraint on stripe_invoice_id to prevent duplicate invoice records
CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_stripe_invoice_id 
  ON transactions(stripe_invoice_id) 
  WHERE stripe_invoice_id IS NOT NULL;

-- Index for webhook lookups by subscription ID
CREATE INDEX IF NOT EXISTS idx_transactions_stripe_subscription_id 
  ON transactions(stripe_subscription_id);

-- Index on subscriptions for faster webhook lookups
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_subscription_id 
  ON subscriptions(stripe_subscription_id);
