#!/usr/bin/env python3
"""
Live Translation App Backend
Handles WebSocket communication with frontend and routes requests to compute providers.
WHIP ingestion is now handled by compute providers.

WebSocket Protocol:
  The frontend connects to /ws and sends JSON messages to control streaming:

  - {"type": "start_stream", "stream_id": "<id>"}
      Looks up the stream session, connects to the provider's SSE data_url,
      and relays transcription events back over this WebSocket.

  - {"type": "stop_stream", "stream_id": "<id>"}
      Disconnects the SSE relay for the given stream.

  - {"type": "config"}
      Legacy acknowledgement message (no-op in the new architecture).

  Messages relayed from the SSE data_url to the frontend:
  - {"type": "transcription", "text": "...", "is_final": true/false}
  - {"type": "status", "text": "..."}
  - {"type": "error", "text": "..."}
"""

import asyncio
import contextlib
import json
import logging
import os
from aiohttp import web, WSMsgType

# Import setup_routes functions from each module
from agents import setup_routes as setup_agents_routes
from auth import setup_routes as setup_auth_routes, auth_middleware, no_auth
from transcribe import setup_routes as setup_transcribe_routes
from translate import setup_routes as setup_translate_routes
from languages import setup_routes as setup_languages_routes
from sessions import setup_routes as setup_sessions_routes
from billing import setup_routes as setup_billing_routes

from supabase_client import supabase

# Import compute provider system
from compute_providers.provider_manager import ComputeProviderManager
from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

# Import SSE relay for bridging provider data_url to WebSocket clients
from sse_relay import get_or_create_relay, stop_relay, stop_all_relays, get_relay, get_active_relay_count

logger = logging.getLogger(__name__)

# Initialize compute provider manager
compute_provider_manager = ComputeProviderManager()

# Register providers from definitions
compute_provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)

# Host IP for SDP munging (Docker container IP replacement)
# This is critical for WebRTC to work when backend runs in Docker
# The container's internal IP (172.x.x.x) must be replaced with host IP for browser connectivity
HOST_IP = os.environ.get("HOST_IP", "127.0.0.1")

# TURN Server Configuration (optional, for complex NAT scenarios)
TURN_SERVER = os.environ.get("TURN_SERVER", "")
TURN_USERNAME = os.environ.get("TURN_USERNAME", "")
TURN_PASSWORD=os.environ.get("TURN_PASSWORD", "")

# ICE Server Configuration
# Get ICE servers from environment variables for NAT traversal
DEFAULT_ICE_SERVERS = ["stun:stun.l.google.com:19302"]
ICE_SERVERS_CONFIG = os.environ.get("ICE_SERVERS", ",".join(DEFAULT_ICE_SERVERS))

def parse_ice_servers(config_string):
    """Parse ICE servers from comma-separated string into RTCIceServer list."""
    servers = []
    for url in config_string.split(","):
        url = url.strip()
        if url:
            # Check if this is a TURN server that needs credentials
            if url.startswith("turn:") and TURN_SERVER and url in TURN_SERVER:
                # Note: In actual implementation, we'd need to import RTCIceServer here
                # For now, we'll keep the logic but note that real WebRTC handling
                # has been moved to compute providers
                pass
            # For now, we'll keep basic parsing but note that real ICE handling
            # has been moved to compute providers
    return servers

# ICE configuration is now handled by compute providers
# We keep this for backward compatibility but it's not used for WHIP anymore
try:
    ICE_SERVERS = parse_ice_servers(ICE_SERVERS_CONFIG)
    ICE_CONFIGURATION = None  # Not used since WHIP is handled by providers
except ImportError:
    ICE_SERVERS = []
    ICE_CONFIGURATION = None

# Logging — only show INFO+ for own modules; suppress noisy third-party libs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

# Log HOST_IP configuration after logger is initialized
logger.info(f"Using HOST_IP for SDP munging: {HOST_IP}")

# Global state
connected_frontends = set()  # WebSocket connections to frontend
# Map of ws -> set of stream_ids this WebSocket is subscribed to
ws_streams: dict = {}  # {WebSocketResponse: set(stream_id, ...)}

# Note: WHIP handling is now delegated to compute providers
# We keep minimal state for backward compatibility


