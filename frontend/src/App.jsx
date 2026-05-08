import React, { useEffect, useState } from 'react';
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
import TranscribeFile from './pages/TranscribeFile';
import TranscribeUrl from './pages/TranscribeUrl';
import TranslatePage from './pages/TranslatePage';
import HistoryPage from './pages/HistoryPage';
import UsagePage from './pages/UsagePage';

import {
  LayoutDashboard, Mic, Upload, Link as LinkIcon, Languages,
  History, BarChart3, CreditCard, Menu, X, Radio, MicOff, ArrowRight, AlertCircle, Clock
} from 'lucide-react';
import { formatDuration } from './lib/streamManager';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/transcribe/stream', icon: Mic, label: 'Live Stream' },
  { to: '/transcribe/file', icon: Upload, label: 'Upload File' },
  { to: '/transcribe/url', icon: LinkIcon, label: 'URL' },
  { to: '/translate', icon: Languages, label: 'Translate' },
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

function LiveTranscriptSidebar({ onStop }) {
  const {
    isStarted,
    status,
    transcriptEntries,
    partialTranscript,
    errorMessage,
    elapsedMs,
    stop,
  } = useLiveTranscription();

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
      <div className="live-sidebar-scroll">
        {transcriptEntries.length === 0 && !partialTranscript ? (
          <div className="empty-state compact">
            <p>Listening…</p>
          </div>
        ) : null}
        {transcriptEntries.map((entry, index) => {
          const tsMatch = entry.match(/^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)$/);
          if (tsMatch) {
            return (
              <article className="live-sidebar-entry" key={`${entry}-${index}`}>
                <p><span className="entry-timestamp">{tsMatch[1]}</span>{' '}{tsMatch[2]}</p>
              </article>
            );
          }
          return (
            <article className="live-sidebar-entry" key={`${entry}-${index}`}>
              <p>{entry}</p>
            </article>
          );
        })}
        {partialTranscript ? (
          <article className="live-sidebar-entry partial-entry">
            <span className="entry-badge">Live</span>
            <p>{partialTranscript}</p>
          </article>
        ) : null}
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const accessTokenRef = React.useRef(null);

  useEffect(() => {
    getSession().then((session) => {
      if (session) {
        setAuthSession(session);
        setAuthUser(session.user);
        accessTokenRef.current = session.access_token;
        fetchSubscriptionTier(session.access_token);
      }
      setAuthReady(true);
    });

    const unsubscribe = onAuthStateChange((_event, session) => {
      setAuthSession(session);
      setAuthUser(session?.user ?? null);
      accessTokenRef.current = session?.access_token ?? null;
      if (session) fetchSubscriptionTier(session.access_token);
      else setSubscriptionTier('free');
      setAuthReady(true);
    });

    return () => { unsubscribe(); };
  }, []);

  const fetchSubscriptionTier = async (accessToken) => {
    try {
      const response = await fetch(`${window.location.origin}/api/v1/billing/subscription`, {
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

      <main className={`main-content ${!authUser ? 'landing-main' : ''} ${location.pathname !== '/transcribe/stream' && authUser ? 'has-live-sidebar' : ''}`}>
        <Routes>
          <Route path="/" element={authUser ? <Dashboard /> : <LandingPage onOpenAuth={() => setAuthModalOpen(true)} />} />
          <Route path="/pricing" element={<PricingPage currentTier={subscriptionTier} onOpenAuth={() => setAuthModalOpen(true)} authUser={authUser} />} />
          <Route path="/transcribe/stream" element={authUser ? <TranscribeStream accessToken={accessTokenRef.current} /> : <Navigate to="/" replace />} />
          <Route path="/transcribe/file" element={authUser ? <TranscribeFile /> : <Navigate to="/" replace />} />
          <Route path="/transcribe/url" element={authUser ? <TranscribeUrl /> : <Navigate to="/" replace />} />
          <Route path="/translate" element={authUser ? <TranslatePage /> : <Navigate to="/" replace />} />
          <Route path="/history" element={authUser ? <HistoryPage /> : <Navigate to="/" replace />} />
          <Route path="/usage" element={authUser ? <UsagePage /> : <Navigate to="/" replace />} />
          <Route path="/billing" element={authUser ? <BillingPage /> : <Navigate to="/" replace />} />
          <Route path="/billing/plans" element={authUser ? <SubscriptionPlans currentTier={subscriptionTier} /> : <Navigate to="/" replace />} />
          <Route path="/billing/success" element={<CheckoutSuccess />} />
          <Route path="/billing/cancel" element={<CheckoutCancel />} />
        </Routes>
      </main>

      {location.pathname !== '/transcribe/stream' && authUser && (
        <LiveTranscriptSidebar />
      )}
    </div>
  );
}

export default App;
