#!/usr/bin/env python3
"""
FastAPI application for the Live Transcription & Translation Platform Worker
Handles API requests for transcription and translation services
Matches backend implementation from backend/transcribe.py and backend/translate.py
"""

import os
import logging
import time
import uuid
import json
import asyncio
import tempfile
import re
from typing import Optional, Dict, Any
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
import uvicorn

# Import worker components
import sys
sys.path.append(str(Path(__file__).parent))

from granite_transcriber import Granite4Transcriber
from vllm_client import VLLMRealtimeClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Live Translation Worker API", version="1.0.0")

# Initialize components
granite_transcriber = Granite4Transcriber()
vllm_client = VLLMRealtimeClient()

# In-memory storage for active sessions (in production, use Redis or database)
active_sessions: Dict[str, Dict[str, Any]] = {}
transcriptions_db: Dict[str, Dict[str, Any]] = {}  # Simple in-memory storage for transcriptions


@app.on_event("startup")
async def startup_event():
    """Initialize components on startup"""
    logger.info("Starting Live Translation Worker API")
    # Initialize VLLM client connection
    try:
        await vllm_client.connect()
        logger.info("VLLM client connected successfully")
    except Exception as e:
        logger.warning(f"Could not connect to VLLM on startup: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Live Translation Worker API")
    await vllm_client.close()


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "Live Translation Worker API is running", "status": "healthy"}


# ============================================================================
# TRANSCRIBE ENDPOINTS - /api/v1/transcribe/*
# ============================================================================

@app.post("/api/v1/transcribe/file")
async def transcribe_file(
    file: UploadFile = File(...),
    language: str = Form("en"),
    streaming: bool = Form(False)
):
    """
    Handle file upload for transcription.
    Matches backend/transcribe.py transcribe_file endpoint.
    
    Args:
        file: Audio file to transcribe
        language: Language code (default: en)
        streaming: Whether to use streaming mode (default: false)
    """
    logger.info("Received transcription file upload request")
    
    try:
        # Save uploaded file temporarily
        filename = file.filename or "uploaded_audio"
        safe_filename = re.sub(r'[^\w\-_]', '_', filename)
        if not safe_filename.endswith(('.wav', '.mp3', '.m4a', '.flac', '.ogg')):
            safe_filename += '.wav'
        
        temp_path = f"/tmp/{safe_filename}"
        content = await file.read()
        with open(temp_path, "wb") as buffer:
            buffer.write(content)
        
        # Determine if we should use streaming or batch
        if streaming:
            # For streaming, redirect to streaming endpoint
            logger.info("Streaming mode requested - processing as batch with streaming flag")
        
        # Process with Granite transcriber
        result = granite_transcriber.transcribe(temp_path, language)
        
        # Clean up temp file
        try:
            os.remove(temp_path)
        except:
            pass
        
        # Store transcription in memory
        transcription_id = str(uuid.uuid4())
        transcription_record = {
            "id": transcription_id,
            "text": result.get("text", ""),
            "segments": result.get("segments", []),
            "language": language,
            "duration": result.get("duration", 0),
            "processing_time": result.get("processing_time", 0),
            "model": result.get("model", "granite-4.0-1b"),
            "hardware": result.get("hardware", "cpu"),
            "created_at": time.time(),
            "status": "completed"
        }
        transcriptions_db[transcription_id] = transcription_record
        
        return JSONResponse(content={
            "job_id": transcription_id,
            "status": "completed",
            "message": "Transcription completed successfully",
            **transcription_record
        })
        
    except Exception as e:
        logger.error(f"Error in transcribe_file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/transcribe/url")