async def _handle_start_stream(ws: web.WebSocketResponse, stream_id: str):
    """
    Handle a 'start_stream' message from the frontend.

    Looks up the stream session to get the provider's data_url (SSE endpoint),
    creates (or reuses) an SSERelay, and subscribes this WebSocket to it.
    """
    from sessions import session_store  # deferred import to avoid circular deps

    # Look up the stream session to find the data_url
    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        await ws.send_json({
            "type": "error",
            "text": f"Stream session '{stream_id}' not found. Create one via POST /api/v1/transcribe/stream first.",
        })
        return

    provider_session = stream_session.get("provider_session", {})
    data_url = provider_session.get("data_url")
    if not data_url:
        await ws.send_json({
            "type": "error",
            "text": f"No data_url available for stream session '{stream_id}'. The compute provider did not return an SSE endpoint.",
        })
        return

    # Get or create the SSE relay for this stream
    try:
        relay = await get_or_create_relay(stream_id, data_url)
    except Exception as exc:
        logger.error(f"Failed to create SSE relay for stream {stream_id}: {exc}")
        await ws.send_json({
            "type": "error",
            "text": f"Failed to connect to transcription stream: {exc}",
        })
        return

    # Subscribe this WebSocket to the relay
    relay.add_client(ws)
    ws_streams.setdefault(ws, set()).add(stream_id)

    await ws.send_json({
        "type": "status",
        "text": f"Stream '{stream_id}' started. Listening for transcription events...",
        "stream_id": stream_id,
    })
    logger.info(f"WebSocket subscribed to SSE relay for stream {stream_id}")


async def _handle_stop_stream(ws: web.WebSocketResponse, stream_id: str):
    """
    Handle a 'stop_stream' message from the frontend.

    Unsubscribes this WebSocket from the SSE relay. If no clients remain,
    the relay is stopped and cleaned up.
    """
    relay = get_relay(stream_id)
    if relay:
        relay.remove_client(ws)
        # Clean up the ws_streams tracking
        if ws in ws_streams:
            ws_streams[ws].discard(stream_id)
            if not ws_streams[ws]:
                del ws_streams[ws]
        # If no more clients, stop the relay entirely
        if not relay.has_clients:
            await stop_relay(stream_id)
            logger.info(f"SSE relay stopped for stream {stream_id} (no clients)")
        await ws.send_json({
            "type": "status",
            "text": f"Stream '{stream_id}' stopped.",
            "stream_id": stream_id,
        })
    else:
        await ws.send_json({
            "type": "status",
            "text": f"No active relay for stream '{stream_id}'.",
            "stream_id": stream_id,
        })


async def _cleanup_ws_streams(ws: web.WebSocketResponse):
    """
    Remove a WebSocket from all SSE relays it was subscribed to.
    Called when a WebSocket disconnects.
    """
    stream_ids = ws_streams.pop(ws, set())
    for stream_id in stream_ids:
        relay = get_relay(stream_id)
        if relay:
            relay.remove_client(ws)
            if not relay.has_clients:
                await stop_relay(stream_id)
                logger.info(f"SSE relay stopped for stream {stream_id} (client disconnected, no remaining clients)")


@no_auth
async def index(request):
    """Serve the frontend HTML."""
    logger.info("Serving index.html")
    try:
        with open('./frontend/dist/index.html', 'r') as f:
            content = f.read()
        return web.Response(text=content, content_type='text/html')
    except FileNotFoundError:
        logger.error("Frontend index.html not found")
        return web.Response(text="Frontend not built. Please run frontend build.", status=500)

@no_auth
async def static_file(request):
    """Serve static files (JS, CSS, etc.) from the dist directory."""
    logger.info(f"Serving static file: {request.match_info.get('path', '')}")
    path = request.match_info.get('path', '')
    # Prevent directory traversal attacks
    if '..' in path or path.startswith('/'):
        raise web.HTTPNotFound()
    try:
        with open(f'./frontend/dist/{path}', 'r') as f:
            content = f.read()
        if path.endswith('.js'):
            return web.Response(text=content, content_type='application/javascript')
        elif path.endswith('.css'):
            return web.Response(text=content, content_type='text/css')
        elif path.endswith('.html'):
            return web.Response(text=content, content_type='text/html')
        else:
            return web.Response(text=content, content_type='text/plain')
    except FileNotFoundError:
        raise web.HTTPNotFound()

