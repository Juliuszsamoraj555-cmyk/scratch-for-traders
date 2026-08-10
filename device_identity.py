"""
Anonymous device identity via a signed, httpOnly cookie — no login
screen, no password, no Supabase Auth. See supabase/schema.sql and db.py
for how this device_id is used to track free/paid export entitlement.

Deliberately anonymous by design (2026-08-10 decision): the pricing model
tracks usage per browser/IP rather than per account, so there is nothing
for a user to sign up for - the cookie is issued transparently on first
contact with the API.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from settings import settings

COOKIE_NAME = "sft_device"
_serializer = URLSafeSerializer(settings.COOKIE_SIGNING_SECRET, salt="device-id")

_TWO_YEARS_SECONDS = 60 * 60 * 24 * 365 * 2


def _client_ip(request: Request) -> str:
    # Render (and most PaaS) sit behind a reverse proxy that sets
    # X-Forwarded-For; fall back to the direct connection for local dev.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_device_id(request: Request, response: Response) -> str:
    """FastAPI dependency: reads+verifies the signed device_id cookie,
    creates a device row (and a fresh cookie) if it's missing, forged, or
    from a rotated signing secret, and re-sets the cookie on every
    response so its expiry keeps rolling forward for active users.

    If DATABASE_URL isn't configured (billing not wired up yet - see
    main.py's _require_export_entitlement), this still issues a cookie so
    the rest of the request pipeline behaves the same either way, but
    skips ever touching the database - the id is just an unpersisted
    UUID in that case."""
    raw = request.cookies.get(COOKIE_NAME)
    candidate_id: Optional[str] = None
    if raw:
        try:
            candidate_id = _serializer.loads(raw)
        except BadSignature:
            candidate_id = None  # tampered, or signed with an old secret - issue a fresh one

    if settings.DATABASE_URL:
        import db  # local import: avoids a circular import at module load time

        device_id = await db.get_or_create_device(candidate_id, _client_ip(request))
    else:
        import uuid

        device_id = candidate_id or str(uuid.uuid4())

    is_https = settings.SITE_URL.startswith("https://")
    response.set_cookie(
        key=COOKIE_NAME,
        value=_serializer.dumps(device_id),
        max_age=_TWO_YEARS_SECONDS,
        httponly=True,
        secure=is_https,
        # Frontend and backend are very likely on different subdomains
        # (or even different hosting platforms) once deployed, which
        # browsers treat as cross-site for cookie purposes unless they
        # share a registrable domain via COOKIE_DOMAIN below - so default
        # to "none" whenever we're on https (requires Secure, which we
        # already set from the same condition). Local http dev uses "lax"
        # since SameSite=None is rejected by browsers without Secure.
        samesite="none" if is_https else "lax",
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )
    return device_id
