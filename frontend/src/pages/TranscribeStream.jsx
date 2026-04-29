import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Mic, MicOff, Radio, AlertCircle, ChevronsDown, Monitor, Upload } from 'lucide-react';
import useLiveTranscription from '../hooks/useLiveTranscription';
import streamManager from '../lib/streamManager';

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

  // Audio source state
  const [audioSource, setAudioSource] = useState('microphone'); // 'microphone' | 'screen' | 'file'
  const [fileObjectUrl, setFileObjectUrl] = useState(null);
  const fileInputRef = useRef(null);
  const mediaRef = useRef(null);

  const handleFileSelect = useCallback((e) => {
    const file = e.target.files[0];
    if (!file) return;
    // Revoke previous URL and reset the AudioContext so a fresh one is created
    if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
    streamManager.resetFileAudio();
    setFileObjectUrl(URL.createObjectURL(file));
    // Reset the input so selecting the same file again triggers onChange
    e.target.value = '';
  }, [fileObjectUrl]);

  // Clean up object URL and file AudioContext on unmount
  useEffect(() => {
    return () => {
      if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
      streamManager.resetFileAudio();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = useCallback(() => {
    if (audioSource === 'file') {
      start(accessToken, { type: 'file', mediaElement: mediaRef.current });
    } else if (audioSource === 'screen') {
      start(accessToken, { type: 'screen' });
    } else {
      start(accessToken, { type: 'microphone' });
    }
  }, [audioSource, accessToken, start]);

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

          {/* Audio source selector */}
          <div className="source-selector">
            <button
              className={`source-btn ${audioSource === 'microphone' ? 'active' : ''}`}
              onClick={() => setAudioSource('microphone')}
              disabled={isStarted}
              title="Capture from microphone"
            >
              <Mic size={13} /> Microphone
            </button>
            <button
              className={`source-btn ${audioSource === 'screen' ? 'active' : ''}`}
              onClick={() => setAudioSource('screen')}
              disabled={isStarted}
              title="Capture audio from a browser tab or window"
            >
              <Monitor size={13} /> Screen Share
            </button>
            <button
              className={`source-btn ${audioSource === 'file' ? 'active' : ''}`}
              onClick={() => setAudioSource('file')}
              disabled={isStarted}
              title="Upload an audio or video file"
            >
              <Upload size={13} /> File
            </button>
          </div>

          {/* File picker (shown when source = file) */}
          {audioSource === 'file' && (
            <div className="file-source-area">
              <input
                ref={fileInputRef}
                type="file"
                accept="audio/*,video/*"
                onChange={handleFileSelect}
                style={{ display: 'none' }}
              />
              <button
                className="secondary-button file-choose-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={isStarted}
              >
                <Upload size={14} />
                {fileObjectUrl ? 'Change file' : 'Choose audio / video file'}
              </button>
              {fileObjectUrl && (
                <div className="file-player-wrap">
                  {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                  <video
                    ref={mediaRef}
                    src={fileObjectUrl}
                    controls
                    className="file-preview-player"
                    onEnded={() => !isStarted && undefined}
                  />
                </div>
              )}
              {audioSource === 'file' && !fileObjectUrl && (
                <p className="source-hint">Select a file to enable the Start button.</p>
              )}
            </div>
          )}

          {audioSource === 'screen' && (
            <p className="source-hint">
              You will be prompted to choose a tab or window. Enable &quot;Share tab audio&quot; in the picker.
            </p>
          )}

          <div className="hero-actions">
            <button
              className="primary-button"
              onClick={handleStart}
              disabled={isStarted || (audioSource === 'file' && !fileObjectUrl)}
            >
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
