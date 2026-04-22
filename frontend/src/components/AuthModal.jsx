import React, { useState } from 'react';
import {
  signInWithEthereum,
  signInWithEmail,
  signUpWithEmail,
  signInWithGoogle,
  signInWithTwitter,
  signOut,
} from '../lib/supabase';

/**
 * AuthModal – Unified authentication modal supporting:
 *   - Google OAuth
 *   - Twitter/X OAuth
 *   - Email + password (sign in / sign up)
 *   - Ethereum wallet (SIWE)
 *
 * Props:
 *   user     – current Supabase user object (null if not signed in)
 *   onAuth   – callback({ user, session, error }) fired after sign-in attempt
 *   onLogout – callback() fired after sign-out
 *   open     – boolean controlling modal visibility
 *   onClose  – callback to close the modal
 */
export default function AuthModal({ user, onAuth, onLogout, open, onClose }) {
  const [mode, setMode] = useState('login'); // 'login' | 'signup'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(null); // null | 'google' | 'twitter' | 'email' | 'wallet'
  const [error, setError] = useState(null);
  const [signupSuccess, setSignupSuccess] = useState(false);

  // ── Helpers ──────────────────────────────────────────────────────────────

  /** Derive a display label from the user's identities / metadata */
  function getUserLabel() {
    if (!user) return '';
    const identities = user.identities || [];

    // Check for social providers
    for (const id of identities) {
      const data = id.identity_data || {};
      if (data.full_name) return data.full_name;
      if (data.name) return data.name;
      if (data.email) return data.email;
    }

    // Fallback to user metadata
    if (user.user_metadata?.full_name) return user.user_metadata.full_name;
    if (user.user_metadata?.name) return user.user_metadata.name;
    if (user.email) return user.email;

    // Wallet address
    const wallet =
      user.user_metadata?.wallet_address ||
      identities.find((i) => i.provider === 'web3')?.identity_data?.wallet_address;
    if (wallet) return `${wallet.slice(0, 6)}…${wallet.slice(-4)}`;

    return user.id.slice(0, 8);
  }

  /** Derive avatar URL from identities */
  function getAvatarUrl() {
    if (!user) return null;
    const identities = user.identities || [];
    for (const id of identities) {
      const data = id.identity_data || {};
      if (data.avatar_url) return data.avatar_url;
      if (data.picture) return data.picture;
    }
    if (user.user_metadata?.avatar_url) return user.user_metadata.avatar_url;
    if (user.user_metadata?.picture) return user.user_metadata.picture;
    return null;
  }

  /** Get list of provider names from identities */
  function getProviders() {
    if (!user) return [];
    return (user.identities || []).map((i) => i.provider);
  }

  // ── Handlers ─────────────────────────────────────────────────────────────

  async function handleGoogle() {
    setLoading('google');
    setError(null);
    try {
      const result = await signInWithGoogle();
      // OAuth redirects the browser, so onAuth won't fire here —
      // onAuthStateChange in App.jsx handles the session after redirect.
      if (result.error) {
        setError(result.error.message || 'Google sign-in failed');
      }
      if (onAuth) onAuth(result);
    } catch (err) {
      setError(err?.message || 'Unexpected error during Google sign-in');
    } finally {
      setLoading(null);
    }
  }

  async function handleTwitter() {
    setLoading('twitter');
    setError(null);
    try {
      const result = await signInWithTwitter();
      if (result.error) {
        setError(result.error.message || 'Twitter sign-in failed');
      }
      if (onAuth) onAuth(result);
    } catch (err) {
      setError(err?.message || 'Unexpected error during Twitter sign-in');
    } finally {
      setLoading(null);
    }
  }

  async function handleEmail(e) {
    e.preventDefault();
    setLoading('email');
    setError(null);
    setSignupSuccess(false);

    try {
      let result;
      if (mode === 'signup') {
        result = await signUpWithEmail({ email, password, name });
        if (!result.error) {
          setSignupSuccess(true);
        }
      } else {
        result = await signInWithEmail({ email, password });
      }
      if (result.error) {
        setError(result.error.message || `${mode === 'signup' ? 'Sign-up' : 'Sign-in'} failed`);
      }
      if (onAuth) onAuth(result);
    } catch (err) {
      setError(err?.message || 'Unexpected error');
    } finally {
      setLoading(null);
    }
  }

  async function handleWallet() {
    setLoading('wallet');
    setError(null);
    try {
      const result = await signInWithEthereum({
        statement: 'I accept the Live Transcript Studio Terms of Service: https://livetranscript.studio/tos',
      });
      if (result.error) {
        setError(result.error.message || 'Wallet sign-in failed');
      }
      if (onAuth) onAuth(result);
    } catch (err) {
      setError(err?.message || 'Unexpected error during wallet sign-in');
    } finally {
      setLoading(null);
    }
  }

  async function handleSignOut() {
    try {
      await signOut();
    } catch (e) {
      console.warn('Sign-out error:', e);
    }
    if (onLogout) onLogout();
  }

  // ── Render ───────────────────────────────────────────────────────────────

  if (!open) return null;

  // ── Authenticated state ──
  if (user) {
    const avatarUrl = getAvatarUrl();
    const label = getUserLabel();
    const providers = getProviders();

    return (
      <div className="auth-modal-overlay" onClick={onClose}>
        <div className="auth-modal" onClick={(e) => e.stopPropagation()}>
          <button className="auth-modal-close" onClick={onClose} aria-label="Close">✕</button>

          <div className="auth-user-info">
            {avatarUrl ? (
              <img className="auth-avatar" src={avatarUrl} alt={label} />
            ) : (
              <div className="auth-avatar-placeholder">
                {label.charAt(0).toUpperCase()}
              </div>
            )}
            <div className="auth-user-details">
              <span className="auth-user-name">{label}</span>
              {user.email && <span className="auth-user-email">{user.email}</span>}
              <div className="auth-providers">
                {providers.map((p) => (
                  <span key={p} className={`auth-provider-badge auth-provider-${p}`}>{p}</span>
                ))}
              </div>
            </div>
          </div>

          <button className="auth-signout-btn" onClick={handleSignOut} disabled={loading}>
            Sign Out
          </button>
        </div>
      </div>
    );
  }

  // ── Unauthenticated state ──
  return (
    <div className="auth-modal-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={(e) => e.stopPropagation()}>
        <button className="auth-modal-close" onClick={onClose} aria-label="Close">✕</button>

        <div className="auth-modal-header">
          <h2 className="auth-modal-title">Sign In</h2>
          <p className="auth-modal-subtitle">Choose a method to continue</p>
        </div>

        {/* ── Social OAuth buttons ── */}
        <div className="auth-social-buttons">
          <button
            className="auth-social-btn auth-social-google"
            onClick={handleGoogle}
            disabled={loading !== null}
          >
            {loading === 'google' ? (
              <span className="auth-btn-loading">Connecting…</span>
            ) : (
              <>
                <svg className="auth-social-icon" viewBox="0 0 24 24" width="18" height="18">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Continue with Google
              </>
            )}
          </button>

          <button
            className="auth-social-btn auth-social-twitter"
            onClick={handleTwitter}
            disabled={loading !== null}
          >
            {loading === 'twitter' ? (
              <span className="auth-btn-loading">Connecting…</span>
            ) : (
              <>
                <svg className="auth-social-icon" viewBox="0 0 24 24" width="18" height="18">
                  <path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
                Continue with X
              </>
            )}
          </button>
        </div>

        {/* ── Divider ── */}
        <div className="auth-divider">
          <span>or continue with email</span>
        </div>

        {/* ── Email form ── */}
        {signupSuccess ? (
          <div className="auth-signup-success">
            <p>✓ Check your email</p>
            <p className="auth-signup-success-detail">
              We've sent a confirmation link to <strong>{email}</strong>.
              Click it to verify your account, then sign in.
            </p>
            <button
              className="auth-email-btn"
              onClick={() => { setMode('login'); setSignupSuccess(false); }}
            >
              Back to Sign In
            </button>
          </div>
        ) : (
          <form className="auth-email-form" onSubmit={handleEmail}>
            {mode === 'signup' && (
              <input
                className="auth-email-input"
                type="text"
                placeholder="Full name (optional)"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={loading !== null}
              />
            )}
            <input
              className="auth-email-input"
              type="email"
              placeholder="Email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading !== null}
            />
            <input
              className="auth-email-input"
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              disabled={loading !== null}
            />
            <button
              className="auth-email-btn"
              type="submit"
              disabled={loading !== null}
            >
              {loading === 'email'
                ? (mode === 'signup' ? 'Creating account…' : 'Signing in…')
                : (mode === 'signup' ? 'Create Account' : 'Sign In with Email')}
            </button>
          </form>
        )}

        {/* ── Toggle sign in / sign up ── */}
        {!signupSuccess && (
          <p className="auth-toggle">
            {mode === 'login' ? (
              <>Don't have an account? <button onClick={() => { setMode('signup'); setError(null); }}>Sign up</button></>
            ) : (
              <>Already have an account? <button onClick={() => { setMode('login'); setError(null); }}>Sign in</button></>
            )}
          </p>
        )}

        {/* ── Divider ── */}
        <div className="auth-divider">
          <span>or connect wallet</span>
        </div>

        {/* ── Wallet (SIWE) ── */}
        <button
          className="auth-social-btn auth-social-wallet"
          onClick={handleWallet}
          disabled={loading !== null}
        >
          {loading === 'wallet' ? (
            <span className="auth-btn-loading">Connecting…</span>
          ) : (
            <>
              <span className="auth-wallet-icon">🦊</span>
              Connect Ethereum Wallet
            </>
          )}
        </button>

        {/* ── Error display ── */}
        {error && <p className="auth-error">{error}</p>}
      </div>
    </div>
  );
}