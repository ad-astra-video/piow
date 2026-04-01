-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users table
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  avatar TEXT,
  ethereum_address TEXT UNIQUE,
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

-- Subscriptions
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

CREATE INDEX idx_transcription_usage_user_id ON transcription_usage(user_id);
CREATE INDEX idx_transcription_usage_created_at ON transcription_usage(created_at);
CREATE INDEX idx_transcription_usage_user_date ON transcription_usage(user_id, created_at);

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

CREATE INDEX idx_translation_usage_user_id ON translation_usage(user_id);
CREATE INDEX idx_translation_usage_created_at ON translation_usage(created_at);
CREATE INDEX idx_translation_usage_user_date ON translation_usage(user_id, created_at);

-- Agents (for API access)
CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  owner_email TEXT NOT NULL,
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

-- SIWE nonces
CREATE TABLE siwe_nonces (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  address TEXT NOT NULL,
  nonce TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_ethereum_address ON users(ethereum_address);
CREATE INDEX idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_status ON subscriptions(status);
CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transcriptions_user_id ON transcriptions(user_id);
CREATE INDEX idx_transcriptions_created_at ON transcriptions(created_at);
CREATE INDEX idx_transcriptions_model_used ON transcriptions(model_used);
CREATE INDEX idx_translations_user_id ON translations(user_id);
CREATE INDEX idx_translations_transcription_id ON translations(transcription_id);
CREATE INDEX idx_agents_owner_email ON agents(owner_email);
CREATE INDEX idx_api_keys_agent_id ON api_keys(agent_id);
CREATE INDEX idx_siwe_nonces_address ON siwe_nonces(address);