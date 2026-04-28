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

    # Return a list of supported languages (Granite 4.0 1B Speech model)
    languages = [
        {"code": "en", "name": "English"},
        {"code": "es", "name": "Spanish"},
        {"code": "fr", "name": "French"},
        {"code": "de", "name": "German"},
        {"code": "pt", "name": "Portuguese"},
        {"code": "ja", "name": "Japanese"}
    ]

    return web.json_response({
        "languages": languages,
        "count": len(languages)
    })