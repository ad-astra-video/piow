import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { Clock, FileAudio, Globe, BarChart3, Calendar } from 'lucide-react';
import { api } from '../lib/api';

export default function UsagePage() {
  const [usage, setUsage] = useState(null);
  const [billingUsage, setBillingUsage] = useState(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [days]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [uRes, bRes] = await Promise.allSettled([
        api.getUsageDetails(days),
        api.getBillingUsage(),
      ]);
      if (uRes.status === 'fulfilled') setUsage(uRes.value);
      if (bRes.status === 'fulfilled') setBillingUsage(bRes.value);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const chartData = usage?.daily_breakdown?.map((d) => ({
    date: d.date.slice(5),
    Transcription: d.transcription_seconds,
    Translation: d.translation_chars,
  })) || [];

  const formatDuration = (sec) => {
    if (!sec) return '0s';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <div className="usage-page">
      <div className="usage-header">
        <h1 className="page-title">Usage Stats</h1>
        <div className="days-selector">
          <Calendar size={16} />
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading usage data...</div>
      ) : !usage ? (
        <div className="empty-state panel-glass">Unable to load usage data.</div>
      ) : (
        <>
          {/* Summary Cards */}
          <div className="stat-grid">
            <div className="stat-card panel-glass">
              <Clock size={22} />
              <div>
                <span className="stat-value">{formatDuration(usage.transcription?.total_seconds)}</span>
                <span className="stat-label">Audio Transcribed</span>
              </div>
            </div>
            <div className="stat-card panel-glass">
              <FileAudio size={22} />
              <div>
                <span className="stat-value">{usage.transcription?.job_count || 0}</span>
                <span className="stat-label">Transcription Jobs</span>
              </div>
            </div>
            <div className="stat-card panel-glass">
              <Globe size={22} />
              <div>
                <span className="stat-value">{(usage.translation?.total_characters || 0).toLocaleString()}</span>
                <span className="stat-label">Chars Translated</span>
              </div>
            </div>
            <div className="stat-card panel-glass">
              <BarChart3 size={22} />
              <div>
                <span className="stat-value">{usage.translation?.job_count || 0}</span>
                <span className="stat-label">Translation Jobs</span>
              </div>
            </div>
          </div>

          {/* Quota Info */}
          {billingUsage && (
            <section className="panel-glass quota-panel">
              <h3>Plan Quotas</h3>
              <p className="quota-tier">Current tier: <strong>{billingUsage.tier || 'free'}</strong></p>
              <div className="quota-bars">
                {billingUsage.usage?.transcription && (
                  <div className="quota-item">
                    <span>Transcription</span>
                    <div className="quota-bar-wrap">
                      <div
                        className="quota-bar"
                        style={{
                          width: `${Math.min(100, (billingUsage.usage.transcription.used / (billingUsage.usage.transcription.limit || 1)) * 100)}%`,
                        }}
                      />
                    </div>
                    <span>{billingUsage.usage.transcription.used} / {billingUsage.usage.transcription.limit === -1 ? '∞' : billingUsage.usage.transcription.limit}</span>
                  </div>
                )}
                {billingUsage.usage?.translation && (
                  <div className="quota-item">
                    <span>Translation</span>
                    <div className="quota-bar-wrap">
                      <div
                        className="quota-bar"
                        style={{
                          width: `${Math.min(100, (billingUsage.usage.translation.used / (billingUsage.usage.translation.limit || 1)) * 100)}%`,
                        }}
                      />
                    </div>
                    <span>{billingUsage.usage.translation.used} / {billingUsage.usage.translation.limit === -1 ? '∞' : billingUsage.usage.translation.limit}</span>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Chart */}
          {chartData.length > 0 && (
            <section className="panel-glass chart-panel">
              <h3>Daily Activity</h3>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="date" stroke="rgba(255,255,255,0.3)" fontSize={12} />
                  <YAxis stroke="rgba(255,255,255,0.3)" fontSize={12} />
                  <Tooltip
                    contentStyle={{ background: '#1a1a2e', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }}
                    labelStyle={{ color: '#fff' }}
                  />
                  <Legend />
                  <Bar dataKey="Transcription" fill="#60a5fa" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="Translation" fill="#34d399" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </section>
          )}

          {/* Source Breakdown */}
          {usage.transcription?.source_breakdown && Object.keys(usage.transcription.source_breakdown).length > 0 && (
            <section className="panel-glass breakdown-panel">
              <h3>Transcription Source Breakdown</h3>
              <div className="breakdown-list">
                {Object.entries(usage.transcription.source_breakdown).map(([src, seconds]) => (
                  <div key={src} className="breakdown-item">
                    <span className="breakdown-label">{src}</span>
                    <span className="breakdown-value">{formatDuration(seconds)}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
