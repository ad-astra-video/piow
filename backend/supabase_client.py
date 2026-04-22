import os
from typing import Optional
from supabase import create_client, Client

# Singleton Supabase client
_supabase_client: Optional[Client] = None

def get_supabase_client() -> Client:
    """
    Get or create a Supabase client singleton.

    The backend uses the SUPABASE_SECRET_KEY (service role key) which bypasses
    Row Level Security (RLS). This is required because the backend is a trusted
    server-side environment that performs its own authentication (JWT verification
    for users, HMAC for agents) and needs cross-user access for operations like
    agent lookups, usage tracking, and billing queries.

    The SUPABASE_PUBLISHABLE_KEY (anon key) is NOT suitable for the backend
    because it enforces RLS and the backend has no authenticated user session
    context — all queries would return empty results or be rejected.

    Returns:
        Client: The Supabase client instance

    Raises:
        ValueError: If SUPABASE_URL or SUPABASE_SECRET_KEY environment variables are not set
    """
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get('SUPABASE_URL')
        # Backend MUST use the secret key (service role) to bypass RLS
        supabase_key = (
            os.environ.get('SUPABASE_SECRET_KEY')
            or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')  # legacy fallback
            or os.environ.get('SUPABASE_KEY')               # generic fallback
        )
        if not supabase_url or not supabase_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SECRET_KEY must be set in environment variables. "
                "The backend requires the service role key to bypass RLS for server-side operations."
            )
        _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client


def __getattr__(name: str):
    """
    Module-level __getattr__ for lazy loading of the supabase client.
    This allows `from supabase_client import supabase` to work without
    initializing the client at import time.
    """
    if name == "supabase":
        return get_supabase_client()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
