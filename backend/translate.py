#!/usr/bin/env python3
"""
Translation Endpoints
Handles text translation and transcription translation API routes.
"""

import aiohttp.web as web
import logging

from auth import require_user_auth
from supabase_client import async_supabase as supabase

logger = logging.getLogger(__name__)

def setup_routes(app):
    """Setup translation-related routes."""
    # Translation history CRUD
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

        result = await supabase.table('translations').select('*').eq('user_id', user_id).order('created_at', desc=True).range(offset, offset + limit - 1).execute()

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

        result = await supabase.table('translations').select('*').eq('id', translation_id).eq('user_id', user_id).execute()

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

        result = await supabase.table('translations').delete().eq('id', translation_id).eq('user_id', user_id).execute()

        if not result.data:
            return web.json_response({"error": "Translation not found"}, status=404)

        return web.json_response({
            "message": "Translation deleted successfully",
            "translation_id": translation_id
        })

    except Exception as e:
        logger.error(f"Error deleting translation: {e}")
        return web.json_response({"error": str(e), "status": "error"}, status=500)
