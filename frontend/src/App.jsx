import React, { useCallback, useEffect, useState } from 'react';
import { Routes, Route, Link, useLocation, Navigate } from 'react-router-dom';
import './App.css';
import AuthModal from './components/AuthModal';
import BillingPage from './components/BillingPage';
import SubscriptionPlans from './components/SubscriptionPlans';
import { CheckoutSuccess, CheckoutCancel } from './components/CheckoutResult';
import { supabase, onAuthStateChange, getSession } from './lib/supabase';
import useLiveTranscription from './hooks/useLiveTranscription';

// Pages
import LandingPage from './pages/LandingPage';
import PricingPage from './pages/PricingPage';
import Dashboard from './pages/Dashboard';
import TranscribeStream from './pages/TranscribeStream';
import HistoryPage from './pages/HistoryPage';
import UsagePage from './pages/UsagePage';

import {
  LayoutDashboard, Mic,
  History, BarChart3, CreditCard, Menu, X, Radio, MicOff, ArrowRight, AlertCircle, Clock, Brain
} from 'lucide-react';
import { formatDuration } from './lib/streamManager';
import Sentence from './components/Sentence';
import MarkdownText from './components/MarkdownText';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/transcribe/stream', icon: Mic, label: 'Live Stream' },
  { to: '/history', icon: History, label: 'History' },
  { to: '/usage', icon: BarChart3, label: 'Usage' },
  { to: '/billing', icon: CreditCard, label: 'Billing' },
];

function Sidebar({ open, onClose, authUser }) {
  const location = useLocation();
  if (!authUser) return null;
  return (
    <>
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <span className="brand-dot" />
          <strong>LiveTranscript</strong>
        </div>
        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`nav-item ${location.pathname === item.to || (item.to !== '/' && location.pathname.startsWith(item.to)) ? 'active' : ''}`}
              onClick={() => onClose()}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
      </aside>
      {open && <div className="sidebar-backdrop" onClick={onClose} />}
    </>
  );
}

function LiveTranscriptSidebar({ onStreamStopped }) {
  const {
    isStarted,
    status,
    transcriptEntries,
    partialTranscript,
    partialTranscriptTimestamp,
    analysisEntries,
    analysisEnabled,
    errorMessage,
    elapsedMs,
    localAnnotations,
    stop,
    addLocalAnnotation,
    updateLocalAnnotation,
    deleteLocalAnnotation,
    toggleLocalTodo,
  } = useLiveTranscription();
  const wasStartedRef = React.useRef(false);
  const [sidebarTab, setSidebarTab] = useState('transcription');

  const analysisDisplayEntries = analysisEntries.filter((entry) => entry.type !== 'analysis.error');

  const getAnalysisModeLabel = (mode) => {
    if (!mode || typeof mode !== 'string') return 'analysis';
    return mode.replace(/_/g, ' ');
  };

  useEffect(() => {
    if (wasStartedRef.current && !isStarted) {
      onStreamStopped?.();
    }
    wasStartedRef.current = isStarted;
  }, [isStarted, onStreamStopped]);

  useEffect(() => {
    if (!analysisEnabled && sidebarTab === 'analysis') {
      setSidebarTab('transcription');
    }
  }, [analysisEnabled, sidebarTab]);

  if (!isStarted) return null;

  return (
    <aside className="live-sidebar">
      <div className="live-sidebar-header">
        <div className="live-sidebar-title">
          <span className="status-dot live" />
          <span>Live Transcript</span>
        </div>
        <div className="live-sidebar-actions">
          <Link to="/transcribe/stream" className="icon-btn" title="Open full view">
            <ArrowRight size={16} />
          </Link>
          <button className="icon-btn danger" onClick={() => stop()} title="Stop session">
            <MicOff size={16} />
          </button>
        </div>
      </div>
      <div className="live-sidebar-status">
        <span>{status}</span>
        <span className="live-sidebar-timer">
          <Clock size={12} /> {formatDuration(elapsedMs)}
        </span>
      </div>
      {analysisEnabled && (
        <div className="live-sidebar-tabs" role="tablist" aria-label="Live sidebar data view">
          <button
            type="button"
            className={`live-sidebar-tab ${sidebarTab === 'transcription' ? 'active' : ''}`}
            aria-pressed={sidebarTab === 'transcription'}
            onClick={() => setSidebarTab('transcription')}
          >
            <Mic size={12} /> Transcription
          </button>
          <button
            type="button"
            className={`live-sidebar-tab ${sidebarTab === 'analysis' ? 'active' : ''}`}
            aria-pressed={sidebarTab === 'analysis'}
            onClick={() => setSidebarTab('analysis')}
          >
            <Brain size={12} /> Analysis
          </button>
        </div>
      )}
      <div className="live-sidebar-scroll">
        {sidebarTab === 'transcription' && transcriptEntries.length === 0 && !partialTranscript ? (
          <div className="empty-state compact">
            <p>Listening…</p>
          </div>
        ) : null}
        {sidebarTab === 'transcription' ? (
          <>
            {transcriptEntries.map((entry, index) => (
              <Sentence
                key={`${entry.timestamp}-${index}`}
                index={index}
                text={entry.text}
                timestamp={entry.timestamp}
                annotations={localAnnotations[index] || []}
                readOnly={false}
                onCreateAnnotation={(idx, text, ts, type, content) => addLocalAnnotation(idx, ts, type, content)}
                onUpdateAnnotation={(id, updates) => updateLocalAnnotation(id, updates)}
                onDeleteAnnotation={(id) => deleteLocalAnnotation(id)}
                onToggleTodo={(id) => toggleLocalTodo(id)}
              />
            ))}
            {partialTranscript ? (
              <article className="live-sidebar-entry partial-entry">
                <div className="entry-row">
                  {partialTranscriptTimestamp ? (
                    <time className="entry-timestamp-col">[{partialTranscriptTimestamp}]</time>
                  ) : (
                    <span className="entry-timestamp-col placeholder" />
                  )}
                  <MarkdownText className="entry-text" content={partialTranscript} />
                </div>
              </article>
            ) : null}
          </>
        ) : (
          <>
            {analysisDisplayEntries.length === 0 ? (
              <div className="empty-state compact">
                <p>No analysis events yet.</p>
              </div>
            ) : (
              analysisDisplayEntries.map((entry) => (
                <article key={entry.id} className="live-sidebar-entry live-sidebar-analysis-entry">
                  <div className="live-sidebar-analysis-meta">
                    <span className="analysis-entry-type">{entry.type.replace('analysis.', '')}</span>
                    <span className="analysis-entry-mode">{getAnalysisModeLabel(entry.mode)}</span>
                    <span className="analysis-entry-ts">{formatDuration(entry.timestampMs || 0)}</span>
                  </div>
                  <MarkdownText className="entry-text" content={entry.text} />
                </article>
              ))
            )}
          </>
        )}
      </div>
      {errorMessage && (
        <div className="live-sidebar-error">
          <AlertCircle size={14} /> {errorMessage}
        </div>
      )}
    </aside>
  );
}

