# Worker V2 - Livepeer Live Runner

Worker V2 re-architecture using the Livepeer **live-runner** system from
https://github.com/livepeer/live-runner-example-apps and the
https://github.com/livepeer/go-livepeer/pull/3938 Orchestrator integration.

## Architecture Overview

### What stays the same

- **Worker core logic** is unchanged: Voxtral transcription, Gemma translation,
  analysis pipeline, audio processing, frame handlers, data channel events.
- **PyTrickle `StreamProcessor`** remains the worker runtime — trickle protocol
  is the transport between Orchestrator and worker.
- **`create_stream_session()`** in the backend still obtains compute from the
  provider/runner — the backend calls the orchestrator to provision a session.
- **Worker registers with a Livepeer Orchestrator** to become discoverable on
  the network.

### What changes: the communication layer

#### Current (V1)

```
Frontend Browser                          Backend                     Worker (V1)
     |                                         |                           |
     |  POST /api/v1/stream (create session)   |                           |
     |---------------------------------------->|                           |
     |                                         |  create_stream_session()  |
     |                                         |  (calls provider API)     |
     |                                         |<--- whip_url returned ----|
     |                                         |                           |
     |  WHIP POST (WebRTC SDP offer)           |                           |
     |----------> POST /stream/{id}/whip ------>|                           |
     |                                         |  POST /process/stream/whip |
     |                                         |-------------------------->|
     |                                         |                           |
     |  WebRTC audio/video stream              |                           |
     |=============> backend proxy ===========>|============== WebRTC =====>|
     |                                         |                           |
     |  Data WS  /stream/{id}/data             |                           |
     |<======================================>|  subscribes to data chan   |
     |  (relay)                                |<==========================|
```

Key characteristics:
- Frontend connects via **WHIP to the backend**, backend proxies WebRTC to worker.
- Backend acts as a **WebRTC proxy** — it holds the peer connection and relays.
- Worker runs `pytrickle StreamProcessor` addressed directly by the backend.
- Worker self-registers with orchestrator via custom HTTP POST.

#### Worker V2 (Live Runner)

```
Frontend Browser                     Backend (Python Gateway)    Orchestrator          Worker V2
     |                                     |                          |                    |
     |  POST /api/v1/stream (create sess)  |                          |                    |
     |------------------------------------>|                          |                    |
     |                                     |  create_stream_session()|                    |
     |                                     |  -> livepeer-gateway SDK|                    |
     |                                     |  -> orchestrator API    |                    |
     |                                     |<-- session + orch URL --|                    |
     |                                     |                          |                    |
     |  WebRTC audio/video stream          |                          |                    |
     |====================================>|========================>|                    |
     |  (WHIP to backend)                  |  WebRTC -> trickle       |  trickle frames    |
     |                                     |  (livepeer-gateway SDK)  |===================>|
     |                                     |                          |                    |
     |                                     |  trickle data events     |  trickle data      |
     |  Data WS  /stream/{id}/data         |<=========================|<==================|
     |<====================================>|  (relay to frontend)     |                    |
```

Key characteristics:
- Frontend connects via **WebRTC WHIP to the backend** (same as V1).
- **Backend imports `livepeer-gateway` SDK** which:
  - Accepts WebRTC from frontend
  - Converts WebRTC to **trickle protocol** internally
  - Sends trickle channels to the **Orchestrator**
  - Handles discovery, session management, and on-chain payment
- **Orchestrator** receives trickle from backend, forwards trickle channels
  to the registered worker.
- **Worker** still uses **PyTrickle `StreamProcessor`** — subscribes to trickle
  input channels, processes frames, publishes output/data channels.
- Worker **registers as a live-runner** with the Orchestrator via SDK
  `register_runner()` (dynamic) or `runners.json` config (static).
- Data channel events flow: worker -> trickle -> orchestrator -> backend SDK
  -> WebSocket relay -> frontend.

## What Changes Are Needed in the Webapp

### 1. Backend: Import livepeer-gateway, Replace WebRTC Proxy with SDK Gateway

**File: `backend/sessions.py`**

a) **Install `livepeer-gateway` SDK** (from `ja/live-runner` branch):

```
# backend/requirements.txt addition:
git+https://github.com/livepeer/livepeer-python-gateway@ja/live-runner
```

b) **`create_stream_session()` now uses livepeer-gateway SDK:**

