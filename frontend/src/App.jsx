import React, { useEffect, useRef, useState } from 'react';
import './App.css';

const WHIP_ENDPOINT = `${window.location.origin}/whip`;
const WS_ENDPOINT = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

// WHIP Client class
class WHIPClient {
    constructor(whipEndpoint) {
        this.whipEndpoint = whipEndpoint;
        this.pc = null;
    }

    async start(tracks) {
        // Validate input tracks
        if (!tracks || !Array.isArray(tracks) || tracks.length === 0) {
            throw new Error('Invalid tracks parameter: expected non-empty array of MediaStreamTrack objects');
        }
        
        // Validate each track is a MediaStreamTrack
        for (let i = 0; i < tracks.length; i++) {
            const track = tracks[i];
            if (!(track instanceof MediaStreamTrack)) {
                throw new Error(`Invalid track at index ${i}: expected MediaStreamTrack, got ${typeof track}`);
            }
            // Optional: check if track is ended
            if (track.readyState === 'ended') {
                throw new Error(`Track at index ${i} is already ended`);
            }
        }

        this.pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });

        // Add tracks properly - create a MediaStream from the tracks
        const mediaStream = new MediaStream(tracks);
        mediaStream.getTracks().forEach(track => this.pc.addTrack(track, mediaStream));

        // Create offer
        const offer = await this.pc.createOffer();
        await this.pc.setLocalDescription(offer);

        // Send to WHIP endpoint via HTTP POST
        const response = await fetch(this.whipEndpoint, {
            method: 'POST',
            body: this.pc.localDescription.sdp,
            headers: {
                'Content-Type': 'application/sdp'
            }
        });

        if (!response.ok) {
            throw new Error(`WHIP failed: ${response.status}`);
        }

        const answerSdp = await response.text();
        const answer = new RTCSessionDescription({ type: 'answer', sdp: answerSdp });
        await this.pc.setRemoteDescription(answer);
        
        return this.pc;
    }

    stop() {
        if (this.pc) {
            this.pc.getSenders().forEach(sender => sender.track.stop());
            this.pc.close();
            this.pc = null;
        }
    }
}

