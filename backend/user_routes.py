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
    app.router.add_get('/api/v1/streams/{id}/sentences', get_stream_sentences)
    app.router.add_get('/api/v1/streams/{id}/analysis', get_stream_analysis)


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
            stream_result = await (
                supabase.table('stream_sessions')
                .select('id, user_session_id, language, status, created_at, updated_at, final_text, stream_settings')
                .eq('user_id', user_id)
                .order('created_at', desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            stream_rows = stream_result.data or []
            stream_session_ids = [str(row.get('id')) for row in stream_rows if row.get('id')]

            latest_transcription_by_stream: Dict[str, Dict[str, Any]] = {}
            langs_by_stream_session: Dict[str, set] = {}
            latest_analysis_by_stream_session: Dict[str, Dict[str, Any]] = {}
            analysis_enabled_by_stream_session: Dict[str, bool] = {}
            analysis_mode_by_stream_session: Dict[str, Any] = {}

            if stream_session_ids:
                t_result = await (
                    supabase.table('transcriptions')
                    .select('*')
                    .eq('user_id', user_id)
                    .in_('stream_session_id', stream_session_ids)
                    .order('created_at', desc=True)
                    .execute()
                )
                for row in (t_result.data or []):
                    stream_session_id = row.get('stream_session_id')
                    if not stream_session_id:
                        continue
                    key = str(stream_session_id)
                    if key not in latest_transcription_by_stream:
                        latest_transcription_by_stream[key] = row

                tr_lang_result = await (
                    supabase.table('translations')
                    .select('stream_session_id, target_language')
                    .eq('user_id', user_id)
                    .in_('stream_session_id', stream_session_ids)
                    .execute()
                )
                for row in (tr_lang_result.data or []):
                    stream_session_id = row.get('stream_session_id')
                    target_language = row.get('target_language')
                    if not stream_session_id or not target_language:
                        continue
                    langs_by_stream_session.setdefault(str(stream_session_id), set()).add(target_language)

                analysis_result = await (
                    supabase.table('stream_analysis')
                    .select('stream_session_id, summary_text, analysis_source, created_at')
                    .eq('user_id', user_id)
                    .in_('stream_session_id', stream_session_ids)
                    .order('created_at', desc=True)
                    .execute()
                )
                for row in (analysis_result.data or []):
                    stream_session_id = row.get('stream_session_id')
                    if not stream_session_id:
                        continue
                    key = str(stream_session_id)
                    if key not in latest_analysis_by_stream_session:
                        latest_analysis_by_stream_session[key] = row

            for row in stream_rows:
                stream_session_id = str(row.get('id'))
                if not stream_session_id:
                    continue

                stream_settings = row.get('stream_settings')
                stream_settings = stream_settings if isinstance(stream_settings, dict) else {}
                analysis_settings = stream_settings.get('analysis')
                analysis_settings = analysis_settings if isinstance(analysis_settings, dict) else {}

                analysis_enabled_by_stream_session[stream_session_id] = bool(analysis_settings.get('enabled'))
                analysis_mode_by_stream_session[stream_session_id] = analysis_settings.get('type')

                transcription = latest_transcription_by_stream.get(stream_session_id, {})
                analysis = latest_analysis_by_stream_session.get(stream_session_id)

                item = {
                    **transcription,
                    'id': transcription.get('id') or stream_session_id,
                    'stream_session_id': stream_session_id,
                    'stream_id': stream_session_id,
                    'session_id': row.get('user_session_id'),
                    'status': row.get('status'),
                    'created_at': transcription.get('created_at') or row.get('created_at'),
                    'updated_at': transcription.get('updated_at') or row.get('updated_at'),
                    'language': transcription.get('language') or row.get('language'),
                    'source_language': transcription.get('language') or row.get('language'),
                    'text': transcription.get('text') or row.get('final_text') or '',
                    'duration': transcription.get('duration') or 0,
                    'word_count': transcription.get('word_count') or 0,
                    'token_count': transcription.get('token_count') or 0,
                    'source_type': transcription.get('source_type') or 'stream',
                    'translated_languages': sorted(list(langs_by_stream_session.get(stream_session_id, set()))),
                    'has_analysis': bool(analysis) or analysis_enabled_by_stream_session.get(stream_session_id, False),
                    'analysis_mode': analysis_mode_by_stream_session.get(stream_session_id),
                    'analysis_response_format': analysis_settings.get('response_format'),
                    'analysis_summary_text': analysis.get('summary_text') if analysis else None,
                    'analysis_source': analysis.get('analysis_source') if analysis else None,
                    '_type': 'transcription',
                }
                transcriptions.append(item)

            if source_type:
                transcriptions = [item for item in transcriptions if item.get('source_type') == source_type]

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
async def get_stream_sentences(request):
    """GET /api/v1/streams/{id}/sentences

    Return per-sentence rows for a transcription, ordered by sentence_index.
    Includes translation sentence rows grouped by target language.
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    stream_id = request.match_info.get('id')
    if not stream_id:
        return web.json_response({'error': 'Stream ID required'}, status=400)

    try:
        ownership = await (
            supabase.table('stream_sessions')
            .select('id, user_id, stream_settings')
            .eq('id', stream_id)
            .execute()
        )
        if not ownership.data:
            return web.json_response({'error': 'Not found'}, status=404)

        stream_owner_id = ownership.data[0].get('user_id')
        if not stream_owner_id or str(stream_owner_id) != str(user_id):
            return web.json_response({'error': 'Not found'}, status=404)

        stream_settings = ownership.data[0].get('stream_settings')
        stream_settings = stream_settings if isinstance(stream_settings, dict) else {}
        if not stream_id:
            return web.json_response({
                'sentences': [],
                'translations_by_language': {},
                'translated_languages': [],
            })

        result = await supabase.table('transcription_sentences') \
            .select('sentence_index, text, translated_text, timestamp') \
            .eq('stream_session_id', stream_id) \
            .order('sentence_index') \
            .execute()

        base_sentences = result.data or []
        base_by_index = {row.get('sentence_index'): row for row in base_sentences}

        translation_settings = stream_settings.get('translation')
        translation_settings = translation_settings if isinstance(translation_settings, dict) else {}
        configured_target_language = translation_settings.get('target_language')

        # Fetch all translation header/legacy rows in one query.
        # Sentence-level translated text now lives on transcription_sentences.
        all_translations_result = await (
            supabase.table('translations')
            .select('id, original_text, translated_text, target_language, sentence_index, created_at')
            .eq('user_id', user_id)
            .eq('stream_session_id', stream_id)
            .order('created_at')
            .execute()
        )

        all_translation_rows = all_translations_result.data or []
        translation_languages = {
            str(row.get('target_language'))
            for row in all_translation_rows
            if row.get('target_language')
        }
        if configured_target_language:
            translation_languages.add(str(configured_target_language))

        translations_by_language: Dict[str, List[Dict[str, Any]]] = {}
        sentence_rows_with_translations = [
            row for row in base_sentences
            if isinstance(row.get('translated_text'), str) and row.get('translated_text').strip()
        ]

        sentence_translation_language = None
        if sentence_rows_with_translations:
            if configured_target_language:
                sentence_translation_language = str(configured_target_language)
            elif len(translation_languages) == 1:
                sentence_translation_language = next(iter(translation_languages))

        if sentence_translation_language:
            translations_by_language[sentence_translation_language] = [
                {
                    'sentence_index': row.get('sentence_index'),
                    'text': row.get('text') or '',
                    'translated_text': row.get('translated_text'),
                    'timestamp': row.get('timestamp'),
                }
                for row in sentence_rows_with_translations
            ]

        if not translations_by_language:
            # Backward compatibility for older rows that still stored one
            # translations record per sentence.
            translation_index_by_language: Dict[str, Dict[int, Dict[str, Any]]] = {}

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
        logger.error(f"Error fetching stream sentences: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_user_auth
async def get_stream_analysis(request):
    """GET /api/v1/streams/{id}/analysis

        Return persisted analysis summaries for a transcription, newest first.
        Query params:
            - limit: optional max rows to return
    """
    user_id = _get_user_id(request)
    if not user_id:
        return web.json_response({'error': 'Authentication required'}, status=401)

    stream_id = request.match_info.get('id')
    if not stream_id:
        return web.json_response({'error': 'Stream ID required'}, status=400)

    try:
        limit_raw = request.query.get('limit')
        limit = int(limit_raw) if limit_raw is not None else None
        if limit is not None and limit < 1:
            limit = 1
    except Exception:
        return web.json_response({'error': 'Invalid limit'}, status=400)

    try:
        ownership = await (
            supabase.table('stream_sessions')
            .select('id, user_id, stream_settings')
            .eq('id', stream_id)
            .execute()
        )
        if not ownership.data:
            return web.json_response({'error': 'Not found'}, status=404)

        stream_owner_id = ownership.data[0].get('user_id')
        if not stream_owner_id or str(stream_owner_id) != str(user_id):
            return web.json_response({'error': 'Not found'}, status=404)

        stream_settings = ownership.data[0].get('stream_settings')
        stream_settings = stream_settings if isinstance(stream_settings, dict) else {}
        analysis_settings = stream_settings.get('analysis')
        analysis_settings = analysis_settings if isinstance(analysis_settings, dict) else {}
        stream_analysis_mode = analysis_settings.get('type')
        stream_analysis_response_format = analysis_settings.get('response_format')

        if not stream_id:
            return web.json_response({'analysis': [], 'count': 0})

        query = (
            supabase.table('stream_analysis')
            .select('id, summary_text, analysis_source, timestamp_ms, source_event_type, created_at')
            .eq('user_id', user_id)
            .eq('stream_session_id', stream_id)
            .order('created_at', desc=True)
        )
        if limit is not None:
            query = query.range(0, limit - 1)
        result = await query.execute()

        rows = []
        for row in (result.data or []):
            rows.append({
                **row,
                'analysis_mode': stream_analysis_mode,
            })

        return web.json_response({
            'analysis': rows,
            'count': len(rows),
            'response_format': stream_analysis_response_format,
        })
    except Exception as e:
        logger.error(f"Error fetching stream analysis: {e}")
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
