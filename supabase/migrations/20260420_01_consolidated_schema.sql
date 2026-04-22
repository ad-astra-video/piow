-- ============================================================
-- Consolidated Schema for Live Translation App
-- Merged from all migration files into a single schema
-- Date: 2026-04-20
--
-- Original migrations merged:
--   20260331203149_init_schema.sql
--   20260405_00_add_usage_tracking.sql (backend)
--   20260405_01_create_compute_providers_table.sql
--   20260415_01_enable_web3_auth.sql
--   20260415_02_sync_social_identities.sql
--   20260417_01_create_payment_tables.sql
--   20260418_01_create_session_tables.sql
--   20240115_01_create_agents_table.sql (backend - subscription_tier only)
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TABLES
-- ============================================================

-- Users table (consolidated with wallet_address, nullable name, provider)
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT NOT NULL UNIQUE,
  name TEXT,  -- nullable: OAuth users may not have a name initially
  avatar TEXT,
  ethereum_address TEXT UNIQUE,
  wallet_address TEXT UNIQUE,
  provider TEXT,  -- primary auth method: email, google, twitter, web3
  email_verified BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User preferences
CREATE TABLE user_preferences (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  transcription_language TEXT NOT NULL DEFAULT 'en',
  translation_language TEXT NOT NULL DEFAULT 'en',
  auto_translate BOOLEAN NOT NULL DEFAULT false,
  theme TEXT NOT NULL DEFAULT 'system',
  notifications BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Subscriptions (consolidated with trial columns)
CREATE TABLE subscriptions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  stripe_customer_id TEXT UNIQUE,
  stripe_subscription_id TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'trialing',
  plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'starter', 'pro', 'enterprise')),
  current_period_start TIMESTAMPTZ NOT NULL,
  current_period_end TIMESTAMPTZ NOT NULL,
  cancel_at_period_end BOOLEAN NOT NULL DEFAULT false,
  canceled_at TIMESTAMPTZ,
  trial_start TIMESTAMPTZ,
  trial_end TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transactions (with crypto support)
