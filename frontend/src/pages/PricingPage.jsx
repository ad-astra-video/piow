import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ArrowRight, Sparkles } from 'lucide-react';
import { supabase } from '../lib/supabase';

const API_BASE = `${window.location.origin}/api/v1`;

const PLANS = [
  {
    tier: 'free',
    name: 'Free',
    price: 0,
    features: [
      '1 hr/day transcription (30 hr/mo)',
      '5,000 characters translation / 30 days',
      'Queue delays & lower priority',
      'Watermark on exports',
      'x402 pay-per-request available',
    ],
    cta: 'Get Started',
    disabled: false,
  },
  {
    tier: 'starter',
    name: 'Starter',
    price: 15,
    features: [
      '3 hr/day transcription (90 hr/mo)',
      '100K characters translation / 30 days',
      'No queue delays',
      'Standard priority',
    ],
    cta: 'Subscribe',
    disabled: false,
  },
  {
    tier: 'pro',
    name: 'Pro',
    price: 39,
    features: [
      '8 hr/day transcription (240 hr/mo)',
      'Unlimited translation',
      'High priority processing',
      'No watermarks',
    ],
    cta: 'Subscribe',
    disabled: false,
    popular: true,
  },
  {
    tier: 'enterprise',
    name: 'Enterprise',
    price: 99,
    features: [
      '24 hr/day — Unlimited transcription',
      'Unlimited translation',
      'Highest priority processing',
      'Dedicated support',
      'Custom SLA & fair-use policy',
    ],
    cta: 'Subscribe',
    disabled: false,
  },
];

export default function PricingPage({ currentTier = 'free', onOpenAuth, authUser }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(null);

  const handleSubscribe = async (tier) => {
    if (!authUser) {
      onOpenAuth();
      return;
    }
    if (tier === 'free') {
      navigate('/');
      return;
    }

    setLoading(tier);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        onOpenAuth();
        return;
      }

      const response = await fetch(`${API_BASE}/billing/create-checkout-session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ tier }),
      });

      const data = await response.json();

      if (response.ok && data.url) {
        window.location.href = data.url;
      } else {
        alert(data.error || 'Failed to create checkout session');
      }
    } catch (error) {
      console.error('Subscription error:', error);
      alert('Failed to start subscription. Please try again.');
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="pricing-page">
      {/* Header */}
      <section className="pricing-hero">
        <div className="hero-badge">
          <Sparkles size={14} />
          <span>Simple, Transparent Pricing</span>
        </div>
        <h1 className="hero-title">Choose Your Plan</h1>
        <p className="hero-subtitle">
          Start free and scale as you grow. No hidden fees. Cancel anytime.
        </p>
      </section>

      {/* Plans */}
      <section className="pricing-grid">
        {PLANS.map((plan) => (
          <div
            key={plan.tier}
            className={`pricing-card panel-glass ${plan.popular ? 'popular' : ''} ${currentTier === plan.tier ? 'current' : ''}`}
          >
            {plan.popular && (
              <div className="popular-badge">
                <Sparkles size={12} />
                Most Popular
              </div>
            )}
            {currentTier === plan.tier && (
              <div className="current-badge">Current Plan</div>
            )}

            <h3 className="plan-name">{plan.name}</h3>
            <div className="plan-price">
              <span className="price-currency">$</span>
              <span className="price-value">{plan.price}</span>
              <span className="price-period">/month</span>
            </div>

            <ul className="plan-features">
              {plan.features.map((feature, i) => (
                <li key={i}>
                  <Check size={16} className="feature-check" />
                  {feature}
                </li>
              ))}
            </ul>

            <button
              className={`plan-cta ${currentTier === plan.tier ? 'current' : ''}`}
              disabled={currentTier === plan.tier || loading !== null}
              onClick={() => handleSubscribe(plan.tier)}
            >
              {loading === plan.tier ? (
                'Redirecting...'
              ) : currentTier === plan.tier ? (
                'Current Plan'
              ) : !authUser ? (
                <>
                  Get Started
                  <ArrowRight size={16} />
                </>
              ) : (
                <>
                  {plan.cta}
                  <ArrowRight size={16} />
                </>
              )}
            </button>
          </div>
        ))}
      </section>

      {/* Pay-per-use note */}
      <section className="pricing-note panel-glass">
        <h3>Pay-Per-Use with x402</h3>
        <p>
          Not ready for a subscription? Use x402 crypto payments to pay only for what you consume.
          No account required — just connect your wallet and go.
        </p>
      </section>

      {/* FAQ / Trust */}
      <section className="pricing-trust">
        <div className="trust-grid">
          <div className="trust-item">
            <strong>Cancel Anytime</strong>
            <span>No long-term contracts. Cancel with one click.</span>
          </div>
          <div className="trust-item">
            <strong>Secure by Default</strong>
            <span>All data is encrypted in transit and at rest.</span>
          </div>
          <div className="trust-item">
            <strong>Decentralized Compute</strong>
            <span>Powered by a network of independent providers.</span>
          </div>
        </div>
      </section>
    </div>
  );
}
