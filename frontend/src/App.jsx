import React, { useEffect, useRef, useState } from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import './App.css';
import AuthModal from './components/AuthModal';
import BillingPage from './components/BillingPage';
import SubscriptionPlans from './components/SubscriptionPlans';
import { CheckoutSuccess, CheckoutCancel } from './components/CheckoutResult';
import { supabase, onAuthStateChange, getSession } from './lib/supabase';

const API_BASE = `${window.location.origin}/api/v1`;
const STREAM_API_ENDPOINT = `${API_BASE}/transcribe/stream`;
const WS_ENDPOINT = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

// WHIP Client class — sends SDP offer/answer through the backend proxy
// instead of connecting directly to the compute provider.
// The backend proxies the SDP to the provider's whip_url and returns the answer.
class WHIPClient {
    /**
     * @param {string} streamId - The stream session ID returned from POST /api/v1/transcribe/stream
     * @param {string} [accessToken] - Optional Supabase JWT for authenticated requests
     */
    constructor(streamId, accessToken) {
        // WHIP proxy endpoint on the backend — the backend forwards SDP to the provider
        this.whipEndpoint = `${API_BASE}/transcribe/stream/${streamId}/whip`;
        this.accessToken = accessToken || null;
        this.pc = null;
    }

    async start(tracks) {
        // Validate input tracks
        if (!tracks || !Array.isArray(tracks) || tracks.length === 0) {
            throw new Error('Invalid tracks parameter: expected non-empty array of MediaStreamTrack objects');
        }
        
        // Validate each track is a MediaStreamTrack
        for (let i = 0; i < tracks.length; i++) {
            const track = tracks[i];
            if (!(track instanceof MediaStreamTrack)) {
                throw new Error(`Invalid track at index ${i}: expected MediaStreamTrack, got ${typeof track}`);
            }
            // Optional: check if track is ended
            if (track.readyState === 'ended') {
                throw new Error(`Track at index ${i} is already ended`);
            }
        }

        this.pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });

        // Add tracks properly - create a MediaStream from the tracks
        const mediaStream = new MediaStream(tracks);
        mediaStream.getTracks().forEach(track => this.pc.addTrack(track, mediaStream));

        // Create offer
        const offer = await this.pc.createOffer();
        await this.pc.setLocalDescription(offer);

        // Wait for ICE candidate gathering to complete before sending the offer.
        // Without this, the SDP offer would contain no ICE candidates and the
        // WebRTC connection to the provider could not be established.
        await this._waitForIceGathering();

        // Send the fully-gathered SDP offer (with ICE candidates) to the backend
        // WHIP proxy endpoint. The backend looks up the provider's whip_url from
        // the session store, forwards the SDP offer, and returns the SDP answer.
        const headers = {
            'Content-Type': 'application/sdp'
        };
        if (this.accessToken) {
            headers['Authorization'] = `Bearer ${this.accessToken}`;
        }
        const response = await fetch(this.whipEndpoint, {
            method: 'POST',
            body: this.pc.localDescription.sdp,
            headers
        });

        if (!response.ok) {
            const errorBody = await response.text().catch(() => '');
            throw new Error(`WHIP proxy failed: ${response.status} ${errorBody}`);
        }

        const answerSdp = await response.text();
        const answer = new RTCSessionDescription({ type: 'answer', sdp: answerSdp });
        await this.pc.setRemoteDescription(answer);
        
        return this.pc;
    }

    /**
     * Wait for ICE candidate gathering to complete.
     * Returns a Promise that resolves when `iceGatheringState` is 'complete',
     * or after a 5-second timeout (whichever comes first). This ensures the
     * SDP offer sent to the WHIP endpoint contains all gathered ICE candidates.
     */
    _waitForIceGathering(timeoutMs = 5000) {
        return new Promise((resolve) => {
            if (this.pc.iceGatheringState === 'complete') {
                resolve();
                return;
            }

            const onStateChange = () => {
                if (this.pc.iceGatheringState === 'complete') {
                    this.pc.removeEventListener('icegatheringstatechange', onStateChange);
                    clearTimeout(timer);
                    resolve();
                }
            };

            this.pc.addEventListener('icegatheringstatechange', onStateChange);

            // Fallback timeout — resolve even if gathering isn't complete
            // so the connection attempt isn't blocked indefinitely
            const timer = setTimeout(() => {
                this.pc.removeEventListener('icegatheringstatechange', onStateChange);
                resolve();
            }, timeoutMs);
        });
    }

    stop() {
        if (this.pc) {
            this.pc.getSenders().forEach(sender => sender.track.stop());
            this.pc.close();
            this.pc = null;
        }
    }
}