CREATE TABLE transactions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id),
  stripe_payment_id TEXT NOT NULL UNIQUE,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'usd',
  status TEXT NOT NULL,
  type TEXT NOT NULL,
  payment_method TEXT CHECK (payment_method IN ('card', 'crypto')),
  crypto_currency TEXT CHECK (crypto_currency IN ('usdc', 'btc', 'eth')),
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transcriptions (PRIMARY offering)
CREATE TABLE transcriptions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  audio_url TEXT NOT NULL,
  text TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'en',
  duration INTEGER NOT NULL DEFAULT 0, -- seconds
  word_count INTEGER NOT NULL DEFAULT 0,
  segments JSONB,
  status TEXT NOT NULL DEFAULT 'processing',
  source_type TEXT CHECK (source_type IN ('upload', 'recording', 'stream', 'whip')),
  model_used TEXT CHECK (model_used IN ('granite-4.0-1b', 'voxtral-realtime')),
  hardware TEXT CHECK (hardware IN ('cpu', 'gpu')),
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Translations (SECONDARY offering)
CREATE TABLE translations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transcription_id UUID REFERENCES transcriptions(id) ON DELETE CASCADE,
  original_text TEXT NOT NULL,
  translated_text TEXT NOT NULL,
  source_language TEXT NOT NULL,
  target_language TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'text',
  token_count INTEGER NOT NULL DEFAULT 0,
  model_used TEXT CHECK (model_used IN ('granite-4.0-1b', 'voxtral-realtime')),
  hardware TEXT CHECK (hardware IN ('cpu', 'gpu')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transcription Usage Tracking (Rolling 30-day window)
CREATE TABLE transcription_usage (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  duration_seconds INTEGER NOT NULL,
  word_count INTEGER NOT NULL DEFAULT 0,
  source_language TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT 'granite-4.0-1b',
  hardware TEXT NOT NULL DEFAULT 'cpu' CHECK (hardware IN ('cpu', 'gpu')),
  source_type TEXT NOT NULL DEFAULT 'upload' CHECK (source_type IN ('upload', 'recording', 'stream', 'whip')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Translation Usage Tracking (Rolling 30-day window)
CREATE TABLE translation_usage (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  characters_translated INTEGER NOT NULL,
  source_language TEXT NOT NULL,
  target_language TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT 'granite-4.0-1b',
  hardware TEXT NOT NULL DEFAULT 'cpu',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agents (for API access, consolidated with subscription_tier)
CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  owner_email TEXT NOT NULL,
  subscription_tier VARCHAR(50) NOT NULL DEFAULT 'free',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- API Keys
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL,
  permissions TEXT[] DEFAULT '{transcribe:read,translate:read}',
  rate_limit INTEGER NOT NULL DEFAULT 100,
  daily_quota INTEGER NOT NULL DEFAULT 1000,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agent Usage Tracking (consolidated with crypto payment columns)
CREATE TABLE agent_usage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
  endpoint TEXT NOT NULL,
  method TEXT NOT NULL,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  success BOOLEAN NOT NULL,
  cost_usdc_cents INTEGER,
  subscription_tier TEXT,
  metadata JSONB,
  agent_wallet TEXT,
  amount_paid NUMERIC,
  asset TEXT,
  transaction_hash TEXT
);

-- SIWE nonces
CREATE TABLE siwe_nonces (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  address TEXT NOT NULL,
  nonce TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Compute providers
CREATE TABLE compute_providers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) UNIQUE NOT NULL,
  type VARCHAR(50) NOT NULL,  -- 'livepeer', 'aws', 'gcp', 'custom'
  enabled BOOLEAN DEFAULT TRUE,
  config JSONB,  -- Provider-specific configuration (URLs, keys, etc.)
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Payment methods linked to users
CREATE TABLE payment_methods (
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

-- x402 payments table (aligned with X402_PAYMENTS.md spec)
CREATE TABLE x402_payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_wallet TEXT NOT NULL,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
  resource_url TEXT NOT NULL,
  amount NUMERIC NOT NULL,
  asset TEXT NOT NULL,
  network TEXT NOT NULL,
  scheme TEXT NOT NULL DEFAULT 'exact',
  service_type TEXT NOT NULL CHECK (service_type IN ('transcribe_cpu', 'transcribe_gpu', 'translate', 'subscription_starter', 'subscription_pro', 'subscription_enterprise')),
  transaction_hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'settled', 'failed')),
  payment_payload JSONB,
  verification_result JSONB,
  settlement_result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  verified_at TIMESTAMPTZ,
  settled_at TIMESTAMPTZ
);

-- Stripe webhook event deduplication table
CREATE TABLE stripe_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  stripe_event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User sessions table (database-backed session storage)
CREATE TABLE user_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  settings JSONB NOT NULL DEFAULT '{"default_language": "en", "translate_to": []}'::jsonb,
  transcription_ids UUID[] DEFAULT '{}',
  stream_session_ids UUID[] DEFAULT '{}'
);

-- Stream sessions table (provider session data as JSONB)
CREATE TABLE stream_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_session_id UUID REFERENCES user_sessions(id) ON DELETE CASCADE,
  language TEXT NOT NULL DEFAULT 'en',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'stopped', 'error')),
  provider_session JSONB NOT NULL DEFAULT '{}'::jsonb,
  total_audio_bytes BIGINT NOT NULL DEFAULT 0,
  transcription_segments JSONB DEFAULT '[]'::jsonb,
  final_text TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transcription sessions table (batch transcription job tracking)
CREATE TABLE transcription_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_session_id UUID REFERENCES user_sessions(id) ON DELETE CASCADE,
  filename TEXT NOT NULL DEFAULT 'unknown',
  duration REAL NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT 'en',
  status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'completed', 'failed')),
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================

-- Users
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_ethereum_address ON users(ethereum_address);
CREATE INDEX idx_users_wallet_address ON users(wallet_address);

-- Subscriptions
CREATE INDEX idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_status ON subscriptions(status);

-- Transactions
CREATE INDEX idx_transactions_user_id ON transactions(user_id);

-- Transcriptions
CREATE INDEX idx_transcriptions_user_id ON transcriptions(user_id);
CREATE INDEX idx_transcriptions_created_at ON transcriptions(created_at);
CREATE INDEX idx_transcriptions_model_used ON transcriptions(model_used);

-- Translations
CREATE INDEX idx_translations_user_id ON translations(user_id);
CREATE INDEX idx_translations_transcription_id ON translations(transcription_id);

-- Transcription usage
CREATE INDEX idx_transcription_usage_user_id ON transcription_usage(user_id);
CREATE INDEX idx_transcription_usage_created_at ON transcription_usage(created_at);
CREATE INDEX idx_transcription_usage_user_date ON transcription_usage(user_id, created_at);

