#!/usr/bin/env python3
"""
User Routes Module
Provides user-scoped endpoints for profile, history, and detailed usage stats.
All requests go through the backend (no direct Supabase/Stripe from frontend).
"""

import aiohttp.web as web
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from auth import require_user_auth
from supabase_client import async_supabase as supabase

logger = logging.getLogger(__name__)

_USAGE_PAGE_SIZE = 1000


def setup_routes(app):
    """Setup user-related routes."""
    app.router.add_get('/api/v1/user/profile', get_user_profile)
    app.router.add_get('/api/v1/user/history', get_user_history)
    app.router.add_get('/api/v1/user/usage-details', get_usage_details)
    app.router.add_get('/api/v1/transcriptions/{id}/sentences', get_transcription_sentences)


def _get_user_id(request):
    """Extract user_id from authenticated request."""
    user = request.get('user')
    if user:
        return str(user.id) if hasattr(user, 'id') else str(user.get('id', ''))
    return None


async def _fetch_usage_rows_paged(table_name: str, user_id: str, since: str) -> List[Dict[str, Any]]:
    """Fetch all usage rows for a user in pages to avoid Supabase row caps."""
    rows: List[Dict[str, Any]] = []
    page = 0

    while True:
        start = page * _USAGE_PAGE_SIZE
        end = start + _USAGE_PAGE_SIZE - 1
        result = await (
            supabase.table(table_name)
            .select('*')
            .eq('user_id', user_id)
            .gte('created_at', since)
            .order('created_at', desc=True)
            .range(start, end)
            .execute()
        )

        batch = result.data or []
        if not batch:
            break

        rows.extend(batch)
        if len(batch) < _USAGE_PAGE_SIZE:
            break

        page += 1

    return rows


