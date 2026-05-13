import React, { useRef, useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mic, MicOff, AlertCircle, ChevronsDown, Monitor, Upload, ChevronDown, ChevronUp, Maximize2, Minimize2, Clock, Languages, Brain, X } from 'lucide-react';
import Sentence from '../components/Sentence';
import useLiveTranscription from '../hooks/useLiveTranscription';
import streamManager, { formatDuration } from '../lib/streamManager';
import { api } from '../lib/api';

const ANALYSIS_MODE_CONFIG = {
  multimodal: {
    label: 'video and audio',
    defaultPrompt: 'Analyze the live conversation using both audio and video context. Summarize key actions, decisions, and risks.',
  },
  audio_only: {
    label: 'audio',
    defaultPrompt: 'Analyze only the spoken audio from the live conversation. Summarize key actions, decisions, and risks.',
  },
  video_only: {
    label: 'video',
    defaultPrompt: 'Analyze only the visual video context from the live conversation. Summarize key actions, decisions, and risks.',
  },
};

const getDefaultAnalysisPrompt = (mode) => (
  ANALYSIS_MODE_CONFIG[mode]?.defaultPrompt || ANALYSIS_MODE_CONFIG.multimodal.defaultPrompt
);

const getAnalysisModeLabel = (mode) => (
  ANALYSIS_MODE_CONFIG[mode]?.label || mode
);

const SOURCE_META = {
  microphone: { Icon: Mic, label: 'Microphone' },
  screen: { Icon: Monitor, label: 'Screen Share' },
  file: { Icon: Upload, label: 'File' },
};

