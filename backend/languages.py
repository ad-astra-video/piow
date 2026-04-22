#!/usr/bin/env python3
"""
Languages Endpoint
Handles the get_languages API route.
"""

import aiohttp.web as web
import logging

logger = logging.getLogger(__name__)

from auth import no_auth


def setup_routes(app):
    """Setup languages-related routes."""
    app.router.add_get('/api/v1/languages', get_languages)


@no_auth
async def get_languages(request):
    """Get supported languages."""
    logger.info("Received get languages request")

    # Return a list of supported languages
    languages = [
        {"code": "en", "name": "English"},
        {"code": "es", "name": "Spanish"},
        {"code": "fr", "name": "French"},
        {"code": "de", "name": "German"},
        {"code": "it", "name": "Italian"},
        {"code": "pt", "name": "Portuguese"},
        {"code": "ru", "name": "Russian"},
        {"code": "ja", "name": "Japanese"},
        {"code": "ko", "name": "Korean"},
        {"code": "zh", "name": "Chinese"},
        {"code": "ar", "name": "Arabic"}
    ]

    return web.json_response({
        "languages": languages,
        "count": len(languages)
    })