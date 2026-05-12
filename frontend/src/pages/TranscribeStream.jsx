import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Mic, MicOff, AlertCircle, ChevronsDown, Monitor, Upload, ChevronDown, ChevronUp, Maximize2, Minimize2, Clock, Languages } from 'lucide-react';
import Sentence from '../components/Sentence';
import useLiveTranscription from '../hooks/useLiveTranscription';
import streamManager, { formatDuration } from '../lib/streamManager';
import { api } from '../lib/api';

const SOURCE_META = {
  microphone: { Icon: Mic, label: 'Microphone' },
  screen: { Icon: Monitor, label: 'Screen Share' },
  file: { Icon: Upload, label: 'File' },
};

export default function TranscribeStream({ accessToken }) {
  const {
    isStarted,
    status,
    transcriptEntries,
    partialTranscript,
    partialTranscriptTimestamp,
    errorMessage,
    elapsedMs,
    localAnnotations,
    start,
    stop,
    addLocalAnnotation,
    updateLocalAnnotation,
    deleteLocalAnnotation,
    toggleLocalTodo,
  } = useLiveTranscription();

  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const [audioSource, setAudioSource] = useState('microphone');
  const [fileObjectUrl, setFileObjectUrl] = useState(null);
  const fileInputRef = useRef(null);
  const mediaRef = useRef(null);

  // Player state for active session
  const [playerHidden, setPlayerHidden] = useState(false);
  const [playerFullscreen, setPlayerFullscreen] = useState(false);

  // Translation state
  const [languages, setLanguages] = useState([]);
  const [translateEnabled, setTranslateEnabled] = useState(false);
  const [sourceLang, setSourceLang] = useState('en');
  const [targetLang, setTargetLang] = useState('');

  // Fetch available languages on mount
  useEffect(() => {
    api.getLanguages().then(res => setLanguages(res.languages || [])).catch(() => {});
  }, []);

  // Mid-stream translation config updates
  useEffect(() => {
    if (isStarted && streamManager.getStreamId()) {
      const config = translateEnabled && targetLang
        ? { source_language: sourceLang, target_language: targetLang }
        : null;
      streamManager.updateTranslationConfig(config);
    }
  }, [isStarted, sourceLang, targetLang, translateEnabled]);

  const handleFileSelect = useCallback((e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
    streamManager.resetFileAudio();
    setFileObjectUrl(URL.createObjectURL(file));
    e.target.value = '';
  }, [fileObjectUrl]);

  useEffect(() => {
    return () => {
      if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
      streamManager.resetFileAudio();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset player state when session ends
  useEffect(() => {
    if (!isStarted) {
      setPlayerHidden(false);
      setPlayerFullscreen(false);
    }
  }, [isStarted]);

  const handleStart = useCallback(() => {
    const sourceConfig = { type: audioSource };
    if (audioSource === 'file') {
      sourceConfig.mediaElement = mediaRef.current;
    }
    const translationConfig = translateEnabled && targetLang
      ? { source_language: sourceLang, target_language: targetLang }
      : null;
    start(accessToken, sourceConfig, translationConfig);
  }, [audioSource, accessToken, start, translateEnabled, sourceLang, targetLang]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcriptEntries, partialTranscript, autoScroll]);

  const { Icon: SourceIcon, label: sourceLabel } = SOURCE_META[audioSource] ?? SOURCE_META.microphone;

  // Determine video container CSS class
  const videoContainerClass = (() => {
    if (!isStarted) return 'fvc-setup';
    if (playerFullscreen) return 'fvc-fullscreen';
    if (playerHidden) return 'fvc-hidden';
    return 'fvc-inline';
  })();

  const transcriptContent = (
    <>
      {transcriptEntries.length === 0 && !partialTranscript ? (
        <div className="empty-state">
          <p>No transcript yet.</p>
          <span>Speak naturally and the transcript will appear here.</span>
        </div>
      ) : null}
      {transcriptEntries.map((entry, index) => (
        <Sentence
          key={`${entry.timestamp}-${index}`}
          index={index}
          text={entry.text}
          timestamp={entry.timestamp}
          annotations={localAnnotations[index] || []}
          readOnly={false}
          onCreateAnnotation={(idx, text, ts, type, content) => addLocalAnnotation(idx, ts, type, content)}
          onUpdateAnnotation={(id, updates) => updateLocalAnnotation(id, updates)}
          onDeleteAnnotation={(id) => deleteLocalAnnotation(id)}
          onToggleTodo={(id) => toggleLocalTodo(id)}
        />
      ))}
      {partialTranscript ? (
        <article className="transcript-entry partial-entry">
          <div className="entry-row">
            {partialTranscriptTimestamp ? (
              <time className="entry-timestamp-col">[{partialTranscriptTimestamp}]</time>
            ) : (
              <span className="entry-timestamp-col placeholder" />
            )}
            <p className="entry-text">{partialTranscript}</p>
          </div>
        </article>
      ) : null}
      <div className="transcript-scroll-spacer" />
    </>
  );

  return (
    <div className="stream-page">
      <h1 className="page-title">Live Stream Transcription</h1>
      <div className="stream-layout">

        {/* Session setup — hidden once session starts */}
        {!isStarted && (
          <section className="panel-glass stream-controls">
            {status !== 'Ready.' && (
              <div className="stream-status">
                <span className="status-dot" />
                <div>
                  <p className="status-text">{status}</p>
                </div>
              </div>
            )}

            <div className="source-selector">
              <button
                className={`source-btn ${audioSource === 'microphone' ? 'active' : ''}`}
                onClick={() => setAudioSource('microphone')}
                title="Capture from microphone"
              >
                <Mic size={13} /> Microphone
              </button>
              <button
                className={`source-btn ${audioSource === 'screen' ? 'active' : ''}`}
                onClick={() => setAudioSource('screen')}
                title="Capture audio from a browser tab or window"
              >
                <Monitor size={13} /> Screen Share
              </button>
              <button
                className={`source-btn ${audioSource === 'file' ? 'active' : ''}`}
                onClick={() => setAudioSource('file')}
                title="Upload an audio or video file"
              >
                <Upload size={13} /> File
              </button>
            </div>

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
                >
                  <Upload size={14} />
                  {fileObjectUrl ? 'Change file' : 'Choose audio / video file'}
                </button>
                {!fileObjectUrl && (
                  <p className="source-hint">Select a file to enable the Start button.</p>
                )}
              </div>
            )}

            {audioSource === 'screen' && (
              <p className="source-hint">
                You will be prompted to choose a tab or window. Enable &quot;Share tab audio&quot; in the picker.
              </p>
            )}

            {/* Translation toggle */}
            <div className="translation-toggle">
              {!translateEnabled ? (
                <label className="toggle-label">
                  <input
                    type="checkbox"
                    checked={translateEnabled}
                    onChange={(e) => {
                      setTranslateEnabled(e.target.checked);
                      if (e.target.checked && !targetLang) {
                        setTargetLang(languages.find((l) => l.code !== sourceLang)?.code || 'es');
                      }
                    }}
                  />
                  <Languages size={14} /> Translate
                </label>
              ) : (
                <div className="translation-config-active">
                  <span className="config-label">Translate:</span>
                  <div className="form-row lang-pair">
                    <div>
                      <label>From</label>
                      <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                        {languages.map((l) => (
                          <option key={l.code} value={l.code}>{l.name}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label>To</label>
                      <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                        {languages.map((l) => (
                          <option key={l.code} value={l.code}>{l.name}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <button
                    className="btn-text"
                    onClick={() => {
                      setTranslateEnabled(false);
                      setTargetLang('');
                    }}
                  >
                    Disable
                  </button>
                </div>
              )}
            </div>

            <div className="hero-actions">
              <button
                className="primary-button"
                onClick={handleStart}
                disabled={audioSource === 'file' && !fileObjectUrl}
              >
                <Mic size={16} /> Start Session
              </button>
            </div>

            {errorMessage && <p className="error-banner"><AlertCircle size={16} /> {errorMessage}</p>}
          </section>
        )}

        {/* Persistent video element */}
        {fileObjectUrl && (
          <div className={`file-video-container ${videoContainerClass}`}>
            {playerFullscreen && (
              <button
                className="player-fullscreen-close"
                onClick={() => setPlayerFullscreen(false)}
                title="Exit fullscreen"
              >
                <Minimize2 size={18} />
              </button>
            )}
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video
              ref={mediaRef}
              src={fileObjectUrl}
              controls
              className="fvc-video"
            />
            {playerFullscreen && (
              <div className="player-fullscreen-transcript">
                <div className="transcript-scroll player-fullscreen-scroll">
                  {transcriptContent}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Transcript — shown only when session is active */}
        {isStarted && (
          <section className="panel-glass transcript-panel">
            {/* Compact session bar */}
            <div className="transcript-session-bar">
              <div className="session-bar-left">
                <div className="session-bar-source">
                  <SourceIcon size={14} />
                  <span>{sourceLabel}</span>
                </div>
                {audioSource === 'file' && (
                  <div className="session-bar-player-controls">
                    <button
                      className="icon-btn-sm"
                      onClick={() => { setPlayerHidden(v => !v); setPlayerFullscreen(false); }}
                      title={playerHidden ? 'Show player' : 'Hide player'}
                    >
                      {playerHidden ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
                      {playerHidden ? 'Show' : 'Hide'}
                    </button>
                    <button
                      className="icon-btn-sm"
                      onClick={() => { setPlayerFullscreen(true); setPlayerHidden(false); }}
                      title="Fullscreen with transcript overlay"
                    >
                      <Maximize2 size={14} /> Fullscreen
                    </button>
                  </div>
                )}
              </div>
              <div className="session-bar-right">
                <div className="stream-timer">
                  <Clock size={14} />
                  <span>{formatDuration(elapsedMs)}</span>
                </div>
                <button className="secondary-button session-stop-btn" onClick={() => stop()}>
                  <MicOff size={14} /> Stop
                </button>
              </div>
            </div>

            {errorMessage && <p className="error-banner"><AlertCircle size={16} /> {errorMessage}</p>}

            <div className="panel-heading transcript-heading">
              <h2>Transcript</h2>
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
              {transcriptContent}
            </div>
          </section>
        )}

      </div>
    </div>
  );
}
