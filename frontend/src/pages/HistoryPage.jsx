import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Trash2, Languages, Mic, Upload, Link as LinkIcon, Globe, Clock, Search, Filter, Maximize2, X, Download } from 'lucide-react';
import { api } from '../lib/api';
import { downloadTranscription } from '../lib/download';
import SentenceList from '../components/SentenceList';
import { splitSentences } from '../lib/download';

export default function HistoryPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [modalItem, setModalItem] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (filter !== 'all') params.type = filter;
      const res = await api.getHistory(params);
      setItems(res.items || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [filter]);

  const handleDelete = async (item) => {
    if (!confirm('Delete this item?')) return;
    try {
      if (item._type === 'transcription') {
        await api.deleteTranscription(item.id);
      } else {
        await api.deleteTranslation(item.id);
      }
      load();
    } catch (e) {
      alert('Failed to delete: ' + e.message);
    }
  };

  const filtered = items.filter((item) => {
    if (!search) return true;
    const q = search.toLowerCase();
    const text = (item.text || item.original_text || '').toLowerCase();
    return text.includes(q);
  });

  const formatDate = (d) => new Date(d).toLocaleString();
  const formatDuration = (s) => s ? `${Math.floor(s / 60)}m ${s % 60}s` : '';



  const sourceIcon = (type, src) => {
    if (type === 'translation') return <Globe size={14} />;
    if (src === 'stream' || src === 'whip') return <Mic size={14} />;
    if (src === 'url') return <LinkIcon size={14} />;
    return <Upload size={14} />;
  };

  return (
    <div className="history-page">
      <h1 className="page-title">History</h1>

      <div className="history-controls panel-glass">
        <div className="search-wrap">
          <Search size={16} />
          <input
            type="text"
            placeholder="Search text..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="filter-wrap">
          <Filter size={16} />
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="transcription">Transcriptions</option>
            <option value="translation">Translations</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading...</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state panel-glass">
          <p>No items found.</p>
        </div>
      ) : (
        <div className="history-list">
          {filtered.map((item) => (
            <div key={`${item._type}-${item.id}`} className="history-item panel-glass">
              <div className="history-main">
                <div className="history-header">
                  <span className={`badge ${item._type}`}>
                    {sourceIcon(item._type, item.source_type)} {item._type}
                  </span>
                  <span className="history-date">{formatDate(item.created_at)}</span>
                </div>
                {item._type === 'transcription' ? (
                  <div className="history-sentences preview" onClick={() => setModalItem(item)}>
                    {splitSentences(item.text).slice(0, 5).map((sentence, i) => (
                      <p key={i} className="history-sentence">{sentence}</p>
                    ))}
                    {splitSentences(item.text).length > 5 && (
                      <p className="history-more">+{splitSentences(item.text).length - 5} more…</p>
                    )}
                  </div>
                ) : (
                  <p className="history-text">{item.translated_text || ''}</p>
                )}
                <div className="history-footer">
                  <span className="lang-tag">
                    {item.language || item.source_language}
                    {item.target_language ? ` → ${item.target_language}` : ''}
                  </span>
                  {item.duration ? <span className="duration-tag"><Clock size={12} /> {formatDuration(item.duration)}</span> : null}
                  {item.word_count ? <span>{item.word_count} words</span> : null}
                  {item.token_count ? <span>{item.token_count} tokens</span> : null}
                </div>
              </div>
              <div className="history-actions">
                {item._type === 'transcription' && (
                  <>
                    <Link to={`/translate?transcription=${item.id}`} className="icon-btn" title="Translate">
                      <Languages size={16} />
                    </Link>
                    <button className="icon-btn" onClick={() => downloadTranscription(item, 'txt')} title="Download TXT">
                      <Download size={16} />
                    </button>
                    <button className="icon-btn" onClick={() => downloadTranscription(item, 'srt')} title="Download SRT">
                      SRT
                    </button>
                    <button className="icon-btn" onClick={() => downloadTranscription(item, 'vtt')} title="Download VTT">
                      VTT
                    </button>
                  </>
                )}
                <button className="icon-btn danger" onClick={() => handleDelete(item)} title="Delete">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modalItem && (
        <div className="history-modal-overlay" onClick={() => setModalItem(null)}>
          <div className="history-modal panel-glass" onClick={(e) => e.stopPropagation()}>
            <div className="history-modal-header">
              <div className="history-modal-title">
                <span className={`badge ${modalItem._type}`}>
                  {sourceIcon(modalItem._type, modalItem.source_type)} {modalItem._type}
                </span>
                <span className="history-date">{formatDate(modalItem.created_at)}</span>
              </div>
              <button className="icon-btn" onClick={() => setModalItem(null)} title="Close">
                <X size={16} />
              </button>
            </div>
            <div className="history-modal-body">
              {modalItem._type === 'transcription' ? (
                <SentenceList
                  transcriptionId={modalItem.id}
                  sentences={splitSentences(modalItem.text).map((s) => ({ text: s }))}
                />
              ) : (
                <p className="history-text">{modalItem.translated_text || ''}</p>
              )}
            </div>
            <div className="history-modal-footer">
              <span className="lang-tag">
                {modalItem.language || modalItem.source_language}
                {modalItem.target_language ? ` → ${modalItem.target_language}` : ''}
              </span>
              {modalItem.duration ? <span className="duration-tag"><Clock size={12} /> {formatDuration(modalItem.duration)}</span> : null}
              {modalItem.word_count ? <span>{modalItem.word_count} words</span> : null}
              {modalItem.token_count ? <span>{modalItem.token_count} tokens</span> : null}
              {modalItem._type === 'transcription' && (
                <div className="history-modal-downloads">
                  <button className="icon-btn" onClick={() => downloadTranscription(modalItem, 'txt')} title="Download TXT">
                    <Download size={14} />
                  </button>
                  <button className="icon-btn" onClick={() => downloadTranscription(modalItem, 'srt')} title="Download SRT">
                    SRT
                  </button>
                  <button className="icon-btn" onClick={() => downloadTranscription(modalItem, 'vtt')} title="Download VTT">
                    VTT
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
