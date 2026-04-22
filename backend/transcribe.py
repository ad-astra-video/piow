#!/usr/bin/env python3
"""
Transcription Service Endpoints
Handles all transcription and translation related API routes.
"""

import asyncio
import aiohttp
import aiohttp.web as web
import logging
import os
import re
import tempfile
import time
import uuid
import json

from auth import no_auth
from payments.payment_strategy import x402_or_subscription
from supabase_client import supabase
from compute_providers.provider_manager import ComputeProviderManager
from compute_providers.livepeer.livepeer import LivepeerComputeProvider
from agents import agent_register, agent_get_usage, agent_list_keys, agent_create_key, agent_revoke_key, agent_get_subscription, agent_create_subscription, agent_delete_subscription, agent_reactivate_subscription
from languages import get_languages
from translate import translate_text, translate_transcription

logger = logging.getLogger(__name__)

# Import compute provider definitions
from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

# Initialize compute provider manager (same as in main.py)
compute_provider_manager = ComputeProviderManager()

# Register providers from definitions
compute_provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)

def setup_routes(app):
    """Setup transcription-related routes."""
    # Transcription endpoints
    app.router.add_post('/api/v1/transcribe/file', transcribe_file)
    app.router.add_post('/api/v1/transcribe/url', transcribe_url)
    app.router.add_post('/api/v1/transcribe/stream', transcribe_stream)
    app.router.add_post('/api/v1/transcribe/stream/{stream_id}/whip', whip_proxy)
    app.router.add_get('/api/v1/transcriptions', list_transcriptions)
    app.router.add_get('/api/v1/transcriptions/{id}', get_transcription)
    app.router.add_delete('/api/v1/transcriptions/{id}', delete_transcription)
    
    # Health check
    app.router.add_get('/api/v1/transcribe/health', transcribe_health_check)

# ============================================================================
# HELPER: Store transcription result and record usage
# ============================================================================

async def _store_transcription_result(request, job_result, audio_url, language, source_type='upload'):
    """
    Store transcription result in the database and record usage.
    
    Args:
        request: aiohttp request (for extracting user/agent info)
        job_result: Result dict from compute provider
        audio_url: URL or path of the source audio
        language: Language code used
        source_type: 'upload', 'url', 'stream', or 'whip'
    
    Returns:
        transcription_id or None if storage failed
    """
    user = request.get('user') or request.get('agent')
    user_id = str(user.id) if hasattr(user, 'id') else str(user.get('id', 'unknown'))
    transcription_id = None

    # Store transcription in database
    try:
        transcription_record = supabase.table('transcriptions').insert({
            'user_id': user_id,
            'audio_url': audio_url,
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'duration': job_result.get('duration', 0) or 0,
            'word_count': job_result.get('word_count', 0) or 0,
            'segments': job_result.get('segments'),
            'status': 'completed',
            'source_type': source_type,
            'model_used': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
        }).execute()
        transcription_id = transcription_record.data[0]['id'] if transcription_record.data else None
    except Exception as db_error:
        logger.warning(f"Failed to store transcription in database: {db_error}")

    # Record usage
    try:
        duration_seconds = job_result.get('duration', 0) or 0
        supabase.table('transcription_usage').insert({
            'user_id': user_id,
            'duration_seconds': int(duration_seconds),
            'word_count': job_result.get('word_count', 0) or 0,
            'source_language': job_result.get('language', language),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'source_type': source_type,
        }).execute()
    except Exception as usage_error:
        logger.warning(f"Failed to record transcription usage: {usage_error}")

    return transcription_id


# ============================================================================
# TRANSCRIPTION ENDPOINTS
# ============================================================================

