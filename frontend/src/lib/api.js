/**
 * Backend API Client
 * All requests go through the backend. No direct Supabase/Stripe calls.
 */

const API_BASE = `${window.location.origin}/api/v1`;

async function _getToken() {
  const { data: { session } } = await import('./supabase.js').then(m => m.supabase.auth.getSession());
  return session?.access_token ?? null;
}

async function _fetch(path, options = {}) {
  const token = await _getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...options.headers,
  };

  // Remove Content-Type for FormData
  if (options.body instanceof FormData) {
    delete headers['Content-Type'];
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let errBody;
    try { errBody = await res.json(); } catch { errBody = await res.text(); }
    throw Object.assign(new Error(errBody?.error || `HTTP ${res.status}`), { status: res.status, body: errBody });
  }
  if (res.status === 204) return null;
  return res.json().catch(() => null);
}

export const api = {
  // Auth / User
  getProfile: () => _fetch('/user/profile'),
  getHistory: (params = {}) => {
    const q = new URLSearchParams(params);
    return _fetch(`/user/history?${q}`);
  },
  getUsageDetails: (days = 30) => _fetch(`/user/usage-details?days=${days}`),

  // Streams
  createStreamSession: (body) => _fetch('/stream/process', { method: 'POST', body: JSON.stringify(body) }),
  updateStreamTranslation: (streamId, body) => _fetch(`/stream/${streamId}/translation`, { method: 'PUT', body: JSON.stringify(body || {}) }),
  updateStreamAnalysis: (streamId, body) => _fetch(`/stream/${streamId}/analysis`, { method: 'PUT', body: JSON.stringify(body || {}) }),
  whipProxy: (streamId, sdpOffer, token) => {
    return fetch(`${API_BASE}/stream/${streamId}/whip`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/sdp',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      },
      body: sdpOffer,
    });
  },
  listStreams: (params = {}) => {
    const q = new URLSearchParams(params);
    return _fetch(`/streams?${q}`);
  },
  getStream: (id) => _fetch(`/streams/${id}`),
  deleteStream: (id) => _fetch(`/streams/${id}`, { method: 'DELETE' }),
  getSentences: (streamId) => _fetch(`/streams/${streamId}/sentences`),
  getStreamAnalysis: (streamId) => _fetch(`/streams/${streamId}/analysis`),

  // Translations (history only)
  listTranslations: (params = {}) => {
    const q = new URLSearchParams(params);
    return _fetch(`/translations?${q}`);
  },
  getTranslation: (id) => _fetch(`/translations/${id}`),
  deleteTranslation: (id) => _fetch(`/translations/${id}`, { method: 'DELETE' }),

  // Languages
  getLanguages: () => _fetch('/languages'),

  // Billing
  getSubscription: () => _fetch('/billing/subscription'),
  getBillingUsage: () => _fetch('/billing/usage'),
  createCheckoutSession: (tier) => _fetch('/billing/create-checkout-session', { method: 'POST', body: JSON.stringify({ tier }) }),

  // Annotations
  getAnnotations: (streamId) => _fetch(`/streams/${streamId}/annotations`),
  createAnnotation: (streamId, body) => _fetch(`/streams/${streamId}/annotations`, { method: 'POST', body: JSON.stringify(body) }),
  updateAnnotation: (annotationId, body) => _fetch(`/annotations/${annotationId}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteAnnotation: (annotationId) => _fetch(`/annotations/${annotationId}`, { method: 'DELETE' }),
};