function App() {
  const [status, setStatus] = useState('Ready for live transcription.');
  const [transcriptEntries, setTranscriptEntries] = useState([]);
  const [partialTranscript, setPartialTranscript] = useState('');
  const [isStarted, setIsStarted] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const whipClientRef = useRef(null);
  const wsRef = useRef(null);
  const localStreamRef = useRef(null);
  const isStartedRef = useRef(false);

  useEffect(() => {
    isStartedRef.current = isStarted;
  }, [isStarted]);

  useEffect(() => {
    return () => {
      if (isStartedRef.current) {
        stopTranscription({ preserveStatus: false });
      }
    };
  }, []);

  const createBlackVideoTrack = async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'black';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const stream = canvas.captureStream();
    return stream.getVideoTracks()[0];
  };

  const stopTranscription = ({ preserveStatus = false } = {}) => {
    const wasStarted = isStartedRef.current;

    isStartedRef.current = false;
    setIsStarted(false);

    if (whipClientRef.current) {
      whipClientRef.current.stop();
      whipClientRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
    }

    setPartialTranscript('');
    if (!preserveStatus && wasStarted) {
      setStatus('Transcription stopped.');
    }
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
      // Note: We don't have a local video preview in the React version for simplicity
      // But we can add it if needed.

      setStatus('Connecting to WHIP endpoint...');

      // Create WHIP client instance and start it
      const whipClient = new WHIPClient(WHIP_ENDPOINT);
      whipClientRef.current = whipClient;
      
      try {
        const pc = await whipClient.start([audioTrack, videoTrack]);
        setStatus('WHIP connected. Opening WebSocket...');

        // Handle incoming tracks from WHIP (if needed)
        pc.ontrack = (event) => {
          console.log('Received track from WHIP:', event.track.kind);
          // We could use this for remote video preview if we wanted to
        };

        // Open WebSocket for receiving transcriptions
        setStatus('Connected. Opening WebSocket...');
        const ws = new WebSocket(WS_ENDPOINT);
        wsRef.current = ws;

        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'config' }));
          setStatus('Listening for speech...');
          setIsStarted(true);
          isStartedRef.current = true;
        };

        ws.onmessage = (event) => {
          if (event.data instanceof Blob) {
            return;
          }

          const message = JSON.parse(event.data);
          if (message.type === 'transcription') {
            const chunk = typeof message.text === 'string' ? message.text : '';
            if (!chunk) {
              return;
            }

            if (message.is_final) {
              setTranscriptEntries((previous) => [...previous, chunk.trim()]);
              setPartialTranscript('');
            } else {
              setPartialTranscript((previous) => `${previous}${chunk}`);
            }
            setStatus('Receiving live transcript...');
          } else if (message.type === 'status') {
            setStatus(message.text);
          }
        };

        ws.onclose = () => {
          if (isStartedRef.current) {
            stopTranscription({ preserveStatus: true });
            setStatus('WebSocket disconnected.');
          }
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          setErrorMessage('Realtime connection failed. Check the backend and try again.');
          setStatus('WebSocket error');
        };
      } catch (whipError) {
        console.error('WHIP connection failed:', whipError);
        setStatus(`WHIP connection failed: ${whipError.message}`);
        setErrorMessage('Could not establish the WHIP session.');
        // Clean up WHIP client if it failed
        if (whipClientRef.current) {
          whipClientRef.current.stop();
          whipClientRef.current = null;
        }
        throw whipError;
      }
    } catch (err) {
      console.error('Error starting transcription:', err);
      setStatus(`Error: ${err.message}`);
      setErrorMessage(err.message);
      stopTranscription({ preserveStatus: true });
    }
  };

  const transcriptCount = transcriptEntries.length + (partialTranscript ? 1 : 0);
  const isLive = isStarted && !errorMessage;

  return (
    <div className="app-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <main className={`page-grid ${isStarted ? 'session-active' : ''}`}>
        <section className={`hero-panel panel-glass ${isStarted ? 'hero-panel-collapsed' : ''}`}>
          <div className="hero-content">
            <div className="hero-copy-block">
              <p className="eyebrow">Realtime speech capture</p>
              <h1>Live Transcript Studio</h1>
              <p className="hero-copy">
                Stream your microphone into Voxtral and watch the transcript build in real time.
                This interface is tuned for a single job: fast, readable speech-to-text.
              </p>

              <p className="supports-line">
                Supports: Arabic, Chinese, Dutch, English, French, German, Hindi, Italian, Japanese, Korean, Portuguese, Russian, Spanish.
              </p>
            </div>

            <div className="hero-controls">
              <div className="stat-strip compact-strip">
                <article>
                  <span>Mode</span>
                  <strong>Transcription only</strong>
                </article>
                <article>
                  <span>Entries</span>
                  <strong>{transcriptCount}</strong>
                </article>
                <article>
                  <span>Engine</span>
                  <strong>Voxtral Realtime</strong>
                </article>
              </div>

              <div className="hero-actions">
                <button className="primary-button" onClick={startTranscription} disabled={isStarted}>
                  {isStarted ? 'Listening…' : 'Start Session'}
                </button>
                <button className="secondary-button" onClick={() => stopTranscription()} disabled={!isStarted}>
                  Stop Session
                </button>
              </div>
            </div>
          </div>

          <div className="status-card">
            <span className={`status-dot ${isLive ? 'live' : ''}`} />
            <div>
              <p className="status-label">Session status</p>
              <p className="status-text">{status}</p>
            </div>
          </div>

          {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
        </section>

        <section className={`transcript-panel panel-glass ${isStarted ? 'transcript-panel-expanded' : ''}`}>
          <div className="panel-heading transcript-heading">
            <div>
              <p className="eyebrow">Output</p>
              <h2>Transcript feed</h2>
            </div>
            <p className="transcript-note">Partial text stays live until the model finalizes the segment.</p>
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
      </main>
    </div>
  );
}

export default App;