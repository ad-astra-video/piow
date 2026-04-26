import React, { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Radio, AlertCircle } from 'lucide-react';

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

export default function TranscribeStream({ accessToken }) {
  const [status, setStatus] = useState('Ready for live transcription.');
  const [transcriptEntries, setTranscriptEntries] = useState([]);
  const [partialTranscript, setPartialTranscript] = useState('');
  const [isStarted, setIsStarted] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const whipClientRef = useRef(null);
  const wsRef = useRef(null);
  const localStreamRef = useRef(null);
  const isStartedRef = useRef(false);
  const streamIdRef = useRef(null);

  useEffect(() => { isStartedRef.current = isStarted; }, [isStarted]);

  useEffect(() => {
    return () => {
      if (isStartedRef.current) stopTranscription({ preserveStatus: false });
    };
  }, []);

  const createBlackVideoTrack = async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 320; canvas.height = 240;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'black';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return canvas.captureStream().getVideoTracks()[0];
  };

  const stopTranscription = ({ preserveStatus = false } = {}) => {
    const wasStarted = isStartedRef.current;
    isStartedRef.current = false;
    setIsStarted(false);

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && streamIdRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: 'stop_stream', stream_id: streamIdRef.current }));
      } catch (e) {}
    }
    streamIdRef.current = null;

    if (whipClientRef.current) {
      whipClientRef.current.stop();
      whipClientRef.current = null;
    }
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
    }
    setPartialTranscript('');
    if (!preserveStatus && wasStarted) setStatus('Transcription stopped.');
  };

  const createStreamSession = async () => {
    const headers = { 'Content-Type': 'application/json' };
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
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
  };

  const startTranscription = async () => {
    if (isStarted) return;
    try {
      setErrorMessage('');
      setTranscriptEntries([]);
      setPartialTranscript('');
      setStatus('Getting user media...');

      const audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audioTrack = audioStream.getAudioTracks()[0];
      const videoTrack = await createBlackVideoTrack();
      localStreamRef.current = new MediaStream([audioTrack, videoTrack]);

      setStatus('Creating stream session...');
      let sessionData;
      try { sessionData = await createStreamSession(); }
      catch (sessionError) {
        setStatus(`Session creation failed: ${sessionError.message}`);
        setErrorMessage('Could not create a streaming session.');
        throw sessionError;
      }

      const { stream_id } = sessionData;
      streamIdRef.current = stream_id;

      setStatus('Connecting to WHIP endpoint...');
      const whipClient = new WHIPClient(stream_id, accessToken || undefined);
      whipClientRef.current = whipClient;

      try {
        const pc = await whipClient.start([audioTrack, videoTrack]);
        setStatus('WHIP connected. Opening WebSocket...');
        pc.ontrack = (event) => { console.log('Received track from WHIP:', event.track.kind); };

        setStatus('Connected. Opening WebSocket...');
        const ws = new WebSocket(WS_ENDPOINT);
        wsRef.current = ws;

        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'start_stream', stream_id }));
          setStatus('Listening for speech...');
          setIsStarted(true);
          isStartedRef.current = true;
        };

        ws.onmessage = (event) => {
          if (event.data instanceof Blob) return;
          try {
            const message = JSON.parse(event.data);
            if (message.type === 'transcription') {
              const chunk = typeof message.text === 'string' ? message.text : '';
              if (!chunk) return;
              if (message.is_final) {
                setTranscriptEntries((prev) => [...prev, chunk.trim()]);
                setPartialTranscript('');
              } else {
                setPartialTranscript((prev) => `${prev}${chunk}`);
              }
              setStatus('Receiving live transcript...');
            } else if (message.type === 'status') {
              setStatus(message.text);
            } else if (message.type === 'error') {
              setErrorMessage(message.text);
              setStatus(`Error: ${message.text}`);
            }
          } catch (parseErr) {}
        };

        ws.onclose = () => {
          if (isStartedRef.current) {
            stopTranscription({ preserveStatus: true });
            setStatus('WebSocket disconnected.');
          }
        };

        ws.onerror = () => {
          setErrorMessage('Realtime connection failed.');
          setStatus('WebSocket error');
        };
      } catch (whipError) {
        setStatus(`WHIP connection failed: ${whipError.message}`);
        setErrorMessage('Could not establish the WHIP session.');
        if (whipClientRef.current) { whipClientRef.current.stop(); whipClientRef.current = null; }
        streamIdRef.current = null;
        throw whipError;
      }
    } catch (err) {
      setStatus(`Error: ${err.message}`);
      setErrorMessage(err.message);
      stopTranscription({ preserveStatus: true });
    }
  };

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
            <button className="primary-button" onClick={startTranscription} disabled={isStarted}>
              {isStarted ? <><Radio size={16} /> Listening…</> : <><Mic size={16} /> Start Session</>}
            </button>
            <button className="secondary-button" onClick={() => stopTranscription()} disabled={!isStarted}>
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
          </div>
          <div className="transcript-scroll">
            {transcriptEntries.length === 0 && !partialTranscript ? (
              <div className="empty-state">
                <p>No transcript yet.</p>
                <span>Start a session, allow microphone access, and speak naturally.</span>
              </div>
            ) : null}
            {transcriptEntries.map((entry, index) => (
              <article className="transcript-entry" key={`${entry}-${index}`}>
                <span className="entry-badge">Final</span>
                <p>{entry}</p>
              </article>
            ))}
            {partialTranscript ? (
              <article className="transcript-entry partial-entry">
                <span className="entry-badge">Live</span>
                <p>{partialTranscript}</p>
              </article>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