@x402_or_subscription(service_type='transcribe_cpu')
async def transcribe_file(request):
    """
    Handle file upload for transcription.

    Accepts multipart form data with:
    - file: Audio file (wav, mp3, m4a, flac, ogg)
    - language: Language code (default: en)

    The file is saved temporarily and its local path is passed to the
    compute provider. For remote providers, the file should first be
    uploaded to accessible storage (e.g., Supabase Storage) and the
    URL passed via the /transcribe/url endpoint instead.
    """
    logger.info("Received transcription file upload request")

    temp_path = None
    try:
        # Parse multipart data
        reader = await request.multipart()

        file_part = None
        language = "en"

        async for part in reader:
            if part.name == 'file':
                file_part = part
            elif part.name == 'language':
                language = await part.text()

        if not file_part:
            return web.json_response({
                "error": "No file provided"
            }, status=400)

        # Save uploaded file temporarily
        filename = file_part.filename or "uploaded_audio"
        # Create a safe filename
        safe_filename = re.sub(r'[^\w\-_]', '_', filename)
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

        # Use compute provider to process the transcription
        provider = compute_provider_manager.select_provider(
            job_type="transcribe_batch",
            requirements={
                "streaming": False,
                "language": language
            }
        )
        if not provider:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
            return web.json_response({
                "error": "No compute provider available"
            }, status=503)

        # Create transcription job using the compute provider
        # Note: For remote providers, the file should be uploaded to accessible storage first.
        # The file:// URL scheme works only for providers running on the same host.
        audio_url = f"file://{temp_path}"

        try:
            job_result = await provider.create_transcription_job(
                audio_url=audio_url,
                language=language,
                format="json"
            )
        except Exception as provider_error:
            logger.error(f"Compute provider error in transcribe_file: {provider_error}")
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
            return web.json_response({
                "error": f"Transcription failed: {str(provider_error)}",
                "status": "error"
            }, status=502)

        # Clean up temp file
        try:
            os.unlink(temp_path)
            temp_path = None
        except:
            pass

        # Store transcription result and record usage
        transcription_id = await _store_transcription_result(
            request, job_result, audio_url, language, source_type='upload'
        )

        # Return the real result from the compute provider
        return web.json_response({
            'id': transcription_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'duration': job_result.get('duration'),
            'word_count': job_result.get('word_count'),
            'segments': job_result.get('segments'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in transcribe_file: {e}")
        # Clean up temp file if it exists
        if temp_path:
            try:
                os.unlink(temp_path)
            except:
                pass
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@x402_or_subscription(service_type='transcribe_cpu')
async def transcribe_url(request):
    """
    Handle transcription from a URL.

    Accepts JSON body with:
    - audio_url: URL to the audio file (required)
    - language: Language code (default: en)
    - format: Response format (default: json)
    """
    logger.info("Received transcription URL request")

    try:
        data = await request.json()

        audio_url = data.get('audio_url')
        language = data.get('language', 'en')
        format = data.get('format', 'json')

        if not audio_url:
            return web.json_response({
                "error": "Missing audio_url parameter"
            }, status=400)

        # Use compute provider to process the transcription
        provider = compute_provider_manager.select_provider(
            job_type="transcribe_batch",
            requirements={
                "language": language
            }
        )
        if not provider:
            return web.json_response({
                "error": "No compute provider available"
            }, status=503)

        # Create transcription job using the compute provider
        try:
            job_result = await provider.create_transcription_job(
                audio_url=audio_url,
                language=language,
                format=format
            )
        except Exception as provider_error:
            logger.error(f"Compute provider error in transcribe_url: {provider_error}")
            return web.json_response({
                "error": f"Transcription failed: {str(provider_error)}",
                "status": "error"
            }, status=502)

        # Store transcription result and record usage
        transcription_id = await _store_transcription_result(
            request, job_result, audio_url, language, source_type='url'
        )

        # Return the real result from the compute provider
        return web.json_response({
            'id': transcription_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'duration': job_result.get('duration'),
            'word_count': job_result.get('word_count'),
            'segments': job_result.get('segments'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in transcribe_url: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@x402_or_subscription(service_type='transcribe_gpu')
async def transcribe_stream(request):
    """
    Handle real-time transcription streaming.
    
    This endpoint:
    1. Selects an appropriate compute provider
    2. Negotiates a stream session with the provider (POST to provider's start endpoint)
    3. Stores the provider's response (URLs) for later use
    4. Returns the stream URLs to the client
    """
    logger.info("Received transcription stream request")

    try:
        data = await request.json()

        session_id = data.get('session_id')
        language = data.get('language', 'en')

        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())

        # Select compute provider based on job requirements
        provider = compute_provider_manager.select_provider(
            job_type="transcribe_stream",
            requirements={"language": language}
        )
        
        if not provider:
            return web.json_response({
                "error": "No compute provider available"
            }, status=503)

        # Create streaming session by negotiating with the provider
        # This makes an HTTP POST to the provider's session start endpoint
        session_result = await provider.create_streaming_session(
            session_id=session_id,
            language=language
        )

        # Import session store to save provider session data
        from sessions import session_store
        
        # Store session in session store with provider data
        stream_id = await session_store.create_stream_session(
            session_id=session_id,
            language=language,
            provider_session_data=session_result
        )

        return web.json_response({
            "session_id": session_id,
            "stream_id": stream_id,
            "status": "active",
            "message": "Streaming session created successfully",
            # WHIP is now proxied through the backend — clients POST SDP offers
            # to /api/v1/transcribe/stream/{stream_id}/whip instead of connecting
            # directly to the provider. The provider's whip_url is stored server-side.
            "data_url": session_result.get("data_url"),
            "update_url": session_result.get("update_url"),
            "stop_url": session_result.get("stop_url"),
            "provider_stream_id": session_result.get("provider_stream_id"),
            "provider": session_result.get("provider")
        })

    except Exception as e:
        logger.error(f"Error in transcribe_stream: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@x402_or_subscription(service_type='transcribe_gpu')
async def whip_proxy(request):
    """
    Proxy WHIP SDP offer/answer through the backend.

    The frontend POSTs an SDP offer to this endpoint. The backend looks up
    the provider's whip_url from the session store, forwards the SDP offer
    to the provider, and returns the SDP answer to the frontend.

    This ensures the provider's internal URL is never exposed to the client,
    and the backend can enforce auth and rate-limiting on WHIP connections.

    Request:
        POST /api/v1/transcribe/stream/{stream_id}/whip
        Content-Type: application/sdp
        Body: SDP offer (text/plain)

    Response:
        200 OK
        Content-Type: application/sdp
        Body: SDP answer (text/plain)
    """
    stream_id = request.match_info.get('stream_id')
    if not stream_id:
        return web.json_response(
            {"error": "Missing stream_id in URL path"},
            status=400
        )

    # Look up the stream session to find the provider's whip_url
    from sessions import session_store

    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        return web.json_response(
            {"error": f"Stream session '{stream_id}' not found. Create one via POST /api/v1/transcribe/stream first."},
            status=404
        )

    provider_session = stream_session.get("provider_session", {})
    provider_whip_url = provider_session.get("whip_url")
    if not provider_whip_url:
        return web.json_response(
            {"error": f"No WHIP URL available for stream session '{stream_id}'. The compute provider did not return a WHIP endpoint."},
            status=503
        )

    # Read the SDP offer from the request body
    sdp_offer = await request.text()
    if not sdp_offer or not sdp_offer.strip().startswith("v=0"):
        logger.warning(f"WHIP proxy: received invalid SDP offer for stream {stream_id} (length={len(sdp_offer)})")

    logger.info(f"WHIP proxy: forwarding SDP offer for stream {stream_id} to provider (offer length={len(sdp_offer)})")

    # Forward the SDP offer to the provider's WHIP endpoint
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                provider_whip_url,
                data=sdp_offer,
                headers={"Content-Type": "application/sdp"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as provider_response:
                if provider_response.status != 200 and provider_response.status != 201:
                    error_text = await provider_response.text()
                    logger.error(
                        f"WHIP proxy: provider returned {provider_response.status} for stream {stream_id}: {error_text[:500]}"
                    )
                    return web.json_response(
                        {"error": f"Provider WHIP endpoint returned {provider_response.status}", "details": error_text[:500]},
                        status=provider_response.status
                    )

                # Get the SDP answer from the provider
                sdp_answer = await provider_response.text()
                logger.info(
                    f"WHIP proxy: received SDP answer for stream {stream_id} (answer length={len(sdp_answer)})"
                )

                # Return the SDP answer to the frontend
                return web.Response(
                    text=sdp_answer,
                    content_type="application/sdp",
                    status=provider_response.status
                )

    except aiohttp.ClientError as e:
        logger.error(f"WHIP proxy: connection error for stream {stream_id}: {e}")
        return web.json_response(
            {"error": f"Failed to connect to provider WHIP endpoint: {str(e)}"},
            status=502
        )
    except asyncio.TimeoutError:
        logger.error(f"WHIP proxy: timeout for stream {stream_id}")
        return web.json_response(
            {"error": "Provider WHIP endpoint timed out"},
            status=504
        )
    except Exception as e:
        logger.error(f"WHIP proxy: unexpected error for stream {stream_id}: {e}")
        return web.json_response(
            {"error": f"WHIP proxy error: {str(e)}"},
            status=500
        )


async def list_transcriptions(request):
    """List transcriptions."""
    logger.info("Received list transcriptions request")

    try:
        # Get query parameters
        limit = int(request.query.get('limit', '100'))
        offset = int(request.query.get('offset', '0'))

        # Query transcriptions from database
        result = supabase.table('transcriptions').select('*').range(offset, offset + limit - 1).execute()

        return web.json_response({
            "transcriptions": result.data if hasattr(result, 'data') else result,
            "count": len(result.data) if hasattr(result, 'data') else 0,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Error listing transcriptions: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


async def get_transcription(request):
    """Get a specific transcription by ID."""
    logger.info("Received get transcription request")

    try:
        transcription_id = request.match_info.get('id')
        if not transcription_id:
            return web.json_response({
                "error": "Missing transcription ID"
            }, status=400)

        # Query transcription from database
        result = supabase.table('transcriptions').select('*').eq('id', transcription_id).execute()

        if not result.data:
            return web.json_response({
                "error": "Transcription not found"
            }, status=404)

        return web.json_response(result.data[0])

    except Exception as e:
        logger.error(f"Error getting transcription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


async def delete_transcription(request):
    """Delete a transcription by ID."""
    logger.info("Received delete transcription request")

    try:
        transcription_id = request.match_info.get('id')
        if not transcription_id:
            return web.json_response({
                "error": "Missing transcription ID"
            }, status=400)

        # Delete transcription from database
        result = supabase.table('transcriptions').delete().eq('id', transcription_id).execute()

        if not result.data:
            return web.json_response({
                "error": "Transcription not found"
            }, status=404)

        return web.json_response({
            "message": "Transcription deleted successfully",
            "transcription_id": transcription_id
        })

    except Exception as e:
        logger.error(f"Error deleting transcription: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@no_auth
async def transcribe_health_check(request):
    """Health check for transcription service."""
    logger.info("Received transcription health check request")

    try:
        # Check compute provider health
        provider_health = {}
        for name in compute_provider_manager.list_providers():
            provider = compute_provider_manager.get_provider(name)
            if provider:
                # In a real implementation, this would be async
                # For now, we'll check if it's enabled
                provider_health[name] = {
                    "status": "healthy" if provider.enabled else "disabled",
                    "provider": name
                }
            else:
                provider_health[name] = {"status": "unknown", "provider": name}

        # Check Supabase connection
        supabase_status = "unknown"
        try:
            if supabase:
                # Simple query to check connection
                supabase.table('agents').select('id').limit(1).execute()
                supabase_status = "ok"
            else:
                supabase_status = "error: client not initialized"
        except Exception as e:
            supabase_status = f"error: {str(e)}"

        return web.json_response({
            "status": "ok",
            "service": "transcription",
            "compute_providers": provider_health,
            "supabase": supabase_status,
            "timestamp": int(time.time())
        })

    except Exception as e:
        logger.error(f"Error in transcription health check: {e}")
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)