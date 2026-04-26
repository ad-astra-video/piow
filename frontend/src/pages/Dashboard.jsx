import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Mic, Upload, Globe, Languages, Clock, BarChart3, FileAudio, Link as LinkIcon, ArrowRight } from 'lucide-react';
import { api } from '../lib/api';

export default function Dashboard() {
  const [recentItems, setRecentItems] = useState([]);
  const [usage, setUsage] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    setLoading(true);
    try {
      const [historyRes, usageRes, subRes] = await Promise.allSettled([
        api.getHistory({ limit: 5 }),
        api.getUsageDetails(30),
        api.getSubscription(),
      ]);
      if (historyRes.status === 'fulfilled') setRecentItems(historyRes.value?.items || []);
      if (usageRes.status === 'fulfilled') setUsage(usageRes.value);
      if (subRes.status === 'fulfilled') setSubscription(subRes.value);
    } catch (e) {
      console.error('Dashboard load error:', e);
    } finally {
      setLoading(false);
    }
  };

  const formatDuration = (sec) => {
    if (!sec) return '0s';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  const quickActions = [
    { to: '/transcribe/stream', icon: Mic, label: 'Live Stream', desc: 'Real-time microphone transcription' },
    { to: '/transcribe/file', icon: Upload, label: 'Upload File', desc: 'Transcribe audio/video files' },
    { to: '/transcribe/url', icon: LinkIcon, label: 'URL', desc: 'Transcribe from a link' },
    { to: '/translate', icon: Languages, label: 'Translate', desc: 'Text or transcription translation' },
  ];

  return (
    <div className="dashboard">
      <h1 className="page-title">Dashboard</h1>

      {/* Quick Actions */}
      <section className="quick-actions">
        <h2 className="section-title">Quick Actions</h2>
        <div className="action-grid">
          {quickActions.map((a) => (
            <Link key={a.label} to={a.to} className="action-card panel-glass">
              <a.icon className="action-icon" size={28} />
              <div>
                <strong>{a.label}</strong>
                <span>{a.desc}</span>
              </div>
              <ArrowRight size={18} className="action-arrow" />
            </Link>
          ))}
        </div>
      </section>

      {/* Usage Snapshot */}
      {usage && (
        <section className="usage-snapshot">
          <h2 className="section-title">This Month</h2>
          <div className="stat-grid">
            <div className="stat-card panel-glass">
              <Clock size={22} />
              <div>
                <span className="stat-value">{formatDuration(usage.transcription?.total_seconds)}</span>
                <span className="stat-label">Transcribed</span>
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
        </section>
      )}

      {/* Recent Activity */}
      <section className="recent-activity">
        <div className="section-header">
          <h2 className="section-title">Recent Activity</h2>
          <Link to="/history" className="view-all">View All →</Link>
        </div>
        {loading ? (
          <div className="loading-state">Loading...</div>
        ) : recentItems.length === 0 ? (
          <div className="empty-state panel-glass">
            <p>No activity yet.</p>
            <span>Start a transcription or translation to see it here.</span>
          </div>
        ) : (
          <div className="recent-list">
            {recentItems.map((item) => (
              <div key={`${item._type}-${item.id}`} className="recent-item panel-glass">
                <div className="recent-meta">
                  <span className={`badge ${item._type}`}>{item._type}</span>
                  <span className="recent-date">{new Date(item.created_at).toLocaleString()}</span>
                </div>
                <p className="recent-text">
                  {item._type === 'transcription'
                    ? (item.text?.slice(0, 120) + (item.text?.length > 120 ? '…' : ''))
                    : (item.original_text?.slice(0, 120) + (item.original_text?.length > 120 ? '…' : ''))}
                </p>
                <div className="recent-footer">
                  <span className="lang-tag">{item.language || item.source_language} → {item.target_language || '—'}</span>
                  {item.duration ? <span>{formatDuration(item.duration)}</span> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
