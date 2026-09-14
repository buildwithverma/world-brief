"""Validate hosted access tokens with Supabase; only the configured owner may enter."""
import httpx

from .config import OWNER_USER_ID, SUPABASE_KEY, SUPABASE_URL


async def authorized(authorization):
    if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 8192:
        return False
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(f"{SUPABASE_URL}/auth/v1/user", headers={
                "apikey": SUPABASE_KEY, "Authorization": authorization,
            })
        return response.status_code == 200 and response.json().get("id") == OWNER_USER_ID
    except (httpx.HTTPError, ValueError):
        return False