-- Translation usage
CREATE INDEX idx_translation_usage_user_id ON translation_usage(user_id);
CREATE INDEX idx_translation_usage_created_at ON translation_usage(created_at);
CREATE INDEX idx_translation_usage_user_date ON translation_usage(user_id, created_at);

-- Agents
CREATE INDEX idx_agents_owner_email ON agents(owner_email);

-- API Keys
CREATE INDEX idx_api_keys_agent_id ON api_keys(agent_id);

-- SIWE nonces
CREATE INDEX idx_siwe_nonces_address ON siwe_nonces(address);

-- Agent usage
CREATE INDEX idx_agent_usage_agent_id ON agent_usage(agent_id);
CREATE INDEX idx_agent_usage_timestamp ON agent_usage(timestamp);
CREATE INDEX idx_agent_usage_agent_id_timestamp ON agent_usage(agent_id, timestamp);
CREATE INDEX idx_agent_usage_agent_wallet ON agent_usage(agent_wallet);

-- Compute providers
CREATE INDEX idx_compute_providers_name ON compute_providers(name);
CREATE INDEX idx_compute_providers_type ON compute_providers(type);
CREATE INDEX idx_compute_providers_enabled ON compute_providers(enabled);

-- Payment methods
CREATE INDEX idx_payment_methods_user_id ON payment_methods(user_id);
CREATE INDEX idx_payment_methods_stripe_customer ON payment_methods(stripe_customer_id);

-- x402 payments
CREATE INDEX idx_x402_payments_agent_wallet ON x402_payments(agent_wallet);
CREATE INDEX idx_x402_payments_resource_url ON x402_payments(resource_url);
CREATE INDEX idx_x402_payments_transaction_hash ON x402_payments(transaction_hash);
CREATE INDEX idx_x402_payments_status ON x402_payments(status);
CREATE INDEX idx_x402_payments_created ON x402_payments(created_at);
CREATE INDEX idx_x402_payments_user_id ON x402_payments(user_id);
CREATE INDEX idx_x402_payments_agent_id ON x402_payments(agent_id);

-- Stripe events
CREATE INDEX idx_stripe_events_stripe_event_id ON stripe_events(stripe_event_id);

-- User sessions
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_last_activity ON user_sessions(last_activity);

-- Stream sessions
CREATE INDEX idx_stream_sessions_user_session_id ON stream_sessions(user_session_id);
CREATE INDEX idx_stream_sessions_status ON stream_sessions(status);
CREATE INDEX idx_stream_sessions_created_at ON stream_sessions(created_at);

-- Transcription sessions
CREATE INDEX idx_transcription_sessions_user_session_id ON transcription_sessions(user_session_id);
CREATE INDEX idx_transcription_sessions_status ON transcription_sessions(status);
CREATE INDEX idx_transcription_sessions_created_at ON transcription_sessions(created_at);

-- ============================================================
-- SEED DATA
-- ============================================================

-- Insert default Livepeer provider configuration
INSERT INTO compute_providers (name, type, enabled, config) VALUES (
    'livepeer',
    'livepeer',
    TRUE,
    jsonb_build_object(
        'gpu_runner_url', 'http://localhost:9935',
        'facilitator_url', 'https://x402.org/facilitator',
        'platform_wallet', '0xYourPlatformWallet'
    )
)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================

-- Auto-update updated_at / last_activity timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Sync user identities from auth.users to public.users
-- Handles all provider types: web3, google, twitter, email, etc.
CREATE OR REPLACE FUNCTION public.sync_user_identities()
RETURNS TRIGGER AS $$
DECLARE
  v_wallet_address TEXT;
  v_avatar_url     TEXT;
  v_full_name      TEXT;
  v_email          TEXT;
  v_provider       TEXT;
  v_identity       RECORD;