@no_auth
async def websocket_handler(request):
    """
    Handle WebSocket connections from frontend.

    Protocol messages from frontend:
      {"type": "start_stream", "stream_id": "<id>"}
          Subscribe this WebSocket to transcription events from the given stream.
          The stream must have been created via POST /api/v1/transcribe/stream.

      {"type": "stop_stream", "stream_id": "<id>"}
          Unsubscribe from transcription events for the given stream.

      {"type": "config"}
          Legacy acknowledgement (no-op).

    Messages pushed to frontend (relayed from provider SSE):
      {"type": "transcription", "text": "...", "is_final": true/false}
      {"type": "status", "text": "..."}
      {"type": "error", "text": "..."}
    """
    logger.info("New WebSocket connection attempt")
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    connected_frontends.add(ws)
    logger.info("Frontend WebSocket connected")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning(f"Received invalid JSON from frontend: {msg.data[:200]}")
                    await ws.send_json({"type": "error", "text": "Invalid JSON message"})
                    continue

                msg_type = data.get("type")
                logger.info(f"Received from frontend: type={msg_type}")

                if msg_type == "start_stream":
                    stream_id = data.get("stream_id")
                    if not stream_id:
                        await ws.send_json({"type": "error", "text": "Missing 'stream_id' in start_stream message"})
                        continue
                    await _handle_start_stream(ws, stream_id)

                elif msg_type == "stop_stream":
                    stream_id = data.get("stream_id")
                    if not stream_id:
                        await ws.send_json({"type": "error", "text": "Missing 'stream_id' in stop_stream message"})
                        continue
                    await _handle_stop_stream(ws, stream_id)

                elif msg_type == "config":
                    # Legacy config acknowledgement
                    await ws.send_json({
                        "type": "status",
                        "text": "Transmission session configured via compute provider.",
                    })
                    logger.info("Acknowledged frontend config message")

                else:
                    logger.warning(f"Unknown WebSocket message type: {msg_type}")
                    await ws.send_json({"type": "error", "text": f"Unknown message type: {msg_type}"})

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        # Clean up: remove this WebSocket from all SSE relays
        await _cleanup_ws_streams(ws)
        connected_frontends.discard(ws)
        logger.info("Frontend WebSocket disconnected")

    return ws

@no_auth
async def health_check(request):
    """Health check endpoint."""
    # Check compute provider health
    provider_health = {}
    for name in compute_provider_manager.list_providers():
        provider = compute_provider_manager.get_provider(name)
        if provider:
            # In a real implementation, this would be async
            provider_health[name] = {"status": "healthy" if provider.enabled else "disabled"}
        else:
            provider_health[name] = {"status": "unknown"}

    # Check Supabase connection
    supabase_status = "unknown"
    try:
        # Simple check to see if client is initialized
        if supabase:
            supabase_status = "ok"
        else:
            supabase_status = "error: client not initialized"
    except Exception as e:
        supabase_status = f"error: {str(e)}"

    return web.json_response({
        "status": "ok",
        "services": {
            "compute_providers": provider_health,
            "supabase": supabase_status,
            "websocket_connections": len(connected_frontends),
            "sse_relays": get_active_relay_count()
        }
    })

async def init_app():
    """Initialize the aiohttp web application."""
    app = web.Application(middlewares=[auth_middleware])
    app.on_shutdown.append(shutdown_app)

    # Add routes
    app.router.add_get('/', index)
    app.router.add_get('/{path:.*}', static_file)
    app.router.add_get('/ws', websocket_handler)  # WebSocket endpoint
    app.router.add_get('/health', health_check)

    # Import and setup all route modules
    setup_auth_routes(app)
    setup_agents_routes(app)
    setup_transcribe_routes(app)
    setup_translate_routes(app)
    setup_languages_routes(app)
    setup_sessions_routes(app)
    setup_billing_routes(app)

    # WHIP is now proxied through the backend at:
    #   POST /api/v1/transcribe/stream/{stream_id}/whip
    # (registered in transcribe.py setup_routes)

    return app

async def shutdown_app(app):
    """Application shutdown handler."""
    logger.info("Application shutdown started")

    # Stop all SSE relays first (so they don't try to send to closing WebSockets)
    await stop_all_relays()

    # Close frontend WebSocket connections
    frontend_close_tasks = []
    for ws in list(connected_frontends):
        frontend_close_tasks.append(ws.close())
        connected_frontends.discard(ws)

    if frontend_close_tasks:
        await asyncio.gather(*frontend_close_tasks, return_exceptions=True)

    # Clear WebSocket stream tracking
    ws_streams.clear()

    logger.info("Application shutdown finished")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    web.run_app(init_app(), host='0.0.0.0', port=port)
