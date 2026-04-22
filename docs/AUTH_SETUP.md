# Authentication Setup Guide

Live Transcript Studio supports multiple authentication methods through Supabase Auth:

| Method | Supabase API | Status |
|--------|-------------|--------|
| 📧 Email + Password | `signUp` / `signInWithPassword` | Enabled by default |
| 🔵 Google OAuth | `signInWithOAuth({ provider: 'google' })` | Requires setup |
| 🐦 Twitter/X OAuth | `signInWithOAuth({ provider: 'twitter' })` | Requires setup |
| 🦊 Ethereum Wallet (SIWE) | `signInWithWeb3({ chain: 'ethereum' })` | Requires setup |

All methods produce the same Supabase JWT, so the backend validates them identically via `verify_supabase_user()`.

---

## Prerequisites

1. A [Supabase](https://supabase.com) project
2. Your project's **URL** and **publishable key** from Project Settings → API
3. Set these in `frontend/.env`:
   ```
   VITE_SUPABASE_URL=https://your-project-ref.supabase.co
   VITE_SUPABASE_PUBLISHABLE_KEY=your_publishable_key_here
   ```

---

## Email + Password

Email authentication is **enabled by default** in Supabase. No additional configuration is needed.

To customize:
- **Email confirmation**: Toggle under Authentication → Email → Confirm email
- **Password requirements**: Configure under Authentication → Email → Password

Users sign up via the AuthModal's email form, receive a confirmation email, and can then sign in.

---

## Google OAuth

### Step 1: Create Google OAuth Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Navigate to **APIs & Services** → **Credentials**
4. Click **Create Credentials** → **OAuth client ID**
5. Set application type to **Web application**
6. Under **Authorized JavaScript origins**, add your app URL:
   - Production: `https://your-domain.com`
   - Development: `http://localhost:5173`
7. Under **Authorized redirect URIs**, add your Supabase callback URL:
   ```
   https://<project-ref>.supabase.co/auth/v1/callback
   ```
8. Click **Create** and copy the **Client ID** and **Client Secret**

### Step 2: Enable in Supabase Dashboard

1. Go to your Supabase project dashboard
2. Navigate to **Authentication** → **Providers**
3. Click on **Google** to expand
4. Toggle **Enable Google** to ON
5. Paste the **Client ID** and **Client Secret** from Step 1
6. Click **Save**

### Step 3: Verify

The Google sign-in button in the AuthModal should now redirect users to Google's consent screen and return with a valid session.

---

## Twitter/X OAuth

### Step 1: Create Twitter App

1. Go to the [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a new app (or select an existing one)
3. Go to **App Settings** → **User authentication settings**
4. Enable **OAuth 2.0**
5. Set the **Callback URL** to your Supabase callback:
   ```
   https://<project-ref>.supabase.co/auth/v1/callback
   ```
6. Set **Website URL** to your app URL
7. Copy the **Client ID** and **Client Secret** from the **Keys and tokens** tab

### Step 2: Enable in Supabase Dashboard

1. Go to your Supabase project dashboard
2. Navigate to **Authentication** → **Providers**
3. Click on **Twitter** to expand
4. Toggle **Enable Twitter** to ON
5. Paste the **Client ID** and **Client Secret** from Step 1
6. Click **Save**

### Step 3: Verify

The "Continue with X" button in the AuthModal should now redirect users to Twitter's authorization page and return with a valid session.

---

## Web3 Wallet (SIWE)

### Step 1: Enable in Supabase Dashboard

1. Go to **Authentication** → **Providers**
2. Click on **Web3 Wallet** to expand
3. Toggle **Enable Web3 Wallet** to ON
4. Click **Save**

### Step 2: Run the Database Migration

The migration at `supabase/migrations/20260415_01_enable_web3_auth.sql` adds:
- A `wallet_address` column to `public.users`
- A trigger to sync wallet addresses from `auth.identities` to `public.users`

Apply it via the Supabase CLI or SQL Editor.

---

## Database Migrations

Two migrations handle identity data syncing:

### `20260415_01_enable_web3_auth.sql`
- Adds `wallet_address` column to `public.users`
- Creates `sync_wallet_address()` trigger for Web3 identity sync

### `20260415_02_sync_social_identities.sql`
- Makes `users.name` nullable (OAuth users may not have a name initially)
- Adds `provider` column to `public.users`
- Replaces the narrow `sync_wallet_address()` trigger with `sync_user_identities()` that handles all provider types (web3, google, twitter, email)
- Backfills existing users with identity data

Apply both migrations in order:
```bash
supabase db push
# Or apply manually via the SQL Editor in the Supabase Dashboard
```

---

## Backend Auth

The backend validates all auth methods identically:

1. Frontend sends `Authorization: Bearer <jwt_token>` with each API request
2. Backend calls `supabase.auth.get_user(token)` to verify the JWT
3. If valid, the user object is stored on `request['user']`

The `@require_auth` decorator accepts either:
- **User auth**: Supabase JWT in `Authorization` header
- **Agent auth**: HMAC-SHA256 signed request with `X-API-Key`, `X-Timestamp`, `X-Nonce`, `X-Signature` headers

The `/api/v1/auth/me` endpoint returns enriched user info including:
- `id`, `email`, `full_name`, `avatar_url`
- `wallet_address` (for Web3 users)
- `providers` — list of linked auth providers (e.g., `["google", "web3"]`)
- `app_metadata`, `user_metadata`

---

## Redirect URLs

For OAuth flows, make sure your **Site URL** and **Redirect URLs** are configured in the Supabase Dashboard under Authentication → URL Configuration:

- **Site URL**: Your production app URL (e.g., `https://livetranscript.studio`)
- **Redirect URLs**: Add all URLs where users can be redirected after auth:
  - `https://livetranscript.studio`
  - `http://localhost:5173` (for development)

The frontend uses `window.location.origin` as the `redirectTo` parameter in OAuth calls, so Supabase will redirect users back to the same origin after authentication.

---

## Troubleshooting

### OAuth buttons don't work
- Verify the provider is enabled in the Supabase Dashboard
- Check that the redirect URL matches: `https://<project-ref>.supabase.co/auth/v1/callback`
- Check browser console for errors

### Email sign-up doesn't create a session
- By default, Supabase requires email confirmation before creating a session
- Check the user's email for a confirmation link
- For development, you can disable email confirmation under Authentication → Email

### Wallet connection fails
- Ensure the user has MetaMask or another Web3 wallet installed
- Verify Web3 Wallet is enabled in the Supabase Dashboard
- Check that the `window.ethereum` object is available in the browser

### Session not persisting after OAuth redirect
- Ensure your Site URL is correctly configured in the Supabase Dashboard
- The `onAuthStateChange` listener in `App.jsx` handles session restoration after redirect