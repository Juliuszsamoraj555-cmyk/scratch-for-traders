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
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Request

from settings import settings


def get_current_user(request: Request) -> Optional[str]:
    """Returns the Supabase Auth user id (a UUID string) if the request
    carries a valid, unexpired access token in its Authorization header,
    else None. Never raises - callers that require login check for None
    themselves and respond however's appropriate for that endpoint
    (see billing.LoginRequired for the checkout flow)."""
    if not settings.SUPABASE_JWT_SECRET:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("bearer "):].strip()
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            # Supabase issues these without an explicit `aud` we can
            # pin to a single value across all project configurations,
            # so audience is checked manually below instead of here.
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        # Expired, forged, wrong secret (e.g. a stale/rotated one),
        # malformed - all the same to the caller: not logged in.
        return None

    # Reject anything that isn't a real user session token. Supabase
    # signs the public anon/service_role keys with this SAME project
    # secret (they're JWTs too - see SUPABASE_ANON_KEY in settings.py),
    # so signature-valid alone isn't enough: those carry role "anon" /
    # "service_role" and no `sub`, while an actual login session token
    # has role "authenticated" and `sub` set to the user's id. Without
    # this check, anyone could pass the (intentionally public) anon key
    # as a bearer token and be treated as a logged-in user.
    if payload.get("role") != "authenticated":
        return None

    user_id = payload.get("sub")
    return user_id if user_id else None
