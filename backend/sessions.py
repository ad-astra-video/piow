#!/usr/bin/env python3
"""
Session Management Endpoints
Handles user sessions, transcription sessions, and streaming sessions.
"""

from aiohttp import web
import logging
import uuid
from typing import Dict, Any, Optional
import time

logger = logging.getLogger(__name__)

# In-memory session store (replace with Supabase/database in production)
class SessionStore:
    def __init__(self):
        self.sessions = {}  # session_id -> session_data
        self.transcriptions = {}  # transcription_id -> transcription_data
        self.stream_sessions = {}  # stream_id -> stream_session_data
    
    def create_session(self, user_id: str = None) -> str:
        """Create a new user session."""
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "id": session_id,
            "user_id": user_id,
            "created_at": time.time(),
            "last_activity": time.time(),
            "transcriptions": [],
            "stream_sessions": [],
            "settings": {
                "default_language": "en",
                "translate_to": []
            }
        }
        logger.info(f"Created session {session_id}")
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        return self.sessions.get(session_id)
    
    def update_session_activity(self, session_id: str):
        """Update session last activity time."""
        if session_id in self.sessions:
            self.sessions[session_id]["last_activity"] = time.time()
    
    def add_transcription_to_session(self, session_id: str, transcription_id: str):
        """Add transcription to session."""
        if session_id in self.sessions:
            self.sessions[session_id]["transcriptions"].append(transcription_id)
            self.sessions[session_id]["last_activity"] = time.time()
    
    def add_stream_to_session(self, session_id: str, stream_id: str):
        """Add stream session to session."""
        if session_id in self.sessions:
            self.sessions[session_id]["stream_sessions"].append(stream_id)
            self.sessions[session_id]["last_activity"] = time.time()

# Global session store
session_store = SessionStore()

