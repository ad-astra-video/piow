import { getSession } from './supabase';

const API_BASE = `${window.location.origin}/api/v1`;
const WS_ENDPOINT = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

class WHIPClient {
  constructor(streamId, accessToken) {
    this.whipEndpoint = `${API_BASE}/stream/${streamId}/whip`;
    this.accessToken = accessToken || null;
    this.pc = null;
  }

  async start(tracks) {
    if (!tracks || !Array.isArray(tracks) || tracks.length === 0) {
      throw new Error('Invalid tracks parameter');
    }
    for (let i = 0; i < tracks.length; i++) {
      const track = tracks[i];
      if (!(track instanceof MediaStreamTrack)) {
        throw new Error(`Invalid track at index ${i}`);
      }
      if (track.readyState === 'ended') {
        throw new Error(`Track at index ${i} is already ended`);
      }
    }

    this.pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
    });

    const mediaStream = new MediaStream(tracks);
    mediaStream.getTracks().forEach(track => this.pc.addTrack(track, mediaStream));

    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    await this._waitForIceGathering();

    const headers = { 'Content-Type': 'application/sdp' };
    if (this.accessToken) headers['Authorization'] = `Bearer ${this.accessToken}`;

    const response = await fetch(this.whipEndpoint, {
      method: 'POST',
      body: this.pc.localDescription.sdp,
      headers
    });

    if (!response.ok) {
      const errorBody = await response.text().catch(() => '');
      throw new Error(`WHIP proxy failed: ${response.status} ${errorBody}`);
    }

    const answerSdp = await response.text();
    const answer = new RTCSessionDescription({ type: 'answer', sdp: answerSdp });
    await this.pc.setRemoteDescription(answer);
    return this.pc;
  }

  _waitForIceGathering(timeoutMs = 5000) {
    return new Promise((resolve) => {
      if (this.pc.iceGatheringState === 'complete') { resolve(); return; }
      const onStateChange = () => {
        if (this.pc.iceGatheringState === 'complete') {
          this.pc.removeEventListener('icegatheringstatechange', onStateChange);
          clearTimeout(timer);
          resolve();
        }
      };
      this.pc.addEventListener('icegatheringstatechange', onStateChange);
      const timer = setTimeout(() => {
        this.pc.removeEventListener('icegatheringstatechange', onStateChange);
        resolve();
      }, timeoutMs);
    });
  }

  stop() {
    if (this.pc) {
      this.pc.getSenders().forEach(sender => sender.track?.stop());
      this.pc.close();
      this.pc = null;
    }
  }
}

function formatDuration(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = totalSeconds % 60;
  return `${hh.toString().padStart(2, '0')}:${mm.toString().padStart(2, '0')}:${ss.toString().padStart(2, '0')}`;
}

function isLikelySentenceBoundary(buffer, segmentStart, matchStart, matchEnd) {
  const punctuation = buffer.slice(matchStart, matchEnd);
  if (/[!?]/.test(punctuation)) {
    return true;
  }

  const leftWithPeriod = buffer.slice(0, matchEnd).trimEnd();
  const right = buffer.slice(matchEnd);
  const rightTrimmed = right.trimStart();

  // Keep decimal values (e.g., 3.14) together.
  const prevChar = matchStart > 0 ? buffer[matchStart - 1] : '';
  const nextChar = rightTrimmed[0] || '';
  if (/\d/.test(prevChar) && /\d/.test(nextChar)) {
    return false;
  }

  const candidate = buffer.slice(segmentStart, matchEnd).trim();
  // Avoid promoting tiny fragments like "U." or "S." to full sentences.
  if (candidate.length < 6 || !/\s/.test(candidate)) {
    return false;
  }

  const stem = candidate.replace(/[.!?]+$/, '').trim();
  const stemParts = stem.split(/\s+/);
  const lastWord = stemParts[stemParts.length - 1] || '';
  if (lastWord.length < 2) {
    return false;
  }

  return true;
}

function isPlaceholderCellValue(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === ''
    || normalized === 'none'
    || normalized === 'null'
    || normalized === 'n/a'
    || normalized === 'na'
    || normalized === 'unknown'
    || normalized === '-'
    || normalized === '--';
}

function isMarkdownSeparatorRow(cells) {
  return cells.length > 0 && cells.every((cell) => /^:?-{2,}:?$/.test(cell));
}

function sanitizeAnalysisText(rawText) {
  const text = typeof rawText === 'string' ? rawText.trim() : '';
  if (!text) {
    return '';
  }

  const lines = text.split(/\r?\n/);
  let removedRows = 0;
  const keptLines = lines.filter((line) => {
    if (!line.includes('|')) {
      return true;
    }

    const cells = line
      .split('|')
      .map((cell) => cell.trim())
      .filter((cell) => cell.length > 0);

    if (cells.length === 0 || isMarkdownSeparatorRow(cells)) {
      return true;
    }

    const allPlaceholderValues = cells.every((cell) => isPlaceholderCellValue(cell));
    if (allPlaceholderValues) {
      removedRows += 1;
      return false;
    }
    return true;
  });

  const normalized = keptLines.join('\n').trim();
  if (removedRows > 0 && !normalized) {
    return '';
  }
  return normalized;
}

function isPlaceholderSignalTimestamp(value) {
  if (typeof value !== 'string') return true;
  const normalized = value.trim();
  return normalized === ''
    || normalized === '0:00'
    || normalized === '00:00'
    || normalized === '0'
    || normalized === '00:0';
}