function App() {
  const location = useLocation();
  const [status, setStatus] = useState('Ready for live transcription.');
  const [transcriptEntries, setTranscriptEntries] = useState([]);
  const [partialTranscript, setPartialTranscript] = useState('');
  const [isStarted, setIsStarted] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // Auth state — populated by Supabase auth (SIWE, email, Google, Twitter)
  const [authUser, setAuthUser] = useState(null);
  const [authSession, setAuthSession] = useState(null);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [subscriptionTier, setSubscriptionTier] = useState('free');
  const accessTokenRef = useRef(null);

  const whipClientRef = useRef(null);
  const wsRef = useRef(null);
  const localStreamRef = useRef(null);
  const isStartedRef = useRef(false);
  const streamIdRef = useRef(null);

  useEffect(() => {
    isStartedRef.current = isStarted;
  }, [isStarted]);

  // Subscribe to Supabase auth state changes and restore session on mount
  useEffect(() => {
    // Restore existing session on mount
    getSession().then((session) => {
      if (session) {
        setAuthSession(session);
        setAuthUser(session.user);
        accessTokenRef.current = session.access_token;
        // Fetch subscription tier
        fetchSubscriptionTier(session.access_token);
      }
    });

    // Listen for future changes (sign-in / sign-out)
    const unsubscribe = onAuthStateChange((_event, session) => {
      setAuthSession(session);
      setAuthUser(session?.user ?? null);
      accessTokenRef.current = session?.access_token ?? null;
      if (session) {
        fetchSubscriptionTier(session.access_token);
      } else {
        setSubscriptionTier('free');
      }
    });

    return () => {
      unsubscribe();
    };
  }, []);

  const fetchSubscriptionTier = async (accessToken) => {
    try {
      const response = await fetch(`${API_BASE}/billing/subscription`, {
        headers: { 'Authorization': `Bearer ${accessToken}` },
      });
      if (response.ok) {
        const data = await response.json();
        setSubscriptionTier(data.tier || 'free');
      }
    } catch (err) {
      console.warn('Failed to fetch subscription tier:', err);
    }
  };

  useEffect(() => {
    return () => {
      if (isStartedRef.current) {
        stopTranscription({ preserveStatus: false });
      }
    };
  }, []);

  const createBlackVideoTrack = async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'black';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const stream = canvas.captureStream();
    return stream.getVideoTracks()[0];
  };

  const stopTranscription = ({ preserveStatus = false } = {}) => {
    const wasStarted = isStartedRef.current;

    isStartedRef.current = false;
    setIsStarted(false);

    // Send stop_stream message before closing WebSocket
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && streamIdRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: 'stop_stream', stream_id: streamIdRef.current }));
      } catch (e) {
        console.warn('Failed to send stop_stream message:', e);
      }
    }
    streamIdRef.current = null;

    if (whipClientRef.current) {
      whipClientRef.current.stop();
      whipClientRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
    }

    setPartialTranscript('');
    if (!preserveStatus && wasStarted) {
      setStatus('Transcription stopped.');
    }
  };

  /**
   * Create a streaming session via the backend REST API.
   * Returns { stream_id, data_url, ... } or throws on error.
   * WHIP is proxied through the backend — use WHIPClient(stream_id) to connect.
   */
  const createStreamSession = async () => {
    const headers = { 'Content-Type': 'application/json' };
    if (accessTokenRef.current) {
      headers['Authorization'] = `Bearer ${accessTokenRef.current}`;
    }
    const response = await fetch(STREAM_API_ENDPOINT, {
      method: 'POST',
      headers,
      body: JSON.stringify({ language: 'en' }),
    });

    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`Stream session creation failed (${response.status}): ${errorBody}`);
    }

    const data = await response.json();
    if (!data.stream_id) {
      throw new Error('Stream session response missing stream_id');
    }

    return data;
  };

  const startTranscription = async () => {
    if (isStarted) return;

    try {
      setErrorMessage('');
      setTranscriptEntries([]);
      setPartialTranscript('');
      setStatus('Getting user media...');
      const audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audioTrack = audioStream.getAudioTracks()[0];

      const videoTrack = await createBlackVideoTrack();

      localStreamRef.current = new MediaStream([audioTrack, videoTrack]);

      // Step 1: Create a stream session via the backend API
      setStatus('Creating stream session...');
      let sessionData;
      try {
        sessionData = await createStreamSession();
      } catch (sessionError) {
        console.error('Failed to create stream session:', sessionError);
        setStatus(`Session creation failed: ${sessionError.message}`);
        setErrorMessage('Could not create a streaming session. Ensure the backend and compute provider are running.');
        throw sessionError;
      }

      const { stream_id } = sessionData;
      streamIdRef.current = stream_id;

      // Step 2: Connect WHIP via backend proxy — the backend forwards SDP to the provider
      setStatus('Connecting to WHIP endpoint...');
      const whipClient = new WHIPClient(stream_id, accessTokenRef.current || undefined);
      whipClientRef.current = whipClient;

      try {
        const pc = await whipClient.start([audioTrack, videoTrack]);
        setStatus('WHIP connected. Opening WebSocket...');

        // Handle incoming tracks from WHIP (if needed)
        pc.ontrack = (event) => {
          console.log('Received track from WHIP:', event.track.kind);
        };

        // Step 3: Open WebSocket and subscribe to transcription events
        setStatus('Connected. Opening WebSocket...');
        const ws = new WebSocket(WS_ENDPOINT);
        wsRef.current = ws;

        ws.onopen = () => {
          // Send start_stream to subscribe to SSE relay for this stream
          ws.send(JSON.stringify({ type: 'start_stream', stream_id }));
          setStatus('Listening for speech...');
          setIsStarted(true);
          isStartedRef.current = true;
        };

        ws.onmessage = (event) => {
          if (event.data instanceof Blob) {
            return;
          }

          try {
            const message = JSON.parse(event.data);
            if (message.type === 'transcription') {
              const chunk = typeof message.text === 'string' ? message.text : '';
              if (!chunk) {
                return;
              }

              if (message.is_final) {
                setTranscriptEntries((previous) => [...previous, chunk.trim()]);
                setPartialTranscript('');
              } else {
                setPartialTranscript((previous) => `${previous}${chunk}`);
              }
              setStatus('Receiving live transcript...');
            } else if (message.type === 'status') {
              setStatus(message.text);
            } else if (message.type === 'error') {
              console.error('Stream error:', message.text);
              setErrorMessage(message.text);
              setStatus(`Error: ${message.text}`);
            }
          } catch (parseErr) {
            console.warn('Failed to parse WebSocket message:', parseErr);
          }
        };

        ws.onclose = () => {
          if (isStartedRef.current) {
            stopTranscription({ preserveStatus: true });
            setStatus('WebSocket disconnected.');
          }
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          setErrorMessage('Realtime connection failed. Check the backend and try again.');
          setStatus('WebSocket error');
        };
      } catch (whipError) {
        console.error('WHIP connection failed:', whipError);
        setStatus(`WHIP connection failed: ${whipError.message}`);
        setErrorMessage('Could not establish the WHIP session.');
        // Clean up WHIP client if it failed
        if (whipClientRef.current) {
          whipClientRef.current.stop();
          whipClientRef.current = null;
        }
        streamIdRef.current = null;
        throw whipError;
      }
    } catch (err) {
      console.error('Error starting transcription:', err);
      setStatus(`Error: ${err.message}`);
      setErrorMessage(err.message);
      stopTranscription({ preserveStatus: true });
    }
  };

  const transcriptCount = transcriptEntries.length + (partialTranscript ? 1 : 0);
  const isLive = isStarted && !errorMessage;

  return (
    <div className="app-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <header className="app-header">
        <Link to="/" className="app-header-title">Live Transcript Studio</Link>
        {authUser && (
          <nav className="app-header-nav">
            <Link to="/" className={`nav-link${location.pathname === '/' ? ' active' : ''}`}>Transcribe</Link>
            <Link to="/billing" className={`nav-link${location.pathname.startsWith('/billing') ? ' active' : ''}`}>Billing</Link>
          </nav>
        )}
        <div className="app-header-auth">
          {authUser ? (
            <button
              className="auth-header-user-btn"
              onClick={() => setAuthModalOpen(true)}
            >
              {authUser.user_metadata?.avatar_url || authUser.user_metadata?.picture ? (
                <img
                  className="auth-header-avatar"
                  src={authUser.user_metadata.avatar_url || authUser.user_metadata.picture}
                  alt=""
                />
              ) : (
                <span className="auth-header-avatar-placeholder">
                  {(authUser.user_metadata?.full_name || authUser.email || 'U').charAt(0).toUpperCase()}
                </span>
              )}
              <span className="auth-header-name">
                {authUser.user_metadata?.full_name || authUser.email || `${(authUser.id || '').slice(0, 8)}`}
              </span>
            </button>
          ) : (
            <button
              className="auth-header-signin-btn"
              onClick={() => setAuthModalOpen(true)}
            >
              Sign In
            </button>
          )}
        </div>
      </header>

      <AuthModal
        user={authUser}
        onAuth={({ user, session }) => {
          setAuthUser(user);
          setAuthSession(session);
          accessTokenRef.current = session?.access_token ?? null;
          if (user) setAuthModalOpen(false);
        }}
        onLogout={() => {
          setAuthUser(null);
          setAuthSession(null);
          accessTokenRef.current = null;
          setAuthModalOpen(false);
        }}
        open={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
      />

      <Routes>
        <Route path="/" element={
          <main className={`page-grid ${isStarted ? 'session-active' : ''}`}>
            <section className={`hero-panel panel-glass ${isStarted ? 'hero-panel-collapsed' : ''}`}>
              <div className="hero-content">
                <div className="hero-copy-block">
                  <p className="eyebrow">Realtime speech capture</p>
                  <h1>Live Transcript Studio</h1>
                  <p className="hero-copy">
                    Stream your microphone into Voxtral and watch the transcript build in real time.
                    This interface is tuned for a single job: fast, readable speech-to-text.
                  </p>

                  <p className="supports-line">
                    Supports: Arabic, Chinese, Dutch, English, French, German, Hindi, Italian, Japanese, Korean, Portuguese, Russian, Spanish.
                  </p>
                </div>

                <div className="hero-controls">
                  <div className="stat-strip compact-strip">
                    <article>
                      <span>Mode</span>
                      <strong>Transcription only</strong>
                    </article>
                    <article>
                      <span>Entries</span>
                      <strong>{transcriptCount}</strong>
                    </article>
                    <article>
                      <span>Engine</span>
                      <strong>Voxtral Realtime</strong>
                    </article>
                  </div>

                  <div className="hero-actions">
                    <button className="primary-button" onClick={startTranscription} disabled={isStarted}>
                      {isStarted ? 'Listening…' : 'Start Session'}
                    </button>
                    <button className="secondary-button" onClick={() => stopTranscription()} disabled={!isStarted}>
                      Stop Session
                    </button>
                  </div>
                </div>
              </div>

              <div className="status-card">
                <span className={`status-dot ${isLive ? 'live' : ''}`} />
                <div>
                  <p className="status-label">Session status</p>
                  <p className="status-text">{status}</p>
                </div>
              </div>

              {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
            </section>

            <section className={`transcript-panel panel-glass ${isStarted ? 'transcript-panel-expanded' : ''}`}>
              <div className="panel-heading transcript-heading">
                <div>
                  <p className="eyebrow">Output</p>
                  <h2>Transcript feed</h2>
                </div>
                <p className="transcript-note">Partial text stays live until the model finalizes the segment.</p>
              </div>

              <div className="transcript-scroll">
                {transcriptEntries.length === 0 && !partialTranscript ? (
                  <div className="empty-state">
                    <p>No transcript yet.</p>
                    <span>Start a session, allow microphone access, and speak naturally.</span>
                  </div>
                ) : null}

                {transcriptEntries.map((entry, index) => (
                  <article className="transcript-entry" key={`${entry}-${index}`}>
                    <span className="entry-badge">Final</span>
                    <p>{entry}</p>
                  </article>
                ))}

                {partialTranscript ? (
                  <article className="transcript-entry partial-entry">
                    <span className="entry-badge">Live</span>
                    <p>{partialTranscript}</p>
                  </article>
                ) : null}
              </div>
            </section>
          </main>
        } />
        <Route path="/billing" element={
          <main className="page-grid">
            <section className="panel-glass billing-route-container">
              <BillingPage />
              <div className="billing-plans-link">
                <Link to="/billing/plans" className="secondary-button">Change Plan</Link>
              </div>
            </section>
          </main>
        } />
        <Route path="/billing/plans" element={
          <main className="page-grid">
            <section className="panel-glass billing-route-container">
              <SubscriptionPlans currentTier={subscriptionTier} />
              <div className="billing-back-link">
                <Link to="/billing" className="secondary-button">← Back to Billing</Link>
              </div>
            </section>
          </main>
        } />
        <Route path="/billing/success" element={
          <main className="page-grid">
            <section className="panel-glass billing-route-container">
              <CheckoutSuccess />
            </section>
          </main>
        } />
        <Route path="/billing/cancel" element={
          <main className="page-grid">
            <section className="panel-glass billing-route-container">
              <CheckoutCancel />
            </section>
          </main>
        } />
      </Routes>
    </div>
  );
}

export default App;