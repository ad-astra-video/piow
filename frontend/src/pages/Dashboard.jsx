import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Mic, Clock, BarChart3, FileAudio, ArrowRight, Download } from 'lucide-react';
import { api } from '../lib/api';
import { downloadTranscription } from '../lib/download';

export default function Dashboard({ usageSnapshot }) {
  const [recentItems, setRecentItems] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    setLoading(true);
    try {
      const historyRes = await api.getHistory({ limit: 5 });
      setRecentItems(historyRes?.items || []);
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
    { to: '/history', icon: FileAudio, label: 'History', desc: 'Review saved stream transcripts' },
    { to: '/usage', icon: BarChart3, label: 'Usage', desc: 'Track stream usage and quotas' },
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
      {usageSnapshot && (
        <section className="usage-snapshot">
          <h2 className="section-title">This Month</h2>
          <div className="stat-grid">
            <div className="stat-card panel-glass">
              <Clock size={22} />
              <div>
                <span className="stat-value">{formatDuration(usageSnapshot.transcription?.total_seconds)}</span>
                <span className="stat-label">Transcribed</span>
              </div>
            </div>
            <div className="stat-card panel-glass">
              <FileAudio size={22} />
              <div>
                <span className="stat-value">{usageSnapshot.transcription?.job_count || 0}</span>
                <span className="stat-label">Transcription Jobs</span>
              </div>
            </div>
            <div className="stat-card panel-glass">
              <BarChart3 size={22} />
              <div>
                <span className="stat-value">{usageSnapshot.transcription?.total_words?.toLocaleString?.() || 0}</span>
                <span className="stat-label">Words Transcribed</span>
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
                  {item._type === 'transcription' && item.has_analysis ? (
                    <span className="badge analysis">analysis{item.analysis_mode ? ` • ${item.analysis_mode.replace('_only', '').replace('_', ' ')}` : ''}{item.analysis_source ? ` • ${item.analysis_source}` : ''}</span>
                  ) : null}
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
                  {item._type === 'transcription' && (
                    <div className="recent-downloads">
                      <button className="icon-btn-sm" onClick={() => downloadTranscription(item, 'txt')} title="Download TXT">
                        <Download size={12} />
                      </button>
                      <button className="icon-btn-sm" onClick={() => downloadTranscription(item, 'srt')} title="Download SRT">
                        SRT
                      </button>
                      <button className="icon-btn-sm" onClick={() => downloadTranscription(item, 'vtt')} title="Download VTT">
                        VTT
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
