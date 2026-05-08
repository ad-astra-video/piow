#!/usr/bin/env python3
"""
User Routes Module
Provides user-scoped endpoints for profile, history, and detailed usage stats.
All requests go through the backend (no direct Supabase/Stripe from frontend).
"""

import aiohttp.web as web
import logging
from datetime import datetime, timedelta

from auth import require_user_auth
from supabase_client import async_supabase as supabase

logger = logging.getLogger(__name__)


def setup_routes(app):
    """Setup user-related routes."""
    app.router.add_get('/api/v1/user/profile', get_user_profile)
    app.router.add_get('/api/v1/user/history', get_user_history)
    app.router.add_get('/api/v1/user/usage-details', get_usage_details)


def _get_user_id(request):
    """Extract user_id from authenticated request."""
    user = request.get('user')
    if user:
        return str(user.id) if hasattr(user, 'id') else str(user.get('id', ''))
    return None


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
            t_result = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
            transcriptions = [
                {**item, '_type': 'transcription'} for item in (t_result.data or [])
            ]

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

        # Transcription usage
        t_usage_result = await supabase.table('transcription_usage').select('*').eq('user_id', user_id).gte('created_at', since).order('created_at', desc=True).execute()
        t_usage = t_usage_result.data or []

        # Translation usage
        tr_usage_result = await supabase.table('translation_usage').select('*').eq('user_id', user_id).gte('created_at', since).order('created_at', desc=True).execute()
        tr_usage = tr_usage_result.data or []

        # Aggregates
        total_transcription_seconds = sum(u.get('duration_seconds', 0) for u in t_usage)
        total_transcription_words = sum(u.get('word_count', 0) for u in t_usage)
        total_translation_chars = sum(u.get('characters_translated', 0) for u in tr_usage)

        # Daily breakdown
        daily = {}
        for u in t_usage:
            day = u.get('created_at', '')[:10]
            if day not in daily:
                daily[day] = {'transcription_seconds': 0, 'transcription_words': 0, 'translation_chars': 0}
            daily[day]['transcription_seconds'] += u.get('duration_seconds', 0)
            daily[day]['transcription_words'] += u.get('word_count', 0)

        for u in tr_usage:
            day = u.get('created_at', '')[:10]
            if day not in daily:
                daily[day] = {'transcription_seconds': 0, 'transcription_words': 0, 'translation_chars': 0}
            daily[day]['translation_chars'] += u.get('characters_translated', 0)

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
                'job_count': len(t_usage),
                'source_breakdown': source_breakdown,
            },
            'translation': {
                'total_characters': total_translation_chars,
                'job_count': len(tr_usage),
            },
            'daily_breakdown': daily_breakdown,
            'raw_transcription_usage': t_usage,
            'raw_translation_usage': tr_usage,
        })

    except Exception as e:
        logger.error(f"Error getting usage details: {e}")
        return web.json_response({'error': str(e)}, status=500)
