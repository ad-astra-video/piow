#!/usr/bin/env python3
"""
Translation Endpoints
Handles text translation and transcription translation API routes.
"""

import aiohttp.web as web
import logging
import json

from auth import no_auth, require_user_auth, track_usage
from payments.payment_strategy import x402_or_subscription
from supabase_client import async_supabase as supabase
from compute_providers.provider_manager import ComputeProviderManager
from compute_providers.livepeer.livepeer import LivepeerComputeProvider

logger = logging.getLogger(__name__)

# Import compute provider definitions
from compute_providers.provider_definitions import PROVIDER_DEFINITIONS

# Initialize compute provider manager (same as in main.py)
compute_provider_manager = ComputeProviderManager()

# Register providers from definitions
compute_provider_manager.register_providers_from_definitions(PROVIDER_DEFINITIONS)

def setup_routes(app):
    """Setup translation-related routes."""
    app.router.add_post('/api/v1/translate/text', translate_text)
    app.router.add_post('/api/v1/translate/transcription', translate_transcription)
    
    # Translation CRUD
    app.router.add_get('/api/v1/translations', list_translations)
    app.router.add_get('/api/v1/translations/{id}', get_translation)
    app.router.add_delete('/api/v1/translations/{id}', delete_translation)


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


def _get_total_text_sent_chars(text):
    """Count total input text characters sent for translation usage accounting."""
    if text is None:
        return 0
    if isinstance(text, str):
        return len(text)
    if isinstance(text, (list, tuple)):
        return sum(_get_total_text_sent_chars(item) for item in text)
    if isinstance(text, dict):
        return sum(_get_total_text_sent_chars(value) for value in text.values())
    return len(str(text))


def _is_successful_translation_result(job_result):
    """Return True when provider result indicates a successful translation."""
    status = str(job_result.get('status', 'completed') or 'completed').strip().lower()
    failure_statuses = {'failed', 'error', 'cancelled', 'canceled'}
    return status not in failure_statuses


# ============================================================================
# HELPER: Store translation result and record usage
# ============================================================================

async def _store_translation_result(request, job_result, original_text, source_language, target_language):
    """
    Store translation result in the database and record usage.
    """
    user_id = _get_user_id(request)
    if not user_id:
        logger.warning("Cannot store translation: no authenticated user")
        return None
    translation_id = None

    try:
        translation_record = supabase.table('translations').insert({
            'user_id': user_id,
            'original_text': original_text,
            'translated_text': job_result.get('translated_text', ''),
            'source_language': job_result.get('source_language', source_language),
            'target_language': job_result.get('target_language', target_language),
            'token_count': job_result.get('token_count', 0) or 0,
            'model_used': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
        }).execute()
        translation_id = translation_record.data[0]['id'] if translation_record.data else None
    except Exception as db_error:
        logger.warning(f"Failed to store translation in database: {db_error}")

    if not translation_id:
        logger.warning("Skipping translation usage log: translation record was not persisted")
        return None

    try:
        total_text_sent_chars = _get_total_text_sent_chars(original_text)
        supabase.table('translation_usage').insert({
            'user_id': user_id,
            'characters_translated': total_text_sent_chars,
            'source_language': job_result.get('source_language', source_language),
            'target_language': job_result.get('target_language', target_language),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
        }).execute()
    except Exception as usage_error:
        logger.warning(f"Failed to record translation usage: {usage_error}")

    return translation_id


# ============================================================================
# TRANSLATION ENDPOINTS
# ============================================================================