BEGIN
  -- Derive the best available profile data from the user's identities.
  -- Priority: first identity that provides the field wins.
  v_wallet_address := NULL;
  v_avatar_url     := NULL;
  v_full_name      := NULL;
  v_email          := NEW.email;
  v_provider       := 'email';  -- default for email/password users

  FOR v_identity IN
    SELECT provider, identity_data
    FROM auth.identities
    WHERE user_id = NEW.id
    ORDER BY created_at ASC
  LOOP
    -- Use the first provider as the "primary" provider
    IF v_provider = 'email' AND v_identity.provider != 'email' THEN
      v_provider := v_identity.provider;
    END IF;

    -- Wallet address (web3 provider)
    IF v_wallet_address IS NULL AND v_identity.identity_data->>'wallet_address' IS NOT NULL THEN
      v_wallet_address := v_identity.identity_data->>'wallet_address';
    END IF;

    -- Avatar URL (social providers)
    IF v_avatar_url IS NULL THEN
      IF v_identity.identity_data->>'avatar_url' IS NOT NULL THEN
        v_avatar_url := v_identity.identity_data->>'avatar_url';
      ELSIF v_identity.identity_data->>'picture' IS NOT NULL THEN
        v_avatar_url := v_identity.identity_data->>'picture';
      END IF;
    END IF;

    -- Full name (social providers)
    IF v_full_name IS NULL THEN
      IF v_identity.identity_data->>'full_name' IS NOT NULL THEN
        v_full_name := v_identity.identity_data->>'full_name';
      ELSIF v_identity.identity_data->>'name' IS NOT NULL THEN
        v_full_name := v_identity.identity_data->>'name';
      END IF;
    END IF;
  END LOOP;

  -- If no social identity provided a name, fall back to user_metadata
  IF v_full_name IS NULL THEN
    v_full_name := NEW.raw_user_meta_data->>'full_name';
    IF v_full_name IS NULL THEN
      v_full_name := NEW.raw_user_meta_data->>'name';
    END IF;
  END IF;

  -- If no social identity provided an avatar, fall back to user_metadata
  IF v_avatar_url IS NULL THEN
    v_avatar_url := NEW.raw_user_meta_data->>'avatar_url';
    IF v_avatar_url IS NULL THEN
      v_avatar_url := NEW.raw_user_meta_data->>'picture';
    END IF;
  END IF;

  -- Upsert into public.users
  IF TG_OP = 'INSERT' THEN
    INSERT INTO public.users (id, email, name, avatar, wallet_address, provider, created_at, updated_at)
    VALUES (
      NEW.id,
      COALESCE(v_email, NEW.email),
      v_full_name,
      v_avatar_url,
      v_wallet_address,
      v_provider,
      NOW(),
      NOW()
    )
    ON CONFLICT (id) DO UPDATE SET
      email         = EXCLUDED.email,
      name          = COALESCE(EXCLUDED.name, public.users.name),
      avatar        = COALESCE(EXCLUDED.avatar, public.users.avatar),
      wallet_address = COALESCE(EXCLUDED.wallet_address, public.users.wallet_address),
      provider      = EXCLUDED.provider,
      updated_at    = NOW();
  ELSIF TG_OP = 'UPDATE' THEN
    UPDATE public.users
    SET
      email          = COALESCE(v_email, NEW.email),
      name           = COALESCE(v_full_name, public.users.name),
      avatar         = COALESCE(v_avatar_url, public.users.avatar),
      wallet_address = COALESCE(v_wallet_address, public.users.wallet_address),
      provider       = v_provider,
      updated_at     = NOW()
    WHERE id = NEW.id;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger: sync user identities on auth user changes
CREATE TRIGGER on_auth_user_changed
  AFTER INSERT OR UPDATE ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.sync_user_identities();

-- Trigger: auto-update updated_at for stream_sessions
CREATE TRIGGER update_stream_sessions_updated_at
    BEFORE UPDATE ON stream_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger: auto-update updated_at for transcription_sessions
CREATE TRIGGER update_transcription_sessions_updated_at
    BEFORE UPDATE ON transcription_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger: auto-update last_activity for user_sessions
CREATE TRIGGER update_user_sessions_last_activity
    BEFORE UPDATE ON user_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================
-- RLS is enabled by default in Supabase. We explicitly enable it
-- on all tables and define policies for service_role and user access.

-- Enable RLS on all tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE translations ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcription_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE translation_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE siwe_nonces ENABLE ROW LEVEL SECURITY;
ALTER TABLE compute_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_methods ENABLE ROW LEVEL SECURITY;
ALTER TABLE x402_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE stripe_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE stream_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcription_sessions ENABLE ROW LEVEL SECURITY;

-- ─── Users ────────────────────────────────────────────────────
CREATE POLICY "Service role can manage users" ON users
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own profile" ON users
  FOR SELECT USING (id = auth.uid());

