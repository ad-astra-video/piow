import os
from supabase import create_client, Client

# Singleton Supabase client
_supabase_client: Client = None

def get_supabase_client() -> Client:
    """
    Get or create a Supabase client singleton.
    """
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_ANON_KEY')
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment variables")
        _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client

# For easy access, we can also create a function to get the client
supabase = get_supabase_client()
