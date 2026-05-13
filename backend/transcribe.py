#!/usr/bin/env python3
"""
Transcription Service Endpoints
Handles all transcription and translation related API routes.
"""

import asyncio
import aiohttp
import aiohttp.web as web
import base64
import io
import ipaddress
import logging
import math
import os
import re
import time
import uuid
import json
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

try:
    import av
except ImportError:
    av = None

from auth import no_auth, require_user_auth, track_usage
from payments.payment_strategy import x402_or_subscription
from supabase_client import async_supabase as supabase
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

MAX_REMOTE_AUDIO_BYTES = 225 * 1024 * 1024  # ~2 hours of 16 kHz mono 16-bit audio


def _coerce_bool(value: Any) -> bool:
    """Normalize booleans from multipart or JSON request values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _infer_audio_mime_type(file_path):
    """Infer audio MIME type from file extension for data URLs."""
    ext = (os.path.splitext(file_path)[1] or "").lower()
    mime_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".aac": "audio/aac",
    }
    return mime_types.get(ext, "application/octet-stream")


def _build_data_url_from_file(file_path):
    """Read local media file and encode it as a base64 data URL."""
    with open(file_path, "rb") as f:
        binary = f.read()

    mime_type = _infer_audio_mime_type(file_path)
    encoded = base64.b64encode(binary).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_data_url_from_bytes(data: bytes, filename: str) -> str:
    """Encode in-memory audio bytes as a base64 data URL."""
    mime_type = _infer_audio_mime_type(filename)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _probe_duration_from_bytes(data: bytes, filename: str = "") -> Optional[int]:
    """Extract audio duration from in-memory bytes using PyAV (no disk I/O)."""
    if not av:
        return None

    ext = (os.path.splitext(filename)[1] or "").lstrip(".").lower()
    fmt = ext if ext else None
    try:
        buf = io.BytesIO(data)
        with av.open(buf, format=fmt) as container:
            if container.duration is not None:
                return max(0, int(math.ceil(float(container.duration / av.time_base))))

            durations = [
                float(s.duration * s.time_base)
                for s in container.streams.audio
                if s.duration is not None and s.time_base is not None
            ]
            if durations:
                return max(0, int(math.ceil(max(durations))))
    except Exception as e:
        logger.warning("Failed to probe duration from bytes (%s): %s", filename, e)
    return None

def setup_routes(app):
    """Setup transcription-related routes."""
    # Streaming transcription endpoints
    app.router.add_post('/api/v1/transcribe/stream', transcribe_stream)
    app.router.add_put('/api/v1/transcribe/stream/{stream_id}/translation', update_stream_translation)
    app.router.add_put('/api/v1/transcribe/stream/{stream_id}/analysis', update_stream_analysis)
    app.router.add_post('/api/v1/transcribe/stream/{stream_id}/whip', whip_proxy)
    app.router.add_get('/api/v1/transcriptions', list_transcriptions)
    app.router.add_get('/api/v1/transcriptions/{id}', get_transcription)
    app.router.add_delete('/api/v1/transcriptions/{id}', delete_transcription)
    
    # Health check
    app.router.add_get('/api/v1/transcribe/health', transcribe_health_check)

# ============================================================================
# HELPER: Get current user_id from request
# ============================================================================

def _get_user_id(request):
    """Extract user_id from authenticated request."""
    user = request.get('user')
    if user:
        return str(user.id) if hasattr(user, 'id') else str(user.get('id', ''))
    agent = request.get('agent')
    if agent:
        return str(agent.get('id', ''))
    return None


def _is_successful_transcription_result(job_result):
    """Return True when provider result indicates a successful transcription."""
    if not isinstance(job_result, dict):
        return False
    status = str(job_result.get('status', 'completed') or 'completed').strip().lower()
    failure_statuses = {'failed', 'error', 'cancelled', 'canceled'}
    return status not in failure_statuses


def _validate_provider_transcription_result(result, provider_name):
    """Raise when a provider returns an unusable transcription payload."""
    if result is None:
        raise ValueError(f"Provider '{provider_name}' returned no response payload")
    if not isinstance(result, dict):
        raise ValueError(
            f"Provider '{provider_name}' returned invalid response type: {type(result).__name__}"
        )
    if not result:
        raise ValueError(f"Provider '{provider_name}' returned an empty response payload")
    return result

# ============================================================================
# HELPER: Store transcription result and record usage
# ============================================================================

async def _store_transcription_result(request, job_result, audio_url, language, source_type='upload', duration_seconds_override=None):
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
    user_id = _get_user_id(request)
    if not user_id:
        logger.warning("Cannot store transcription: no authenticated user")
        return None
    transcription_id = None

    effective_duration_seconds = (
        duration_seconds_override
        if duration_seconds_override is not None
        else (job_result.get('duration', 0) or 0)
    )

    # Store transcription in database
    try:
        transcription_record = await supabase.table('transcriptions').insert({
            'user_id': user_id,
            'audio_url': audio_url,
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'duration': int(effective_duration_seconds),
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

    if not transcription_id:
        logger.warning("Skipping transcription usage log: transcription record was not persisted")
        return None

    # Record usage
    try:
        await supabase.table('transcription_usage').insert({
            'user_id': user_id,
            'duration_seconds': int(effective_duration_seconds),
            'word_count': job_result.get('word_count', 0) or 0,
            'source_language': job_result.get('language', language),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'source_type': source_type,
        }).execute()
    except Exception as usage_error:
        logger.warning(f"Failed to record transcription usage: {usage_error}")

    return transcription_id


def _get_audio_duration_seconds(file_path):
    """Extract audio duration in whole seconds from a local media file using PyAV."""
    if not av:
        return None

    try:
        with av.open(file_path) as container:
            if container.duration is not None:
                duration_seconds = float(container.duration / av.time_base)
                return max(0, int(math.ceil(duration_seconds)))

            audio_stream_durations = []
            for stream in container.streams.audio:
                if stream.duration is not None and stream.time_base is not None:
                    audio_stream_durations.append(float(stream.duration * stream.time_base))

            if audio_stream_durations:
                return max(0, int(math.ceil(max(audio_stream_durations))))
    except Exception as e:
        logger.warning("Failed to derive duration with PyAV for %s: %s", file_path, e)

    return None


async def _resolve_public_ips_for_host(hostname):
    """Resolve hostname and return a list of public IP objects."""
    loop = asyncio.get_running_loop()
    try:
        addr_infos = await loop.getaddrinfo(hostname, None, type=0, proto=0)
    except Exception as e:
        raise ValueError(f"Failed to resolve host '{hostname}': {e}") from e

    public_ips = []
    for info in addr_infos:
        sockaddr = info[4]
        if not sockaddr:
            continue

        host = sockaddr[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"URL host resolves to a non-public address: {ip}")

        public_ips.append(ip)

    if not public_ips:
        raise ValueError("URL host did not resolve to any valid public IP address")

    return public_ips


def _filename_from_url(audio_url: str) -> str:
    """Infer a safe filename (with extension) from a URL path."""
    parsed = urlparse(audio_url)
    basename = os.path.basename(parsed.path or "") or "audio"
    _, ext = os.path.splitext(basename)
    ext = (ext or "").lower()
    allowed = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".aac", ".mp4"}
    if ext not in allowed:
        ext = ".bin"
    name = re.sub(r'[^\w\-.]', '_', os.path.splitext(basename)[0])
    return f"{name}{ext}" if name else f"audio{ext}"


async def _safe_download_audio_bytes(audio_url: str) -> tuple[bytes, str]:
    """
    Download a remote audio URL into memory with SSRF/size protections.

    Returns (data_bytes, inferred_filename).
    """
    parsed = urlparse(audio_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https audio URLs are supported")
    if not parsed.hostname:
        raise ValueError("Invalid audio URL: missing hostname")

    await _resolve_public_ips_for_host(parsed.hostname)

    filename = _filename_from_url(audio_url)
    chunks: list[bytes] = []
    bytes_downloaded = 0
    timeout = aiohttp.ClientTimeout(total=90, connect=10, sock_read=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(audio_url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f"Failed to download audio URL (HTTP {response.status})")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_REMOTE_AUDIO_BYTES:
                raise ValueError("Remote audio file is too large")

            async for chunk in response.content.iter_chunked(8192):
                if not chunk:
                    continue
                bytes_downloaded += len(chunk)
                if bytes_downloaded > MAX_REMOTE_AUDIO_BYTES:
                    raise ValueError("Remote audio file exceeds size limit")
                chunks.append(chunk)

    return b"".join(chunks), filename


# ============================================================================
# TRANSCRIPTION ENDPOINTS
# ============================================================================

@track_usage
@x402_or_subscription(service_type='transcribe_cpu')
async def transcribe_file(request):
    """
    Handle file upload for transcription.

    Accepts multipart form data with:
    - file: Audio file (wav, mp3, m4a, flac, ogg)
    - language: Language code (default: en)
    """
    logger.info("Received transcription file upload request")

    try:
        reader = await request.multipart()

        file_data = None
        filename = "uploaded_audio"
        language = "en"
        punctuation_pass = False
        with_speakers = False
        with_word_timestamps = False
        source_language = None
        target_language = None

        async for part in reader:
            if part.name == 'file':
                filename = part.filename or "uploaded_audio"
                file_data = await part.read()
            elif part.name == 'language':
                language = await part.text()
            elif part.name == 'punctuation_pass':
                punctuation_pass = _coerce_bool(await part.text())
            elif part.name == 'with_speakers':
                with_speakers = _coerce_bool(await part.text())
            elif part.name == 'with_word_timestamps':
                with_word_timestamps = _coerce_bool(await part.text())
            elif part.name == 'source_language':
                source_language = (await part.text()).strip() or None
            elif part.name == 'target_language':
                target_language = (await part.text()).strip() or None

        if not file_data:
            return web.json_response({"error": "No file provided"}, status=400)

        if len(file_data) == 0:
            return web.json_response({"error": "Uploaded file is empty"}, status=400)

        source_duration_seconds = _probe_duration_from_bytes(file_data, filename)
        logger.info(
            "File upload received: name=%s size=%d bytes duration_probe=%s",
            filename,
            len(file_data),
            f"{source_duration_seconds}s" if source_duration_seconds is not None else "unknown",
        )

        ranked_providers = compute_provider_manager.select_providers(
            job_type="transcribe_batch",
            requirements={"streaming": False, "language": language}
        )
        if not ranked_providers:
            return web.json_response({"error": "No compute provider available"}, status=503)

        safe_filename = re.sub(r'[^\w\-.()]', '_', filename)
        provider_audio_url = _build_data_url_from_bytes(file_data, filename)
        audio_url = f"upload://{safe_filename}"

        job_result = None
        last_error = None
        for provider in ranked_providers:
            try:
                candidate_result = await provider.create_transcription_job(
                    audio_url=provider_audio_url, language=language, format="json",
                    punctuation_pass=punctuation_pass,
                    with_speakers=with_speakers,
                    with_word_timestamps=with_word_timestamps,
                    source_language=source_language, target_language=target_language
                )
                job_result = _validate_provider_transcription_result(
                    candidate_result,
                    provider.provider_name,
                )
                break
            except Exception as provider_error:
                logger.warning(
                    "Compute provider error in transcribe_file: provider=%s error=%s",
                    provider.provider_name,
                    provider_error,
                )
                last_error = provider_error

        if not job_result:
            return web.json_response({
                "error": f"All providers failed for transcription. Last error: {str(last_error)}",
                "status": "error"
            }, status=503)

        if not _is_successful_transcription_result(job_result):
            return web.json_response({
                "error": "Provider transcription job did not complete successfully",
                "status": job_result.get('status', 'error'),
            }, status=502)

        if source_duration_seconds is not None:
            job_result['duration'] = source_duration_seconds

        transcription_id = await _store_transcription_result(
            request,
            job_result,
            audio_url,
            language,
            source_type='upload',
            duration_seconds_override=source_duration_seconds,
        )

        return web.json_response({
            'id': transcription_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'source_language': source_language or job_result.get('language', language),
            'target_language': target_language,
            'duration': job_result.get('duration'),
            'word_count': job_result.get('word_count'),
            'segments': job_result.get('segments'),
            'words': job_result.get('words'),
            'speakers': job_result.get('speakers'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in transcribe_file: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@track_usage
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
        punctuation_pass = _coerce_bool(data.get('punctuation_pass', False))
        with_speakers = _coerce_bool(data.get('with_speakers', False))
        with_word_timestamps = _coerce_bool(data.get('with_word_timestamps', False))
        source_language = data.get('source_language') or None
        target_language = data.get('target_language') or None

        if not audio_url:
            return web.json_response({"error": "Missing audio_url parameter"}, status=400)

        try:
            file_data, filename = await _safe_download_audio_bytes(audio_url)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        source_duration_seconds = _probe_duration_from_bytes(file_data, filename)
        logger.info(
            "URL audio downloaded: url=%s size=%d bytes duration_probe=%s",
            audio_url,
            len(file_data),
            f"{source_duration_seconds}s" if source_duration_seconds is not None else "unknown",
        )

        provider_audio_url = _build_data_url_from_bytes(file_data, filename)

        ranked_providers = compute_provider_manager.select_providers(
            job_type="transcribe_batch", requirements={"language": language}
        )
        if not ranked_providers:
            return web.json_response({"error": "No compute provider available"}, status=503)

        job_result = None
        last_error = None
        for provider in ranked_providers:
            try:
                candidate_result = await provider.create_transcription_job(
                    audio_url=provider_audio_url, language=language, format=format,
                    punctuation_pass=punctuation_pass,
                    with_speakers=with_speakers,
                    with_word_timestamps=with_word_timestamps,
                    source_language=source_language, target_language=target_language
                )
                job_result = _validate_provider_transcription_result(
                    candidate_result,
                    provider.provider_name,
                )
                break
            except Exception as provider_error:
                logger.warning(
                    "Compute provider error in transcribe_url: provider=%s error=%s",
                    provider.provider_name,
                    provider_error,
                )
                last_error = provider_error

        if not job_result:
            return web.json_response({
                "error": f"All providers failed for transcription. Last error: {str(last_error)}",
                "status": "error"
            }, status=503)

        if not _is_successful_transcription_result(job_result):
            return web.json_response({
                "error": "Provider transcription job did not complete successfully",
                "status": job_result.get('status', 'error'),
            }, status=502)

        if source_duration_seconds is not None:
            job_result['duration'] = source_duration_seconds

        transcription_id = await _store_transcription_result(
            request,
            job_result,
            audio_url,
            language,
            source_type='url',
            duration_seconds_override=source_duration_seconds,
        )

        return web.json_response({
            'id': transcription_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'text': job_result.get('text', ''),
            'language': job_result.get('language', language),
            'source_language': source_language or job_result.get('language', language),
            'target_language': target_language,
            'duration': job_result.get('duration'),
            'word_count': job_result.get('word_count'),
            'segments': job_result.get('segments'),
            'words': job_result.get('words'),
            'speakers': job_result.get('speakers'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in transcribe_url: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


def _is_valid_streaming_session(session_result):
    """Check if a provider returned a usable streaming session with a WHIP URL."""
    if not session_result:
        return False
    whip_url = session_result.get("whip_url")
    return bool(whip_url and str(whip_url).strip())


@track_usage
@x402_or_subscription(service_type='transcribe_gpu')
async def transcribe_stream(request):
    """Handle real-time transcription streaming."""
    logger.info("Received transcription stream request")

    try:
        data = await request.json()
        session_id = data.get('session_id')
        language = data.get('language', 'en')
        live_transcription_enabled = bool(data.get('live_transcription_enabled', True))
        live_translation_enabled = bool(data.get('live_translation_enabled', bool(data.get('target_language'))))
        source_language = data.get('source_language') or language
        target_language = data.get('target_language') or None
        if not live_translation_enabled:
            target_language = None
        analysis_enabled = bool(data.get('analysis_enabled', False))
        analysis_mode = str(data.get('analysis_mode') or 'multimodal')
        analysis_audio_chunk_seconds = float(data.get('analysis_audio_chunk_seconds') or 1.0)
        analysis_video_fps = int(data.get('analysis_video_fps') or 3)
        analysis_prompt = data.get('analysis_prompt')
        if analysis_prompt is not None:
            analysis_prompt = str(analysis_prompt).strip() or None

        if not live_transcription_enabled and not analysis_enabled:
            return web.json_response({
                'error': 'At least one live service must be enabled (transcription or analysis).',
                'code': 'invalid_service_selection',
            }, status=400)

        if live_translation_enabled and not live_transcription_enabled:
            return web.json_response({
                'error': 'Live translation requires live transcription to be enabled.',
                'code': 'invalid_service_selection',
            }, status=400)

        if not session_id:
            session_id = str(uuid.uuid4())
        user_id = _get_user_id(request)
        stream_request_id = uuid.uuid4().hex[:12]

        # Enforce transcription quota before provisioning provider resources.
        # x402/subscription auth already ran via decorator; this check enforces
        # rolling-window plan limits for authenticated user sessions.
        user = request.get('user')
        if user and user_id:
            from payments.quotas import check_quota
            from supabase_client import async_supabase as supabase

            tier = 'free'
            try:
                sub_result = await (
                    supabase.table('subscriptions')
                    .select('plan,status')
                    .eq('user_id', user_id)
                    .execute()
                )
                if sub_result.data and sub_result.data[0].get('status') in ('active', 'trialing'):
                    tier = sub_result.data[0].get('plan', 'free')
            except Exception as sub_exc:
                logger.warning(
                    "Could not determine subscription tier for stream quota check: user_id=%s error=%s",
                    user_id,
                    sub_exc,
                )

            allowed, quota_info = await check_quota(user_id, 'transcribe_gpu', tier)
            if not allowed:
                return web.json_response({
                    'error': 'Transcription quota exceeded for current plan',
                    'code': 'quota_exceeded',
                    'service_type': 'transcribe_gpu',
                    'tier': tier,
                    'quota': quota_info,
                }, status=402)

        # Get ranked list of providers for failover
        ranked_providers = compute_provider_manager.select_providers(
            job_type="transcribe_stream", requirements={"language": language}
        )
        logger.info(
            "Transcribe stream provider selection: request_id=%s session_id=%s language=%s providers=%s",
            stream_request_id,
            session_id,
            language,
            [provider.provider_name for provider in ranked_providers],
        )
        if not ranked_providers:
            return web.json_response({"error": "No compute provider available"}, status=503)

        # Try providers in order until one returns a valid session with whip_url
        session_result = None
        last_error = None
        for provider in ranked_providers:
            try:
                logger.info(
                    "Starting provider stream session request: request_id=%s provider=%s session_id=%s language=%s",
                    stream_request_id,
                    provider.provider_name,
                    session_id,
                    language,
                )
                session_result = await provider.create_streaming_session(
                    session_id=session_id,
                    language=language,
                    stream_request_id=stream_request_id,
                    live_transcription_enabled=live_transcription_enabled,
                    live_translation_enabled=live_translation_enabled,
                    source_language=source_language,
                    target_language=target_language,
                    analysis_enabled=analysis_enabled,
                    analysis_mode=analysis_mode,
                    analysis_audio_chunk_seconds=analysis_audio_chunk_seconds,
                    analysis_video_fps=analysis_video_fps,
                    analysis_prompt=analysis_prompt,
                )
                if _is_valid_streaming_session(session_result):
                    logger.info(
                        "Provider stream session ready: request_id=%s provider=%s session_id=%s provider_stream_id=%s",
                        stream_request_id,
                        provider.provider_name,
                        session_id,
                        session_result.get("provider_stream_id"),
                    )
                    break
                else:
                    response_keys = sorted(list(session_result.keys())) if isinstance(session_result, dict) else []
                    logger.warning(
                        "Provider stream session missing whip_url: request_id=%s provider=%s session_id=%s response_keys=%s",
                        stream_request_id,
                        provider.provider_name,
                        session_id,
                        response_keys,
                    )
                    session_result = None
            except Exception as e:
                logger.warning(
                    "Provider stream session request failed: request_id=%s provider=%s session_id=%s language=%s error=%s",
                    stream_request_id,
                    provider.provider_name,
                    session_id,
                    language,
                    e,
                )
                last_error = e
                session_result = None

        if not session_result:
            error_msg = (
                f"All providers failed to return a valid streaming session. Last error: {last_error}"
                if last_error
                else "All providers returned invalid streaming sessions (missing whip_url)"
            )
            logger.error(error_msg)
            return web.json_response({"error": error_msg}, status=503)

        from sessions import session_store
        try:
            stream_id = await session_store.create_stream_session(
                session_id=session_id,
                language=language,
                provider_session_data=session_result,
                user_id=user_id,
                live_transcription_enabled=live_transcription_enabled,
                live_translation_enabled=live_translation_enabled,
                source_language=source_language,
                target_language=target_language,
                analysis_enabled=analysis_enabled,
                analysis_mode=analysis_mode,
                analysis_audio_chunk_seconds=analysis_audio_chunk_seconds,
                analysis_video_fps=analysis_video_fps,
                analysis_prompt=analysis_prompt,
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=403)

        await session_store.add_stream_to_session(session_id, stream_id)

        return web.json_response({
            "session_id": session_id,
            "stream_id": stream_id,
            "status": "active",
            "message": "Streaming session created successfully",
            "data_url": session_result.get("data_url"),
            "update_url": session_result.get("update_url"),
            "stop_url": session_result.get("stop_url"),
            "provider_stream_id": session_result.get("provider_stream_id"),
            "provider": session_result.get("provider"),
            "live_transcription_enabled": live_transcription_enabled,
            "live_translation_enabled": live_translation_enabled,
            "analysis_enabled": analysis_enabled,
            "analysis_mode": analysis_mode,
            "analysis_audio_chunk_seconds": analysis_audio_chunk_seconds,
            "analysis_video_fps": analysis_video_fps,
            "analysis_prompt": analysis_prompt,
        })

    except Exception as e:
        logger.error(f"Error in transcribe_stream: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


async def update_stream_translation(request):
    """Update live translation settings for an active transcription stream."""
    stream_id = request.match_info.get('stream_id')
    if not stream_id:
        return web.json_response({"error": "Stream ID required"}, status=400)

    try:
        data = await request.json()
    except Exception:
        data = {}

    from sessions import session_store, _verify_stream_ownership
    from sse_relay import get_relay

    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        return web.json_response({"error": "Stream session not found"}, status=404)

    if not await _verify_stream_ownership(request, stream_id):
        return web.json_response({"error": "Access denied"}, status=403)

    source_language = (
        data.get('source_language')
        or stream_session.get('source_language')
        or stream_session.get('language')
        or 'en'
    )
    target_language = data.get('target_language') or None

    logger.info(
        "Translation config update requested: stream_id=%s source_language=%s target_language=%s",
        stream_id,
        source_language,
        target_language,
    )

    updated_session = await session_store.update_stream_translation_config(
        stream_id=stream_id,
        source_language=source_language,
        target_language=target_language,
    )

    relay = get_relay(stream_id)
    if relay:
        logger.info("Clearing translation callback before reconfiguration: stream_id=%s", stream_id)
        relay.set_translation_callback(None)

        provider_session = (updated_session or stream_session).get('provider_session', {})
        metadata = provider_session.get('metadata') if isinstance(provider_session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        provider_name = provider_session.get('provider') if isinstance(provider_session, dict) else None
        provider_stream_id = provider_session.get('provider_stream_id') if isinstance(provider_session, dict) else None
        effective_source_language = (
            (updated_session or stream_session).get('source_language')
            or (provider_session.get('source_language') if isinstance(provider_session, dict) else None)
            or metadata.get('source_language')
            or (updated_session or stream_session).get('language')
            or 'en'
        )
        effective_target_language = (
            (updated_session or stream_session).get('target_language')
            or (provider_session.get('target_language') if isinstance(provider_session, dict) else None)
            or metadata.get('target_language')
        )

        if effective_target_language and provider_name and provider_stream_id:
            provider = compute_provider_manager.get_provider(provider_name)
            update_streaming_session: Optional[Callable[..., Awaitable[Any]]] = (
                getattr(provider, 'update_streaming_session', None) if provider else None
            )
            if callable(update_streaming_session):
                async def _translate_sentence(sentence: str):
                    try:
                        await update_streaming_session(
                            provider_stream_id=provider_stream_id,
                            params={
                                'translate_sentence': sentence,
                                'source_language': effective_source_language,
                                'target_language': effective_target_language,
                            },
                            capability='live-transcription',
                            timeout_seconds=30,
                        )
                    except Exception as exc:
                        logger.warning(
                            'Translation request failed for stream %s: %s',
                            stream_id,
                            exc,
                        )

                relay.set_translation_callback(_translate_sentence)
                logger.info(
                    "Translation callback reconfigured: stream_id=%s provider=%s provider_stream_id=%s source_language=%s target_language=%s",
                    stream_id,
                    provider_name,
                    provider_stream_id,
                    effective_source_language,
                    effective_target_language,
                )
            else:
                logger.warning(
                    "Unable to reconfigure translation callback: stream_id=%s provider=%s has_update_streaming_session=%s",
                    stream_id,
                    provider_name,
                    False,
                )
        else:
            logger.warning(
                "Translation callback remains disabled after update: stream_id=%s effective_target_language=%s provider=%s provider_stream_id=%s",
                stream_id,
                bool(effective_target_language),
                bool(provider_name),
                bool(provider_stream_id),
            )

    return web.json_response({
        'stream_id': stream_id,
        'source_language': source_language,
        'target_language': target_language,
        'translation_enabled': bool(target_language),
        'message': 'Stream translation configuration updated',
    })


async def update_stream_analysis(request):
    """Update live analysis settings for an active transcription stream."""
    stream_id = request.match_info.get('stream_id')
    if not stream_id:
        return web.json_response({"error": "Stream ID required"}, status=400)

    try:
        data = await request.json()
    except Exception:
        data = {}

    from sessions import session_store, _verify_stream_ownership

    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        return web.json_response({"error": "Stream session not found"}, status=404)

    if not await _verify_stream_ownership(request, stream_id):
        return web.json_response({"error": "Access denied"}, status=403)

    analysis_enabled = bool(data.get('analysis_enabled', False))
    analysis_mode = str(data.get('analysis_mode') or stream_session.get('analysis_mode') or 'multimodal')
    if analysis_mode not in {'multimodal', 'audio_only', 'video_only'}:
        return web.json_response({"error": "Invalid analysis_mode"}, status=400)

    try:
        analysis_audio_chunk_seconds = float(
            data.get('analysis_audio_chunk_seconds', stream_session.get('analysis_audio_chunk_seconds', 1.0))
        )
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid analysis_audio_chunk_seconds"}, status=400)

    try:
        analysis_video_fps = int(data.get('analysis_video_fps', stream_session.get('analysis_video_fps', 3)))
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid analysis_video_fps"}, status=400)

    analysis_prompt = data.get('analysis_prompt')
    if analysis_prompt is None:
        analysis_prompt = stream_session.get('analysis_prompt')
    if analysis_prompt is not None:
        analysis_prompt = str(analysis_prompt).strip() or None

    updated_session = await session_store.update_stream_analysis_config(
        stream_id=stream_id,
        analysis_enabled=analysis_enabled,
        analysis_mode=analysis_mode,
        analysis_audio_chunk_seconds=analysis_audio_chunk_seconds,
        analysis_video_fps=analysis_video_fps,
        analysis_prompt=analysis_prompt,
    )

    provider_session = (updated_session or stream_session).get('provider_session', {})
    provider_name = provider_session.get('provider') if isinstance(provider_session, dict) else None
    provider_stream_id = provider_session.get('provider_stream_id') if isinstance(provider_session, dict) else None

    if provider_name and provider_stream_id:
        provider = compute_provider_manager.get_provider(provider_name)
        update_streaming_session: Optional[Callable[..., Awaitable[Any]]] = (
            getattr(provider, 'update_streaming_session', None) if provider else None
        )
        if callable(update_streaming_session):
            try:
                await update_streaming_session(
                    provider_stream_id=provider_stream_id,
                    params={
                        'analysis_enabled': analysis_enabled,
                        'analysis_mode': analysis_mode,
                        'analysis_audio_chunk_seconds': analysis_audio_chunk_seconds,
                        'analysis_video_fps': analysis_video_fps,
                        'analysis_prompt': analysis_prompt,
                    },
                    capability='live-transcription',
                    timeout_seconds=30,
                )
            except Exception as exc:
                logger.warning(
                    'Analysis config update failed for stream %s: %s',
                    stream_id,
                    exc,
                )

    return web.json_response({
        'stream_id': stream_id,
        'analysis_enabled': analysis_enabled,
        'analysis_mode': analysis_mode,
        'analysis_audio_chunk_seconds': analysis_audio_chunk_seconds,
        'analysis_video_fps': analysis_video_fps,
        'analysis_prompt': analysis_prompt,
        'message': 'Stream analysis configuration updated',
    })


@x402_or_subscription(service_type='transcribe_gpu')
async def whip_proxy(request):
    """Proxy WHIP SDP offer/answer through the backend."""
    stream_id = request.match_info.get('stream_id')
    if not stream_id:
        return web.json_response({"error": "Missing stream_id in URL path"}, status=400)

    from sessions import session_store
    stream_session = await session_store.get_stream_session(stream_id)
    if not stream_session:
        return web.json_response({
            "error": f"Stream session '{stream_id}' not found. Create one via POST /api/v1/transcribe/stream first."
        }, status=404)

    provider_session = stream_session.get("provider_session", {})
    provider_whip_url = provider_session.get("whip_url")
    if not provider_whip_url:
        return web.json_response({
            "error": f"No WHIP URL available for stream session '{stream_id}'."
        }, status=503)

    sdp_offer = await request.text()
    has_audio_mline = "m=audio" in sdp_offer
    has_video_mline = "m=video" in sdp_offer
    video_direction = "unknown"
    if "m=video" in sdp_offer:
        video_index = sdp_offer.find("m=video")
        video_block = sdp_offer[video_index:]
        for direction in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"):
            if direction in video_block:
                video_direction = direction
                break

    logger.info(
        "WHIP proxy: forwarding SDP offer for stream %s (audio_mline=%s video_mline=%s video_direction=%s)",
        stream_id,
        has_audio_mline,
        has_video_mline,
        video_direction,
    )

    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                provider_whip_url, data=sdp_offer,
                headers={"Content-Type": "application/sdp"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as provider_response:
                if provider_response.status not in (200, 201):
                    error_text = await provider_response.text()
                    return web.json_response({
                        "error": f"Provider WHIP endpoint returned {provider_response.status}",
                        "details": error_text[:500]
                    }, status=provider_response.status)

                sdp_answer = await provider_response.text()
                return web.Response(text=sdp_answer, content_type="application/sdp", status=provider_response.status)

    except aiohttp.ClientError as e:
        return web.json_response({"error": f"Failed to connect to provider WHIP endpoint: {str(e)}"}, status=502)
    except asyncio.TimeoutError:
        return web.json_response({"error": "Provider WHIP endpoint timed out"}, status=504)
    except Exception as e:
        return web.json_response({"error": f"WHIP proxy error: {str(e)}"}, status=500)


# ============================================================================
# LIST / GET / DELETE TRANSCRIPTIONS (user-scoped)
# ============================================================================

@require_user_auth
async def list_transcriptions(request):
    """List transcriptions for the authenticated user."""
    logger.info("Received list transcriptions request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        limit = int(request.query.get('limit', '100'))
        offset = int(request.query.get('offset', '0'))
        source_type = request.query.get('source_type')

        query = supabase.table('transcriptions').select('*').eq('user_id', user_id)
        if source_type:
            query = query.eq('source_type', source_type)
        result = await query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()

        return web.json_response({
            "transcriptions": result.data if hasattr(result, 'data') else result,
            "count": len(result.data) if hasattr(result, 'data') else 0,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Error listing transcriptions: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def get_transcription(request):
    """Get a specific transcription by ID (user must own it)."""
    logger.info("Received get transcription request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        transcription_id = request.match_info.get('id')
        if not transcription_id:
            return web.json_response({"error": "Missing transcription ID"}, status=400)

        result = await supabase.table('transcriptions').select('*').eq('id', transcription_id).eq('user_id', user_id).execute()

        if not result.data:
            return web.json_response({"error": "Transcription not found"}, status=404)

        return web.json_response(result.data[0])

    except Exception as e:
        logger.error(f"Error getting transcription: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def delete_transcription(request):
    """Delete a transcription by ID (user must own it)."""
    logger.info("Received delete transcription request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        transcription_id = request.match_info.get('id')
        if not transcription_id:
            return web.json_response({"error": "Missing transcription ID"}, status=400)

        result = await supabase.table('transcriptions').delete().eq('id', transcription_id).eq('user_id', user_id).execute()

        if not result.data:
            return web.json_response({"error": "Transcription not found"}, status=404)

        return web.json_response({
            "message": "Transcription deleted successfully",
            "transcription_id": transcription_id
        })

    except Exception as e:
        logger.error(f"Error deleting transcription: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@no_auth
async def transcribe_health_check(request):
    """Health check for transcription service."""
    logger.info("Received transcription health check request")

    try:
        provider_health = {}
        for name in compute_provider_manager.list_providers():
            provider = compute_provider_manager.get_provider(name)
            if provider:
                provider_health[name] = {
                    "status": "healthy" if provider.enabled else "disabled",
                    "provider": name
                }
            else:
                provider_health[name] = {"status": "unknown", "provider": name}

        supabase_status = "unknown"
        try:
            if supabase:
                await supabase.table('agents').select('id').limit(1).execute()
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
        return web.json_response({"error": str(e), "status": "error"}, status=500)
