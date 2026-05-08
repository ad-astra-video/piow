import { getSession } from './supabase';

const API_BASE = `${window.location.origin}/api/v1`;
const WS_ENDPOINT = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

class WHIPClient {
  constructor(streamId, accessToken) {
    this.whipEndpoint = `${API_BASE}/transcribe/stream/${streamId}/whip`;
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

class StreamManager {
  constructor() {
    this.state = {
      isStarted: false,
      status: 'Ready.',
      transcriptEntries: [],
      partialTranscript: '',
      textTimestamps: [],
      errorMessage: '',
      elapsedMs: 0,
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
    // Screen share video tracks kept alive to prevent Chrome from killing the audio track
    this._screenVideoTracks = [];
    // Timer
    this._timerInterval = null;
    this._streamStartTime = 0;
    // Sentence buffering for timestamped transcript
    this._textBuffer = '';
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

  // Call when the file media element is being replaced (e.g. user picks a new file)
  // so the cached AudioContext/SourceNode is recreated on the next start.
  resetFileAudio() {
    if (this._fileAudioCtx) {
      this._fileAudioCtx.close().catch(() => {});
    }
    this._fileAudioCtx = null;
    this._fileSourceNode = null;
    this._fileMediaElement = null;
  }

  async _createStreamSession() {
    const headers = { 'Content-Type': 'application/json' };
    if (this.accessToken) headers['Authorization'] = `Bearer ${this.accessToken}`;
    const response = await fetch(`${API_BASE}/transcribe/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ language: 'en' }),
    });
    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`Stream session creation failed (${response.status}): ${errorBody}`);
    }
    const data = await response.json();
    if (!data.stream_id) throw new Error('Stream session response missing stream_id');
    return data;
  }

  async _requestStreamStop(streamId) {
    if (!streamId) return;
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
  }

  async start(accessToken, sourceConfig) {
    if (this.state.isStarted) return;
    this.accessToken = accessToken || null;

    const sourceType = sourceConfig?.type || 'microphone';
    const statusLabels = {
      microphone: 'Starting...',
      screen: 'Starting...',
      file: 'Starting...',
    };

    try {
      this._setState({
        errorMessage: '',
        transcriptEntries: [],
        partialTranscript: '',
        textTimestamps: [],
        status: statusLabels[sourceType] || 'Getting user media...',
        elapsedMs: 0,
      });

      const audioTrack = await this._getAudioTrack(sourceConfig);
      const videoTrack = await this._createBlackVideoTrack();
      this.localStream = new MediaStream([audioTrack, videoTrack]);

      this._setState({ status: 'Connecting...' });
      let sessionData;
      try { sessionData = await this._createStreamSession(); }
      catch (sessionError) {
        this._setState({ status: 'Connection failed.', errorMessage: 'Could not create a streaming session.' });
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
              // Add space if neither side already has one
              const needsSpace = !buffer.endsWith(' ') && !text.startsWith(' ');
              return buffer + (needsSpace ? ' ' : '') + text;
            };

            // Helper: extract sentences from buffer and timestamp them
            const _processBuffer = (buffer) => {
              const sentences = [];
              let remaining = buffer;
              // Find sentence endings (. ! ?)
              const regex = /[.!?]+/g;
              let match;
              let lastEnd = 0;
              while ((match = regex.exec(buffer)) !== null) {
                const sentence = buffer.slice(lastEnd, match.index + match[0].length).trim();
                if (sentence) {
                  const ts = formatDuration(this.state.elapsedMs);
                  sentences.push(`[${ts}] ${sentence}`);
                }
                lastEnd = match.index + match[0].length;
              }
              remaining = buffer.slice(lastEnd).trimStart();
              return { sentences, remaining };
            };

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
              this._textBuffer = _appendText(this._textBuffer, delta);
              const { sentences, remaining } = _processBuffer(this._textBuffer);
              if (sentences.length > 0) {
                this._textBuffer = remaining;
                this._setState({
                  transcriptEntries: [...this.state.transcriptEntries, ...sentences],
                  partialTranscript: remaining,
                  status: 'Connected.',
                });
              } else {
                this._setState({ partialTranscript: this._textBuffer, status: 'Connected.' });
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
              // If the buffer already contains this transcript (built from prior
              // deltas), avoid appending it again and causing duplication.
              if (!this._textBuffer.endsWith(transcript)) {
                this._textBuffer = _appendText(this._textBuffer, transcript);
              }
              const { sentences, remaining } = _processBuffer(this._textBuffer);
              const allEntries = [...this.state.transcriptEntries, ...sentences];
              if (remaining) {
                const ts = formatDuration(this.state.elapsedMs);
                allEntries.push(`[${ts}] ${remaining}`);
              }
              this._textBuffer = '';
              this._setState({
                transcriptEntries: allEntries,
                partialTranscript: '',
                status: 'Connected.',
              });
            } else if (msgType === 'transcription') {
              const text = typeof message.text === 'string' ? message.text : '';
              const isFinal = message.is_final;
              if (!text) return;
              // The 'transcription' message type sends the FULL cumulative text
              // (not a delta), so we replace the buffer rather than append.
              if (isFinal) {
                this._textBuffer = text;
                const { sentences, remaining } = _processBuffer(this._textBuffer);
                const allEntries = [...this.state.transcriptEntries, ...sentences];
                if (remaining) {
                  const ts = formatDuration(this.state.elapsedMs);
                  allEntries.push(`[${ts}] ${remaining}`);
                }
                this._textBuffer = '';
                this._setState({
                  transcriptEntries: allEntries,
                  partialTranscript: '',
                  status: 'Connected.',
                });
              } else {
                this._textBuffer = text;
                const { sentences, remaining } = _processBuffer(this._textBuffer);
                if (sentences.length > 0) {
                  this._textBuffer = remaining;
                  this._setState({
                    transcriptEntries: [...this.state.transcriptEntries, ...sentences],
                    partialTranscript: remaining,
                    status: 'Connected.',
                  });
                } else {
                  this._setState({ partialTranscript: this._textBuffer, status: 'Connected.' });
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
      try {
        await this._requestStreamStop(activeStreamId);
      } catch (stopErr) {
        this._setState({ errorMessage: this.state.errorMessage || `Failed to stop stream cleanly: ${stopErr.message}` });
      }
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

    if (this._beforeunloadHandler) {
      window.removeEventListener('beforeunload', this._beforeunloadHandler);
      this._beforeunloadHandler = null;
    }

    this._textBuffer = '';
    this._setState({
      isStarted: false,
      partialTranscript: '',
      status: !preserveStatus && wasStarted ? 'Transcription stopped.' : this.state.status,
      elapsedMs: 0,
    });
  }
}

const streamManager = new StreamManager();
export default streamManager;
export { formatDuration };
