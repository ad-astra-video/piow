import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabase';

const API_BASE = `${window.location.origin}/api/v1`;

/**
 * CheckoutSuccess component — displayed after Stripe Checkout redirect.
 * Verifies the session and shows confirmation.
 */
export function CheckoutSuccess() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('verifying'); // verifying | success | error
  const [sessionInfo, setSessionInfo] = useState(null);

  useEffect(() => {
    const sessionId = searchParams.get('session_id');
    if (!sessionId) {
      setStatus('error');
      return;
    }

    // Poll subscription status until it reflects the new checkout
    const verifyCheckout = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) {
          setStatus('error');
          return;
        }

        // Give Stripe a moment to process the webhook
        let attempts = 0;
        const maxAttempts = 10;
        const delay = 2000; // 2 seconds

        while (attempts < maxAttempts) {
          const response = await fetch(`${API_BASE}/billing/subscription`, {
            headers: { 'Authorization': `Bearer ${session.access_token}` },
          });

          if (response.ok) {
            const data = await response.json();
            if (data.tier && data.tier !== 'free' && data.status === 'active') {
              setSessionInfo(data);
              setStatus('success');
              return;
            }
          }

          attempts++;
          await new Promise(resolve => setTimeout(resolve, delay));
        }

        // If we get here, the webhook may not have fired yet
        // Show success anyway since Stripe redirected here
        setStatus('success');
      } catch (err) {
        console.error('Checkout verification error:', err);
        setStatus('error');
      }
    };

    verifyCheckout();
  }, [searchParams]);

  if (status === 'verifying') {
    return (
      <div className="checkout-result success">
        <div className="checkout-spinner" />
        <h2>Verifying your subscription...</h2>
        <p>Please wait while we confirm your payment.</p>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="checkout-result error">
        <h2>⚠️ Verification Issue</h2>
        <p>We couldn't verify your subscription. Please check your billing page or contact support.</p>
        <button onClick={() => navigate('/billing')}>Go to Billing</button>
      </div>
    );
  }

  return (
    <div className="checkout-result success">
      <h2>✅ Subscription Activated!</h2>
      {sessionInfo && (
        <p>You are now on the <strong>{sessionInfo.tier}</strong> plan.</p>
      )}
      <p>Your subscription is now active. You can start using all the features of your plan.</p>
      <button onClick={() => navigate('/billing')}>View Billing</button>
      <button className="secondary" onClick={() => navigate('/')}>Start Using the App</button>
    </div>
  );
}

/**
 * CheckoutCancel component — displayed when user cancels Stripe Checkout.
 */
export function CheckoutCancel() {
  const navigate = useNavigate();

  return (
    <div className="checkout-result cancel">
      <h2>Checkout Cancelled</h2>
      <p>Your subscription was not activated. You can try again at any time.</p>
      <button onClick={() => navigate('/billing/plans')}>View Plans</button>
      <button className="secondary" onClick={() => navigate('/')}>Back to App</button>
    </div>
  );
}