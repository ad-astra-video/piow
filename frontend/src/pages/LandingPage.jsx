import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Mic, Upload, Link as LinkIcon, Languages, ArrowRight,
  Zap, Shield, Globe, Clock, ChevronRight, Sparkles,
  Radio, FileAudio, BarChart3
} from 'lucide-react';

export default function LandingPage({ onOpenAuth }) {
  const navigate = useNavigate();

  const features = [
    {
      icon: Radio,
      title: 'Live Stream Transcription',
      desc: 'Real-time microphone transcription with sub-second latency. Perfect for meetings, podcasts, and live events.',
    },
    {
      icon: Upload,
      title: 'File Upload',
      desc: 'Upload audio or video files in any major format. Get accurate transcripts with speaker detection and timestamps.',
    },
    {
      icon: LinkIcon,
      title: 'URL Transcription',
      desc: 'Transcribe directly from any publicly accessible audio or video URL. No download required.',
    },
    {
      icon: Languages,
      title: 'Translation',
      desc: 'Translate transcripts into 50+ languages instantly. Preserve context and nuance with AI-powered translation.',
    },
  ];

  const steps = [
    { num: '01', title: 'Connect', desc: 'Sign in with Google, X, email, or your Ethereum wallet in seconds.' },
    { num: '02', title: 'Transcribe', desc: 'Choose your source — live mic, file upload, or URL — and select your language.' },
    { num: '03', title: 'Export', desc: 'Download transcripts in multiple formats or translate them with one click.' },
  ];

  const stats = [
    { value: '50+', label: 'Languages' },
    { value: '<1s', label: 'Latency' },
    { value: '99%', label: 'Uptime' },
    { value: 'x402', label: 'Crypto Payments' },
  ];

  return (
    <div className="landing-page">
      {/* Hero */}
      <section className="landing-hero">
        <div className="landing-hero-content">
          <div className="hero-badge">
            <Sparkles size={14} />
            <span>AI-Powered Transcription Studio</span>
          </div>
          <h1 className="hero-title">
            Transcribe & Translate
            <span className="gradient-text"> in Real Time</span>
          </h1>
          <p className="hero-subtitle">
            Professional-grade speech-to-text and translation powered by decentralized compute.
            Start free, pay only for what you use, or subscribe for unlimited access.
          </p>
          <div className="hero-ctas">
            <button className="primary-button hero-cta-primary" onClick={onOpenAuth}>
              Get Started Free
              <ArrowRight size={18} />
            </button>
            <button className="secondary-button" onClick={() => navigate('/pricing')}>
              View Pricing
            </button>
          </div>
        </div>
        <div className="hero-visual">
          <div className="hero-card panel-glass">
            <div className="hero-card-header">
              <span className="status-dot live" />
              <span className="hero-card-label">Live Transcription</span>
            </div>
            <div className="hero-card-body">
              <p className="hero-card-text">"Welcome everyone to today's quarterly review..."</p>
              <div className="hero-card-meta">
                <span>English</span>
                <span>•</span>
                <span>00:42</span>
              </div>
            </div>
          </div>
          <div className="hero-card-secondary panel-glass">
            <Globe size={18} className="hero-card-icon" />
            <span>Translating to Spanish...</span>
          </div>
        </div>
      </section>

      {/* Stats strip */}
      <section className="landing-stats">
        {stats.map((s) => (
          <div key={s.label} className="stat-item">
            <strong>{s.value}</strong>
            <span>{s.label}</span>
          </div>
        ))}
      </section>

      {/* Features */}
      <section className="landing-features">
        <div className="section-header-center">
          <h2>Everything You Need</h2>
          <p>One platform for all your transcription and translation workflows</p>
        </div>
        <div className="features-grid">
          {features.map((f) => (
            <div key={f.title} className="feature-card panel-glass">
              <div className="feature-icon-wrap">
                <f.icon size={22} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How It Works */}
      <section className="landing-steps">
        <div className="section-header-center">
          <h2>How It Works</h2>
          <p>From signup to transcript in under 60 seconds</p>
        </div>
        <div className="steps-grid">
          {steps.map((step) => (
            <div key={step.num} className="step-card panel-glass">
              <span className="step-number">{step.num}</span>
              <h3>{step.title}</h3>
              <p>{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="landing-cta">
        <div className="cta-content panel-glass">
          <h2>Ready to get started?</h2>
          <p>Join thousands of creators, researchers, and teams using LiveTranscript Studio.</p>
          <div className="cta-buttons">
            <button className="primary-button" onClick={onOpenAuth}>
              Start Transcribing Free
              <ArrowRight size={18} />
            </button>
            <button className="secondary-button" onClick={() => navigate('/pricing')}>
              Compare Plans
            </button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="landing-footer">
        <div className="footer-brand">
          <span className="brand-dot" />
          <strong>LiveTranscript</strong>
        </div>
        <div className="footer-links">
          <Link to="/pricing">Pricing</Link>
          <a href="#" onClick={(e) => { e.preventDefault(); onOpenAuth(); }}>Sign In</a>
        </div>
        <span className="footer-copy">© {new Date().getFullYear()} LiveTranscript Studio</span>
      </footer>
    </div>
  );
}
