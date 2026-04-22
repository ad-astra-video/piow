/**
 * Supabase client for the Live Translation App.
 *
 * Environment variables (set in .env or host env):
 *   VITE_SUPABASE_URL             – e.g. https://your-project.supabase.co
 *   VITE_SUPABASE_PUBLISHABLE_KEY – the public publishable key (enforces RLS)
 *
 * NOTE: The frontend MUST use the publishable key (not the secret/service role key)
 * so that Row Level Security policies are enforced. The secret key bypasses RLS
 * and should only be used on the backend.
 *
 * Auth providers that must be enabled in the Supabase dashboard
 * under Authentication → Providers:
 *   - Email (enabled by default)
 *   - Google OAuth
 *   - Twitter OAuth
 *   - Web3 Wallet (SIWE)
 */

import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabasePublishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || import.meta.env.VITE_SUPABASE_ANON_KEY; // legacy fallback

if (!supabaseUrl || !supabasePublishableKey) {
  console.warn(
    '[supabase] VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY must be set ' +
    'for authentication to work. App will run without auth.'
  );
}

/**
 * Singleton Supabase client.  Falls back to null when env vars
 * are missing so the rest of the app can still function (just without auth).
 */
export const supabase =
  supabaseUrl && supabasePublishableKey
    ? createClient(supabaseUrl, supabasePublishableKey)
    : null;

/* ─── Helper ─────────────────────────────────────────────────────────────── */

/**
 * Normalise the Supabase auth response into a consistent shape.
 * @param {{ data: object, error: object|null }} result
 * @returns {{ user: object|null, session: object|null, error: Error|null }}
 */
function normaliseAuthResult({ data, error }) {
  if (error) {
    return { user: null, session: null, error };
  }
  return {
    user: data.user ?? null,
    session: data.session ?? null,
    error: null,
  };
}

const NOT_CONFIGURED = { user: null, session: null, error: new Error('Supabase is not configured') };

/* ─── Email / Password ───────────────────────────────────────────────────── */

/**
 * Sign up with email and password.
 *
 * Supabase sends a confirmation email by default (configurable in dashboard).
 * The user won't be able to sign in until they confirm unless auto-confirm is on.
 *
 * @param {object} options
 * @param {string} options.email
 * @param {string} options.password
 * @param {string} [options.name] – Optional display name stored in user_metadata
 * @returns {Promise<{ user: object|null, session: object|null, error: Error|null }>}
 */
export async function signUpWithEmail({ email, password, name } = {}) {
  if (!supabase) return NOT_CONFIGURED;

  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    ...(name ? { options: { data: { full_name: name } } } : {}),
  });

  return normaliseAuthResult({ data, error });
}

/**
 * Sign in with email and password.
 *
 * @param {object} options
 * @param {string} options.email
 * @param {string} options.password
 * @returns {Promise<{ user: object|null, session: object|null, error: Error|null }>}
 */
export async function signInWithEmail({ email, password } = {}) {
  if (!supabase) return NOT_CONFIGURED;

  const { data, error } = await supabase.auth.signInWithPassword({ email, password });

  return normaliseAuthResult({ data, error });
}

/* ─── Social OAuth ───────────────────────────────────────────────────────── */

/**
 * Sign in with Google via Supabase OAuth.
 *
 * Opens a popup / redirect to Google's consent screen. On success the browser
 * is redirected back and `onAuthStateChange` fires with the new session.
 *
 * @returns {Promise<{ user: object|null, session: object|null, error: Error|null }>}
 */
export async function signInWithGoogle() {
  if (!supabase) return NOT_CONFIGURED;

  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: window.location.origin,
    },
  });

  // OAuth redirects the browser — data is the URL string, not a session.
  // The actual session arrives via onAuthStateChange after redirect.
  if (error) {
    return { user: null, session: null, error };
  }

  return { user: null, session: null, error: null };
}

/**
 * Sign in with Twitter/X via Supabase OAuth.
 *
 * @returns {Promise<{ user: object|null, session: object|null, error: Error|null }>}
 */
export async function signInWithTwitter() {
  if (!supabase) return NOT_CONFIGURED;

  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: 'twitter',
    options: {
      redirectTo: window.location.origin,
    },
  });

  if (error) {
    return { user: null, session: null, error };
  }

  return { user: null, session: null, error: null };
}

/* ─── Web3 / SIWE ────────────────────────────────────────────────────────── */

/**
 * Sign in with an Ethereum wallet via Supabase's built-in Web3 auth.
 *
 * Supabase handles the full SIWE flow:
 *   1. Generates an EIP-4361 message
 *   2. Prompts the wallet to sign it (window.ethereum)
 *   3. Verifies the signature server-side
 *   4. Returns a session with access_token / refresh_token
 *
 * @param {object} [options]
 * @param {string} [options.statement] – Optional TOS statement shown in the wallet prompt
 * @returns {Promise<{ user: object|null, session: object|null, error: Error|null }>}
 */
export async function signInWithEthereum({ statement } = {}) {
  if (!supabase) return NOT_CONFIGURED;

  if (!window.ethereum) {
    return {
      user: null,
      session: null,
      error: new Error('No Ethereum wallet detected. Please install MetaMask or another Web3 wallet.'),
    };
  }

  const { data, error } = await supabase.auth.signInWithWeb3({
    chain: 'ethereum',
    ...(statement ? { statement } : {}),
  });

  return normaliseAuthResult({ data, error });
}

/* ─── Session helpers ────────────────────────────────────────────────────── */

/**
 * Sign out the current Supabase session.
 */
export async function signOut() {
  if (!supabase) return;
  await supabase.auth.signOut();
}

/**
 * Get the current Supabase session (null if not signed in).
 */
export async function getSession() {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session;
}

/**
 * Get the current Supabase user (null if not signed in).
 */
export async function getUser() {
  if (!supabase) return null;
  const { data } = await supabase.auth.getUser();
  return data.user;
}

/**
 * Subscribe to auth state changes.
 * @param {function} callback – Called with (event, session)
 * @returns {function} Unsubscribe function
 */
export function onAuthStateChange(callback) {
  if (!supabase) {
    return () => {};
  }
  const { data } = supabase.auth.onAuthStateChange(callback);
  return () => data.subscription.unsubscribe();
}