export default function TranscribeStream({ accessToken, onStreamStopped }) {
  const {
    isStarted,
    status,
    transcriptEntries,
    partialTranscript,
    partialTranscriptTimestamp,
    errorMessage,
    quotaError,
    elapsedMs,
    localAnnotations,
    analysisEntries,
    hasAudioTrack,
    hasVideoTrack,
    start,
    stop,
    dismissQuotaError,
    addLocalAnnotation,
    updateLocalAnnotation,
    deleteLocalAnnotation,
    toggleLocalTodo,
  } = useLiveTranscription();

  const scrollRef = useRef(null);
  const navigate = useNavigate();
  const [autoScroll, setAutoScroll] = useState(true);
  const wasStartedRef = useRef(false);

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
  const [analysisEnabled, setAnalysisEnabled] = useState(false);
  const [analysisMode, setAnalysisMode] = useState('multimodal');
  const [analysisPrompt, setAnalysisPrompt] = useState(getDefaultAnalysisPrompt('multimodal'));
  const [analysisPromptTouched, setAnalysisPromptTouched] = useState(false);
  const [fileHasVideo, setFileHasVideo] = useState(false);

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

  const setupTrackAvailability = (() => {
    if (audioSource === 'microphone') return { hasAudio: true, hasVideo: false };
    if (audioSource === 'screen') return { hasAudio: true, hasVideo: true };
    return { hasAudio: !!fileObjectUrl, hasVideo: !!fileHasVideo };
  })();

  const runtimeTrackAvailability = {
    hasAudio: isStarted ? !!hasAudioTrack : setupTrackAvailability.hasAudio,
    hasVideo: isStarted ? !!hasVideoTrack : setupTrackAvailability.hasVideo,
  };

  const isModeSupported = useCallback((mode, tracks) => {
    if (mode === 'audio_only') return !!tracks.hasAudio;
    if (mode === 'video_only') return !!tracks.hasVideo;
    return !!tracks.hasAudio && !!tracks.hasVideo;
  }, []);

  const effectiveAnalysisEnabled = analysisEnabled && isModeSupported(analysisMode, runtimeTrackAvailability);

  useEffect(() => {
    if (!analysisPromptTouched) {
      setAnalysisPrompt(getDefaultAnalysisPrompt(analysisMode));
    }
  }, [analysisMode, analysisPromptTouched]);

  useEffect(() => {
    if (!isStarted || !streamManager.getStreamId()) return;
    streamManager.updateAnalysisConfig({
      analysis_enabled: effectiveAnalysisEnabled,
      analysis_mode: analysisMode,
      analysis_audio_chunk_seconds: 1.0,
      analysis_video_fps: 3,
      analysis_prompt: analysisPrompt,
    });
  }, [isStarted, effectiveAnalysisEnabled, analysisMode, analysisPrompt]);

  useEffect(() => {
    if (wasStartedRef.current && !isStarted) {
      onStreamStopped?.();
    }
    wasStartedRef.current = isStarted;
  }, [isStarted, onStreamStopped]);

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

  useEffect(() => {
    if (!mediaRef.current || !fileObjectUrl) {
      setFileHasVideo(false);
      return;
    }

    const mediaEl = mediaRef.current;
    const handleLoadedMetadata = () => {
      setFileHasVideo((mediaEl.videoWidth || 0) > 0 && (mediaEl.videoHeight || 0) > 0);
    };

    mediaEl.addEventListener('loadedmetadata', handleLoadedMetadata);
    handleLoadedMetadata();
    return () => {
      mediaEl.removeEventListener('loadedmetadata', handleLoadedMetadata);
    };
  }, [fileObjectUrl]);

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
      sourceConfig.hasVideo = fileHasVideo;
    }
    const translationConfig = translateEnabled && targetLang
      ? { source_language: sourceLang, target_language: targetLang }
      : null;
    const analysisConfig = {
      analysis_enabled: effectiveAnalysisEnabled,
      analysis_mode: analysisMode,
      analysis_audio_chunk_seconds: 1.0,
      analysis_video_fps: 3,
      analysis_prompt: analysisPrompt,
    };
    start(accessToken, sourceConfig, translationConfig, analysisConfig);
  }, [
    audioSource,
    fileHasVideo,
    accessToken,
    start,
    translateEnabled,
    sourceLang,
    targetLang,
    effectiveAnalysisEnabled,
    analysisMode,
    analysisPrompt,
  ]);

  const modeDisabledReason = (mode) => {
    if (mode === 'multimodal' && !(runtimeTrackAvailability.hasAudio && runtimeTrackAvailability.hasVideo)) {
      if (audioSource === 'microphone') {
        return 'Microphone source has no video track.';
      }
      if (audioSource === 'file' && fileObjectUrl && !fileHasVideo) {
        return 'Uploaded file has no video track.';
      }
      return 'Needs microphone/audio and video tracks.';
    }
    if (mode === 'audio_only' && !runtimeTrackAvailability.hasAudio) {
      return 'No microphone or audio track detected.';
    }
    if (mode === 'video_only' && !runtimeTrackAvailability.hasVideo) {
      if (audioSource === 'microphone') {
        return 'Microphone source has no video track.';
      }
      if (audioSource === 'file' && fileObjectUrl && !fileHasVideo) {
        return 'Uploaded file has no video track.';
      }
      return 'No camera/screen video track detected.';
    }
    return '';
  };

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

            <div className="analysis-toggle">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={analysisEnabled}
                  onChange={(e) => setAnalysisEnabled(e.target.checked)}
                />
                <Brain size={14} /> Live Analysis (Beta)
              </label>

              {analysisEnabled && (
                <div className="analysis-config-active">
                  <span className="config-label">Mode</span>
                  <div className="analysis-mode-options">
                    {['multimodal', 'audio_only', 'video_only'].map((mode) => {
                      const disabled = !isModeSupported(mode, runtimeTrackAvailability);
                      const reason = modeDisabledReason(mode);
                      const isVideoMissingState = disabled
                        && (mode === 'video_only' || mode === 'multimodal')
                        && (
                          audioSource === 'microphone'
                          || (audioSource === 'file' && fileObjectUrl && !fileHasVideo)
                        );
                      return (
                        <button
                          key={mode}
                          type="button"
                          className={`analysis-mode-option ${analysisMode === mode ? 'active' : ''} ${isVideoMissingState ? 'video-unavailable' : ''}`}
                          disabled={disabled}
                          title={disabled ? reason : `Switch to ${getAnalysisModeLabel(mode)}`}
                          onClick={() => setAnalysisMode(mode)}
                        >
                          {getAnalysisModeLabel(mode)}
                        </button>
                      );
                    })}
                  </div>
                  {!isModeSupported(analysisMode, runtimeTrackAvailability) && (
                    <small className="analysis-mode-help">{modeDisabledReason(analysisMode)}</small>
                  )}
                  <div className="form-row analysis-prompt-row">
                    <label htmlFor="analysis_prompt">Prompt</label>
                    <textarea
                      id="analysis_prompt"
                      rows={3}
                      value={analysisPrompt}
                      onChange={(e) => {
                        setAnalysisPromptTouched(true);
                        setAnalysisPrompt(e.target.value);
                      }}
                      placeholder="Describe what analysis you want to see in real time."
                    />
                  </div>
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

        {/* Transcript and analysis panels — shown only when session is active */}
        {isStarted && (
          <div className={`stream-live-panels ${analysisEnabled ? 'analysis-open' : ''}`}>
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

          {analysisEnabled && (
            <section className="panel-glass analysis-panel">
              <div className="analysis-panel-header">
                <h2>Live Analysis</h2>
                <span className="analysis-mode-badge">{getAnalysisModeLabel(analysisMode)}</span>
              </div>

              {!effectiveAnalysisEnabled && (
                <div className="analysis-degraded-banner">
                  <AlertCircle size={14} />
                  <span>{modeDisabledReason(analysisMode)}</span>
                </div>
              )}

              <div className="analysis-scroll">
                {analysisEntries.length === 0 ? (
                  <div className="empty-state compact">
                    <p>No analysis events yet.</p>
                    <span>Events will appear here once analysis is active.</span>
                  </div>
                ) : (
                  analysisEntries.map((entry) => (
                    <article key={entry.id} className={`analysis-entry ${entry.type === 'analysis.error' ? 'error' : ''}`}>
                      <div className="analysis-entry-meta">
                        <span className="analysis-entry-type">{entry.type.replace('analysis.', '')}</span>
                        <span className="analysis-entry-mode">{getAnalysisModeLabel(entry.mode)}</span>
                        <span className="analysis-entry-ts">{formatDuration(entry.timestampMs || 0)}</span>
                      </div>
                      <p>{entry.text}</p>
                    </article>
                  ))
                )}
              </div>
            </section>
          )}
          </div>
        )}

      </div>

      {quotaError?.code === 'quota_exceeded' && (
        <div className="quota-modal-overlay" onClick={() => dismissQuotaError()}>
          <div className="quota-modal panel-glass" onClick={(e) => e.stopPropagation()}>
            <div className="quota-modal-header">
              <h2>Stream Limit Reached</h2>
              <button className="icon-btn" onClick={() => dismissQuotaError()} title="Close">
                <X size={14} />
              </button>
            </div>
            <p className="quota-modal-message">
              Your current plan has reached its live streaming quota. Upgrade to a higher tier to start streaming again.
            </p>
            <div className="quota-modal-meta">
              <span>Tier: <strong>{quotaError.tier || 'free'}</strong></span>
              {quotaError.quota?.used != null && quotaError.quota?.limit != null && (
                <span>
                  Usage: <strong>{quotaError.quota.used}</strong> / <strong>{quotaError.quota.limit === -1 ? 'infinite' : quotaError.quota.limit}</strong>
                </span>
              )}
            </div>
            <div className="quota-modal-actions">
              <button
                className="secondary-button"
                onClick={() => {
                  dismissQuotaError();
                  navigate('/usage');
                }}
              >
                View Usage
              </button>
              <button
                className="primary-button"
                onClick={() => {
                  dismissQuotaError();
                  navigate('/billing/plans');
                }}
              >
                Upgrade Plan
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
