#!/usr/bin/env python3
"""
Translation Endpoints
Handles text translation and transcription translation API routes.
"""

import aiohttp.web as web
import logging
import json

from payments.payment_strategy import x402_or_subscription
from supabase_client import supabase
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


# ============================================================================
# HELPER: Store translation result and record usage
# ============================================================================

async def _store_translation_result(request, job_result, original_text, source_language, target_language):
    """
    Store translation result in the database and record usage.
    
    Args:
        request: aiohttp request (for extracting user/agent info)
        job_result: Result dict from compute provider
        original_text: Original text that was translated
        source_language: Source language code
        target_language: Target language code
    
    Returns:
        translation_id or None if storage failed
    """
    user = request.get('user') or request.get('agent')
    user_id = str(user.id) if hasattr(user, 'id') else str(user.get('id', 'unknown'))
    translation_id = None

    # Store translation in database
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

    # Record usage
    try:
        supabase.table('translation_usage').insert({
            'user_id': user_id,
            'characters_translated': len(original_text),
            'source_language': job_result.get('source_language', source_language),
            'target_language': job_result.get('target_language', target_language),
            'model': job_result.get('model', 'unknown'),
            'hardware': job_result.get('hardware', 'unknown'),
        }).execute()
    except Exception as usage_error:
        logger.warning(f"Failed to record translation usage: {usage_error}")

    return translation_id


@x402_or_subscription(service_type='translate')
async def translate_text(request):
    """
    Handle text translation.

    Accepts JSON body with:
    - text: Text to translate (required)
    - source_language: Source language code (default: en)
    - target_language: Target language code (default: es)
    """
    logger.info("Received translate text request")

    try:
        data = await request.json()

        text = data.get('text')
        source_lang = data.get('source_language', 'en')
        target_lang = data.get('target_language', 'es')

        if not text:
            return web.json_response({
                "error": "Missing text parameter"
            }, status=400)

        # Use compute provider to process the translation
        provider = compute_provider_manager.select_provider(
            job_type="translate",
            requirements={
                "source_language": source_lang,
                "target_language": target_lang
            }
        )
        if not provider:
            return web.json_response({
                "error": "No compute provider available"
            }, status=503)

        # Create translation job using the compute provider
        try:
            job_result = await provider.create_translation_job(
                text=text,
                source_language=source_lang,
                target_language=target_lang
            )
        except Exception as provider_error:
            logger.error(f"Compute provider error in translate_text: {provider_error}")
            return web.json_response({
                "error": f"Translation failed: {str(provider_error)}",
                "status": "error"
            }, status=502)

        # Store translation result and record usage
        translation_id = await _store_translation_result(
            request, job_result, text, source_lang, target_lang
        )

        # Return the real result from the compute provider
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
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)


@x402_or_subscription(service_type='translate')
async def translate_transcription(request):
    """
    Translate an existing transcription by ID.

    Accepts JSON body with:
    - transcription_id: UUID of the transcription to translate (required)
    - target_language: Target language code (default: es)
    """
    logger.info("Received translate transcription request")

    try:
        data = await request.json()

        transcription_id = data.get('transcription_id')
        target_language = data.get('target_language', 'es')

        if not transcription_id:
            return web.json_response({
                "error": "Missing transcription_id parameter"
            }, status=400)

        # Get the original transcription
        trans_result = supabase.table('transcriptions').select('*').eq('id', transcription_id).execute()

        if not trans_result.data:
            return web.json_response({
                "error": "Transcription not found"
            }, status=404)

        transcription = trans_result.data[0]
        original_text = transcription.get('text', '')
        source_language = transcription.get('language', 'en')

        if not original_text:
            return web.json_response({
                "error": "Transcription has no text to translate"
            }, status=400)

        # Use compute provider to process the translation
        provider = compute_provider_manager.select_provider(
            job_type="translate",
            requirements={
                "source_language": source_language,
                "target_language": target_language
            }
        )
        if not provider:
            return web.json_response({
                "error": "No compute provider available"
            }, status=503)

        # Create translation job using the compute provider
        try:
            job_result = await provider.create_translation_job(
                text=original_text,
                source_language=source_language,
                target_language=target_language
            )
        except Exception as provider_error:
            logger.error(f"Compute provider error in translate_transcription: {provider_error}")
            return web.json_response({
                "error": f"Translation failed: {str(provider_error)}",
                "status": "error"
            }, status=502)

        # Store translation result and record usage
        translation_id = await _store_translation_result(
            request, job_result, original_text, source_language, target_language
        )

        # Link translation to the original transcription
        try:
            supabase.table('translations').update({
                'transcription_id': transcription_id,
            }).eq('id', translation_id).execute()
        except Exception as link_error:
            logger.warning(f"Failed to link translation to transcription: {link_error}")

        # Return the real result from the compute provider
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
        return web.json_response({
            "error": str(e),
            "status": "error"
        }, status=500)