function parseSignalTimestampToMs(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().replace(/^\[/, '').replace(/\]$/, '');
  if (!normalized) return null;

  const parts = normalized.split(':').map((segment) => Number.parseInt(segment, 10));
  if (parts.some((part) => Number.isNaN(part) || part < 0)) {
    return null;
  }

  if (parts.length === 2) {
    const [mm, ss] = parts;
    if (ss > 59) return null;
    return ((mm * 60) + ss) * 1000;
  }

  if (parts.length === 3) {
    const [hh, mm, ss] = parts;
    if (mm > 59 || ss > 59) return null;
    return (((hh * 60) + mm) * 60 + ss) * 1000;
  }

  return null;
}

function normalizeSignalTimestamp(value, fallbackTimestampMs) {
  const fallbackMs = Math.max(Number(fallbackTimestampMs) || 0, 0);
  if (isPlaceholderSignalTimestamp(value)) {
    return formatDuration(fallbackMs);
  }

  const parsedMs = parseSignalTimestampToMs(value);
  if (parsedMs == null) {
    return formatDuration(fallbackMs);
  }

  // Keep model-provided timestamps only when they are close to the event time.
  // Large drift means the model likely emitted a non-stream-relative value.
  const MAX_TIMESTAMP_DRIFT_MS = 45000;
  if (Math.abs(parsedMs - fallbackMs) > MAX_TIMESTAMP_DRIFT_MS) {
    return formatDuration(fallbackMs);
  }

  return formatDuration(parsedMs);
}

function escapePipe(value) {
  return String(value ?? '').replace(/\|/g, '\\|');
}

function normalizeSignalCellValue(value) {
  return value == null ? '' : String(value);
}

function extractAnalysisSignalRows(signalData, fallbackTimestampMs) {
  if (!signalData || !Array.isArray(signalData.items)) {
    return [];
  }

  return signalData.items
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const rowTimestamp = normalizeSignalTimestamp(item.timestamp, fallbackTimestampMs || 0);
      return {
        timestamp: rowTimestamp,
        category: normalizeSignalCellValue(item.category),
        item: normalizeSignalCellValue(item.item),
        priority: normalizeSignalCellValue(item.priority),
      };
    })
    .filter(Boolean);
}

function formatAnalysisSignalData(signalData, fallbackTimestampMs) {
  const signalRows = extractAnalysisSignalRows(signalData, fallbackTimestampMs);
  if (signalRows.length > 0) {
    const rows = signalRows.map((row) => (
      `| ${escapePipe(row.timestamp)} | ${escapePipe(row.category)} | ${escapePipe(row.item)} | ${escapePipe(row.priority)} |`
    ));

    if (rows.length > 0) {
      return [
        '| Timestamp | Category | Item | Priority |',
        '| --- | --- | --- | --- |',
        ...rows,
      ].join('\n');
    }
  }

  return `\`\`\`json\n${JSON.stringify(signalData || {}, null, 2)}\n\`\`\``;
}

class StreamManager {
  constructor() {
    this.state = {
      isStarted: false,
      status: 'Ready.',
      transcriptEntries: [],
      partialTranscript: '',
      partialTranscriptTimestamp: '',
      textTimestamps: [],
      errorMessage: '',
      elapsedMs: 0,
      localAnnotations: {},
      translationEntries: [],
      analysisEntries: [],
      transcriptionEnabled: true,
      analysisEnabled: false,
      analysisMode: 'multimodal',
      quotaError: null,
      hasAudioTrack: false,
      hasVideoTrack: false,
    };
    this.listeners = new Set();
    this.whipClient = null;
    this.ws = null;
    this.localStream = null;
    this.blackVideoSource = null;
    this.streamId = null;
    this.accessToken = null;
    this._beforeunloadHandler = null;
    // File audio capture state
    this._fileAudioCtx = null;
    this._fileSourceNode = null;
    this._fileMediaElement = null;
    this._fileVideoStream = null;
    // Screen share video tracks kept alive to prevent Chrome from killing the audio track
    this._screenVideoTracks = [];
    this._drainAudioCtx = null;
    this._drainAudioSource = null;
    this._drainAudioStream = null;
    this._drainTracks = [];
    // Timer
    this._timerInterval = null;
    this._streamStartTime = 0;
    // Sentence buffering for timestamped transcript
    this._textBuffer = '';
    this._textBufferStartMs = null;
  }

  _setState(partial) {
    this.state = { ...this.state, ...partial };
    this.listeners.forEach((cb) => cb(this.state));
  }

  subscribe(callback) {
    this.listeners.add(callback);
    callback(this.state);
    return () => this.listeners.delete(callback);
  }

  getState() {
    return this.state;
  }

  getStreamId() {
    return this.streamId;
  }

  // Local annotation methods (for live streams before transcription is persisted)
  addLocalAnnotation(sentenceIndex, sentenceTimestamp, type, content) {
    const id = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const annotation = {
      id,
      sentence_index: sentenceIndex,
      sentence_timestamp: sentenceTimestamp || '',
      type,
      content,
      completed: false,
    };
    const next = { ...this.state.localAnnotations };
    next[sentenceIndex] = [...(next[sentenceIndex] || []), annotation];
    this._setState({ localAnnotations: next });
    return annotation;
  }

  updateLocalAnnotation(annotationId, updates) {
    const next = { ...this.state.localAnnotations };
    for (const idx of Object.keys(next)) {
      next[idx] = next[idx].map((a) => (a.id === annotationId ? { ...a, ...updates } : a));
    }
    this._setState({ localAnnotations: next });
  }