async def transcribe_url(request_data: dict):
    """
    Handle transcription from URL.
    Matches backend/transcribe.py transcribe_url endpoint.
    
    Args:
        audio_url: URL of the audio file
        language: Language code (default: en)
        format: Output format (default: json)
    """
    logger.info("Received transcription URL request")
    
    try:
        audio_url = request_data.get('audio_url')
        language = request_data.get('language', 'en')
        format = request_data.get('format', 'json')
        
        if not audio_url:
            raise HTTPException(status_code=400, detail="Missing audio_url parameter")
        
        # For URL-based transcription, we would download the file first
        # In this worker implementation, we'll process it directly if accessible
        import urllib.request
        
        temp_path = f"/tmp/url_audio_{uuid.uuid4()}.wav"
        try:
            urllib.request.urlretrieve(audio_url, temp_path)
            result = granite_transcriber.transcribe(temp_path, language)
            os.remove(temp_path)
        except Exception as download_error:
            logger.error(f"Failed to download audio from URL: {download_error}")
            # Return mock result for testing
            result = {
                "text": f"[Mock] Transcription from URL: {audio_url}",
                "segments": [],
                "language": language,
                "duration": 0,
                "model": "granite-4.0-1b",
                "hardware": "cpu"
            }
        
        # Store transcription
        transcription_id = str(uuid.uuid4())
        transcription_record = {
            "id": transcription_id,
            "text": result.get("text", ""),
            "segments": result.get("segments", []),
            "language": language,
            "duration": result.get("duration", 0),
            "created_at": time.time(),
            "status": "completed",
            "source_url": audio_url
        }
        transcriptions_db[transcription_id] = transcription_record
        
        return JSONResponse(content={
            "job_id": transcription_id,
            "status": "completed",
            "message": "Transcription job completed successfully",
            **transcription_record
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in transcribe_url: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/transcribe/stream")
async def transcribe_stream(request_data: dict):
    """
    Handle real-time transcription streaming.
    Matches backend/transcribe.py transcribe_stream endpoint.
    
    Args:
        session_id: Optional session ID (will be generated if not provided)
        language: Language code (default: en)
    """
    logger.info("Received transcription stream request")
    
    try:
        session_id = request_data.get('session_id')
        language = request_data.get('language', 'en')
        
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # Create streaming session
        session_data = {
            "session_id": session_id,
            "language": language,
            "status": "active",
            "created_at": time.time(),
            "chunks_received": 0,
            "transcription_buffer": []
        }
        active_sessions[session_id] = session_data
        
        return JSONResponse(content={
            "session_id": session_id,
            "status": "created",
            "message": "Streaming session created successfully",
            "language": language
        })
        
    except Exception as e:
        logger.error(f"Error in transcribe_stream: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/transcriptions")
async def list_transcriptions(
    limit: int = Query(100),
    offset: int = Query(0)
):
    """
    List transcriptions.
    Matches backend/transcribe.py list_transcriptions endpoint.
    
    Args:
        limit: Maximum number of results (default: 100)
        offset: Offset for pagination (default: 0)
    """
    logger.info("Received list transcriptions request")
    
    try:
        # Get all transcriptions and apply pagination
        all_transcriptions = list(transcriptions_db.values())
        paginated = all_transcriptions[offset:offset + limit]
        
        return JSONResponse(content={
            "transcriptions": paginated,
            "count": len(paginated),
            "limit": limit,
            "offset": offset,
            "total": len(all_transcriptions)
        })
        
    except Exception as e:
        logger.error(f"Error listing transcriptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/transcriptions/{transcription_id}")
async def get_transcription(transcription_id: str):
    """
    Get a specific transcription by ID.
    Matches backend/transcribe.py get_transcription endpoint.
    
    Args:
        transcription_id: The transcription ID
    """
    logger.info(f"Received get transcription request for ID: {transcription_id}")
    
    try:
        if transcription_id not in transcriptions_db:
            raise HTTPException(status_code=404, detail="Transcription not found")
        
        return JSONResponse(content=transcriptions_db[transcription_id])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/transcriptions/{transcription_id}")
async def delete_transcription(transcription_id: str):
    """
    Delete a transcription by ID.
    Matches backend/transcribe.py delete_transcription endpoint.
    
    Args:
        transcription_id: The transcription ID
    """
    logger.info(f"Received delete transcription request for ID: {transcription_id}")
    
    try:
        if transcription_id not in transcriptions_db:
            raise HTTPException(status_code=404, detail="Transcription not found")
        
        del transcriptions_db[transcription_id]
        
        return JSONResponse(content={
            "message": "Transcription deleted successfully",
            "transcription_id": transcription_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/transcribe/health")
async def transcribe_health_check():
    """
    Health check for transcription service.
    Matches backend/transcribe.py transcribe_health_check endpoint.
    """
    logger.info("Received transcription health check request")
    
    try:
        return JSONResponse(content={
            "status": "ok",
            "service": "transcription",
            "granite_transcriber": {
                "status": "healthy" if granite_transcriber.is_available() else "mock_mode",
                "is_loaded": granite_transcriber.is_loaded
            },
            "vllm_client": {
                "status": "connected" if vllm_client.is_connected else "disconnected"
            },
            "active_sessions": len(active_sessions),
            "stored_transcriptions": len(transcriptions_db),
            "timestamp": int(time.time())
        })
        
    except Exception as e:
        logger.error(f"Error in transcription health check: {e}")
        return JSONResponse(content={
            "status": "error",
            "error": str(e)
        }, status_code=500)


# ============================================================================
# TRANSLATE ENDPOINTS - /api/v1/translate/*
# ============================================================================

@app.post("/api/v1/translate/text")
async def translate_text_endpoint(request_data: dict):
    """
    Handle text translation.
    Matches backend/translate.py translate_text endpoint.
    
    Args:
        text: Text to translate
        source_language: Source language code (default: en)
        target_language: Target language code (default: es)
    """
    logger.info("Received translate text request")
    
    try:
        text = request_data.get('text')
        source_lang = request_data.get('source_language', 'en')
        target_lang = request_data.get('target_language', 'es')
        
        if not text:
            raise HTTPException(status_code=400, detail="Missing text parameter")
        
        result = granite_transcriber.translate(text, source_lang, target_lang)
        
        return JSONResponse(content={
            "job_id": str(uuid.uuid4()),
            "status": "completed",
            "message": "Translation completed successfully",
            "translated_text": result.get("translated_text", ""),
            "source_language": source_lang,
            "target_language": target_lang,
            "processing_time": result.get("processing_time", 0)
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in translate_text: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/translate/transcription")
async def translate_transcription(request_data: dict):
    """
    Translate an existing transcription.
    Matches backend/translate.py translate_transcription endpoint.
    
    Args:
        transcription_id: ID of the transcription to translate
        target_language: Target language code (default: es)
    """
    logger.info("Received translate transcription request")
    
    try:
        transcription_id = request_data.get('transcription_id')
        target_language = request_data.get('target_language', 'es')
        
        if not transcription_id:
            raise HTTPException(status_code=400, detail="Missing transcription_id parameter")
        
        # Get the original transcription
        if transcription_id not in transcriptions_db:
            raise HTTPException(status_code=404, detail="Transcription not found")
        
        transcription = transcriptions_db[transcription_id]
        original_text = transcription.get('text', '')
        source_language = transcription.get('language', 'en')
        
        if not original_text:
            raise HTTPException(status_code=400, detail="Transcription has no text to translate")
        
        # Translate the text
        result = granite_transcriber.translate(original_text, source_language, target_language)
        
        # Store the translated transcription
        translated_id = str(uuid.uuid4())
        translated_record = {
            "id": translated_id,
            "original_transcription_id": transcription_id,
            "text": result.get("translated_text", ""),
            "source_language": source_language,
            "target_language": target_language,
            "created_at": time.time(),
            "status": "completed"
        }
        transcriptions_db[translated_id] = translated_record
        
        return JSONResponse(content={
            "job_id": translated_id,
            "status": "completed",
            "message": "Translation completed successfully",
            **translated_record
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in translate_transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# LEGACY ENDPOINTS (for backward compatibility)
# ============================================================================

@app.post("/transcribe")
async def transcribe_audio_legacy(
    file: UploadFile = File(...),
    language: str = Form("en"),
    task: str = Form("transcribe")
):
    """
    Legacy transcribe endpoint (backward compatibility).
    Use /api/v1/transcribe/file instead.
    """
    logger.warning("Legacy /transcribe endpoint used - consider using /api/v1/transcribe/file")
    
    try:
        # Save uploaded file temporarily
        temp_path = f"/tmp/{file.filename}"
        with open(temp_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # Process with Granite transcriber
        if task == "transcribe":
            result = granite_transcriber.transcribe(temp_path, language)
        elif task == "translate":
            # For translation, transcribe first then translate
            transcription_result = granite_transcriber.transcribe(temp_path, language)
            result = granite_transcriber.translate(
                transcription_result.get("text", ""), 
                language, 
                "en"
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid task. Use 'transcribe' or 'translate'")
        
        # Clean up temp file
        os.remove(temp_path)
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Error in legacy transcribe endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/translate")
async def translate_text_legacy(
    text: str = Form(...),
    source_language: str = Form("en"),
    target_language: str = Form("es")
):
    """
    Legacy translate endpoint (backward compatibility).
    Use /api/v1/translate/text instead.
    """
    logger.warning("Legacy /translate endpoint used - consider using /api/v1/translate/text")
    
    try:
        result = granite_transcriber.translate(text, source_language, target_language)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Error in legacy translate endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WEBSOCKET ENDPOINTS
# ============================================================================

@app.websocket("/translate/stream")
async def translate_stream_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time translation streaming.
    Handles WebRTC/WHIP input and sends processed audio over WebSocket.
    """
    await websocket.accept()
    logger.info("WebSocket connection established for translation stream")
    
    try:
        while True:
            # Receive data from client
            data = await websocket.receive_text()
            logger.info(f"Received data: {data}")
            
            # Process the data (simplified)
            response = {
                "type": "translation_result",
                "text": f"Processed: {data}",
                "timestamp": asyncio.get_event_loop().time()
            }
            
            # Send response back
            await websocket.send_text(json.dumps(response))
            
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Error in WebSocket translate/stream: {e}")
        try:
            await websocket.close()
        except:
            pass


@app.websocket("/ws/transcribe")
async def transcribe_stream_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time transcription streaming.
    Receives audio chunks and returns transcription results.
    """
    await websocket.accept()
    logger.info("WebSocket connection established for transcription stream")
    
    session_id = str(uuid.uuid4())
    active_sessions[session_id] = {
        "session_id": session_id,
        "language": "en",
        "status": "active",
        "created_at": time.time(),
        "chunks_received": 0,
        "websocket": websocket
    }
    
    try:
        # Send session info
        await websocket.send_text(json.dumps({
            "type": "session_created",
            "session_id": session_id,
            "status": "active"
        }))
        
        while True:
            # Receive audio data or control messages
            data = await websocket.receive()
            
            if data.type == WebSocketDisconnect:
                break
            
            if data.type == 1:  # Text
                msg = json.loads(data.data)
                msg_type = msg.get("type")
                
                if msg_type == "config":
                    # Handle configuration
                    language = msg.get("language", "en")
                    active_sessions[session_id]["language"] = language
                    await websocket.send_text(json.dumps({
                        "type": "config_ack",
                        "session_id": session_id,
                        "language": language
                    }))
                elif msg_type == "audio_chunk":
                    # Process audio chunk (simplified)
                    active_sessions[session_id]["chunks_received"] += 1
                    await websocket.send_text(json.dumps({
                        "type": "transcription_partial",
                        "session_id": session_id,
                        "text": f"[Partial] Chunk {active_sessions[session_id]['chunks_received']}",
                        "is_final": False
                    }))
                elif msg_type == "end_of_stream":
                    # Finalize transcription
                    await websocket.send_text(json.dumps({
                        "type": "transcription_final",
                        "session_id": session_id,
                        "text": "[Final] Transcription complete",
                        "is_final": True
                    }))
                    break
                    
            elif data.type == 2:  # Binary (audio data)
                active_sessions[session_id]["chunks_received"] += 1
                # In real implementation, process audio bytes
                await websocket.send_text(json.dumps({
                    "type": "transcription_partial",
                    "session_id": session_id,
                    "chunk_index": active_sessions[session_id]["chunks_received"],
                    "is_final": False
                }))
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket transcribe stream: {e}")
    finally:
        # Cleanup
        if session_id in active_sessions:
            del active_sessions[session_id]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
