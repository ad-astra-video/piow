import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { supabase } from '../lib/supabase';

const API_BASE = `${window.location.origin}/api/v1`;

export default function BillingPage({ billingUsage, accountDataLoading, onRefreshAccountData }) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState(null);

  const loading = accountDataLoading && !billingUsage;

  const handleCancel = async () => {
    if (!confirm('Are you sure you want to cancel your subscription? You will lose access to paid features at the end of your billing period.')) {
      return;
    }
    setCancelling(true);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const response = await fetch(`${API_BASE}/billing/cancel`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
      });

      if (response.ok) {
        alert('Subscription cancelled successfully');
        await onRefreshAccountData?.();
      } else {
        const data = await response.json();
        alert(data.error || 'Failed to cancel subscription');
      }
    } catch (err) {
      console.error('Cancel error:', err);
      alert('Failed to cancel subscription');
    } finally {
      setCancelling(false);
    }
  };

  const formatQuota = (info) => {
    if (!info) return '—';
    if (info.unlimited) return 'Unlimited';
    const used = info.used || 0;
    const limit = info.limit || 0;
    const remaining = info.remaining || 0;
    const pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
    return { used, limit, remaining, pct };
  };

  if (loading) {
    return <div className="billing-page"><h2>Billing</h2><p>Loading...</p></div>;
  }

  if (error) {
    return <div className="billing-page"><h2>Billing</h2><p className="error">{error}</p></div>;
  }

  if (!billingUsage) {
    return <div className="billing-page"><h2>Billing & Usage</h2><p className="error">Billing data is unavailable right now.</p></div>;
  }

  const tier = billingUsage?.tier || 'free';
  const status = billingUsage?.status || 'none';

  return (
    <div className="billing-page">
      <h2>Billing & Usage</h2>

      {/* Current Plan */}
      <div className="billing-section">
        <h3>Current Plan</h3>
        <div className="plan-info">
          <span className="tier-badge">{tier.charAt(0).toUpperCase() + tier.slice(1)}</span>
          {status === 'trialing' && <span className="trial-badge">Trial</span>}
          {status === 'active' && <span className="active-badge">Active</span>}
          {status === 'canceled' && <span className="canceled-badge">Canceled</span>}
        </div>
        <Link to="/billing/plans" className="secondary-button">
          {tier === 'free' || status === 'none' ? 'Choose a Plan' : 'Change Plan'}
        </Link>
        {tier !== 'free' && status !== 'canceled' && (
          <button
            className="cancel-button"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? 'Cancelling...' : 'Cancel Subscription'}
          </button>
        )}
      </div>

      {/* Usage */}
      {billingUsage && (
        <div className="billing-section">
          <h3>Usage (Last 30 Days)</h3>
          <div className="usage-grid">
            {/* Transcription (combined CPU+GPU) */}
            <div className="usage-item">
              <label>Transcription</label>
              {(() => {
                const tx = formatQuota(billingUsage.usage?.transcription);
                if (tx === '—' || typeof tx === 'string') return <span>{tx}</span>;
                return (
                  <div className="usage-bar-container">
                    <div className="usage-bar">
                      <div
                        className="usage-fill"
                        style={{ width: `${Math.min(tx.pct, 100)}%` }}
                      />
                    </div>
                    <span className="usage-text">
                      {tx.used.toFixed(1)} / {tx.limit === -1 ? '∞' : tx.limit} min ({tx.pct}%)
                    </span>
                  </div>
                );
              })()}
            </div>

          </div>
        </div>
      )}

      {/* Plan Limits Reference */}
      <div className="billing-section">
        <h3>Plan Limits</h3>
        <table className="limits-table">
          <thead>
            <tr>
              <th>Feature</th>
              <th>Free</th>
              <th>Starter</th>
              <th>Pro</th>
              <th>Enterprise</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Transcription</td>
              <td>1 hr/day (30 hr/mo)</td>
              <td>3 hr/day (90 hr/mo)</td>
              <td>8 hr/day (240 hr/mo)</td>
              <td>Unlimited</td>
            </tr>
            <tr>
              <td>Priority</td>
              <td>Low (queue delays)</td>
              <td>Normal</td>
              <td>High</td>
              <td>Highest</td>
            </tr>
            <tr>
              <td>Price</td>
              <td>$0</td>
              <td>$15/mo</td>
              <td>$39/mo</td>
              <td>$99/mo</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}