  deleteLocalAnnotation(annotationId) {
    const next = { ...this.state.localAnnotations };
    for (const idx of Object.keys(next)) {
      next[idx] = next[idx].filter((a) => a.id !== annotationId);
      if (next[idx].length === 0) delete next[idx];
    }
    this._setState({ localAnnotations: next });
  }

  toggleLocalTodo(annotationId) {
    const annotation = Object.values(this.state.localAnnotations)
      .flat()
      .find((a) => a.id === annotationId);
    if (annotation) {
      this.updateLocalAnnotation(annotationId, { completed: !annotation.completed });
    }
  }

  async flushLocalAnnotations(streamId) {
    const allAnnotations = Object.values(this.state.localAnnotations).flat();
    if (!streamId || allAnnotations.length === 0) return;

    for (const a of allAnnotations) {
      try {
        await fetch(`${API_BASE}/streams/${streamId}/annotations`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(this.accessToken ? { 'Authorization': `Bearer ${this.accessToken}` } : {}),
          },
          body: JSON.stringify({
            sentence_index: a.sentence_index,
            sentence_text: this.state.transcriptEntries[a.sentence_index]?.text || '',
            sentence_timestamp: a.sentence_timestamp || this.state.transcriptEntries[a.sentence_index]?.timestamp || '',
            type: a.type,
            content: a.content,
            completed: a.completed,
          }),
        });
      } catch (e) {
        console.error('Failed to flush local annotation:', e);
      }
    }
    this._setState({ localAnnotations: {} });
  }

  _startTimer() {
    this._streamStartTime = Date.now();
    this._setState({ elapsedMs: 0 });
    if (this._timerInterval) clearInterval(this._timerInterval);
    this._timerInterval = setInterval(() => {
      if (!this.state.isStarted) {
        clearInterval(this._timerInterval);
        this._timerInterval = null;
        return;
      }
      this._setState({ elapsedMs: Date.now() - this._streamStartTime });
    }, 1000);
  }

  _stopTimer() {
    if (this._timerInterval) {
      clearInterval(this._timerInterval);
      this._timerInterval = null;
    }
  }

  async _createBlackVideoTrack() {
    if (this.blackVideoSource?.intervalId) {
      clearInterval(this.blackVideoSource.intervalId);
    }

    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;

    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('Failed to initialize canvas context for black video track');
    }

    const drawBlackFrame = () => {
      ctx.fillStyle = 'black';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };
    drawBlackFrame();

    const intervalId = setInterval(drawBlackFrame, 1000 / 15);

    const stream = canvas.captureStream(15);
    const videoTrack = stream.getVideoTracks()[0];

    if (!videoTrack) {
      throw new Error('Failed to create black video track');
    }

    try {
      await videoTrack.applyConstraints({
        width: { ideal: 320, max: 320 },
        height: { ideal: 240, max: 240 },
        frameRate: { ideal: 15, max: 15 },
      });
    } catch (e) {
      // Some browsers may reject constraints for canvas tracks; continue with capture defaults.
    }

    this.blackVideoSource = { canvas, stream, intervalId };
    return videoTrack;
  }

  async _createSilentAudioTrack() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      throw new Error('Web Audio API is not available in this browser.');
    }

    const audioCtx = new AudioContextClass();
    const destination = audioCtx.createMediaStreamDestination();
    const source = audioCtx.createConstantSource();
    const gain = audioCtx.createGain();
    gain.gain.value = 0;
    source.connect(gain);
    gain.connect(destination);
    source.start();

    const track = destination.stream.getAudioTracks()[0];
    if (!track) {
      source.stop();
      await audioCtx.close().catch(() => {});
      throw new Error('Failed to create silent audio track');
    }

    this._drainAudioCtx = audioCtx;
    this._drainAudioSource = source;
    this._drainAudioStream = destination.stream;
    this._drainTracks.push(track);
    return track;
  }

  async _replaceOutboundTracksForStopDrain({ replaceVideo = false } = {}) {
    const pc = this.whipClient?.pc;
    if (!pc) return;

    const senders = pc.getSenders();
    const hasAudioSender = senders.some((sender) => sender?.track?.kind === 'audio');
    const hasVideoSender = senders.some((sender) => sender?.track?.kind === 'video');

    let silentAudioTrack = null;
    let blackVideoTrack = null;

    try {
      if (hasAudioSender) {
        silentAudioTrack = await this._createSilentAudioTrack();
      }
      if (replaceVideo && hasVideoSender) {
        blackVideoTrack = await this._createBlackVideoTrack();
        this._drainTracks.push(blackVideoTrack);
      }

      const replacements = senders.map(async (sender) => {
        if (!sender?.track) return;
        if (sender.track.kind === 'audio' && silentAudioTrack) {
          await sender.replaceTrack(silentAudioTrack);
        }
        if (sender.track.kind === 'video' && blackVideoTrack) {
          await sender.replaceTrack(blackVideoTrack);
        }
      });

      await Promise.all(replacements);
    } catch (err) {
      console.warn('Failed to switch outbound tracks to stop-drain media:', err);
    }
  }

  async _getAudioTrack(sourceConfig) {
    const type = sourceConfig?.type || 'microphone';

    if (type === 'microphone') {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      return stream.getAudioTracks()[0];
    }

    if (type === 'screen') {
      const stream = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
      // Keep video tracks alive until stop() — stopping them immediately causes Chrome to
      // end the entire capture session, which kills the audio track along with it.
      this._screenVideoTracks = stream.getVideoTracks();
      const audioTrack = stream.getAudioTracks()[0];
      if (!audioTrack) {
        throw new Error(
          'Screen share did not include audio. Share a browser tab and enable the "Share tab audio" option.'
        );
      }
      return audioTrack;
    }

    if (type === 'file') {
      const { mediaElement } = sourceConfig;
      if (!mediaElement) throw new Error('No media element provided for file audio capture.');

      // If the AudioContext was created for a different element (e.g. user navigated away
      // and back), close it so we start fresh.
      if (this._fileAudioCtx && this._fileMediaElement !== mediaElement) {
        this._fileAudioCtx.close().catch(() => {});
        this._fileAudioCtx = null;
        this._fileSourceNode = null;
        this._fileMediaElement = null;
      }

      if (!this._fileAudioCtx || this._fileAudioCtx.state === 'closed') {
        this._fileAudioCtx = new AudioContext();
        this._fileSourceNode = this._fileAudioCtx.createMediaElementSource(mediaElement);
        this._fileMediaElement = mediaElement;
      }

      if (this._fileAudioCtx.state === 'suspended') {
        await this._fileAudioCtx.resume();
      }

      // Disconnect any previous routing before reconnecting
      try { this._fileSourceNode.disconnect(); } catch (_) {}

      const dest = this._fileAudioCtx.createMediaStreamDestination();
      // Route audio to both the capture stream and the speakers so the user can hear it
      this._fileSourceNode.connect(dest);
      this._fileSourceNode.connect(this._fileAudioCtx.destination);

      // Playback is intentionally deferred until after the WHIP connection is established.

      const audioTrack = dest.stream.getAudioTracks()[0];
      if (!audioTrack) throw new Error('Failed to capture audio track from media element.');
      return audioTrack;
    }

    throw new Error(`Unknown audio source type: ${type}`);
  }

  _getFileVideoTrack(sourceConfig) {
    const { mediaElement } = sourceConfig || {};
    if (!mediaElement) return null;

    const captureStreamFn = mediaElement.captureStream || mediaElement.mozCaptureStream;
    if (typeof captureStreamFn !== 'function') {
      return null;
    }

    const stream = captureStreamFn.call(mediaElement);
    const videoTrack = stream?.getVideoTracks?.()[0] || null;
    if (!videoTrack) {
      return null;
    }

    this._fileVideoStream = stream;
    return videoTrack;
  }

  // Call when the file media element is being replaced (e.g. user picks a new file)
  // so the cached AudioContext/SourceNode is recreated on the next start.
  resetFileAudio() {
    if (this._fileAudioCtx) {
      this._fileAudioCtx.close().catch(() => {});
    }
    this._fileAudioCtx = null;
    this._fileSourceNode = null;
    this._fileMediaElement = null;
    if (this._fileVideoStream) {
      this._fileVideoStream.getTracks().forEach((track) => track.stop());
    }
    this._fileVideoStream = null;
  }

  async _createStreamSession(translationConfig, analysisConfig = null, serviceConfig = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.accessToken) headers['Authorization'] = `Bearer ${this.accessToken}`;
    const body = { language: 'en' };
    if (serviceConfig) {
      body.live_transcription_enabled = serviceConfig.live_transcription_enabled !== false;
      body.live_translation_enabled = !!serviceConfig.live_translation_enabled;
    }
    if (translationConfig) {
      body.source_language = translationConfig.source_language;
      body.target_language = translationConfig.target_language;
    }
    if (analysisConfig) {
      body.analysis_enabled = !!analysisConfig.analysis_enabled;
      body.analysis_mode = analysisConfig.analysis_mode || 'multimodal';
      if (typeof analysisConfig.analysis_audio_chunk_seconds === 'number') {
        body.analysis_audio_chunk_seconds = analysisConfig.analysis_audio_chunk_seconds;
      }
      if (typeof analysisConfig.analysis_video_chunk_seconds === 'number') {
        body.analysis_video_chunk_seconds = analysisConfig.analysis_video_chunk_seconds;
      }
      if (typeof analysisConfig.analysis_video_fps === 'number') {
        body.analysis_video_fps = analysisConfig.analysis_video_fps;
      }
      if (typeof analysisConfig.analysis_max_tokens === 'number') {
        body.analysis_max_tokens = analysisConfig.analysis_max_tokens;
      }
      if (typeof analysisConfig.analysis_prompt === 'string') {
        body.analysis_prompt = analysisConfig.analysis_prompt;
      }
      if (analysisConfig.analysis_response_format != null) {
        body.analysis_response_format = analysisConfig.analysis_response_format;
      }
    }
    const response = await fetch(`${API_BASE}/stream/process`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let errorPayload = null;
      let errorBody = '';
      try {
        errorPayload = await response.json();
      } catch (_jsonErr) {
        errorBody = await response.text().catch(() => '');
      }

      const err = new Error(
        (errorPayload && (errorPayload.error || errorPayload.message))
          || `Stream session creation failed (${response.status})`
      );
      err.status = response.status;
      err.payload = errorPayload;
      err.code = errorPayload?.code;
      err.serviceType = errorPayload?.service_type;
      err.tier = errorPayload?.tier;
      err.quota = errorPayload?.quota;
      err.errorBody = errorBody;
      throw err;
    }
    const data = await response.json();
    if (!data.stream_id) throw new Error('Stream session response missing stream_id');
    return data;
  }

  async _requestStreamStop(streamId) {
    if (!streamId) return null;
    const headers = {};

    // Refresh token at stop time so long-running streams can stop after token rotation.
    try {
      const session = await getSession();
      if (session?.access_token) {
        this.accessToken = session.access_token;
      }
    } catch (_err) {}

    if (this.accessToken) headers['Authorization'] = `Bearer ${this.accessToken}`;

    const response = await fetch(`${API_BASE}/stream/${streamId}/stop`, {
      method: 'POST',
      headers,
    });

    if (!response.ok) {
      const errorBody = await response.text().catch(() => '');
      throw new Error(`Stop request failed (${response.status}): ${errorBody}`);
    }

    try {
      return await response.json();
    } catch (_e) {
      return null;
    }
  }

  async updateTranslationConfig(config) {
    if (!this.streamId) return;
    try {
      const headers = {
        'Content-Type': 'application/json',
        ...(this.accessToken ? { 'Authorization': `Bearer ${this.accessToken}` } : {}),
      };
      const response = await fetch(`${API_BASE}/stream/${this.streamId}/translation`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(config || {}),
      });
      if (!response.ok) {
        console.warn('Failed to update translation config:', response.status);
      }
    } catch (err) {
      console.warn('Translation config update error:', err);
    }
  }

  async updateAnalysisConfig(config) {
    if (!this.streamId) return;
    try {
      const headers = {
        'Content-Type': 'application/json',
        ...(this.accessToken ? { 'Authorization': `Bearer ${this.accessToken}` } : {}),
      };
      const response = await fetch(`${API_BASE}/stream/${this.streamId}/analysis`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(config || {}),
      });
      if (!response.ok) {
        console.warn('Failed to update analysis config:', response.status);
      }
    } catch (err) {
      console.warn('Analysis config update error:', err);
    }
  }

  async start(accessToken, sourceConfig, translationConfig = null, analysisConfig = null, serviceConfig = null) {
    if (this.state.isStarted) return;
    this.accessToken = accessToken || null;

    const sourceType = sourceConfig?.type || 'microphone';
    const transcriptionEnabled = serviceConfig?.live_transcription_enabled !== false;
    const analysisRequested = !!analysisConfig?.analysis_enabled;
    const analysisMode = analysisConfig?.analysis_mode || 'multimodal';
    const shouldSendRealVideoTrack = analysisRequested && (analysisMode === 'multimodal' || analysisMode === 'video_only');
    const statusLabels = {
      microphone: 'Starting...',
      screen: 'Starting...',
      file: 'Starting...',
    };

    try {
      const sourceTracks = {
        hasAudioTrack: true,
        hasVideoTrack: shouldSendRealVideoTrack && (sourceType === 'screen' || (sourceType === 'file' && !!sourceConfig?.hasVideo)),
      };
      if (sourceType === 'microphone') {
        sourceTracks.hasVideoTrack = false;
      }

      this._setState({
        errorMessage: '',
        quotaError: null,
        transcriptEntries: [],
        partialTranscript: '',
        partialTranscriptTimestamp: '',
        textTimestamps: [],
        translationEntries: [],
        analysisEntries: [],
        transcriptionEnabled,
        analysisEnabled: !!analysisConfig?.analysis_enabled,
        analysisMode: analysisConfig?.analysis_mode || 'multimodal',
        hasAudioTrack: sourceTracks.hasAudioTrack,
        hasVideoTrack: sourceTracks.hasVideoTrack,
        status: statusLabels[sourceType] || 'Getting user media...',
        elapsedMs: 0,
      });

      const audioTrack = await this._getAudioTrack(sourceConfig);
      let videoTrack = null;

      if (shouldSendRealVideoTrack && sourceType === 'screen') {
        videoTrack = this._screenVideoTracks[0] || null;
        if (!videoTrack) {
          sourceTracks.hasVideoTrack = false;
        }
      }

      if (shouldSendRealVideoTrack && sourceType === 'file' && sourceTracks.hasVideoTrack) {
        videoTrack = this._getFileVideoTrack(sourceConfig);
        if (!videoTrack) {
          sourceTracks.hasVideoTrack = false;
        }
      }

      if (!videoTrack) {
        videoTrack = await this._createBlackVideoTrack();
      }

      this._setState({
        hasAudioTrack: sourceTracks.hasAudioTrack,
        hasVideoTrack: sourceTracks.hasVideoTrack,
      });

      this.localStream = new MediaStream([audioTrack, videoTrack]);

      this._setState({ status: 'Connecting...' });
      let sessionData;
      try { sessionData = await this._createStreamSession(translationConfig, analysisConfig, serviceConfig); }
      catch (sessionError) {
        const isQuotaExceeded = sessionError?.status === 402 && sessionError?.code === 'quota_exceeded';
        this._setState({
          status: isQuotaExceeded ? 'Ready.' : 'Connection failed.',
          errorMessage: isQuotaExceeded ? '' : 'Could not create a streaming session.',
          quotaError: isQuotaExceeded
            ? {
                code: sessionError.code,
                status: sessionError.status,
                serviceType: sessionError.serviceType,
                tier: sessionError.tier,
                quota: sessionError.quota || null,
                message: sessionError.message || 'Quota exceeded for your current plan.',
              }
            : null,
        });
        throw sessionError;
      }

      const { stream_id } = sessionData;
      this.streamId = stream_id;

      this._setState({ status: 'Connecting...' });
      this.whipClient = new WHIPClient(stream_id, this.accessToken || undefined);

      try {
        const pc = await this.whipClient.start([audioTrack, videoTrack]);
        pc.ontrack = (event) => { console.log('Received track from WHIP:', event.track.kind); };

        this._setState({ status: 'Connecting...' });
        const ws = new WebSocket(WS_ENDPOINT);
        this.ws = ws;

        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'start_stream', stream_id }));
          this._setState({ status: 'Connected.', isStarted: true });
          this._startTimer();
          // For file sources, start playback now that the pipeline is ready.
          if (this._fileMediaElement) {
            this._fileMediaElement.currentTime = 0;
            this._fileMediaElement.play().catch(() => {});
          }
        };

        ws.onmessage = (event) => {
          if (event.data instanceof Blob) return;
          try {
            const message = JSON.parse(event.data);
            const msgType = typeof message.type === 'string' ? message.type : '';

            // Helper: safely append text with space between words
            const _appendText = (buffer, text) => {
              if (!buffer) return text;
              if (!text) return buffer;
              // Preserve provider token spacing exactly; do not synthesize spaces.
              return buffer + text;
            };

            // Helper: extract sentences from buffer and timestamp them
            const _processBuffer = (buffer, startMs, currentMs) => {
              const sentences = [];
              let remaining = buffer;
              let sentenceStartMs = typeof startMs === 'number' ? startMs : currentMs;
              // Find sentence endings (. ! ?)
              const regex = /[.!?]+/g;
              let match;
              let lastEnd = 0;
              while ((match = regex.exec(buffer)) !== null) {
                if (!isLikelySentenceBoundary(buffer, lastEnd, match.index, match.index + match[0].length)) {
                  continue;
                }
                const sentence = buffer.slice(lastEnd, match.index + match[0].length).trim();
                if (sentence) {
                  const ts = formatDuration(sentenceStartMs);
                  sentences.push({ timestamp: ts, text: sentence });
                }
                lastEnd = match.index + match[0].length;
                sentenceStartMs = currentMs;
              }
              remaining = buffer.slice(lastEnd).trimStart();
              const remainingStartMs = remaining ? sentenceStartMs : null;
              return { sentences, remaining, remainingStartMs };
            };

            if (
              !this.state.transcriptionEnabled && (
                msgType === 'transcription.delta' ||
                msgType === 'conversation.item.input_audio_transcription.delta' ||
                msgType === 'response.output_text.delta' ||
                msgType === 'response.output_audio_transcript.delta' ||
                msgType === 'response.text.delta' ||
                msgType === 'response.audio_transcript.delta' ||
                msgType === 'transcription.done' ||
                msgType === 'conversation.item.input_audio_transcription.completed' ||
                msgType === 'response.output_text.done' ||
                msgType === 'response.output_audio_transcript.done' ||
                msgType === 'response.text.done' ||
                msgType === 'response.audio_transcript.done' ||
                msgType === 'transcription'
              )
            ) {
              return;
            }

            if (
              msgType === 'transcription.delta' ||
              msgType === 'conversation.item.input_audio_transcription.delta' ||
              msgType === 'response.output_text.delta' ||
              msgType === 'response.output_audio_transcript.delta' ||
              msgType === 'response.text.delta' ||
              msgType === 'response.audio_transcript.delta'
            ) {
              const delta = typeof message.delta === 'string' ? message.delta : '';
              if (!delta) return;
              const currentMs = this.state.elapsedMs;
              if (!this._textBuffer) {
                this._textBufferStartMs = currentMs;
              }
              this._textBuffer = _appendText(this._textBuffer, delta);
              const { sentences, remaining, remainingStartMs } = _processBuffer(
                this._textBuffer,
                this._textBufferStartMs,
                currentMs
              );
              if (sentences.length > 0) {
                this._textBuffer = remaining;
                this._textBufferStartMs = remainingStartMs;
                this._setState({
                  transcriptEntries: [...this.state.transcriptEntries, ...sentences],
                  partialTranscript: remaining,
                  partialTranscriptTimestamp: remaining ? formatDuration(remainingStartMs) : '',
                  status: 'Connected.',
                });
              } else {
                this._setState({
                  partialTranscript: this._textBuffer,
                  partialTranscriptTimestamp: this._textBuffer ? formatDuration(this._textBufferStartMs) : '',
                  status: 'Connected.'
                });
              }
            } else if (
              msgType === 'transcription.done' ||
              msgType === 'conversation.item.input_audio_transcription.completed' ||
              msgType === 'response.output_text.done' ||
              msgType === 'response.output_audio_transcript.done' ||
              msgType === 'response.text.done' ||
              msgType === 'response.audio_transcript.done'
            ) {
              const transcript =
                (typeof message.transcript === 'string' && message.transcript) ||
                (typeof message.text === 'string' && message.text) ||
                '';
              if (!transcript) return;
              const currentMs = this.state.elapsedMs;
              if (!this._textBuffer) {
                this._textBufferStartMs = currentMs;
              }
              // If the buffer already contains this transcript (built from prior
              // deltas), avoid appending it again and causing duplication.
              if (!this._textBuffer.endsWith(transcript)) {
                this._textBuffer = _appendText(this._textBuffer, transcript);
              }
              const { sentences, remaining, remainingStartMs } = _processBuffer(
                this._textBuffer,
                this._textBufferStartMs,
                currentMs
              );
              const allEntries = [...this.state.transcriptEntries, ...sentences];
              if (remaining) {
                const ts = formatDuration(remainingStartMs ?? currentMs);
                allEntries.push({ timestamp: ts, text: remaining });
              }
              this._textBuffer = '';
              this._textBufferStartMs = null;
              this._setState({
                transcriptEntries: allEntries,
                partialTranscript: '',
                partialTranscriptTimestamp: '',
                status: 'Connected.',
              });
            } else if (msgType === 'transcription') {
              const text = typeof message.text === 'string' ? message.text : '';
              const isFinal = message.is_final;
              if (!text) return;
              const currentMs = this.state.elapsedMs;
              // The 'transcription' message type sends the FULL cumulative text
              // (not a delta), so we replace the buffer rather than append.
              if (isFinal) {
                if (!this._textBufferStartMs) {
                  this._textBufferStartMs = currentMs;
                }
                this._textBuffer = text;
                const { sentences, remaining, remainingStartMs } = _processBuffer(
                  this._textBuffer,
                  this._textBufferStartMs,
                  currentMs
                );
                const allEntries = [...this.state.transcriptEntries, ...sentences];
                if (remaining) {
                  const ts = formatDuration(remainingStartMs ?? currentMs);
                  allEntries.push({ timestamp: ts, text: remaining });
                }
                this._textBuffer = '';
                this._textBufferStartMs = null;
                this._setState({
                  transcriptEntries: allEntries,
                  partialTranscript: '',
                  partialTranscriptTimestamp: '',
                  status: 'Connected.',
                });
              } else {
                if (!this._textBuffer) {
                  this._textBufferStartMs = currentMs;
                }
                this._textBuffer = text;
                const { sentences, remaining, remainingStartMs } = _processBuffer(
                  this._textBuffer,
                  this._textBufferStartMs,
                  currentMs
                );
                if (sentences.length > 0) {
                  this._textBuffer = remaining;
                  this._textBufferStartMs = remainingStartMs;
                  this._setState({
                    transcriptEntries: [...this.state.transcriptEntries, ...sentences],
                    partialTranscript: remaining,
                    partialTranscriptTimestamp: remaining ? formatDuration(remainingStartMs) : '',
                    status: 'Connected.',
                  });
                } else {
                  this._setState({
                    partialTranscript: this._textBuffer,
                    partialTranscriptTimestamp: this._textBuffer ? formatDuration(this._textBufferStartMs) : '',
                    status: 'Connected.'
                  });
                }
              }
            } else if (msgType === 'text_timestamps') {
              const words = Array.isArray(message.words) ? message.words : [];
              const transcript = typeof message.transcript === 'string' ? message.transcript : '';
              const windowId = message.window_id;
              const next = [...this.state.textTimestamps, { windowId, transcript, words }];
              this._setState({
                textTimestamps: next.slice(-50),
                status: 'Connected.',
              });
            } else if (msgType === 'text_timestamps.error') {
              const errorText =
                (typeof message.error === 'string' && message.error) ||
                (typeof message.text === 'string' && message.text) ||
                'Timestamp alignment error';
              this._setState({ errorMessage: errorText, status: 'Connected.' });
            } else if (msgType === 'translation') {
              const translatedText = typeof message.text === 'string' ? message.text : '';
              const originalText = typeof message.original === 'string' ? message.original : '';
              if (translatedText) {
                // Keep original text intact; attach translatedText to the matching entry
                const entries = this.state.transcriptEntries;
                const matchIdx = entries.findLastIndex((e) => e.text === originalText);
                const updatedEntries =
                  matchIdx >= 0
                    ? entries.map((e, i) =>
                        i === matchIdx ? { ...e, translatedText } : e
                      )
                    : entries;
                this._setState({
                  transcriptEntries: updatedEntries,
                  translationEntries: [
                    ...this.state.translationEntries,
                    { text: translatedText, original: originalText, status: 'done' },
                  ],
                });
              }
            } else if (msgType === 'translation.error') {
              const errorText = typeof message.error === 'string' ? message.error : 'Translation failed';
              this._setState({ errorMessage: errorText });
            } else if (msgType === 'analysis.delta' || msgType === 'analysis.done' || msgType === 'analysis.signal') {
              const isSignal = msgType === 'analysis.signal';
              const resolvedTimestampMs = typeof message.timestamp_ms === 'number'
                ? message.timestamp_ms
                : (typeof message.window_end_ms === 'number' ? message.window_end_ms : this.state.elapsedMs);
              const rawAnalysisText = isSignal
                ? formatAnalysisSignalData(message.data || {}, resolvedTimestampMs)
                : (typeof message.text === 'string'
                  ? message.text
                  : (typeof message.summary === 'string' ? message.summary : ''));
              const normalizedAnalysisText = isSignal ? rawAnalysisText : sanitizeAnalysisText(rawAnalysisText);
              const entry = {
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                type: msgType,
                mode: typeof message.mode === 'string' ? message.mode : this.state.analysisMode,
                text: normalizedAnalysisText,
                timestampMs: resolvedTimestampMs,
                signalRows: isSignal ? extractAnalysisSignalRows(message.data || {}, resolvedTimestampMs) : null,
              };
              if (entry.text) {
                this._setState({
                  analysisEntries: [...this.state.analysisEntries, entry].slice(-200),
                });
              } else {
                console.debug('Dropped empty/suppressed analysis update', {
                  type: msgType,
                  mode: entry.mode,
                  timestampMs: entry.timestampMs,
                });
              }
            } else if (msgType === 'analysis.status') {
              const statusText = typeof message.text === 'string' ? message.text : '';
              if (statusText) {
                this._setState({ status: statusText });
              }
            } else if (msgType === 'analysis.error') {
              const errorText = typeof message.error === 'string' ? message.error : 'Analysis failed';
              const parseError = typeof message.parse_error === 'string' ? message.parse_error : '';
              const rawText = typeof message.raw_text === 'string' ? message.raw_text : '';
              const detailLines = [errorText];
              if (parseError) {
                detailLines.push(`Parse error: ${parseError}`);
              }
              if (rawText) {
                detailLines.push('', 'Raw response:', '```', rawText, '```');
              }
              const entry = {
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                type: 'analysis.error',
                mode: typeof message.mode === 'string' ? message.mode : this.state.analysisMode,
                text: detailLines.join('\n'),
                timestampMs: typeof message.timestamp_ms === 'number'
                  ? message.timestamp_ms
                  : this.state.elapsedMs,
              };
              this._setState({
                analysisEntries: [...this.state.analysisEntries, entry].slice(-200),
              });
            } else if (msgType === 'status') {
              this._setState({ status: message.text });
            } else if (msgType === 'error') {
              const errorText =
                (typeof message.text === 'string' && message.text) ||
                (message.error && typeof message.error.message === 'string' && message.error.message) ||
                'Realtime error';
              this._setState({ errorMessage: errorText, status: 'Error.' });
            }
          } catch (parseErr) {}
        };

        ws.onclose = () => {
          if (this.state.isStarted) {
            this.stop({ preserveStatus: true });
            this._setState({ status: 'Disconnected.' });
          }
        };

        ws.onerror = () => {
          this._setState({ errorMessage: 'Realtime connection failed.', status: 'Connection error.' });
        };
      } catch (whipError) {
        this._setState({ status: 'Connection failed.', errorMessage: 'Could not establish the WHIP session.' });
        if (this.whipClient) { this.whipClient.stop(); this.whipClient = null; }
        this.streamId = null;
        throw whipError;
      }

      // Register beforeunload handler so we clean up server-side on page close
      this._beforeunloadHandler = () => {
        if (this.streamId) {
          navigator.sendBeacon?.(`${API_BASE}/stream/${this.streamId}/stop`, new Blob([]));
        }
        this.stop({ preserveStatus: true });
      };
      window.addEventListener('beforeunload', this._beforeunloadHandler);
    } catch (err) {
      this._setState({ status: `Error: ${err.message}`, errorMessage: err.message });
      this.stop({ preserveStatus: true });
    }
  }

  async stop({ preserveStatus = false } = {}) {
    const wasStarted = this.state.isStarted;
    const activeStreamId = this.streamId;

    this._stopTimer();

    if (activeStreamId) {
      await this._replaceOutboundTracksForStopDrain({
        replaceVideo: !!this.state.analysisEnabled,
      });
    }

    let stopResult = null;
    if (activeStreamId) {
      try {
        stopResult = await this._requestStreamStop(activeStreamId);
      } catch (stopErr) {
        this._setState({ errorMessage: this.state.errorMessage || `Failed to stop stream cleanly: ${stopErr.message}` });
      }
    }

    // Flush local annotations if we got a transcription_id
    const transcriptionId = stopResult?.transcription_id;
    if (transcriptionId) {
      await this.flushLocalAnnotations(transcriptionId);
    }

    if (this.ws && this.ws.readyState === WebSocket.OPEN && activeStreamId) {
      try {
        this.ws.send(JSON.stringify({ type: 'stop_stream', stream_id: activeStreamId }));
      } catch (e) {}
    }
    this.streamId = null;

    if (this.whipClient) {
      this.whipClient.stop();
      this.whipClient = null;
    }
    if (this.ws) { this.ws.close(); this.ws = null; }
    if (this.localStream) {
      this.localStream.getTracks().forEach((track) => {
        // Don't stop file audio tracks — the AudioContext manages them.
        // Don't stop screen share video — handled separately in _screenVideoTracks.
        // Stop microphone and black-video tracks normally.
        if (this._fileSourceNode && track.kind === 'audio') return;
        track.stop();
      });
      this.localStream = null;
    }
    // Stop deferred screen share video tracks now that the session is ending.
    this._screenVideoTracks.forEach(t => t.stop());
    this._screenVideoTracks = [];
    if (this._fileMediaElement) {
      this._fileMediaElement.pause();
    }
    if (this.blackVideoSource?.intervalId) {
      clearInterval(this.blackVideoSource.intervalId);
    }
    this.blackVideoSource = null;
    this._drainTracks.forEach((track) => {
      try {
        track.stop();
      } catch (_e) {}
    });
    this._drainTracks = [];
    if (this._drainAudioSource) {
      try {
        this._drainAudioSource.stop();
      } catch (_e) {}
    }
    this._drainAudioSource = null;
    this._drainAudioStream = null;
    if (this._drainAudioCtx) {
      this._drainAudioCtx.close().catch(() => {});
    }
    this._drainAudioCtx = null;
    if (this._fileVideoStream) {
      this._fileVideoStream.getTracks().forEach((track) => track.stop());
      this._fileVideoStream = null;
    }

    if (this._beforeunloadHandler) {
      window.removeEventListener('beforeunload', this._beforeunloadHandler);
      this._beforeunloadHandler = null;
    }

    this._textBuffer = '';
    this._textBufferStartMs = null;
    this._setState({
      isStarted: false,
      partialTranscript: '',
      partialTranscriptTimestamp: '',
      status: !preserveStatus && wasStarted ? 'Transcription stopped.' : this.state.status,
      elapsedMs: 0,
      localAnnotations: transcriptionId ? {} : this.state.localAnnotations,
      translationEntries: [],
      analysisEntries: [],
      quotaError: null,
      hasAudioTrack: false,
      hasVideoTrack: false,
    });
  }

  dismissQuotaError() {
    this._setState({ quotaError: null });
  }
}

const streamManager = new StreamManager();
export default streamManager;
export { formatDuration };
