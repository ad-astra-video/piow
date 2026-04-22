#!/usr/bin/env python3
"""
WHIP (WebRTC-HTTP Ingest Protocol) Handler for WebRTC ingestion
Handles WHIP requests and proxies them to GPU workers for processing
"""

import logging
import uuid
import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

# Configuration - these should come from environment variables
GPU_RUNNER_URL = "http://localhost:9935"  # Default, should be overridden by env


class WHIPHandler:
    """Handle WHIP POST requests by proxying to GPU worker."""
    
    def __init__(self):
        self.pcs = set()  # PeerConnections for WHIP

    async def whip(self, request):
        """Handle WHIP POST request by proxying to GPU worker."""
        logger.info("Received WHIP request")
        
        # Get the request body (SDP offer)
        body = await request.text()
        
        # Extract session ID from query parameters or headers if needed
        # For now, we will generate a session ID or get it from request
        session_id = request.query.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # Prepare request to GPU worker
        whip_url = f"{GPU_RUNNER_URL}/process/stream/{session_id}/whip"
        logger.info(f"Proxying WHIP request to GPU worker: {whip_url}")
        
        # Forward the WHIP request to GPU worker
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    whip_url,
                    data=body,
                    headers={
                        "Content-Type": "application/sdp"
                    },
                ) as worker_response:
                    if worker_response.status != 200:
                        error_text = await worker_response.text()
                        logger.error(f"GPU worker WHIP request failed: {worker_response.status} - {error_text}")
                        return web.json_response(
                            {"error": "Failed to establish WHIP connection with GPU worker"},
                            status=worker_response.status,
                        )
                    
                    # Get the answer from GPU worker
                    answer_sdp = await worker_response.text()
                    logger.info(f"Received WHIP answer from GPU worker (length: {len(answer_sdp)})")
                    
                    # Return the answer to the client
                    return web.Response(
                        text=answer_sdp,
                        content_type="application/sdp"
                    )
            except Exception as e:
                logger.error(f"HTTP error forwarding WHIP to GPU worker: {e}")
                return web.json_response(
                    {"error": "Failed to connect to GPU worker"},
                    status=500,
                )