@require_user_auth
async def get_user_profile(request):
    """GET /api/v1/user/profile

    Return the current user's profile and subscription info.
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        # Get user from public.users table
        user_result = await supabase.table('users').select('*').eq('id', user_id).execute()
        user_data = user_result.data[0] if user_result.data else {}

        # Get subscription
        sub_result = await supabase.table('subscriptions').select('*').eq('user_id', user_id).execute()
        subscription = sub_result.data[0] if sub_result.data else None

        # Get preferences
        pref_result = await supabase.table('user_preferences').select('*').eq('user_id', user_id).execute()
        preferences = pref_result.data[0] if pref_result.data else {}

        return web.json_response({
            'user': {
                'id': user_id,
                'email': user_data.get('email'),
                'name': user_data.get('name'),
                'avatar': user_data.get('avatar'),
                'provider': user_data.get('provider'),
                'created_at': user_data.get('created_at'),
            },
            'subscription': subscription,
            'preferences': preferences,
        })

    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_user_history(request):
    """GET /api/v1/user/history

    Return a unified list of transcriptions and translations for the user.
    Query params:
      - type: 'transcription', 'translation', or 'all' (default: all)
      - limit: default 50
      - offset: default 0
      - source_type: filter transcriptions by source_type
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        item_type = request.query.get('type', 'all')
        limit = int(request.query.get('limit', '50'))
        offset = int(request.query.get('offset', '0'))
        source_type = request.query.get('source_type')

        transcriptions = []
        translations = []

        if item_type in ('all', 'transcription'):
            query = supabase.table('transcriptions').select('*').eq('user_id', user_id)
            if source_type:
                query = query.eq('source_type', source_type)
            t_result = await query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
            transcriptions = [
                {**item, '_type': 'transcription'} for item in (t_result.data or [])
            ]

            transcription_ids = [item.get('id') for item in transcriptions if item.get('id')]
            if transcription_ids:
                tr_lang_result = await (
                    supabase.table('translations')
                    .select('transcription_id, target_language')
                    .eq('user_id', user_id)
                    .in_('transcription_id', transcription_ids)
                    .execute()
                )

                langs_by_transcription: Dict[str, set] = {}
                for row in (tr_lang_result.data or []):
                    transcription_id = row.get('transcription_id')
                    target_language = row.get('target_language')
                    if not transcription_id or not target_language:
                        continue
                    langs_by_transcription.setdefault(str(transcription_id), set()).add(target_language)

                for item in transcriptions:
                    item['translated_languages'] = sorted(
                        list(langs_by_transcription.get(str(item.get('id')), set()))
                    )

        if item_type in ('all', 'translation'):
            tr_result = await supabase.table('translations').select('*').eq('user_id', user_id).order('created_at', desc=True).range(offset, offset + limit - 1).execute()
            translations = [
                {**item, '_type': 'translation'} for item in (tr_result.data or [])
            ]

        # Merge and sort by created_at desc
        combined = transcriptions + translations
        combined.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # Re-apply limit after merge
        combined = combined[:limit]

        return web.json_response({
            'items': combined,
            'count': len(combined),
            'limit': limit,
            'offset': offset,
        })

    except Exception as e:
        logger.error(f"Error getting user history: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_transcription_sentences(request):
    """GET /api/v1/transcriptions/{id}/sentences

    Return per-sentence rows for a transcription, ordered by sentence_index.
    Includes translation sentence rows grouped by target language.
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    transcription_id = request.match_info.get('id')
    if not transcription_id:
        return web.json_response({'error': 'Transcription ID required'}, status=400)

    try:
        # Verify ownership
        ownership = await supabase.table('transcriptions').select('id').eq('id', transcription_id).eq('user_id', user_id).execute()
        if not ownership.data:
            return web.json_response({'error': 'Not found'}, status=404)

        result = await supabase.table('transcription_sentences') \
            .select('sentence_index, text, translated_text, timestamp') \
            .eq('transcription_id', transcription_id) \
            .order('sentence_index') \
            .execute()

        base_sentences = result.data or []
        base_by_index = {row.get('sentence_index'): row for row in base_sentences}

        # Fetch all translations in one query and split in Python.
        # This avoids dialect differences around null filter operators.
        all_translations_result = await (
            supabase.table('translations')
            .select('id, original_text, translated_text, target_language, sentence_index, created_at')
            .eq('user_id', user_id)
            .eq('transcription_id', transcription_id)
            .order('created_at')
            .execute()
        )

        # Group translations by language and sentence_index
        translation_index_by_language: Dict[str, Dict[int, Dict[str, Any]]] = {}
        all_translation_rows = all_translations_result.data or []

        # First, consume rows that have explicit sentence_index.
        for row in all_translation_rows:
            language = row.get('target_language')
            translated_text = row.get('translated_text')
            sentence_index = row.get('sentence_index')
            if not language or not translated_text or sentence_index is None:
                continue

            bucket = translation_index_by_language.setdefault(language, {})
            base_sentence = base_by_index.get(sentence_index, {})
            bucket[sentence_index] = {
                'sentence_index': sentence_index,
                'text': row.get('original_text') or base_sentence.get('text') or '',
                'translated_text': translated_text,
                'timestamp': base_sentence.get('timestamp'),
            }

        # Backward compatibility: rows without sentence_index still use legacy
        # text/fallback cursor matching.
        legacy_translation_rows = [
            row for row in all_translation_rows
            if row.get('sentence_index') is None
        ]

        sentence_indices = [row.get('sentence_index') for row in base_sentences]
        text_to_indices: Dict[str, List[int]] = {}
        for row in base_sentences:
            sentence_text = row.get('text')
            sentence_index = row.get('sentence_index')
            if not sentence_text or sentence_index is None:
                continue
            text_to_indices.setdefault(sentence_text, []).append(sentence_index)

        text_match_cursors: Dict[str, Dict[str, int]] = {}
        fallback_cursor_by_language: Dict[str, int] = {}

        for row in legacy_translation_rows:
            language = row.get('target_language')
            translated_text = row.get('translated_text')
            original_text = row.get('original_text')
            if not language or not translated_text:
                continue

            language_cursor = text_match_cursors.setdefault(language, {})
            sentence_index = None

            if isinstance(original_text, str) and original_text in text_to_indices:
                candidate_indices = text_to_indices.get(original_text) or []
                cursor_pos = language_cursor.get(original_text, 0)
                if cursor_pos < len(candidate_indices):
                    sentence_index = candidate_indices[cursor_pos]
                    language_cursor[original_text] = cursor_pos + 1

            if sentence_index is None:
                fallback_pos = fallback_cursor_by_language.get(language, 0)
                if fallback_pos < len(sentence_indices):
                    sentence_index = sentence_indices[fallback_pos]
                    fallback_cursor_by_language[language] = fallback_pos + 1

            if sentence_index is None:
                continue

            bucket = translation_index_by_language.setdefault(language, {})
            bucket[sentence_index] = {
                'sentence_index': sentence_index,
                'text': original_text or base_by_index.get(sentence_index, {}).get('text') or '',
                'translated_text': translated_text,
                'timestamp': base_by_index.get(sentence_index, {}).get('timestamp'),
            }

        translations_by_language = {
            language: sorted(bucket.values(), key=lambda r: r.get('sentence_index', 0))
            for language, bucket in translation_index_by_language.items()
        }

        return web.json_response({
            'sentences': base_sentences,
            'translations_by_language': translations_by_language,
            'translated_languages': sorted(list(translations_by_language.keys())),
        })
    except Exception as e:
        logger.error(f"Error fetching transcription sentences: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_usage_details(request):
    """GET /api/v1/user/usage-details

    Return detailed usage statistics for charts and breakdowns.
    Query params:
      - days: number of days to look back (default: 30)
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    try:
        days = int(request.query.get('days', '30'))
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()

        # Usage rows are minute-level for streams, so page through all rows.
        t_usage = await _fetch_usage_rows_paged('transcription_usage', user_id, since)
        # Actual job counts (from transcriptions/translations tables, not usage rows)
        t_jobs_result = await supabase.table('transcriptions').select('id', count='exact').eq('user_id', user_id).gte('created_at', since).execute()
        transcription_job_count = t_jobs_result.count if hasattr(t_jobs_result, 'count') else len(t_jobs_result.data or [])

        # Aggregates
        total_transcription_seconds = sum(u.get('duration_seconds', 0) for u in t_usage)
        total_transcription_words = sum(u.get('word_count', 0) for u in t_usage)

        # Daily breakdown
        daily = {}
        for u in t_usage:
            day = u.get('created_at', '')[:10]
            if day not in daily:
                daily[day] = {'transcription_seconds': 0, 'transcription_words': 0}
            daily[day]['transcription_seconds'] += u.get('duration_seconds', 0)
            daily[day]['transcription_words'] += u.get('word_count', 0)

        daily_breakdown = [
            {'date': d, **v} for d, v in sorted(daily.items())
        ]

        # Source type breakdown
        source_breakdown = {}
        for u in t_usage:
            st = u.get('source_type', 'unknown')
            source_breakdown[st] = source_breakdown.get(st, 0) + u.get('duration_seconds', 0)

        return web.json_response({
            'period_days': days,
            'transcription': {
                'total_seconds': total_transcription_seconds,
                'total_words': total_transcription_words,
                'job_count': transcription_job_count,
                'source_breakdown': source_breakdown,
            },
            'daily_breakdown': daily_breakdown,
            'raw_transcription_usage': t_usage,
        })

    except Exception as e:
        logger.error(f"Error getting usage details: {e}")
        return web.json_response({'error': str(e)}, status=500)
