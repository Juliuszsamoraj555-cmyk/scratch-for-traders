"""
Stripe integration — two one-time Prices (NOT subscriptions, per the
2026-08-10 pricing-model decision): a single-export credit (~5 zl) and a
30-day "unlimited exports" pass (~30 zl). Both use Stripe Checkout in
`mode="payment"` so Stripe hosts the actual card entry - this app never
sees a card number, and there's no subscription-cancellation flow to
build.

Correlating a payment back to an anonymous device_id (see
device_identity.py) is done via Checkout Session `metadata` (primary) and
`client_reference_id` (belt-and-suspenders backup) - both are set to the
paying browser's device_id when the session is created, then read back
out of the webhook event once Stripe confirms payment.
"""
from __future__ import annotations

from typing import Literal, Optional

import stripe

from settings import settings

Kind = Literal["export_credit", "day_pass"]


class BillingNotConfigured(RuntimeError):
    """Raised when a Stripe/price env var is missing - lets main.py turn
    this into a clean 503 instead of a stack trace."""


def _require_configured() -> None:
    missing = [
        name
        for name, val in [
            ("STRIPE_SECRET_KEY", settings.STRIPE_SECRET_KEY),
            ("STRIPE_PRICE_EXPORT", settings.STRIPE_PRICE_EXPORT),
            ("STRIPE_PRICE_PASS", settings.STRIPE_PRICE_PASS),
        ]
        if not val
    ]
    if missing:
        raise BillingNotConfigured(
            f"Stripe is not configured - missing env var(s): {', '.join(missing)}. "
            "See .env.example."
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY


def create_checkout_session(device_id: str, kind: Kind) -> str:
    """Creates a Stripe Checkout Session for the given product and
    returns its hosted URL - the caller (main.py) redirects/returns this
    to the frontend, which sends the browser there."""
    _require_configured()

    price_id = settings.STRIPE_PRICE_EXPORT if kind == "export_credit" else settings.STRIPE_PRICE_PASS

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=device_id,
        metadata={"device_id": device_id, "kind": kind},
        success_url=f"{settings.SITE_URL}/?checkout=success&kind={kind}",
        cancel_url=f"{settings.SITE_URL}/?checkout=cancel",
        # Collects the payer's email without requiring an account -
        # stored against the device row for future re-engagement (e.g. a
        # "your pass expires tomorrow" email), never required to export.
        customer_creation="if_required",
    )
    if not session.url:
        raise RuntimeError("Stripe did not return a Checkout URL")
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str) -> "stripe.Event":
    """Verifies the webhook signature so a request can't just POST fake
    'payment succeeded' events at this endpoint. Raises
    stripe.error.SignatureVerificationError on failure - main.py turns
    that into a 400."""
    _require_configured()
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise BillingNotConfigured("STRIPE_WEBHOOK_SECRET is not set - see .env.example.")
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)


async def apply_completed_checkout(event: "stripe.Event") -> Optional[str]:
    """Handles a verified `checkout.session.completed` event: grants the
    right entitlement to the right device_id. Returns the kind granted
    ('export_credit' | 'day_pass'), or None if this event wasn't one we
    act on (main.py should still 200 those - Stripe sends many event
    types to one webhook URL, ignoring the rest is normal)."""
    import db  # local import: avoids a circular import at module load time

    if event["type"] != "checkout.session.completed":
        return None

    session = event["data"]["object"]
    if session.get("payment_status") != "paid":
        return None

    device_id = (session.get("metadata") or {}).get("device_id") or session.get("client_reference_id")
    kind = (session.get("metadata") or {}).get("kind")
    if not device_id or kind not in ("export_credit", "day_pass"):
        # Shouldn't happen for sessions we created, but a malformed/
        # unrelated event should never crash the webhook (Stripe would
        # just retry it forever).
        return None

    email = None
    customer_details = session.get("customer_details") or {}
    email = customer_details.get("email")

    amount_total = session.get("amount_total") or 0
    currency = session.get("currency") or "pln"
    stripe_event_id = event["id"]
    session_id = session["id"]

    if kind == "export_credit":
        await db.grant_export_credits(device_id, 1, stripe_event_id, session_id, amount_total, currency, email)
    else:
        await db.grant_day_pass(
            device_id, settings.PASS_DURATION_DAYS, stripe_event_id, session_id, amount_total, currency, email
        )
    return kind