async def create_session(request):
    """Create a new user session."""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        session_id = session_store.create_session(user_id)
        
        return web.json_response({
            "session_id": session_id,
            "message": "Session created successfully"
        })
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def get_session(request):
    """Get session information."""
    try:
        session_id = request.match_info.get('session_id')
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)
        
        session = session_store.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)
        
        return web.json_response(session)
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def get_user_transcriptions(request):
    """Get transcriptions for a user/session."""
    try:
        session_id = request.match_info.get('session_id')
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)
        
        session = session_store.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)
        
        # Get transcription details (would query database in real implementation)
        transcriptions = []
        for tid in session["transcriptions"]:
            if tid in session_store.transcriptions:
                transcriptions.append(session_store.transcriptions[tid])
        
        return web.json_response({
            "session_id": session_id,
            "transcriptions": transcriptions,
            "count": len(transcriptions)
        })
    except Exception as e:
        logger.error(f"Error getting user transcriptions: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def create_transcription_session(request):
    """Create a transcription session record."""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        filename = data.get('filename', 'unknown')
        duration = data.get('duration', 0)
        language = data.get('language', 'en')
        
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)
        
        transcription_id = str(uuid.uuid4())
        transcription_data = {
            "id": transcription_id,
            "session_id": session_id,
            "filename": filename,
            "duration": duration,
            "language": language,
            "status": "processing",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None
        }
        
        session_store.transcriptions[transcription_id] = transcription_data
        
        # Link to user session
        session_store.add_transcription_to_session(session_id, transcription_id)
        
        return web.json_response({
            "transcription_id": transcription_id,
            "message": "Transcription session created",
            "status": "processing"
        })
    except Exception as e:
        logger.error(f"Error creating transcription session: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def update_transcription_result(request):
    """Update transcription with results."""
    try:
        data = await request.json()
        transcription_id = data.get('transcription_id')
        result = data.get('result')
        status = data.get('status', 'completed')
        
        if not transcription_id:
            return web.json_response({"error": "Transcription ID required"}, status=400)
        
        if transcription_id not in session_store.transcriptions:
            return web.json_response({"error": "Transcription not found"}, status=404)
        
        transcription = session_store.transcriptions[transcription_id]
        transcription["result"] = result
        transcription["status"] = status
        transcription["updated_at"] = time.time()
        
        if status == "completed":
            logger.info(f"Transcription {transcription_id} completed")
        
        return web.json_response({
            "transcription_id": transcription_id,
            "status": status,
            "message": f"Transcription updated to {status}"
        })
    except Exception as e:
        logger.error(f"Error updating transcription result: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def create_stream_session(request):
    """Create a streaming session."""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        language = data.get('language', 'en')
        
        if not session_id:
            return web.json_response({"error": "Session ID required"}, status=400)
        
        stream_id = str(uuid.uuid4())
        stream_data = {
            "id": stream_id,
            "session_id": session_id,
            "language": language,
            "status": "active",
            "created_at": time.time(),
            "updated_at": time.time(),
            "total_audio_bytes": 0,
            "transcription_segments": []
        }
        
        session_store.stream_sessions[stream_id] = stream_data
        
        # Link to user session
        session_store.add_stream_to_session(session_id, stream_id)
        
        return web.json_response({
            "stream_id": stream_id,
            "message": "Stream session created",
            "status": "active"
        })
    except Exception as e:
        logger.error(f"Error creating stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def update_stream_session(request):
    """Update streaming session with new data."""
    try:
        data = await request.json()
        stream_id = data.get('stream_id')
        audio_bytes = data.get('audio_bytes', 0)
        transcription_segment = data.get('transcription_segment')
        
        if not stream_id:
            return web.json_response({"error": "Stream ID required"}, status=400)
        
        if stream_id not in session_store.stream_sessions:
            return web.json_response({"error": "Stream session not found"}, status=404)
        
        stream = session_store.stream_sessions[stream_id]
        stream["total_audio_bytes"] += audio_bytes
        stream["updated_at"] = time.time()
        
        if transcription_segment:
            stream["transcription_segments"].append(transcription_segment)
        
        return web.json_response({
            "stream_id": stream_id,
            "status": stream["status"],
            "total_audio_bytes": stream["total_audio_bytes"],
            "message": "Stream session updated"
        })
    except Exception as e:
        logger.error(f"Error updating stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def close_stream_session(request):
    """Close a streaming session."""
    try:
        data = await request.json()
        stream_id = data.get('stream_id')
        final_text = data.get('final_text', '')
        
        if not stream_id:
            return web.json_response({"error": "Stream ID required"}, status=400)
        
        if stream_id not in session_store.stream_sessions:
            return web.json_response({"error": "Stream session not found"}, status=404)
        
        stream = session_store.stream_sessions[stream_id]
        stream["status"] = "completed"
        stream["final_text"] = final_text
        stream["updated_at"] = time.time()
        
        logger.info(f"Stream session {stream_id} closed")
        
        return web.json_response({
            "stream_id": stream_id,
            "status": "completed",
            "final_text": final_text,
            "total_audio_bytes": stream["total_audio_bytes"],
            "message": "Stream session closed"
        })
    except Exception as e:
        logger.error(f"Error closing stream session: {e}")
        return web.json_response({"error": str(e)}, status=500)

def setup_routes(app: web.Application):
    """Setup session-related routes."""
    app.router.add_post('/api/v1/sessions', create_session)
    app.router.add_get('/api/v1/sessions/{session_id}', get_session)
    app.router.add_get('/api/v1/sessions/{session_id}/transcriptions', get_user_transcriptions)
    app.router.add_post('/api/v1/transcriptions/session', create_transcription_session)
    app.router.add_post('/api/v1/transcriptions/{transcription_id}/result', update_transcription_result)
    app.router.add_post('/api/v1/stream/session', create_stream_session)
    app.router.add_post('/api/v1/stream/{stream_id}/update', update_stream_session)
    app.router.add_post('/api/v1/stream/{stream_id}/close', close_stream_session)
    
    logger.info("Session routes configured")
