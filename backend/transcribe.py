#!/usr/bin/env python3
"""
Transcription API Endpoints
Handles HTTP requests for transcription and translation services.
"""

from aiohttp import web
import logging
import os
import tempfile
from typing import Dict, Any

logger = logging.getLogger(__name__)

# These will be imported when needed to avoid circular imports
# and to allow for lazy loading of heavy models

async def transcribe_file(request):
    """
    Handle file upload for transcription.
    
    Expected: POST request with multipart/form-data containing:
    - file: audio file to transcribe
    - language: language code (optional, defaults to 'en')
    - streaming: boolean for streaming vs batch (optional, defaults to False)
    """
    logger.info("Received transcription file upload request")
    
    try:
        # Parse multipart data
        reader = await request.multipart()
        
        file_part = None
        language = "en"
        streaming = False
        
        async for part in reader:
            if part.name == 'file':
                file_part = part
            elif part.name == 'language':
                language = await part.text()
            elif part.name == 'streaming':
                streaming_str = await part.text()
                streaming = streaming_str.lower() in ('true', '1', 'yes')
        
        if not file_part:
            return web.json_response(
                {"error": "No file provided"}, 
                status=400
            )
        
        # Save uploaded file temporarily
        filename = file_part.filename or "uploaded_audio"
        # Create a safe filename
        import re
        safe_filename = re.sub(r'[^\w\-_\.]', '_', filename)
        if not safe_filename.endswith(('.wav', '.mp3', '.m4a', '.flac', '.ogg')):
            safe_filename += '.wav'  # Default extension
        
        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(safe_filename)[1]) as tmp_file:
            temp_path = tmp_file.name
            
            # Write file content
            chunk_size = 8192
            while True:
                chunk = await file_part.read_chunk(chunk_size)
                if not chunk:
                    break
                tmp_file.write(chunk)
        
        logger.info(f"Saved uploaded file to {temp_path}")
        
        # Import model router here to avoid circular imports
        from model_router import ModelRouter
        router = ModelRouter()
        
        # Route to appropriate model
        if streaming:
            # For streaming, we need to handle this differently - return WebSocket info
            result = router.route_transcription(
                audio_input=temp_path,
                language=language,
                streaming=True
            )
            
            # For streaming, we return connection information
            if result.get("status") == "streaming_required":
                return web.json_response({
                    "status": "streaming_setup_required",
                    "message": "Real-time streaming requires WebSocket connection",
                    "websocket_url": os.environ.get("API_URL", "http://localhost:8000") + "/ws",
                    "session_id": f"stream_{int(__import__('time').time())}",
                    "instructions": "Connect to WebSocket and send audio chunks for real-time transcription"
                })
            else:
                return web.json_response(result)
        else:
            # Batch transcription
            result = router.route_transcription(
                audio_input=temp_path,
                language=language,
                streaming=False
            )
            return web.json_response(result)
            
    except Exception as e:
        logger.error(f"Error in transcription endpoint: {e}")
        return web.json_response(
            {"error": str(e)}, 
            status=500
        )
    finally:
        # Clean up temp file
        try:
            if 'temp_path' in locals():
                os.unlink(temp_path)
        except:
            pass

async def transcribe_url(request):
    """
    Handle transcription from URL.
    
    Expected: POST request with JSON containing:
    - audio_url: URL to audio file
    - language: language code (optional)
    - streaming: boolean for streaming vs batch (optional)
    """
    logger.info("Received transcription URL request")
    
    try:
        data = await request.json()
        audio_url = data.get('audio_url')
        language = data.get('language', 'en')
        streaming = data.get('streaming', False)
        
        if not audio_url:
            return web.json_response(
                {"error": "No audio_url provided"}, 
                status=400
            )
        
        # Import model router
        from model_router import ModelRouter
        router = ModelRouter()
        
        # Route to appropriate model
        if streaming:
            result = router.route_transcription(
                audio_input=audio_url,
                language=language,
                streaming=True
            )
            
            if result.get("status") == "streaming_required":
                return web.json_response({
                    "status": "streaming_setup_required",
                    "message": "Real-time streaming requires WebSocket connection",
                    "websocket_url": os.environ.get("API_URL", "http://localhost:8000") + "/ws",
                    "session_id": f"stream_{int(__import__('time').time())}",
                    "instructions": "Connect to WebSocket and send audio chunks for real-time transcription"
                })
            else:
                return web.json_response(result)
        else:
            result = router.route_transcription(
                audio_input=audio_url,
                language=language,
                streaming=False
            )
            return web.json_response(result)
            
    except Exception as e:
        logger.error(f"Error in transcription URL endpoint: {e}")
        return web.json_response(
            {"error": str(e)}, 
            status=500
        )

async def translate_text(request):
    """
    Handle translation requests.
    
    Expected: POST request with JSON containing:
    - text: text to translate
    - source_language: source language code
    - target_language: target language code
    """
    logger.info("Received translation request")
    
    try:
        data = await request.json()
        text = data.get('text')
        source_lang = data.get('source_language', 'en')
        target_lang = data.get('target_language', 'es')
        
        if not text:
            return web.json_response(
                {"error": "No text provided"}, 
                status=400
            )
        
        # Import model router
        from model_router import ModelRouter
        router = ModelRouter()
        
        result = router.route_translation(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang
        )
        
        return web.json_response(result)
        
    except Exception as e:
        logger.error(f"Error in translation endpoint: {e}")
        return web.json_response(
            {"error": str(e)}, 
            status=500
        )

async def get_languages(request):
    """
    Get list of supported languages.
    
    Returns:
        JSON list of supported language codes and names
    """
    logger.info("Received languages request")
    
    # Common language codes - in production this might come from model config
    languages = [
        {"code": "en", "name": "English"},
        {"code": "es", "name": "Spanish"},
        {"code": "fr", "name": "French"},
        {"code": "de", "name": "German"},
        {"code": "it", "name": "Italian"},
        {"code": "pt", "name": "Portuguese"},
        {"code": "ru", "name": "Russian"},
        {"code": "zh", "name": "Chinese"},
        {"code": "ja", "name": "Japanese"},
        {"code": "ko", "name": "Korean"},
        {"code": "ar", "name": "Arabic"},
        {"code": "hi", "name": "Hindi"},
    ]
    
    return web.json_response({
        "languages": languages,
        "default": "en"
    })

# Health check endpoint for transcription service
async def transcribe_health_check(request):
    """Health check for transcription service."""
    try:
        from model_router import ModelRouter
        router = ModelRouter()
        models_info = router.get_available_models()
        
        return web.json_response({
            "status": "healthy",
            "service": "transcription",
            "models": models_info,
            "timestamp": __import__('time').time()
        })
    except Exception as e:
        return web.json_response(
            {"status": "unhealthy", "error": str(e)},
            status=500
        )

def setup_routes(app: web.Application):
    """Setup transcription-related routes."""
    app.router.add_post('/api/v1/transcribe/file', transcribe_file)
    app.router.add_post('/api/v1/transcribe/url', transcribe_url)
    app.router.add_post('/api/v1/translate', translate_text)
    app.router.add_get('/api/v1/languages', get_languages)
    app.router.add_get('/api/v1/transcribe/health', transcribe_health_check)
    
    logger.info("Transcription routes configured")
