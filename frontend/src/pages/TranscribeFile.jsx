import React, { useState, useRef, useEffect } from 'react';
import { Upload, FileAudio, Loader2, CheckCircle, AlertCircle, Download, Copy } from 'lucide-react';
import { api } from '../lib/api';

export default function TranscribeFile() {
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState('en');
  const [languages, setLanguages] = useState([]);
  const [punctuationPass, setPunctuationPass] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);

  useEffect(() => {
    api.getLanguages().then(res => setLanguages(res.languages || [])).catch(() => {});
  }, []);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  };

  const handleFileSelect = (e) => {
    const f = e.target.files[0];
    if (f) setFile(f);
  };

  const handleSubmit = async () => {
    if (!file) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('language', language);
      formData.append('punctuation_pass', punctuationPass ? 'true' : 'false');
      const res = await api.transcribeFile(formData);
      setResult(res);
    } catch (err) {
      setError(err.message || 'Transcription failed');
    } finally {
      setLoading(false);
    }
  };

  const copyText = () => {
    if (result?.text) navigator.clipboard.writeText(result.text);
  };

  const downloadJson = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcription-${result.id || 'result'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="transcribe-page">
      <h1 className="page-title">Transcribe File</h1>

      <div className="transcribe-layout">
        <section className="panel-glass upload-panel">
          <div
            className={`drop-zone ${dragOver ? 'drag-over' : ''} ${file ? 'has-file' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input ref={fileInputRef} type="file" accept="audio/*,video/*" hidden onChange={handleFileSelect} />
            {file ? (
              <>
                <FileAudio size={36} />
                <p className="file-name">{file.name}</p>
                <p className="file-size">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </>
            ) : (
              <>
                <Upload size={36} />
                <p>Drop an audio or video file here</p>
                <span>or click to browse</span>
              </>
            )}
          </div>

          <div className="form-row">
            <label>Language</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {languages.map((lang) => (
                <option key={lang.code} value={lang.code}>{lang.name}</option>
              ))}
            </select>
          </div>

          <div className="form-row form-row-check">
            <label>
              <input
                type="checkbox"
                checked={punctuationPass}
                onChange={(e) => setPunctuationPass(e.target.checked)}
              />
              Add punctuation
            </label>
          </div>

          <button
            className="primary-button full-width"
            onClick={handleSubmit}
            disabled={!file || loading}
          >
            {loading ? <><Loader2 className="spin" size={18} /> Processing…</> : 'Transcribe'}
          </button>

          {error && <p className="error-banner"><AlertCircle size={16} /> {error}</p>}
        </section>

        {result && (
          <section className="panel-glass result-panel">
            <div className="result-header">
              <h2><CheckCircle size={20} /> Transcription Complete</h2>
              <div className="result-actions">
                <button className="icon-btn" onClick={copyText} title="Copy text"><Copy size={16} /></button>
                <button className="icon-btn" onClick={downloadJson} title="Download JSON"><Download size={16} /></button>
              </div>
            </div>
            <div className="result-meta">
              <span>Language: {result.language}</span>
              {result.duration ? <span>Duration: {result.duration}s</span> : null}
              {result.word_count ? <span>Words: {result.word_count}</span> : null}
            </div>
            <div className="result-text">
              {result.text}
            </div>
            {result.segments && (
              <details className="result-segments">
                <summary>Segments ({result.segments.length})</summary>
                <pre>{JSON.stringify(result.segments, null, 2)}</pre>
              </details>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