```python
from livepeer_gateway import OrchestratorSession

async def create_stream_session(request):
    # Use livepeer-gateway SDK to create session with orchestrator
    session = OrchestratorSession(
        orchestrator_url=os.environ["LIVERUNNER_ORCHESTRATOR"],
        capability="live-transcription",
    )

    # SDK handles:
    # - Discovery of available orchestrators
    # - Session creation
    # - WebRTC to trickle conversion
    # - On-chain payment setup (if enabled)
    await session.start()

    # Store session in backend for data relay
    stream_sessions[stream_id] = {
        "orchestrator_session": session,
        "orchestrator_url": session.orchestrator_url,
        "session_id": session.session_id,
    }

    return web.json_response({
        "stream_id": stream_id,
        "ice_servers": session.ice_servers,  # for frontend WebRTC
        "orchestrator_url": session.orchestrator_url,
    })
```

c) **Remove WHIP proxy endpoint** — the SDK handles WebRTC ingestion:

The custom WHIP proxy in `backend/sessions.py` that forwards to `worker:9935`
is replaced by the SDK's built-in WebRTC-to-trickle conversion. The SDK
exposes a WHIP endpoint that:
1. Accepts WebRTC SDP offers from frontend
2. Converts media to trickle protocol
3. Sends trickle channels to the orchestrator

```python
# The SDK provides this - no custom WHIP proxy needed
app.router.add_post("/stream/{stream_id}/whip",
    lambda req: orchestrator_session.handle_whip(req))
```

d) **Data channel relay** — subscribe from SDK instead of worker:

```python
# Current: connect to worker:9935 data channel
# V2: SDK provides data channel from trickle output
async def relay_data(stream_id):
    session = stream_sessions[stream_id]
    orchestrator_session = session["orchestrator_session"]

    # SDK exposes data channel events from trickle
    async for event in orchestrator_session.data_channel():
        await websocket.send_str(json.dumps(event))
```

### 2. Frontend: Minimal Changes

The frontend WebRTC connection stays the same — it still connects to the
backend via WHIP. The difference is the backend now uses the livepeer-gateway
SDK instead of proxying directly to the worker.

**Possible frontend changes:**
- ICE servers may come from the orchestrator instead of being hardcoded
- If orchestrator URL is returned in session creation, use it for WHIP

```typescript
// Session creation returns orchestrator info
const session = await fetch('/api/v1/stream', { method: 'POST' });
const { stream_id, ice_servers, orchestrator_url } = await session.json();

// WebRTC connection - same pattern as before
const peerConnection = new RTCPeerConnection({ iceServers: ice_servers });
// ... add tracks, create offer ...

// WHIP to orchestrator (via backend SDK)
const response = await fetch(`${orchestrator_url}/whip?sessionId=${stream_id}`, {
  method: 'POST',
  body: offer.sdp,
  headers: { 'Content-Type': 'application/sdp' },
});
```

### 3. Worker: Replace Custom Registration with Live Runner SDK

**File: `worker/app.py`** -> **`worker_v2/app.py`**

Current registration (custom):
```python
# Custom HTTP POST to orchestrator /capability/register
requests.post(
    "https://" + ORCH_SERVICE_ADDR + "/capability/register",
    json={"url": CAPABILITY_URL, "name": CAPABILITY_NAME, ...},
    headers={"Authorization": ORCH_SECRET},
)
```

V2 registration (live-runner SDK):
```python
from livepeer_gateway.live_runner import register_runner

# On startup, register as a dynamic live-runner
registration = await register_runner(
    orchestrator_url=os.environ["LIVERUNNER_ORCHESTRATOR"],
    secret=os.environ["LIVERUNNER_SECRET"],
    runner_url=os.environ["LIVERUNNER_URL"],
    app=os.environ["LIVERUNNER_APP_ID"],
    price_per_unit=int(os.environ.get("LIVERUNNER_PRICE", "0")),
    pixels_per_unit=int(os.environ.get("LIVERUNNER_PIXELS", "1")),
)

# registration.runner_id = unique runner identifier
# registration.orchestrator_url = orchestrator that accepted us

# On shutdown:
await registration.close()  # deregisters cleanly
```

The `StreamProcessor` and all frame handlers remain unchanged — trickle
protocol handles frame transport between orchestrator and worker.

### 4. Docker Compose: Add Orchestrator Service

**Current** (`worker/docker-compose.yml`):
- `worker` — pytrickle StreamProcessor on :9935
- `gemma-vllm` — translation model on :6100
- `vllm` — Voxtral transcription on :6000

