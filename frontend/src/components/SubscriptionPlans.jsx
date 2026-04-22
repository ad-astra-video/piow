import React, { useState } from 'react';
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
    cta: 'Current Plan',
    disabled: true,
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

export default function SubscriptionPlans({ currentTier = 'free', onSubscribe }) {
  const [loading, setLoading] = useState(null);

  const handleSubscribe = async (tier) => {
    setLoading(tier);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        alert('Please sign in to subscribe');
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
        // Redirect to Stripe Checkout
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
    <div className="subscription-plans">
      <h2>Choose Your Plan</h2>
      <div className="plans-grid">
        {PLANS.map((plan) => (
          <div
            key={plan.tier}
            className={`plan-card ${plan.popular ? 'popular' : ''} ${currentTier === plan.tier ? 'current' : ''}`}
          >
            {plan.popular && <div className="popular-badge">Most Popular</div>}
            <h3>{plan.name}</h3>
            <div className="plan-price">
              ${plan.price}<span>/month</span>
            </div>
            <ul className="plan-features">
              {plan.features.map((feature, i) => (
                <li key={i}>{feature}</li>
              ))}
            </ul>
            <button
              className={`plan-button ${currentTier === plan.tier ? 'current' : ''}`}
              disabled={plan.disabled || currentTier === plan.tier || loading !== null}
              onClick={() => handleSubscribe(plan.tier)}
            >
              {loading === plan.tier
                ? 'Redirecting...'
                : currentTier === plan.tier
                  ? 'Current Plan'
                  : plan.cta}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}