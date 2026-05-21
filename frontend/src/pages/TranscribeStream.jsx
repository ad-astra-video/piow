import React, { useRef, useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mic, MicOff, AlertCircle, ChevronsDown, Monitor, Upload, ChevronDown, ChevronUp, Maximize2, Minimize2, Clock, Languages, Brain, X, FileCode } from 'lucide-react';
import AnalysisContent from '../components/AnalysisContent';
import Sentence from '../components/Sentence';
import MarkdownText from '../components/MarkdownText';
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

const ANALYSIS_WINDOW_MIN_SECONDS = 1;
const ANALYSIS_WINDOW_MAX_SECONDS = 30;
const ANALYSIS_WINDOW_DEFAULT_SECONDS = 10;
const ANALYSIS_MAX_TOKENS_DEFAULT = 1024;
const ANALYSIS_MAX_TOKENS_MIN = 64;
const ANALYSIS_MAX_TOKENS_MAX = 4096;

const clampAnalysisWindowSeconds = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return ANALYSIS_WINDOW_DEFAULT_SECONDS;
  return Math.min(ANALYSIS_WINDOW_MAX_SECONDS, Math.max(ANALYSIS_WINDOW_MIN_SECONDS, Math.round(numeric)));
};

const clampAnalysisMaxTokens = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return ANALYSIS_MAX_TOKENS_DEFAULT;
  return Math.min(ANALYSIS_MAX_TOKENS_MAX, Math.max(ANALYSIS_MAX_TOKENS_MIN, Math.round(numeric)));
};

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
    transcriptionEnabled: activeTranscriptionEnabled,
    analysisEnabled: activeAnalysisEnabled,
    analysisSchemaStatus: activeSchemaStatus,
    analysisResponseFormat: activeResponseFormat,
    hasAudioTrack,
    hasVideoTrack,
    start,
    stop,
    addLocalAnnotation,
    updateLocalAnnotation,
    deleteLocalAnnotation,
    toggleLocalTodo,
    regenerateAnalysisSchema,
  } = useLiveTranscription();

  const scrollRef = useRef(null);
  const analysisScrollRef = useRef(null);
  const navigate = useNavigate();
  const [autoScroll, setAutoScroll] = useState(true);
  const [analysisAutoScroll, setAnalysisAutoScroll] = useState(true);
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
  const [transcriptionServiceEnabled, setTranscriptionServiceEnabled] = useState(true);
  const [translateEnabled, setTranslateEnabled] = useState(false);
  const [sourceLang, setSourceLang] = useState('en');
  const [targetLang, setTargetLang] = useState('');
  const [analysisEnabled, setAnalysisEnabled] = useState(false);
  const [analysisMode, setAnalysisMode] = useState('multimodal');
  const [analysisAudioWindowSeconds, setAnalysisAudioWindowSeconds] = useState(ANALYSIS_WINDOW_DEFAULT_SECONDS);
  const [analysisVideoWindowSeconds, setAnalysisVideoWindowSeconds] = useState(ANALYSIS_WINDOW_DEFAULT_SECONDS);
  const [analysisMaxTokens, setAnalysisMaxTokens] = useState(ANALYSIS_MAX_TOKENS_DEFAULT);
  const [analysisPrompt, setAnalysisPrompt] = useState(getDefaultAnalysisPrompt('multimodal'));
  const [analysisPromptTouched, setAnalysisPromptTouched] = useState(false);
  const [analysisResponseFormat, setAnalysisResponseFormat] = useState(null);
  const [showResponseFormatModal, setShowResponseFormatModal] = useState(false);
  const [showAnalysisErrorsModal, setShowAnalysisErrorsModal] = useState(false);
  const [responseFormatDraft, setResponseFormatDraft] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [fileHasVideo, setFileHasVideo] = useState(false);

  const analysisErrorEntries = analysisEntries.filter((entry) => entry.type === 'analysis.error');
  const analysisDisplayEntries = analysisEntries.filter((entry) => entry.type !== 'analysis.error');
  const analysisSignalRows = analysisDisplayEntries.flatMap((entry) => {
    if (entry.type !== 'analysis.signal') return [];
    return Array.isArray(entry.signalRows) ? entry.signalRows : [];
  });
  const analysisNarrativeEntries = analysisDisplayEntries.filter((entry) => entry.type !== 'analysis.signal');

  // Fetch available languages on mount
  useEffect(() => {
    api.getLanguages().then(res => setLanguages(res.languages || [])).catch(() => {});
  }, []);

  // Mid-stream translation config updates
  useEffect(() => {
    if (isStarted && streamManager.getStreamId()) {
      const config = transcriptionServiceEnabled && translateEnabled && targetLang
        ? { source_language: sourceLang, target_language: targetLang }
        : null;
      streamManager.updateTranslationConfig(config);
    }
  }, [isStarted, sourceLang, targetLang, translateEnabled, transcriptionServiceEnabled]);

  useEffect(() => {
    if (!transcriptionServiceEnabled && translateEnabled) {
      setTranslateEnabled(false);
      setTargetLang('');
    }
  }, [transcriptionServiceEnabled, translateEnabled]);

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
      analysis_audio_chunk_seconds: clampAnalysisWindowSeconds(analysisAudioWindowSeconds),
      analysis_video_chunk_seconds: clampAnalysisWindowSeconds(analysisVideoWindowSeconds),
      analysis_video_fps: 3,
      analysis_max_tokens: clampAnalysisMaxTokens(analysisMaxTokens),
      analysis_prompt: analysisPrompt,
      analysis_response_format: analysisResponseFormat,
    });
  }, [
    isStarted,
    effectiveAnalysisEnabled,
    analysisMode,
    analysisAudioWindowSeconds,
    analysisVideoWindowSeconds,
    analysisMaxTokens,
    analysisPrompt,
  ]);

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

  const handleAudioSourceChange = useCallback((nextSource) => {
    if (nextSource !== 'file' && audioSource === 'file') {
      if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
      streamManager.resetFileAudio();
      setFileObjectUrl(null);
      setFileHasVideo(false);
      setPlayerHidden(false);
      setPlayerFullscreen(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }

    setAudioSource(nextSource);
  }, [audioSource, fileObjectUrl]);

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

  useEffect(() => {
    if (!isStarted) {
      setShowAnalysisErrorsModal(false);
    }
  }, [isStarted]);

  useEffect(() => {
    if (!isStarted) return;
    setTranscriptionServiceEnabled(Boolean(activeTranscriptionEnabled));
    setAnalysisEnabled(Boolean(activeAnalysisEnabled));
  }, [isStarted, activeTranscriptionEnabled, activeAnalysisEnabled]);

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
      analysis_audio_chunk_seconds: clampAnalysisWindowSeconds(analysisAudioWindowSeconds),
      analysis_video_chunk_seconds: clampAnalysisWindowSeconds(analysisVideoWindowSeconds),
      analysis_video_fps: 3,
      analysis_max_tokens: clampAnalysisMaxTokens(analysisMaxTokens),
      analysis_prompt: analysisPrompt,
      analysis_response_format: analysisResponseFormat,
    };
    const serviceConfig = {
      live_transcription_enabled: transcriptionServiceEnabled,
      live_translation_enabled: transcriptionServiceEnabled && translateEnabled && !!targetLang,
    };
    start(accessToken, sourceConfig, translationConfig, analysisConfig, serviceConfig);
  }, [
    audioSource,
    fileHasVideo,
    accessToken,
    start,
    translateEnabled,
    sourceLang,
    targetLang,
    transcriptionServiceEnabled,
    effectiveAnalysisEnabled,
    analysisMode,
    analysisAudioWindowSeconds,
    analysisVideoWindowSeconds,
    analysisPrompt,
  ]);

  const handleToggleTranslate = useCallback(() => {
    if (!transcriptionServiceEnabled) return;

    if (translateEnabled) {
      setTranslateEnabled(false);
      setTargetLang('');
      return;
    }

    setTranslateEnabled(true);
    if (!targetLang) {
      setTargetLang(languages.find((l) => l.code !== sourceLang)?.code || 'es');
    }
  }, [transcriptionServiceEnabled, translateEnabled, targetLang, languages, sourceLang]);

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

  useEffect(() => {
    if (!analysisEnabled || !analysisAutoScroll || !analysisScrollRef.current) return;
    analysisScrollRef.current.scrollTop = analysisScrollRef.current.scrollHeight;
  }, [analysisEntries, analysisEnabled, analysisAutoScroll]);

  const { Icon: SourceIcon, label: sourceLabel } = SOURCE_META[audioSource] ?? SOURCE_META.microphone;
  const hasQuotaExceededSignal =
    quotaError?.code === 'quota_exceeded'
    || /quota exceeded/i.test(errorMessage || '')
    || /quota exceeded/i.test(status || '');
  const quotaErrorText =
    quotaError?.message
    || errorMessage
    || status
    || 'Transcription quota exceeded for current plan';

  // Determine video container CSS class
  const videoContainerClass = (() => {
    if (!isStarted) return 'fvc-setup';
    if (playerFullscreen) return 'fvc-fullscreen';
    if (playerHidden) return 'fvc-hidden';
    return 'fvc-inline';
  })();

  const livePanelsClassName = [
    'stream-live-panels',
    activeAnalysisEnabled && activeTranscriptionEnabled ? 'analysis-open' : '',
  ].filter(Boolean).join(' ');

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
          translatedText={entry.translatedText}
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
            <MarkdownText className="entry-text" content={partialTranscript} />
          </div>
        </article>
      ) : null}
      <div className="transcript-scroll-spacer" />
    </>
  );

  return (
    <div className="stream-page">
      <h1 className="page-title">Live Stream Services</h1>
      <div className="stream-layout">

        {/* Session setup — hidden once session starts */}
        {!isStarted && (
          <section className="panel-glass stream-controls">
             {hasQuotaExceededSignal ? (
               <div className="quota-error-bar">
                 <div className="quota-error-content">
                   <AlertCircle size={18} />
                   <span className="quota-error-text">
                     {quotaErrorText}
                   </span>
                 </div>
                 <button
                   className="primary-button"
                   onClick={() => navigate('/billing/plans')}
                   style={{ whiteSpace: 'nowrap' }}
                 >
                   Upgrade Plan
                 </button>
               </div>
             ) : status !== 'Ready.' ? (
               <div className="stream-status">
                 <span className="status-dot" />
                 <div>
                   <p className="status-text">{status}</p>
                 </div>
               </div>
             ) : null}

            <div className="source-selector">
              <button
                className={`source-btn ${audioSource === 'microphone' ? 'active' : ''}`}
                onClick={() => handleAudioSourceChange('microphone')}
                title="Capture from microphone"
              >
                <Mic size={13} /> Microphone
              </button>
              <button
                className={`source-btn ${audioSource === 'screen' ? 'active' : ''}`}
                onClick={() => handleAudioSourceChange('screen')}
                title="Capture audio from a browser tab or window"
              >
                <Monitor size={13} /> Screen Share
              </button>
              <button
                className={`source-btn ${audioSource === 'file' ? 'active' : ''}`}
                onClick={() => handleAudioSourceChange('file')}
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

            <div className="service-toggle-grid">
              <div className="analysis-toggle">
                <button
                  type="button"
                  className={`service-toggle-btn ${transcriptionServiceEnabled ? 'active' : ''}`}
                  onClick={() => setTranscriptionServiceEnabled((enabled) => !enabled)}
                  title={transcriptionServiceEnabled ? 'Turn off Live Transcription' : 'Turn on Live Transcription'}
                >
                  <span className="service-toggle-btn-main">
                    <Mic size={14} /> Live Transcription
                  </span>
                  <span className="service-toggle-btn-state">{transcriptionServiceEnabled ? 'On' : 'Off'}</span>
                </button>

                <div className="translation-toggle">
                  <button
                    type="button"
                    className={`service-subtoggle-btn ${translateEnabled ? 'active' : ''}`}
                    onClick={handleToggleTranslate}
                    disabled={!transcriptionServiceEnabled}
                    title={
                      transcriptionServiceEnabled
                        ? (translateEnabled ? 'Turn off translation' : 'Turn on translation')
                        : 'Enable Live Transcription first'
                    }
                  >
                    <span className="service-toggle-btn-main">
                      <Languages size={14} /> Translate
                    </span>
                    <span className="service-toggle-btn-state">{translateEnabled ? 'On' : 'Off'}</span>
                  </button>

                  {translateEnabled && (
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
                    </div>
                  )}

                  {!transcriptionServiceEnabled && (
                    <small className="analysis-mode-help">Enable Live Transcription to use Live Translation.</small>
                  )}
                </div>
              </div>

              <div className="analysis-toggle">
                <button
                  type="button"
                  className={`service-toggle-btn ${analysisEnabled ? 'active' : ''}`}
                  onClick={() => setAnalysisEnabled((enabled) => !enabled)}
                  title={analysisEnabled ? 'Turn off Live Analysis' : 'Turn on Live Analysis'}
                >
                  <span className="service-toggle-btn-main">
                    <Brain size={14} /> Live Analysis (Beta)
                  </span>
                  <span className="service-toggle-btn-state">{analysisEnabled ? 'On' : 'Off'}</span>
                </button>

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
                  <div className="advanced-dropdown">
                    <button
                      type="button"
                      className="advanced-dropdown-toggle"
                      onClick={() => setShowAdvanced((v) => !v)}
                    >
                      <span>Advanced</span>
                      {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                    {showAdvanced && (
                      <div className="advanced-dropdown-content">
                        <div className="form-row analysis-response-format-row">
                          <label>Structured Output</label>
                          <button
                            type="button"
                            className="secondary-button structured-output-btn"
                            onClick={() => {
                              setResponseFormatDraft(analysisResponseFormat ? JSON.stringify(analysisResponseFormat, null, 2) : '');
                              setShowResponseFormatModal(true);
                            }}
                          >
                            <FileCode size={14} />
                            {analysisResponseFormat ? 'Edit JSON Schema' : 'Set JSON Schema'}
                          </button>
                          {analysisResponseFormat && (
                            <span className="response-format-badge">Schema active</span>
                          )}
                          {!analysisResponseFormat && (
                            <span className="response-format-badge auto">Auto-generate on start</span>
                          )}
                        </div>
                        <div className="form-row analysis-window-row">
                          <label htmlFor="analysis_max_tokens">
                            Analysis max tokens ({ANALYSIS_MAX_TOKENS_MIN}-{ANALYSIS_MAX_TOKENS_MAX})
                          </label>
                          <div className="analysis-window-controls">
                            <input
                              id="analysis_max_tokens"
                              type="number"
                              min={ANALYSIS_MAX_TOKENS_MIN}
                              max={ANALYSIS_MAX_TOKENS_MAX}
                              step={1}
                              value={clampAnalysisMaxTokens(analysisMaxTokens)}
                              onChange={(e) => setAnalysisMaxTokens(clampAnalysisMaxTokens(e.target.value))}
                            />
                            <span>tokens</span>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
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
                  <div className="form-row analysis-window-row">
                    <label htmlFor="analysis_audio_window_seconds">
                      Audio analysis window ({ANALYSIS_WINDOW_MIN_SECONDS}-{ANALYSIS_WINDOW_MAX_SECONDS}s)
                    </label>
                    <div className="analysis-window-controls">
                      <input
                        id="analysis_audio_window_seconds"
                        type="range"
                        min={ANALYSIS_WINDOW_MIN_SECONDS}
                        max={ANALYSIS_WINDOW_MAX_SECONDS}
                        step={1}
                        value={clampAnalysisWindowSeconds(analysisAudioWindowSeconds)}
                        onChange={(e) => setAnalysisAudioWindowSeconds(clampAnalysisWindowSeconds(e.target.value))}
                      />
                      <input
                        type="number"
                        min={ANALYSIS_WINDOW_MIN_SECONDS}
                        max={ANALYSIS_WINDOW_MAX_SECONDS}
                        step={1}
                        value={clampAnalysisWindowSeconds(analysisAudioWindowSeconds)}
                        onChange={(e) => setAnalysisAudioWindowSeconds(clampAnalysisWindowSeconds(e.target.value))}
                      />
                      <span>s</span>
                    </div>
                  </div>
                  <div className="form-row analysis-window-row">
                    <label htmlFor="analysis_video_window_seconds">
                      Video analysis window ({ANALYSIS_WINDOW_MIN_SECONDS}-{ANALYSIS_WINDOW_MAX_SECONDS}s)
                    </label>
                    <div className="analysis-window-controls">
                      <input
                        id="analysis_video_window_seconds"
                        type="range"
                        min={ANALYSIS_WINDOW_MIN_SECONDS}
                        max={ANALYSIS_WINDOW_MAX_SECONDS}
                        step={1}
                        value={clampAnalysisWindowSeconds(analysisVideoWindowSeconds)}
                        onChange={(e) => setAnalysisVideoWindowSeconds(clampAnalysisWindowSeconds(e.target.value))}
                      />
                      <input
                        type="number"
                        min={ANALYSIS_WINDOW_MIN_SECONDS}
                        max={ANALYSIS_WINDOW_MAX_SECONDS}
                        step={1}
                        value={clampAnalysisWindowSeconds(analysisVideoWindowSeconds)}
                        onChange={(e) => setAnalysisVideoWindowSeconds(clampAnalysisWindowSeconds(e.target.value))}
                      />
                      <span>s</span>
                    </div>
                  </div>
                </div>
                )}
              </div>
            </div>

            <div className="hero-actions">
              <button
                className="primary-button"
                onClick={handleStart}
                disabled={(audioSource === 'file' && !fileObjectUrl) || (!transcriptionServiceEnabled && !analysisEnabled)}
              >
                <Mic size={16} /> Start Session
              </button>
            </div>

            {!transcriptionServiceEnabled && !analysisEnabled && (
              <p className="source-hint">Select at least one service before starting.</p>
            )}

            {errorMessage && !hasQuotaExceededSignal && <p className="error-banner"><AlertCircle size={16} /> {errorMessage}</p>}
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

        {showResponseFormatModal && (
          <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setShowResponseFormatModal(false); }}>
            <div className="modal-content panel-glass">
              <div className="modal-header">
                <h3>Structured Output Schema</h3>
                <button className="icon-btn" onClick={() => setShowResponseFormatModal(false)}>
                  <X size={16} />
                </button>
              </div>
              <p className="modal-hint">
                Paste a JSON schema to enforce structured output from the analysis LLM.
                Example: <code>{'{"type": "json_object", "schema": {"probability": {"type": "number"}}}'}</code>
              </p>
              <textarea
                className="modal-textarea"
                rows={8}
                value={responseFormatDraft}
                onChange={(e) => setResponseFormatDraft(e.target.value)}
                placeholder={'{\n  "type": "json_object",\n  "schema": {\n    "probability": { "type": "number", "minimum": 0, "maximum": 1 },\n    "confidence": { "type": "string", "enum": ["high", "medium", "low"] },\n    "reasoning": { "type": "string" }\n  }\n}'}
              />
              <div className="modal-actions">
                <button
                  className="primary-button"
                  onClick={() => {
                    const draft = responseFormatDraft.trim();
                    if (!draft) {
                      setAnalysisResponseFormat(null);
                      setShowResponseFormatModal(false);
                      return;
                    }
                    try {
                      const parsed = JSON.parse(draft);
                      if (typeof parsed !== 'object' || parsed === null) {
                        alert('Schema must be a JSON object');
                        return;
                      }
                      setAnalysisResponseFormat(parsed);
                      setShowResponseFormatModal(false);
                    } catch (err) {
                      alert('Invalid JSON: ' + err.message);
                    }
                  }}
                >
                  Save Schema
                </button>
                <button
                  className="secondary-button"
                  onClick={() => {
                    setAnalysisResponseFormat(null);
                    setResponseFormatDraft('');
                    setShowResponseFormatModal(false);
                  }}
                >
                  Clear
                </button>
              </div>
            </div>
          </div>
        )}

        {showAnalysisErrorsModal && (
          <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setShowAnalysisErrorsModal(false); }}>
            <div className="modal-content panel-glass analysis-error-modal">
              <div className="modal-header">
                <h3>Analysis Errors ({analysisErrorEntries.length})</h3>
                <button className="icon-btn" onClick={() => setShowAnalysisErrorsModal(false)}>
                  <X size={16} />
                </button>
              </div>
              <p className="modal-hint">
                Errors emitted by live analysis are shown here, including any raw model output when parsing fails.
              </p>
              <div className="analysis-error-modal-list">
                {analysisErrorEntries.length === 0 ? (
                  <div className="empty-state compact">
                    <p>No analysis errors in this session.</p>
                  </div>
                ) : (
                  analysisErrorEntries.map((entry) => (
                    <article key={entry.id} className="analysis-entry error">
                      <div className="analysis-entry-meta">
                        <span className="analysis-entry-type">error</span>
                        <span className="analysis-entry-mode">{getAnalysisModeLabel(entry.mode)}</span>
                        <span className="analysis-entry-ts">{formatDuration(entry.timestampMs || 0)}</span>
                      </div>
                      <MarkdownText content={entry.text} />
                    </article>
                  ))
                )}
              </div>
              <div className="modal-actions">
                <button className="secondary-button" onClick={() => setShowAnalysisErrorsModal(false)}>Close</button>
              </div>
            </div>
          </div>
        )}

        {/* Transcript and analysis panels — shown only when session is active */}
        {isStarted && (
            <div>
          {/* Compact session bar — shown whenever session is active, regardless of services */}
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

            <div className={livePanelsClassName}>
          {activeTranscriptionEnabled && (
          <section className="panel-glass transcript-panel">
            {errorMessage && !hasQuotaExceededSignal && <p className="error-banner"><AlertCircle size={16} /> {errorMessage}</p>}

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

          {activeAnalysisEnabled && (
            <section className="panel-glass analysis-panel">
              <div className="analysis-panel-header">
                <h2>Live Analysis</h2>
                <div className="analysis-panel-actions">
                  <button
                    className={`analysis-error-count-btn ${analysisErrorEntries.length > 0 ? 'has-errors' : ''}`}
                    onClick={() => setShowAnalysisErrorsModal(true)}
                    disabled={analysisErrorEntries.length === 0}
                    title={analysisErrorEntries.length > 0 ? 'View analysis errors' : 'No analysis errors yet'}
                  >
                    <AlertCircle size={14} />
                    Errors ({analysisErrorEntries.length})
                  </button>
                  <button
                    className={`autoscroll-toggle ${analysisAutoScroll ? 'active' : ''}`}
                    onClick={() => setAnalysisAutoScroll((v) => !v)}
                    title={analysisAutoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
                  >
                    <ChevronsDown size={14} />
                    {analysisAutoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
                  </button>
                  <span className="analysis-mode-badge">{getAnalysisModeLabel(analysisMode)}</span>
                </div>
              </div>

              {!effectiveAnalysisEnabled && (
                <div className="analysis-degraded-banner">
                  <AlertCircle size={14} />
                  <span>{modeDisabledReason(analysisMode)}</span>
                </div>
              )}

              <div className="analysis-scroll" ref={analysisScrollRef}>
                {analysisDisplayEntries.length === 0 ? (
                  <div className="empty-state compact">
                    <p>No analysis events yet.</p>
                    <span>Events will appear here once analysis is active.</span>
                  </div>
                ) : (
                  <>
                    {analysisSignalRows.length > 0 && (
                      <article className="analysis-entry">
                        <div className="analysis-entry-meta">
                          <span className="analysis-entry-type">signal</span>
                          <span className="analysis-entry-mode">{getAnalysisModeLabel(analysisMode)}</span>
                          <span className="analysis-entry-ts">{analysisSignalRows.length} rows</span>
                        </div>
                        <AnalysisContent
                          content=""
                          responseFormat={analysisResponseFormat}
                          signalRows={analysisSignalRows}
                          emptyMessage="No analysis rows yet."
                        />
                      </article>
                    )}
                    {analysisNarrativeEntries.map((entry) => (
                      <article key={entry.id} className="analysis-entry">
                        <div className="analysis-entry-meta">
                          <span className="analysis-entry-type">{entry.type.replace('analysis.', '')}</span>
                          <span className="analysis-entry-mode">{getAnalysisModeLabel(entry.mode)}</span>
                          <span className="analysis-entry-ts">{formatDuration(entry.timestampMs || 0)}</span>
                        </div>
                        <AnalysisContent
                          content={entry.text}
                          responseFormat={analysisResponseFormat}
                          signalRows={entry.signalRows}
                          emptyMessage="No analysis text available."
                        />
                      </article>
                    ))}
                  </>
                )}
              </div>
            </section>
          )}
            </div>
          </div>
        )}

      </div>

    </div>
  );
}
