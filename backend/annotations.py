#!/usr/bin/env python3
"""
Sentence Annotations API
CRUD endpoints for notes and todos attached to individual transcription sentences.
"""

import logging
import uuid
from aiohttp import web

from auth import require_user_auth
from supabase_client import async_supabase as supabase

logger = logging.getLogger(__name__)


def setup_routes(app):
    """Setup annotation-related routes."""
    app.router.add_get('/api/v1/transcriptions/{id}/annotations', list_annotations)
    app.router.add_post('/api/v1/transcriptions/{id}/annotations', create_annotation)
    app.router.add_put('/api/v1/annotations/{annotation_id}', update_annotation)
    app.router.add_delete('/api/v1/annotations/{annotation_id}', delete_annotation)


def _get_user_id(request):
    """Extract user_id from authenticated request."""
    user = request.get('user')
    if user:
        return str(user.id) if hasattr(user, 'id') else str(user.get('id', ''))
    return None


async def _verify_transcription_ownership(transcription_id: str, user_id: str) -> bool:
    """Return True if the transcription belongs to the user."""
    try:
        result = await supabase.table('transcriptions').select('id').eq('id', transcription_id).eq('user_id', user_id).execute()
        return bool(result.data)
    except Exception as e:
        logger.warning(f"Failed to verify transcription ownership: {e}")
        return False


async def _verify_annotation_ownership(annotation_id: str, user_id: str) -> bool:
    """Return True if the annotation's transcription belongs to the user."""
    try:
        result = await supabase.table('sentence_annotations').select('transcription_id').eq('id', annotation_id).execute()
        if not result.data:
            return False
        transcription_id = result.data[0]['transcription_id']
        return await _verify_transcription_ownership(transcription_id, user_id)
    except Exception as e:
        logger.warning(f"Failed to verify annotation ownership: {e}")
        return False


@require_user_auth
async def list_annotations(request):
    """GET /api/v1/transcriptions/{id}/annotations

    List all annotations for a transcription.
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    transcription_id = request.match_info.get('id')
    if not transcription_id:
        return web.json_response({'error': 'Missing transcription ID'}, status=400)

    if not await _verify_transcription_ownership(transcription_id, user_id):
        return web.json_response({'error': 'Transcription not found or access denied'}, status=404)

    try:
        result = await supabase.table('sentence_annotations').select('*').eq('transcription_id', transcription_id).order('sentence_index').order('created_at').execute()
        annotations = result.data or []
        return web.json_response({'annotations': annotations})
    except Exception as e:
        logger.error(f"Error listing annotations: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def create_annotation(request):
    """POST /api/v1/transcriptions/{id}/annotations

    Create a new annotation for a sentence.
    Request body:
      - sentence_index: int (required)
      - sentence_text: str (required)
      - sentence_timestamp: str (optional)
      - type: 'note' | 'todo' (required)
      - content: str (required)
      - completed: bool (optional, default false)
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    transcription_id = request.match_info.get('id')
    if not transcription_id:
        return web.json_response({'error': 'Missing transcription ID'}, status=400)

    if not await _verify_transcription_ownership(transcription_id, user_id):
        return web.json_response({'error': 'Transcription not found or access denied'}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON body'}, status=400)

    sentence_index = body.get('sentence_index')
    sentence_text = body.get('sentence_text')
    annotation_type = body.get('type')
    content = body.get('content')

    if sentence_index is None or not isinstance(sentence_index, int):
        return web.json_response({'error': 'sentence_index is required and must be an integer'}, status=400)
    if not sentence_text or not isinstance(sentence_text, str):
        return web.json_response({'error': 'sentence_text is required and must be a string'}, status=400)
    if annotation_type not in ('note', 'todo'):
        return web.json_response({'error': "type must be 'note' or 'todo'"}, status=400)
    if not content or not isinstance(content, str):
        return web.json_response({'error': 'content is required and must be a string'}, status=400)

    try:
        result = await supabase.table('sentence_annotations').insert({
            'transcription_id': transcription_id,
            'sentence_index': sentence_index,
            'sentence_text': sentence_text,
            'sentence_timestamp': body.get('sentence_timestamp'),
            'type': annotation_type,
            'content': content,
            'completed': bool(body.get('completed', False)),
        }).execute()

        annotation = result.data[0] if result.data else None
        return web.json_response(annotation, status=201)
    except Exception as e:
        logger.error(f"Error creating annotation: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def update_annotation(request):
    """PUT /api/v1/annotations/{annotation_id}

    Update an annotation's content or completed status.
    Request body:
      - content: str (optional)
      - completed: bool (optional)
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    annotation_id = request.match_info.get('annotation_id')
    if not annotation_id:
        return web.json_response({'error': 'Missing annotation ID'}, status=400)

    if not await _verify_annotation_ownership(annotation_id, user_id):
        return web.json_response({'error': 'Annotation not found or access denied'}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON body'}, status=400)

    update_data = {}
    if 'content' in body and isinstance(body['content'], str):
        update_data['content'] = body['content']
    if 'completed' in body:
        update_data['completed'] = bool(body['completed'])

    if not update_data:
        return web.json_response({'error': 'No valid fields to update'}, status=400)

    try:
        result = await supabase.table('sentence_annotations').update(update_data).eq('id', annotation_id).execute()
        annotation = result.data[0] if result.data else None
        if not annotation:
            return web.json_response({'error': 'Annotation not found'}, status=404)
        return web.json_response(annotation)
    except Exception as e:
        logger.error(f"Error updating annotation: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def delete_annotation(request):
    """DELETE /api/v1/annotations/{annotation_id}

    Delete an annotation.
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    annotation_id = request.match_info.get('annotation_id')
    if not annotation_id:
        return web.json_response({'error': 'Missing annotation ID'}, status=400)

    if not await _verify_annotation_ownership(annotation_id, user_id):
        return web.json_response({'error': 'Annotation not found or access denied'}, status=404)

    try:
        await supabase.table('sentence_annotations').delete().eq('id', annotation_id).execute()
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Error deleting annotation: {e}")
        return web.json_response({'error': str(e)}, status=500)
