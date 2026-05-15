#!/usr/bin/env python3
"""
Transcription Service Endpoints
Handles all transcription and translation related API routes.
"""

import asyncio
import aiohttp
import aiohttp.web as web
import logging
import uuid
import json
from typing import Any, Awaitable, Callable, Optional

from auth import require_user_auth, track_usage
from payments.payment_strategy import x402_or_subscription
from supabase_client import async_supabase as supabase
from compute_providers.provider_manager import ComputeProviderManager
from compute_providers.livepeer.livepeer import LivepeerComputeProvider
from agents import agent_register, agent_get_usage, agent_list_keys, agent_create_key, agent_revoke_key, agent_get_subscription, agent_create_subscription, agent_delete_subscription, agent_reactivate_subscription
from languages import get_languages

logger = logging.getLogger(__name__)

# Import compute provider definitions
from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

# Initialize compute provider manager (same as in main.py)
compute_provider_manager = ComputeProviderManager()

# Register providers from definitions
compute_provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)

ANALYSIS_WINDOW_MIN_SECONDS = 1.0
ANALYSIS_WINDOW_MAX_SECONDS = 30.0
ANALYSIS_WINDOW_DEFAULT_SECONDS = 10.0


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


def _clamp_analysis_window_seconds(value: Any, default: float = ANALYSIS_WINDOW_DEFAULT_SECONDS) -> float:
    """Parse and clamp analysis window values into [1, 30] seconds."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    return max(ANALYSIS_WINDOW_MIN_SECONDS, min(ANALYSIS_WINDOW_MAX_SECONDS, numeric))


def setup_routes(app):
    """Setup transcription-related routes."""
    # Streaming transcription endpoints
    app.router.add_post('/api/v1/stream/process', transcribe_stream)
    app.router.add_put('/api/v1/stream/{stream_id}/translation', update_stream_translation)
    app.router.add_put('/api/v1/stream/{stream_id}/analysis', update_stream_analysis)
    app.router.add_post('/api/v1/stream/{stream_id}/whip', whip_proxy)
    app.router.add_get('/api/v1/streams', list_streams)
    app.router.add_get('/api/v1/streams/{id}', get_stream)
    app.router.add_delete('/api/v1/streams/{id}', delete_stream)

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
        analysis_audio_chunk_seconds = _clamp_analysis_window_seconds(
            data.get('analysis_audio_chunk_seconds', ANALYSIS_WINDOW_DEFAULT_SECONDS)
        )
        analysis_video_chunk_seconds = _clamp_analysis_window_seconds(
            data.get('analysis_video_chunk_seconds', ANALYSIS_WINDOW_DEFAULT_SECONDS)
        )
        analysis_video_fps = int(data.get('analysis_video_fps') or 3)
        analysis_prompt = data.get('analysis_prompt')
        if analysis_prompt is not None:
            analysis_prompt = str(analysis_prompt).strip() or None
        analysis_response_format = data.get('analysis_response_format')
        if analysis_response_format is not None:
            if not isinstance(analysis_response_format, dict):
                return web.json_response({
                    'error': 'analysis_response_format must be a JSON object (e.g., {"type": "json_object", "schema": {...}})',
                    'code': 'invalid_response_format',
                }, status=400)

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
                    analysis_video_chunk_seconds=analysis_video_chunk_seconds,
                    analysis_video_fps=analysis_video_fps,
                    analysis_prompt=analysis_prompt,
                    analysis_response_format=analysis_response_format,
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
                analysis_video_chunk_seconds=analysis_video_chunk_seconds,
                analysis_video_fps=analysis_video_fps,
                analysis_prompt=analysis_prompt,
                analysis_response_format=analysis_response_format,
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
            "analysis_video_chunk_seconds": analysis_video_chunk_seconds,
            "analysis_video_fps": analysis_video_fps,
            "analysis_prompt": analysis_prompt,
            "analysis_response_format": analysis_response_format,
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
        analysis_audio_chunk_seconds = _clamp_analysis_window_seconds(
            data.get('analysis_audio_chunk_seconds', stream_session.get('analysis_audio_chunk_seconds', ANALYSIS_WINDOW_DEFAULT_SECONDS))
        )
    except Exception:
        return web.json_response({"error": "Invalid analysis_audio_chunk_seconds"}, status=400)

    try:
        analysis_video_chunk_seconds = _clamp_analysis_window_seconds(
            data.get('analysis_video_chunk_seconds', stream_session.get('analysis_video_chunk_seconds', ANALYSIS_WINDOW_DEFAULT_SECONDS))
        )
    except Exception:
        return web.json_response({"error": "Invalid analysis_video_chunk_seconds"}, status=400)

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
        analysis_video_chunk_seconds=analysis_video_chunk_seconds,
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
                        'analysis_video_chunk_seconds': analysis_video_chunk_seconds,
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
        'analysis_video_chunk_seconds': analysis_video_chunk_seconds,
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
            "error": f"Stream session '{stream_id}' not found. Create one via POST /api/v1/stream/process first."
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
# LIST / GET / DELETE STREAMS (user-scoped)
# ============================================================================

async def _get_user_session_ids(user_id: str) -> list[str]:
    result = await supabase.table('user_sessions').select('id').eq('user_id', user_id).execute()
    return [str(row.get('id')) for row in (result.data or []) if row.get('id')]


async def _build_stream_items(user_id: str, stream_rows: list[dict]) -> list[dict]:
    stream_ids = [str(row.get('id')) for row in stream_rows if row.get('id')]
    if not stream_ids:
        return []

    transcriptions_result = await (
        supabase.table('transcriptions')
        .select('*')
        .eq('user_id', user_id)
        .in_('stream_session_id', stream_ids)
        .execute()
    )
    transcription_by_stream: dict[str, dict] = {
        str(row.get('stream_session_id')): row
        for row in (transcriptions_result.data or [])
        if row.get('stream_session_id')
    }

    translations_result = await (
        supabase.table('translations')
        .select('stream_session_id, target_language')
        .eq('user_id', user_id)
        .in_('stream_session_id', stream_ids)
        .execute()
    )
    translated_languages_by_stream: dict[str, set[str]] = {}
    for row in (translations_result.data or []):
        stream_id = row.get('stream_session_id')
        lang = row.get('target_language')
        if not stream_id or not lang:
            continue
        translated_languages_by_stream.setdefault(str(stream_id), set()).add(lang)

    analysis_result = await (
        supabase.table('stream_analysis')
        .select('stream_session_id, analysis_mode, created_at')
        .eq('user_id', user_id)
        .in_('stream_session_id', stream_ids)
        .order('created_at', desc=True)
        .execute()
    )
    latest_analysis_by_stream: dict[str, dict] = {}
    for row in (analysis_result.data or []):
        stream_id = row.get('stream_session_id')
        if not stream_id:
            continue
        key = str(stream_id)
        if key not in latest_analysis_by_stream:
            latest_analysis_by_stream[key] = row

    items: list[dict] = []
    for stream_row in stream_rows:
        stream_id = str(stream_row.get('id'))
        transcription = transcription_by_stream.get(stream_id, {})
        analysis = latest_analysis_by_stream.get(stream_id)

        items.append({
            'id': stream_id,
            '_type': 'stream',
            'stream_id': stream_id,
            'stream_session_id': stream_id,
            'session_id': stream_row.get('user_session_id'),
            'status': stream_row.get('status'),
            'created_at': stream_row.get('created_at'),
            'updated_at': stream_row.get('updated_at'),
            'language': transcription.get('language') or stream_row.get('language'),
            'source_language': transcription.get('language') or stream_row.get('language'),
            'text': transcription.get('text') or stream_row.get('final_text') or '',
            'duration': transcription.get('duration') or 0,
            'word_count': transcription.get('word_count') or 0,
            'total_audio_bytes': stream_row.get('total_audio_bytes') or 0,
            'transcription_id': transcription.get('id'),
            'translated_languages': sorted(list(translated_languages_by_stream.get(stream_id, set()))),
            'has_analysis': bool(analysis),
            'analysis_mode': analysis.get('analysis_mode') if analysis else None,
        })

    return items


@require_user_auth
async def list_streams(request):
    """List stream sessions for the authenticated user."""
    logger.info("Received list streams request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        limit = int(request.query.get('limit', '100'))
        offset = int(request.query.get('offset', '0'))

        session_ids = await _get_user_session_ids(user_id)
        if not session_ids:
            return web.json_response({"streams": [], "count": 0, "limit": limit, "offset": offset})

        stream_result = await (
            supabase.table('stream_sessions')
            .select('*')
            .in_('user_session_id', session_ids)
            .order('created_at', desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        streams = stream_result.data or []
        items = await _build_stream_items(user_id, streams)

        return web.json_response({
            'streams': items,
            'count': len(items),
            'limit': limit,
            'offset': offset,
        })
    except Exception as e:
        logger.error(f"Error listing streams: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def get_stream(request):
    """Get a specific stream by ID (user must own it)."""
    logger.info("Received get stream request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        stream_id = request.match_info.get('id')
        if not stream_id:
            return web.json_response({"error": "Missing stream ID"}, status=400)

        session_ids = await _get_user_session_ids(user_id)
        if not session_ids:
            return web.json_response({"error": "Stream not found"}, status=404)

        stream_result = await (
            supabase.table('stream_sessions')
            .select('*')
            .eq('id', stream_id)
            .in_('user_session_id', session_ids)
            .limit(1)
            .execute()
        )
        if not stream_result.data:
            return web.json_response({"error": "Stream not found"}, status=404)

        stream_item = (await _build_stream_items(user_id, [stream_result.data[0]]))[0]
        return web.json_response(stream_item)
    except Exception as e:
        logger.error(f"Error getting stream: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def delete_stream(request):
    """Delete a stream by ID (user must own it)."""
    logger.info("Received delete stream request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        stream_id = request.match_info.get('id')
        if not stream_id:
            return web.json_response({"error": "Missing stream ID"}, status=400)

        session_ids = await _get_user_session_ids(user_id)
        if not session_ids:
            return web.json_response({"error": "Stream not found"}, status=404)

        stream_lookup = await (
            supabase.table('stream_sessions')
            .select('id')
            .eq('id', stream_id)
            .in_('user_session_id', session_ids)
            .limit(1)
            .execute()
        )
        if not stream_lookup.data:
            return web.json_response({"error": "Stream not found"}, status=404)

        # Explicit cleanup for transcription headers since transcriptions.stream_session_id
        # may be nullable after stream deletion depending on DB FK mode.
        await supabase.table('transcriptions').delete().eq('user_id', user_id).eq('stream_session_id', stream_id).execute()
        await supabase.table('stream_sessions').delete().eq('id', stream_id).in_('user_session_id', session_ids).execute()

        return web.json_response({
            'message': 'Stream deleted successfully',
            'stream_id': stream_id,
        })
    except Exception as e:
        logger.error(f"Error deleting stream: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)

