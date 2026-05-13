#!/usr/bin/env python3
"""
Quota Checking Module
Enforces usage limits per subscription tier on a rolling 30-day window.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Plan prices (USD per month)
PLAN_PRICES = {
    'free': 0,
    'starter': 15,
    'pro': 39,
    'enterprise': 99,
}

# Plan limits (rolling 30-day window)
# transcription_minutes: combined CPU+GPU transcription pool (1 hr/day = 1800 min/30 days)
PLAN_LIMITS = {
    'free': {
        'transcription_minutes': 1800,       # 1 hr/day
        'queue_delay': True,                 # Free tier has queue delays
        'priority': 'low',                   # Lower priority processing
        'watermark': True,                   # Export watermark
    },
    'starter': {
        'transcription_minutes': 5400,       # 3 hr/day
        'queue_delay': False,
        'priority': 'normal',
        'watermark': False,
    },
    'pro': {
        'transcription_minutes': 14400,      # 8 hr/day
        'queue_delay': False,
        'priority': 'high',
        'watermark': False,
    },
    'enterprise': {
        'transcription_minutes': -1,         # unlimited (24 hr/day)
        'queue_delay': False,
        'priority': 'highest',
        'watermark': False,
    },
}

# Map service_type to quota key and usage table/column
# Both transcribe_cpu and transcribe_gpu draw from the same transcription_minutes pool
QUOTA_MAPPING = {
    'transcribe_cpu': ('transcription_minutes', 'transcription_usage', 'duration_seconds'),
    'transcribe_gpu': ('transcription_minutes', 'transcription_usage', 'duration_seconds'),
}


async def check_quota(user_id: str, service_type: str, tier: str = 'free') -> Tuple[bool, Dict[str, Any]]:
    """Check if user has remaining quota for the given service type.

    Args:
        user_id: The user or agent ID to check quota for
        service_type: Type of service ('transcribe_cpu', 'transcribe_gpu')
        tier: Subscription tier ('free', 'starter', 'pro', 'enterprise')

    Returns:
        Tuple of (allowed: bool, quota_info: dict with remaining, limit, used)
    """
    from supabase_client import async_supabase as supabase

    limits = PLAN_LIMITS.get(tier, PLAN_LIMITS['free'])

    if service_type not in QUOTA_MAPPING:
        # Unknown service type — allow by default
        return True, {'remaining': -1, 'limit': -1, 'used': 0, 'unlimited': True}

    quota_key, table, column = QUOTA_MAPPING[service_type]
    limit = limits[quota_key]

    # Unlimited quota (-1 means unlimited)
    if limit == -1:
        return True, {'remaining': -1, 'limit': -1, 'used': 0, 'unlimited': True}

    # Query usage for the last 30 days
    thirty_days_ago = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 30 * 24 * 60 * 60))

    try:
        # Supabase/PostgREST may cap unpaged SELECT results (commonly 1000 rows).
        # Stream usage is written once per minute, so we must page through all rows.
        used_raw = 0
        page_size = 1000
        page = 0
        while True:
            start = page * page_size
            end = start + page_size - 1
            result = await (
                supabase.table(table)
                .select(column)
                .eq('user_id', user_id)
                .gte('created_at', thirty_days_ago)
                .range(start, end)
                .execute()
            )

            rows = result.data or []
            if not rows:
                break

            used_raw += sum(row.get(column, 0) or 0 for row in rows)
            if len(rows) < page_size:
                break
            page += 1

        # Convert to quota units
        if 'minutes' in quota_key:
            used = used_raw / 60  # seconds to minutes
        else:
            used = used_raw  # already in characters

        remaining = max(0, limit - used)
        allowed = used < limit

        return allowed, {
            'remaining': round(remaining, 2),
            'limit': limit,
            'used': round(used, 2),
            'unlimited': False,
        }
    except Exception as e:
        logger.error(f"Error checking quota: {e}")
        # Fail closed: deny access if quota check fails
        return False, {'remaining': 0, 'limit': limit, 'used': 0, 'error': str(e), 'unlimited': False}