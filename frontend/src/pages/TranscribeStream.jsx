import React, { useRef, useState, useEffect } from 'react';
import { Mic, MicOff, Radio, AlertCircle, ChevronsDown } from 'lucide-react';
import useLiveTranscription from '../hooks/useLiveTranscription';

function formatSentences(text) {
  // Insert a newline after sentence-ending punctuation followed by whitespace
  return text.replace(/([.!?]+)\s+/g, '$1\n');
}

export default function TranscribeStream({ accessToken }) {
  const {
    isStarted,
    status,
    transcriptEntries,
    partialTranscript,
    errorMessage,
    start,
    stop,
  } = useLiveTranscription();

  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcriptEntries, partialTranscript, autoScroll]);

  const transcriptCount = transcriptEntries.length + (partialTranscript ? 1 : 0);
  const isLive = isStarted && !errorMessage;

  return (
    <div className="stream-page">
      <h1 className="page-title">Live Stream Transcription</h1>
      <div className="stream-layout">
        <section className="panel-glass stream-controls">
          <div className="stream-status">
            <span className={`status-dot ${isLive ? 'live' : ''}`} />
            <div>
              <p className="status-label">Session status</p>
              <p className="status-text">{status}</p>
            </div>
          </div>

          <div className="stat-strip compact-strip">
            <article><span>Entries</span><strong>{transcriptCount}</strong></article>
            <article><span>Engine</span><strong>Voxtral Realtime</strong></article>
          </div>

          <div className="hero-actions">
            <button className="primary-button" onClick={() => start(accessToken)} disabled={isStarted}>
              {isStarted ? <><Radio size={16} /> Listening…</> : <><Mic size={16} /> Start Session</>}
            </button>
            <button className="secondary-button" onClick={() => stop()} disabled={!isStarted}>
              <MicOff size={16} /> Stop Session
            </button>
          </div>

          {errorMessage && <p className="error-banner"><AlertCircle size={16} /> {errorMessage}</p>}
        </section>

        <section className="panel-glass transcript-panel">
          <div className="panel-heading transcript-heading">
            <div>
              <p className="eyebrow">Output</p>
              <h2>Transcript feed</h2>
            </div>
            <button
              className={`autoscroll-toggle ${autoScroll ? 'active' : ''}`}
              onClick={() => setAutoScroll(v => !v)}
              title={autoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
            >
              <ChevronsDown size={14} />
              {autoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
            </button>
          </div>
          <div className="transcript-scroll" ref={scrollRef}>
            {transcriptEntries.length === 0 && !partialTranscript ? (
              <div className="empty-state">
                <p>No transcript yet.</p>
                <span>Start a session, allow microphone access, and speak naturally.</span>
              </div>
            ) : null}
            {transcriptEntries.map((entry, index) => (
              <article className="transcript-entry" key={`${entry}-${index}`}>
                <span className="entry-badge">Final</span>
                <p style={{ whiteSpace: 'pre-wrap' }}>{formatSentences(entry)}</p>
              </article>
            ))}
            {partialTranscript ? (
              <article className="transcript-entry partial-entry">
                <span className="entry-badge">Live</span>
                <p style={{ whiteSpace: 'pre-wrap' }}>{formatSentences(partialTranscript)}</p>
              </article>
            ) : null}
            <div className="transcript-scroll-spacer" />
          </div>
        </section>
      </div>
    </div>
  );
}
