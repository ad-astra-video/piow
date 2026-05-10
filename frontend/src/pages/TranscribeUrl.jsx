import React, { useState, useEffect } from 'react';
import { Link as LinkIcon, Loader2, CheckCircle, AlertCircle, Download, Copy, HelpCircle } from 'lucide-react';
import { api } from '../lib/api';
import SentenceList from '../components/SentenceList';
import { splitSentences } from '../lib/download';

const TRANSCRIPTION_MODES = [
  { id: 'standard', label: 'Standard Text', description: 'Plain transcription output.' },
  { id: 'speakers', label: 'Speaker Segments', description: 'Add per-segment speaker labels.' },
  { id: 'timestamps', label: 'Word Timestamps', description: 'Add timestamp metadata for words.' },
];

const MODE_LABELS = {
  standard: 'Standard Text',
  speakers: 'Speaker Segments',
  timestamps: 'Word Timestamps',
};

export default function TranscribeUrl() {
  const [url, setUrl] = useState('');
  const [languages, setLanguages] = useState([]);
  const [selectedModes, setSelectedModes] = useState(['standard']);
  const [translate, setTranslate] = useState(false);
  const [fromLang, setFromLang] = useState('en');
  const [toLang, setToLang] = useState('es');
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getLanguages().then(res => setLanguages(res.languages || [])).catch(() => {});
  }, []);

  const nonEnglishLangs = languages.filter(l => l.code !== 'en');
  const toLangLocked = fromLang !== 'en';

  const handleFromLangChange = (code) => {
    setFromLang(code);
    if (code !== 'en') {
      setToLang('en');
    } else if (toLang === 'en') {
      setToLang(nonEnglishLangs[0]?.code || 'es');
    }
  };

  const toggleMode = (modeId) => {
    setSelectedModes((prev) => {
      if (prev.includes(modeId)) {
        if (prev.length === 1) return prev;
        return prev.filter((m) => m !== modeId);
      }
      return [...prev, modeId];
    });
  };

  const modeFlags = (modeId) => ({
    with_speakers: modeId === 'speakers',
    with_word_timestamps: modeId === 'timestamps',
  });

  const handleSubmit = async () => {
    if (!url.trim() || selectedModes.length === 0) return;
    setLoading(true);
    setError('');
    setResults([]);
    setProgress({ done: 0, total: selectedModes.length });
    try {
      const requests = selectedModes.map((modeId) => {
        const flags = modeFlags(modeId);
        const body = {
          audio_url: url.trim(),
          language: translate ? fromLang : 'en',
          with_speakers: flags.with_speakers,
          with_word_timestamps: flags.with_word_timestamps,
        };
        if (translate) {
          body.source_language = fromLang;
          body.target_language = toLangLocked ? 'en' : toLang;
        }

        return api.transcribeUrl(body)
          .then((res) => {
            setProgress((prev) => ({ ...prev, done: prev.done + 1 }));
            return {
              mode: modeId,
              ok: true,
              data: res,
            };
          })
          .catch((err) => {
            setProgress((prev) => ({ ...prev, done: prev.done + 1 }));
            return {
              mode: modeId,
              ok: false,
              error: err?.message || 'Transcription failed',
            };
          });
      });

      const modeResults = await Promise.all(requests);
      const successful = modeResults.filter((r) => r.ok);
      const failed = modeResults.filter((r) => !r.ok);

      if (successful.length > 0) {
        setResults(successful.map((r) => ({ mode: r.mode, ...r.data })));
      }

      if (failed.length > 0) {
        const failures = failed.map((f) => `${MODE_LABELS[f.mode]}: ${f.error}`).join(' | ');
        setError(successful.length > 0 ? `Some modes failed: ${failures}` : failures);
      }
    } catch (err) {
      setError(err.message || 'Transcription failed');
    } finally {
      setLoading(false);
    }
  };

  const copyText = (entry) => {
    if (entry?.text) navigator.clipboard.writeText(entry.text);
  };

  const downloadJson = (entry) => {
    if (!entry) return;
    const blob = new Blob([JSON.stringify(entry, null, 2)], { type: 'application/json' });
    const urlObj = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = urlObj;
    a.download = `transcription-${entry.mode}-${entry.id || 'result'}.json`;
    a.click();
    URL.revokeObjectURL(urlObj);
  };

  return (
    <div className="transcribe-page">
      <h1 className="page-title">Transcribe from URL</h1>

      <div className="transcribe-layout">
        <section className="panel-glass upload-panel">
          <div className="form-row">
            <label>Audio/Video URL</label>
            <div className="url-input-wrap">
              <LinkIcon size={18} />
              <input
                type="url"
                placeholder="https://example.com/audio.mp3"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
          </div>

          <div className="form-row">
            <label>Transcription modes</label>
            <div className="mode-grid">
              {TRANSCRIPTION_MODES.map((mode) => (
                <label key={mode.id} className="mode-option">
                  <input
                    type="checkbox"
                    checked={selectedModes.includes(mode.id)}
                    onChange={() => toggleMode(mode.id)}
                  />
                  <span>
                    <strong>{mode.label}</strong>
                    <small>{mode.description}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="form-row form-row-check">
            <label>
              <input
                type="checkbox"
                checked={translate}
                onChange={(e) => setTranslate(e.target.checked)}
              />
              Translate
            </label>
          </div>

          {translate && (
            <div className="translate-pair">
              <div className="form-row">
                <label>From</label>
                <select value={fromLang} onChange={(e) => handleFromLangChange(e.target.value)}>
                  {languages.map((lang) => (
                    <option key={lang.code} value={lang.code}>{lang.name}</option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <label className="translate-to-label">
                  To
                  {toLangLocked && (
                    <span className="help-icon" aria-label="Only translation to English is supported when the source language is not English">
                      <HelpCircle size={13} />
                      <span className="help-tooltip">Non-English audio can only be translated to English.</span>
                    </span>
                  )}
                </label>
                <select
                  value={toLangLocked ? 'en' : toLang}
                  onChange={(e) => setToLang(e.target.value)}
                  disabled={toLangLocked}
                >
                  {toLangLocked
                    ? <option value="en">English</option>
                    : nonEnglishLangs.map((lang) => (
                        <option key={lang.code} value={lang.code}>{lang.name}</option>
                      ))
                  }
                </select>
              </div>
            </div>
          )}

          <button
            className="primary-button full-width"
            onClick={handleSubmit}
            disabled={!url.trim() || loading || selectedModes.length === 0}
          >
            {loading
              ? <><Loader2 className="spin" size={18} /> Processing {progress.done}/{progress.total}…</>
              : 'Transcribe'}
          </button>

          {error && <p className="error-banner"><AlertCircle size={16} /> {error}</p>}

          {languages.length > 0 && (
            <div className="supported-langs">
              <span className="supported-langs-label">Supported languages</span>
              <div className="supported-langs-list">
                {languages.map((lang) => (
                  <span key={lang.code} className="lang-tag">{lang.name}</span>
                ))}
              </div>
            </div>
          )}
        </section>

        {results.length > 0 && (
          <div className="mode-results-list">
            {results.map((entry) => (
              <section key={entry.mode} className="panel-glass result-panel">
                <div className="result-header">
                  <h2><CheckCircle size={20} /> {MODE_LABELS[entry.mode]} Complete</h2>
                  <div className="result-actions">
                    <button className="icon-btn" onClick={() => copyText(entry)} title="Copy text"><Copy size={16} /></button>
                    <button className="icon-btn" onClick={() => downloadJson(entry)} title="Download JSON"><Download size={16} /></button>
                  </div>
                </div>
                <div className="result-meta">
                  <span>Language: {entry.language}</span>
                  {entry.target_language && <span>→ {entry.target_language.toUpperCase()}</span>}
                  {entry.duration ? <span>Duration: {entry.duration}s</span> : null}
                  {entry.word_count ? <span>Words: {entry.word_count}</span> : null}
                  {entry.words ? <span>Word timestamps: {entry.words.length}</span> : null}
                  {entry.speakers ? <span>Speakers: {entry.speakers.length}</span> : null}
                </div>
                {entry.id ? (
                  <SentenceList
                    transcriptionId={entry.id}
                    sentences={splitSentences(entry.text).map((s) => ({ text: s }))}
                  />
                ) : (
                  <div className="result-text">{entry.text}</div>
                )}
                {entry.segments && (
                  <details className="result-segments">
                    <summary>Segments ({entry.segments.length})</summary>
                    <pre>{JSON.stringify(entry.segments, null, 2)}</pre>
                  </details>
                )}
                {entry.words && (
                  <details className="result-segments">
                    <summary>Words ({entry.words.length})</summary>
                    <pre>{JSON.stringify(entry.words, null, 2)}</pre>
                  </details>
                )}
                {entry.speakers && (
                  <details className="result-segments">
                    <summary>Speakers ({entry.speakers.length})</summary>
                    <pre>{JSON.stringify(entry.speakers, null, 2)}</pre>
                  </details>
                )}
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