CREATE POLICY "Users can update own profile" ON users
  FOR UPDATE USING (id = auth.uid());

-- ─── User Preferences ────────────────────────────────────────
CREATE POLICY "Service role can manage user_preferences" ON user_preferences
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own preferences" ON user_preferences
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can update own preferences" ON user_preferences
  FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Users can insert own preferences" ON user_preferences
  FOR INSERT WITH CHECK (user_id = auth.uid());

-- ─── Subscriptions ───────────────────────────────────────────
CREATE POLICY "Service role can manage subscriptions" ON subscriptions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own subscriptions" ON subscriptions
  FOR SELECT USING (user_id = auth.uid());

-- ─── Transactions ────────────────────────────────────────────
CREATE POLICY "Service role can manage transactions" ON transactions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own transactions" ON transactions
  FOR SELECT USING (user_id = auth.uid());

-- ─── Transcriptions ──────────────────────────────────────────
CREATE POLICY "Service role can manage transcriptions" ON transcriptions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own transcriptions" ON transcriptions
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert own transcriptions" ON transcriptions
  FOR INSERT WITH CHECK (user_id = auth.uid());

-- ─── Translations ────────────────────────────────────────────
CREATE POLICY "Service role can manage translations" ON translations
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own translations" ON translations
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert own translations" ON translations
  FOR INSERT WITH CHECK (user_id = auth.uid());

-- ─── Transcription Usage ─────────────────────────────────────
CREATE POLICY "Service role can manage transcription_usage" ON transcription_usage
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own transcription_usage" ON transcription_usage
  FOR SELECT USING (user_id = auth.uid());

-- ─── Translation Usage ───────────────────────────────────────
CREATE POLICY "Service role can manage translation_usage" ON translation_usage
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own translation_usage" ON translation_usage
  FOR SELECT USING (user_id = auth.uid());

-- ─── Agents ──────────────────────────────────────────────────
CREATE POLICY "Service role can manage agents" ON agents
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own agents" ON agents
  FOR SELECT USING (owner_email = auth.jwt()->>'email');

-- ─── API Keys ────────────────────────────────────────────────
CREATE POLICY "Service role can manage api_keys" ON api_keys
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- ─── Agent Usage ─────────────────────────────────────────────
CREATE POLICY "Service role can manage agent_usage" ON agent_usage
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- ─── SIWE Nonces ─────────────────────────────────────────────
CREATE POLICY "Service role can manage siwe_nonces" ON siwe_nonces
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- ─── Compute Providers ───────────────────────────────────────
CREATE POLICY "Service role can manage compute_providers" ON compute_providers
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Anyone can view compute_providers" ON compute_providers
  FOR SELECT USING (true);

-- ─── Payment Methods ─────────────────────────────────────────
CREATE POLICY "Service role can manage payment_methods" ON payment_methods
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own payment_methods" ON payment_methods
  FOR SELECT USING (user_id = auth.uid());

-- ─── x402 Payments ───────────────────────────────────────────
CREATE POLICY "Service role can manage x402_payments" ON x402_payments
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Agents can view own x402_payments" ON x402_payments
  FOR SELECT USING (agent_wallet = auth.jwt()->>'wallet_address');

CREATE POLICY "Users can view own x402_payments" ON x402_payments
  FOR SELECT USING (user_id = auth.uid());

-- ─── Stripe Events ───────────────────────────────────────────
CREATE POLICY "Service role can manage stripe_events" ON stripe_events
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- ─── User Sessions ───────────────────────────────────────────
CREATE POLICY "Service role can manage user_sessions" ON user_sessions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can manage own sessions" ON user_sessions
  FOR ALL USING (user_id = auth.uid());

-- ─── Stream Sessions ─────────────────────────────────────────
CREATE POLICY "Service role can manage stream_sessions" ON stream_sessions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can manage own stream_sessions" ON stream_sessions
  FOR ALL USING (user_session_id IN (SELECT id FROM user_sessions WHERE user_id = auth.uid()));

-- ─── Transcription Sessions ──────────────────────────────────
CREATE POLICY "Service role can manage transcription_sessions" ON transcription_sessions
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can manage own transcription_sessions" ON transcription_sessions
  FOR ALL USING (user_session_id IN (SELECT id FROM user_sessions WHERE user_id = auth.uid()));