@track_usage
@x402_or_subscription(service_type='translate')
async def translate_text(request):
    """Handle text translation."""
    logger.info("Received translate text request")

    try:
        data = await request.json()
        text = data.get('text')
        source_lang = data.get('source_language', 'en')
        target_lang = data.get('target_language', 'es')

        if not text:
            return web.json_response({"error": "Missing text parameter"}, status=400)

        ranked_providers = compute_provider_manager.select_providers(
            job_type="translate",
            requirements={"source_language": source_lang, "target_language": target_lang}
        )
        if not ranked_providers:
            return web.json_response({"error": "No compute provider available"}, status=503)

        job_result = None
        last_error = None
        for provider in ranked_providers:
            try:
                job_result = await provider.create_translation_job(
                    text=text, source_language=source_lang, target_language=target_lang
                )
                break
            except Exception as provider_error:
                logger.warning(
                    "Compute provider error in translate_text: provider=%s error=%s",
                    provider.provider_name,
                    provider_error,
                )
                last_error = provider_error

        if not job_result:
            return web.json_response({
                "error": f"All providers failed for translation. Last error: {str(last_error)}",
                "status": "error"
            }, status=503)

        if not _is_successful_translation_result(job_result):
            return web.json_response({
                "error": "Provider translation job did not complete successfully",
                "status": job_result.get('status', 'error'),
            }, status=502)

        translation_id = await _store_translation_result(
            request, job_result, text, source_lang, target_lang
        )

        return web.json_response({
            'id': translation_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'original_text': job_result.get('original_text', text),
            'translated_text': job_result.get('translated_text', ''),
            'source_language': job_result.get('source_language', source_lang),
            'target_language': job_result.get('target_language', target_lang),
            'token_count': job_result.get('token_count'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in translate_text: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@track_usage
@x402_or_subscription(service_type='translate')
async def translate_transcription(request):
    """Translate an existing transcription by ID."""
    logger.info("Received translate transcription request")

    try:
        data = await request.json()
        transcription_id = data.get('transcription_id')
        target_language = data.get('target_language', 'es')

        if not transcription_id:
            return web.json_response({"error": "Missing transcription_id parameter"}, status=400)

        trans_result = await await supabase.table('transcriptions').select('*').eq('id', transcription_id).execute()

        if not trans_result.data:
            return web.json_response({"error": "Transcription not found"}, status=404)

        transcription = trans_result.data[0]
        original_text = transcription.get('text', '')
        source_language = transcription.get('language', 'en')

        if not original_text:
            return web.json_response({"error": "Transcription has no text to translate"}, status=400)

        ranked_providers = compute_provider_manager.select_providers(
            job_type="translate",
            requirements={"source_language": source_language, "target_language": target_language}
        )
        if not ranked_providers:
            return web.json_response({"error": "No compute provider available"}, status=503)

        job_result = None
        last_error = None
        for provider in ranked_providers:
            try:
                job_result = await provider.create_translation_job(
                    text=original_text,
                    source_language=source_language,
                    target_language=target_language
                )
                break
            except Exception as provider_error:
                logger.warning(
                    "Compute provider error in translate_transcription: provider=%s error=%s",
                    provider.provider_name,
                    provider_error,
                )
                last_error = provider_error

        if not job_result:
            return web.json_response({
                "error": f"All providers failed for translation. Last error: {str(last_error)}",
                "status": "error"
            }, status=503)

        if not _is_successful_translation_result(job_result):
            return web.json_response({
                "error": "Provider translation job did not complete successfully",
                "status": job_result.get('status', 'error'),
            }, status=502)

        translation_id = await _store_translation_result(
            request, job_result, original_text, source_language, target_language
        )

        try:
            await supabase.table('translations').update({'transcription_id': transcription_id}).eq('id', translation_id).execute()
        except Exception as link_error:
            logger.warning(f"Failed to link translation to transcription: {link_error}")

        return web.json_response({
            'id': translation_id,
            'job_id': job_result.get('job_id'),
            'status': job_result.get('status', 'completed'),
            'transcription_id': transcription_id,
            'original_text': job_result.get('original_text', original_text),
            'translated_text': job_result.get('translated_text', ''),
            'source_language': job_result.get('source_language', source_language),
            'target_language': job_result.get('target_language', target_language),
            'token_count': job_result.get('token_count'),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
            'provider': job_result.get('provider', 'unknown'),
        })

    except Exception as e:
        logger.error(f"Error in translate_transcription: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


# ============================================================================
# LIST / GET / DELETE TRANSLATIONS (user-scoped)
# ============================================================================

@require_user_auth
async def list_translations(request):
    """List translations for the authenticated user."""
    logger.info("Received list translations request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        limit = int(request.query.get('limit', '100'))
        offset = int(request.query.get('offset', '0'))

        result = await await supabase.table('translations').select('*').eq('user_id', user_id).order('created_at', desc=True).range(offset, offset + limit - 1).execute()

        return web.json_response({
            "translations": result.data if hasattr(result, 'data') else result,
            "count": len(result.data) if hasattr(result, 'data') else 0,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Error listing translations: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def get_translation(request):
    """Get a specific translation by ID (user must own it)."""
    logger.info("Received get translation request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        translation_id = request.match_info.get('id')
        if not translation_id:
            return web.json_response({"error": "Missing translation ID"}, status=400)

        result = await await supabase.table('translations').select('*').eq('id', translation_id).eq('user_id', user_id).execute()

        if not result.data:
            return web.json_response({"error": "Translation not found"}, status=404)

        return web.json_response(result.data[0])

    except Exception as e:
        logger.error(f"Error getting translation: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)


@require_user_auth
async def delete_translation(request):
    """Delete a translation by ID (user must own it)."""
    logger.info("Received delete translation request")

    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        translation_id = request.match_info.get('id')
        if not translation_id:
            return web.json_response({"error": "Missing translation ID"}, status=400)

        result = await await supabase.table('translations').delete().eq('id', translation_id).eq('user_id', user_id).execute()

        if not result.data:
            return web.json_response({"error": "Translation not found"}, status=404)

        return web.json_response({
            "message": "Translation deleted successfully",
            "translation_id": translation_id
        })

    except Exception as e:
        logger.error(f"Error deleting translation: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)
