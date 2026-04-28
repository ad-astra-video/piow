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
from sessions import (
    setup_routes as setup_sessions_routes,
    start_stream_usage_monitor,
    stop_stream_usage_monitor,
)
from billing import setup_routes as setup_billing_routes
from user_routes import setup_routes as setup_user_routes

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


def _substitute_provider_env_vars(config):
    """Resolve ${ENV_VAR} placeholders in provider configs."""
    resolved = {}
    for key, value in config.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            resolved[key] = os.environ.get(env_var, value)
        else:
            resolved[key] = value
    return resolved


def _provider_type_from_definition(definition):
    """Infer provider type for database storage from definition metadata."""
    tags = definition.get("tags") or []
    if tags:
        return tags[0]

    name = definition.get("name", "")
    if "-" in name:
        return name.split("-", 1)[0]
    return name or "custom"


async def sync_compute_providers_to_db(_app):
    """Sync in-code provider definitions into the compute_providers table on startup."""
    try:
        provider_rows = []
        for definition in compute_provider_manager.get_provider_definitions():
            provider_rows.append({
                "name": definition["name"],
                "type": _provider_type_from_definition(definition),
                "enabled": bool(definition.get("config", {}).get("enabled", True)),
                "config": _substitute_provider_env_vars(definition.get("config", {})),
            })

        if not provider_rows:
            logger.info("No compute provider definitions found to sync")
            return

        supabase.table("compute_providers").upsert(
            provider_rows,
            on_conflict="name",
        ).execute()
        logger.info(f"Synced {len(provider_rows)} compute providers to database")
    except Exception as e:
        # Do not block app startup if DB sync fails.
        logger.warning(f"Failed to sync compute providers to database at startup: {e}")

# Host IP for SDP munging (Docker container IP replacement)
HOST_IP = os.environ.get("HOST_IP", "127.0.0.1")

# TURN Server Configuration (optional, for complex NAT scenarios)
TURN_SERVER = os.environ.get("TURN_SERVER", "")
TURN_USERNAME = os.environ.get("TURN_USERNAME", "")
TURN_PASSWORD=os.environ.get("TURN_PASSWORD", "")

# ICE Server Configuration
DEFAULT_ICE_SERVERS = ["stun:stun.l.google.com:19302"]
ICE_SERVERS_CONFIG = os.environ.get("ICE_SERVERS", ",".join(DEFAULT_ICE_SERVERS))

def parse_ice_servers(config_string):
    """Parse ICE servers from comma-separated string into RTCIceServer list."""
    servers = []
    for url in config_string.split(","):
        url = url.strip()
        if url:
            if url.startswith("turn:") and TURN_SERVER and url in TURN_SERVER:
                pass
    return servers

try:
    ICE_SERVERS = parse_ice_servers(ICE_SERVERS_CONFIG)
    ICE_CONFIGURATION = None
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

logger.info(f"Using HOST_IP for SDP munging: {HOST_IP}")

# Global state
connected_frontends = set()
ws_streams: dict = {}


async def _handle_start_stream(ws: web.WebSocketResponse, stream_id: str):
    """Handle a 'start_stream' message from the frontend."""
    from sessions import session_store

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

    try:
        relay = await get_or_create_relay(stream_id, data_url)
    except Exception as exc:
        logger.error(f"Failed to create SSE relay for stream {stream_id}: {exc}")
        await ws.send_json({
            "type": "error",
            "text": f"Failed to connect to transcription stream: {exc}",
        })
        return

    relay.add_client(ws)
    ws_streams.setdefault(ws, set()).add(stream_id)

    await ws.send_json({
        "type": "status",
        "text": f"Stream '{stream_id}' started. Listening for transcription events...",
        "stream_id": stream_id,
    })
    logger.info(f"WebSocket subscribed to SSE relay for stream {stream_id}")


async def _handle_stop_stream(ws: web.WebSocketResponse, stream_id: str):
    """Handle a 'stop_stream' message from the frontend."""
    relay = get_relay(stream_id)
    if relay:
        relay.remove_client(ws)
        if ws in ws_streams:
            ws_streams[ws].discard(stream_id)
            if not ws_streams[ws]:
                del ws_streams[ws]
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
    """Remove a WebSocket from all SSE relays it was subscribed to."""
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
    """Handle WebSocket connections from frontend."""
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
        await _cleanup_ws_streams(ws)
        connected_frontends.discard(ws)
        logger.info("Frontend WebSocket disconnected")

    return ws

@no_auth
async def health_check(request):
    """Health check endpoint."""
    provider_health = {}
    for name in compute_provider_manager.list_providers():
        provider = compute_provider_manager.get_provider(name)
        if provider:
            provider_health[name] = {"status": "healthy" if provider.enabled else "disabled"}
        else:
            provider_health[name] = {"status": "unknown"}

    supabase_status = "unknown"
    try:
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
    app.on_startup.append(sync_compute_providers_to_db)
    app.on_startup.append(start_stream_usage_monitor)
    app.on_shutdown.append(stop_stream_usage_monitor)
    app.on_shutdown.append(shutdown_app)

    # Add routes
    app.router.add_get('/', index)
    app.router.add_get('/{path:.*}', static_file)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/health', health_check)

    # Import and setup all route modules
    setup_auth_routes(app)
    setup_agents_routes(app)
    setup_transcribe_routes(app)
    setup_translate_routes(app)
    setup_languages_routes(app)
    setup_sessions_routes(app)
    setup_billing_routes(app)
    setup_user_routes(app)

    return app

async def shutdown_app(app):
    """Application shutdown handler."""
    logger.info("Application shutdown started")
    await stop_all_relays()
    frontend_close_tasks = []
    for ws in list(connected_frontends):
        frontend_close_tasks.append(ws.close())
        connected_frontends.discard(ws)

    if frontend_close_tasks:
        await asyncio.gather(*frontend_close_tasks, return_exceptions=True)

    ws_streams.clear()
    logger.info("Application shutdown finished")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    web.run_app(init_app(), host='0.0.0.0', port=port)
