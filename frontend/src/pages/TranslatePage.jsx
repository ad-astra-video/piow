import React, { useState, useEffect } from 'react';
import { Languages, Loader2, CheckCircle, AlertCircle, Copy, FileText } from 'lucide-react';
import { api } from '../lib/api';

export default function TranslatePage() {
  const [tab, setTab] = useState('text'); // 'text' | 'transcription'
  const [text, setText] = useState('');
  const [sourceLang, setSourceLang] = useState('en');
  const [targetLang, setTargetLang] = useState('es');
  const [languages, setLanguages] = useState([]);
  const [transcriptions, setTranscriptions] = useState([]);
  const [selectedTranscription, setSelectedTranscription] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getLanguages().then(res => setLanguages(res.languages || [])).catch(() => {});
    api.listTranscriptions({ limit: 100 }).then(res => setTranscriptions(res.transcriptions || [])).catch(() => {});
  }, []);

  const handleTranslate = async () => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      let res;
      if (tab === 'text') {
        if (!text.trim()) { setLoading(false); return; }
        res = await api.translateText({ text: text.trim(), source_language: sourceLang, target_language: targetLang });
      } else {
        if (!selectedTranscription) { setLoading(false); return; }
        res = await api.translateTranscription({ transcription_id: selectedTranscription, target_language: targetLang });
      }
      setResult(res);
    } catch (err) {
      setError(err.message || 'Translation failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="transcribe-page">
      <h1 className="page-title">Translate</h1>

      <div className="translate-tabs">
        <button className={tab === 'text' ? 'active' : ''} onClick={() => setTab('text')}>
          <FileText size={16} /> Direct Text
        </button>
        <button className={tab === 'transcription' ? 'active' : ''} onClick={() => setTab('transcription')}>
          <Languages size={16} /> Transcription
        </button>
      </div>

      <div className="transcribe-layout">
        <section className="panel-glass upload-panel">
          {tab === 'text' ? (
            <div className="form-row">
              <label>Text to Translate</label>
              <textarea
                rows={6}
                placeholder="Enter text here..."
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
          ) : (
            <div className="form-row">
              <label>Select Transcription</label>
              <select value={selectedTranscription} onChange={(e) => setSelectedTranscription(e.target.value)}>
                <option value="">Choose a transcription…</option>
                {transcriptions.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.text?.slice(0, 60)}{t.text?.length > 60 ? '…' : ''} ({t.language})
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="form-row lang-pair">
            {tab === 'text' && (
              <div>
                <label>From</label>
                <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                  {languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
                </select>
              </div>
            )}
            <div>
              <label>To</label>
              <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                {languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
              </select>
            </div>
          </div>

          <button
            className="primary-button full-width"
            onClick={handleTranslate}
            disabled={loading || (tab === 'text' ? !text.trim() : !selectedTranscription)}
          >
            {loading ? <><Loader2 className="spin" size={18} /> Translating…</> : 'Translate'}
          </button>

          {error && <p className="error-banner"><AlertCircle size={16} /> {error}</p>}
        </section>

        {result && (
          <section className="panel-glass result-panel">
            <div className="result-header">
              <h2><CheckCircle size={20} /> Translation</h2>
              <button className="icon-btn" onClick={() => navigator.clipboard.writeText(result.translated_text)} title="Copy">
                <Copy size={16} />
              </button>
            </div>
            <div className="result-meta">
              <span>{result.source_language} → {result.target_language}</span>
              {result.token_count ? <span>{result.token_count} tokens</span> : null}
            </div>
            <div className="result-text">{result.translated_text}</div>
            {tab === 'transcription' && result.original_text && (
              <details className="result-segments">
                <summary>Original Text</summary>
                <p className="original-text">{result.original_text}</p>
              </details>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
