"""
Supabase Auth token verification - login for the 30-day pass only (see
schema.sql's header comment). The frontend talks to Supabase Auth's own
REST API directly (signup/login/password-reset - see index_1.html) using
the public anon key; this backend never issues or stores passwords, it
only *verifies* the access token Supabase hands back after a successful
login, on requests that need to know who's logged in.

Deliberately optional everywhere it's used: a missing/invalid/expired
token just means "not logged in right now", not an error - the free
tier and single-export purchases work with zero token at all. Only the
day_pass checkout endpoint actually requires one.

Verification: newer Supabase projects (this one included, confirmed
2026-08-14 by decoding a real token's header) sign access tokens with
ES256 - an asymmetric algorithm - not the legacy shared "JWT Secret"
(HS256) every earlier version of this file used. That mismatch is why
login silently never worked even with a correctly-copied
SUPABASE_JWT_SECRET: PyJWT rejected every real token outright, algorithm
mismatch, before signature was ever checked. ES256 only needs the
project's PUBLIC signing keys, fetched from Supabase's own JWKS
endpoint - no shared secret required for this path at all. HS256 is
kept as a fallback for older Supabase projects that still use a shared
secret, so this doesn't assume every project is on the new scheme.
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Request

from settings import settings

_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_jwks_client() -> Optional[jwt.PyJWKClient]:
    """Lazily created, reused across requests - PyJWKClient caches the
    fetched keys internally, so this doesn't refetch the JWKS on every
    single request, just the first time a given `kid` is seen (or the
    cache expires)."""
    global _jwks_client
    if _jwks_client is None and settings.SUPABASE_URL:
        _jwks_client = jwt.PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def get_current_user(request: Request) -> Optional[str]:
    """Returns the Supabase Auth user id (a UUID string) if the request
    carries a valid, unexpired access token in its Authorization header,
    else None. Never raises - callers that require login check for None
    themselves and respond however's appropriate for that endpoint
    (see billing.LoginRequired for the checkout flow)."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("bearer "):].strip()
    if not token:
        return None

    payload = None

    # Try the modern (ES256, JWKS-based) path first - this is what a
    # real access token from this project actually is.
    jwks_client = _get_jwks_client()
    if jwks_client is not None:
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256", "RS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            payload = None

    # Fall back to the legacy shared-secret (HS256) scheme, for Supabase
    # projects that predate the JWKS rotation and still sign this way.
    if payload is None and settings.SUPABASE_JWT_SECRET:
        try:
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            payload = None

    if payload is None:
        # Expired, forged, wrong key entirely, malformed - all the same
        # to the caller: not logged in.
        return None

    # Reject anything that isn't a real user session token. Supabase
    # signs the public anon/service_role keys with this SAME project
    # (they're JWTs too - see SUPABASE_ANON_KEY in settings.py), so a
    # verified signature alone isn't enough: those carry role "anon" /
    # "service_role" and no `sub`, while an actual login session token
    # has role "authenticated" and `sub` set to the user's id. Without
    # this check, anyone could pass the (intentionally public) anon key
    # as a bearer token and be treated as a logged-in user.
    if payload.get("role") != "authenticated":
        return None

    user_id = payload.get("sub")
    return user_id if user_id else None