function App() {
  const location = useLocation();
  const [authUser, setAuthUser] = useState(null);
  const [authSession, setAuthSession] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [subscriptionTier, setSubscriptionTier] = useState('free');
  const [usageSnapshot, setUsageSnapshot] = useState(null);
  const [billingUsageSnapshot, setBillingUsageSnapshot] = useState(null);
  const [accountDataLoading, setAccountDataLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const accessTokenRef = React.useRef(null);

  const refreshAccountData = useCallback(async (accessToken) => {
    const token = accessToken || accessTokenRef.current;
    if (!token) {
      setSubscriptionTier('free');
      setUsageSnapshot(null);
      setBillingUsageSnapshot(null);
      return;
    }

    setAccountDataLoading(true);
    try {
      const headers = { Authorization: `Bearer ${token}` };
      const [subscriptionRes, usageRes, billingUsageRes] = await Promise.allSettled([
        fetch(`${window.location.origin}/api/v1/billing/subscription`, { headers }),
        fetch(`${window.location.origin}/api/v1/user/usage-details?days=30`, { headers }),
        fetch(`${window.location.origin}/api/v1/billing/usage`, { headers }),
      ]);

      let nextTier = 'free';

      if (subscriptionRes.status === 'fulfilled' && subscriptionRes.value.ok) {
        try {
          const data = await subscriptionRes.value.json();
          nextTier = data.tier || 'free';
        } catch (err) {
          console.warn('Failed to parse subscription payload:', err);
        }
      }

      if (usageRes.status === 'fulfilled' && usageRes.value.ok) {
        try {
          setUsageSnapshot(await usageRes.value.json());
        } catch (err) {
          console.warn('Failed to parse usage details payload:', err);
          setUsageSnapshot(null);
        }
      } else {
        setUsageSnapshot(null);
      }

      if (billingUsageRes.status === 'fulfilled' && billingUsageRes.value.ok) {
        try {
          const billingUsage = await billingUsageRes.value.json();
          setBillingUsageSnapshot(billingUsage);
          if (nextTier === 'free' && billingUsage?.tier) {
            nextTier = billingUsage.tier;
          }
        } catch (err) {
          console.warn('Failed to parse billing usage payload:', err);
          setBillingUsageSnapshot(null);
        }
      } else {
        setBillingUsageSnapshot(null);
      }

      setSubscriptionTier(nextTier);
    } catch (err) {
      console.warn('Failed to refresh account data:', err);
      setSubscriptionTier('free');
      setUsageSnapshot(null);
      setBillingUsageSnapshot(null);
    } finally {
      setAccountDataLoading(false);
    }
  }, []);

  useEffect(() => {
    getSession().then((session) => {
      if (session) {
        setAuthSession(session);
        setAuthUser(session.user);
        accessTokenRef.current = session.access_token;
        refreshAccountData(session.access_token);
      } else {
        setSubscriptionTier('free');
        setUsageSnapshot(null);
        setBillingUsageSnapshot(null);
      }
      setAuthReady(true);
    });

    const unsubscribe = onAuthStateChange((_event, session) => {
      setAuthSession(session);
      setAuthUser(session?.user ?? null);
      accessTokenRef.current = session?.access_token ?? null;
      if (session) refreshAccountData(session.access_token);
      else {
        setSubscriptionTier('free');
        setUsageSnapshot(null);
        setBillingUsageSnapshot(null);
      }
      setAuthReady(true);
    });

    return () => { unsubscribe(); };
  }, [refreshAccountData]);

  if (!authReady) return null;

  return (
    <div className="app-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <header className="app-header">
        <div className="header-left">
          {authUser && (
            <button className="menu-btn" onClick={() => setSidebarOpen(!sidebarOpen)}>
              {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          )}
          <Link to="/" className="app-header-title">Live Transcript Studio</Link>
        </div>
        <div className="app-header-auth">
          {!authUser && (
            <Link to="/pricing" className="header-pricing-link">Pricing</Link>
          )}
          {authUser ? (
            <button className="auth-header-user-btn" onClick={() => setAuthModalOpen(true)}>
              {authUser.user_metadata?.avatar_url || authUser.user_metadata?.picture ? (
                <img className="auth-header-avatar" src={authUser.user_metadata.avatar_url || authUser.user_metadata.picture} alt="" />
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
            <button className="auth-header-signin-btn" onClick={() => setAuthModalOpen(true)}>Sign In</button>
          )}
        </div>
      </header>

      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} authUser={authUser} />

      <AuthModal
        user={authUser}
        onAuth={({ user, session }) => {
          setAuthUser(user);
          setAuthSession(session);
          accessTokenRef.current = session?.access_token ?? null;
          if (user) {
            setAuthModalOpen(false);
            refreshAccountData(session?.access_token);
          }
        }}
        onLogout={() => {
          setAuthUser(null);
          setAuthSession(null);
          accessTokenRef.current = null;
          setSubscriptionTier('free');
          setUsageSnapshot(null);
          setBillingUsageSnapshot(null);
          setAuthModalOpen(false);
        }}
        open={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
      />

      <main className={`main-content ${!authUser ? 'landing-main' : ''} ${location.pathname !== '/transcribe/stream' && authUser ? 'has-live-sidebar' : ''}`}>
        <Routes>
          <Route path="/" element={authUser ? <Dashboard usageSnapshot={usageSnapshot} /> : <LandingPage onOpenAuth={() => setAuthModalOpen(true)} />} />
          <Route path="/pricing" element={<PricingPage currentTier={subscriptionTier} onOpenAuth={() => setAuthModalOpen(true)} authUser={authUser} />} />
          <Route path="/transcribe/stream" element={authUser ? <TranscribeStream accessToken={accessTokenRef.current} onStreamStopped={() => refreshAccountData(accessTokenRef.current)} /> : <Navigate to="/" replace />} />
          <Route path="/transcribe/file" element={<Navigate to="/transcribe/stream" replace />} />
          <Route path="/transcribe/url" element={<Navigate to="/transcribe/stream" replace />} />
          <Route path="/translate" element={<Navigate to="/transcribe/stream" replace />} />
          <Route path="/history" element={authUser ? <HistoryPage /> : <Navigate to="/" replace />} />
          <Route path="/usage" element={authUser ? <UsagePage /> : <Navigate to="/" replace />} />
          <Route path="/billing" element={authUser ? <BillingPage billingUsage={billingUsageSnapshot} accountDataLoading={accountDataLoading} onRefreshAccountData={() => refreshAccountData(accessTokenRef.current)} /> : <Navigate to="/" replace />} />
          <Route path="/billing/plans" element={authUser ? <SubscriptionPlans currentTier={subscriptionTier} /> : <Navigate to="/" replace />} />
          <Route path="/billing/success" element={<CheckoutSuccess />} />
          <Route path="/billing/cancel" element={<CheckoutCancel />} />
        </Routes>
      </main>

      {location.pathname !== '/transcribe/stream' && authUser && (
        <LiveTranscriptSidebar onStreamStopped={() => refreshAccountData(accessTokenRef.current)} />
      )}
    </div>
  );
}

export default App;