**V2** (`worker_v2/docker-compose.yml`):
- `orchestrator` — `livepeer/go-livepeer:ja-live-runner` on :8935
- `worker_v2` — pytrickle StreamProcessor on :8000 (registers with orchestrator)
- `gemma-vllm` — translation model on :6100 (unchanged)
- `vllm` — Voxtral transcription on :6000 (unchanged)

```yaml
services:
  orchestrator:
    image: livepeer/go-livepeer:ja-live-runner
    command:
      - -orchestrator
      - -useLiveRunners
      - -serviceAddr=127.0.0.1:8935
      - -httpAddr=0.0.0.0:8935
      - -liveRunnerAddr=https://orchestrator:8935
      - -orchSecret=abcdef
      - -network=offchain
      - -monitor=false
      - -v=6
    ports:
      - "8935:8935"
      - "8935:8935/udp"  # WebRTC media
    healthcheck:
      test: ["CMD", "curl", "-f", "https://localhost:8935/healthz"]
      interval: 3s
      timeout: 3s
      retries: 40
      start_period: 5s

  worker_v2:
    build: .
    depends_on:
      orchestrator:
        condition: service_healthy
    environment:
      - LIVERUNNER_ORCHESTRATOR=https://orchestrator:8935
      - LIVERUNNER_SECRET=abcdef
      - LIVERUNNER_URL=http://worker_v2:8000
      - LIVERUNNER_APP_ID=livepeer/live-transcription
      - VLLM_WS_URL=ws://vllm:6000/v1/realtime
```

### 5. Trickle Protocol Integration

The trickle protocol is already used by pytrickle. With live-runner, the
orchestrator manages the trickle channel lifecycle:

**Channel flow:**
1. Backend SDK creates trickle channels on orchestrator via session API
2. Orchestrator calls worker `POST /api/stream/start` with trickle URLs
3. Worker PyTrickle subscribes to input trickle channels
4. Frames flow: backend -> trickle -> orchestrator -> trickle -> worker
5. Data flows: worker -> trickle -> orchestrator -> backend SDK -> frontend

PyTrickle already implements the stream lifecycle endpoints:
- `POST /api/stream/start` — start session with trickle URLs
- `POST /api/stream/stop` — tear down session
- `POST /api/stream/params` — mid-stream updates
- `GET /api/stream/status` — session status

### 6. Configuration Changes

**Current env vars (worker/.env):**
```
WORKER_PORT=9935
VLLM_WS_URL=ws://vllm:6000/v1/realtime
ORCH_SERVICE_ADDR=        # custom registration endpoint
ORCH_SECRET=              # custom registration secret
CAPABILITY_URL=           # custom capability URL
```

**V2 env vars (worker_v2/.env):**
```
LIVERUNNER_ORCHESTRATOR=https://orchestrator:8935
LIVERUNNER_SECRET=abcdef
LIVERUNNER_URL=http://worker_v2:8000
LIVERUNNER_APP_ID=livepeer/live-transcription
LIVERUNNER_PRICE=0
LIVERUNNER_PIXELS=1
WORKER_PORT=8000
VLLM_WS_URL=ws://vllm:6000/v1/realtime
```

### 7. Summary of Changes by Component

| Component | Current (V1) | V2 (Live Runner) |
|-----------|-------------|-----------------|
| **Frontend WebRTC** | WHIP to backend | WHIP to backend (via SDK) |
| **Backend WebRTC** | Custom WHIP proxy to worker | livepeer-gateway SDK converts to trickle |
| **Backend->Orchestrator** | Direct HTTP to worker | Trickle protocol via SDK |
| **Orchestrator->Worker** | N/A (no orchestrator) | Trickle protocol |
| **Worker ingest** | WebRTC from backend | Trickle from orchestrator |
| **Worker registration** | Custom HTTP POST | SDK `register_runner()` |
| **Data channel** | Worker -> backend WS | Worker -> trickle -> orch -> SDK -> WS |
| **Session mgmt** | Backend stores whip_url | SDK manages session lifecycle |
| **Payment (on-chain)** | Custom ticket signing | Built into live-runner SDK |

### 8. Migration Strategy

1. **Phase 1:** Install `livepeer-gateway` SDK in backend. Test trickle
   channel creation and WebRTC-to-trickle conversion with local orchestrator.
2. **Phase 2:** Build worker_v2 with `register_runner()` SDK integration.
   Verify trickle channel lifecycle with orchestrator.
3. **Phase 3:** Update `create_stream_session()` to use SDK session management.
   Remove custom WHIP proxy.
4. **Phase 4:** Update data channel relay to use SDK trickle output.
   Toggle production traffic. Decommission worker v1.
