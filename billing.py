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

from marketplace_strategies import get_marketplace_strategy_tier
from settings import settings

Kind = Literal["export_credit", "day_pass", "strategy_purchase"]


class BillingNotConfigured(RuntimeError):
    """Raised when a Stripe/price env var is missing - lets main.py turn
    this into a clean 503 instead of a stack trace."""


class LoginRequired(RuntimeError):
    """Raised when day_pass checkout is attempted without a logged-in
    user - lets main.py turn this into a clean 401 instead of a 503."""


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
    # The stripe==11.1.1 SDK defaults to API version 2024-09-30.acacia,
    # which this Stripe account rejects ("Managed Payments is not
    # supported on API version 2024-09-30.acacia") - the account's
    # products were created with a product category (Downloadable
    # Software) that opts into Managed Payments, which needs a newer
    # API version than the pinned SDK's default. Pinning it explicitly
    # here (rather than bumping the whole SDK) keeps every other Stripe
    # call's behavior exactly as tested.
    stripe.api_version = "2025-03-31.basil"


def _require_strategy_pricing_configured() -> str:
    """Separate from _require_configured()'s three vars - checked only when
    kind="strategy_purchase" is actually requested, so a deploy that hasn't
    set these two yet doesn't break export/pass checkout (already live,
    already taking real money) by suddenly demanding them too. Returns
    nothing useful; raises BillingNotConfigured if either is missing."""
    missing = [
        name
        for name, val in [
            ("STRIPE_PRICE_STRATEGY_FEATURED", settings.STRIPE_PRICE_STRATEGY_FEATURED),
            ("STRIPE_PRICE_STRATEGY_STANDARD", settings.STRIPE_PRICE_STRATEGY_STANDARD),
        ]
        if not val
    ]
    if missing:
        raise BillingNotConfigured(
            f"Marketplace purchases are not configured - missing env var(s): {', '.join(missing)}. "
            "See .env.example."
        )


def create_checkout_session(
    device_id: str, kind: Kind, user_id: Optional[str] = None, strategy_id: Optional[str] = None
) -> str:
    """Creates a Stripe Checkout Session for the given product and
    returns its hosted URL - the caller (main.py) redirects/returns this
    to the frontend, which sends the browser there.

    kind="day_pass" and kind="strategy_purchase" both require user_id (a
    logged-in Supabase Auth user - see main.py's get_current_user): both
    are granted to the ACCOUNT, not the device, so ownership follows the
    trader across every browser/device they log into rather than being
    stuck to whichever one happened to complete checkout. kind=
    "strategy_purchase" used to be anonymous/device-based like
    export_credit still is below - changed 2026-08-31 (explicit product
    decision: something a trader paid for and expects to keep needs to
    survive clearing cookies or switching devices, which device-only
    ownership never could). kind="export_credit" is the one exception
    that still never requires login - a single $2 credit is small enough
    that account-gating it isn't worth the friction - but attributes to
    the account when the buyer happens to be logged in anyway (see
    db.grant_export_credits).

    kind="strategy_purchase" also requires strategy_id, and only for one
    of the five PAID marketplace strategies (see marketplace_strategies.
    MARKETPLACE_STRATEGY_TIERS) - the free strategy of the week never goes
    through Stripe (or requires login) at all - see main.py's
    _require_strategy_ownership - and an unknown/non-purchasable id raises
    ValueError rather than ever reaching Stripe, same "never trust the
    client with price" reasoning as marketplace_strategies.py's own module
    docstring."""
    _require_configured()
    if not user_id:
        # Every kind requires login as of 2026-09-03 - export_credit was
        # the last anonymous one, and it stopped making sense the moment
        # exports themselves required an account (see main.py's
        # _require_export_entitlement): the credit would have been
        # unspendable.
        raise LoginRequired({
            "day_pass": "Log in to buy the 30-day pass - it needs to work across your devices.",
            "strategy_purchase": "Log in to buy this strategy - it needs to work across your devices.",
        }.get(kind, "Log in to buy an export - it's credited to your account."))

    tier: Optional[str] = None
    if kind == "strategy_purchase":
        if not strategy_id:
            raise ValueError("strategy_id is required for kind='strategy_purchase'")
        _require_strategy_pricing_configured()
        tier = get_marketplace_strategy_tier(strategy_id)
        if tier not in ("featured", "standard"):
            raise ValueError(f"strategy {strategy_id!r} is not purchasable (tier={tier!r})")

    if kind == "export_credit":
        price_id = settings.STRIPE_PRICE_EXPORT
    elif kind == "day_pass":
        price_id = settings.STRIPE_PRICE_PASS
    else:
        price_id = settings.STRIPE_PRICE_STRATEGY_FEATURED if tier == "featured" else settings.STRIPE_PRICE_STRATEGY_STANDARD

    metadata = {"device_id": device_id, "kind": kind}
    if user_id:
        metadata["user_id"] = user_id
    if strategy_id:
        metadata["strategy_id"] = strategy_id

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=(user_id if kind in ("day_pass", "strategy_purchase") else device_id),
        metadata=metadata,
        # Dedicated per-kind confirmation pages/states (see thank-you/export/
        # and thank-you/pass/ for the first two), not the homepage. This
        # used to point at "{SITE_URL}/?checkout=success" - a real bug
        # found 2026-08-24: the JS that actually shows a "payment received"
        # toast only ever existed in index_1.html (the builder), so a
        # paying trader landed on the plain marketing homepage with an
        # unhandled ?checkout=... query string and zero confirmation their
        # payment went through. strategy_purchase reuses strategy-detail.html
        # itself (its own ?purchased=1 handling, see that file) rather than
        # a dedicated thank-you page - a buyer's next step is "download the
        # file", and that page is exactly where the download buttons live.
        success_url=(
            f"{settings.SITE_URL}/thank-you/export/"
            if kind == "export_credit"
            else f"{settings.SITE_URL}/thank-you/pass/"
            if kind == "day_pass"
            else f"{settings.SITE_URL}/strategy-detail.html?id={strategy_id}&purchased=1"
        ),
        # Cancel routes back to wherever the buyer was trying to buy
        # something (a toast, no charge made) rather than the homepage -
        # index_1.html's handleCheckoutReturn() already handles
        # ?checkout=cancel correctly for export/pass; strategy-detail.html
        # has the equivalent handling for its own cancel_url below.
        # Uses the real filename (not /builder/) - the clean-URL routing
        # that would have made /builder/ resolve was fully reverted the
        # same day after a live outage, see render.yaml's own comment.
        cancel_url=(
            f"{settings.SITE_URL}/strategy-detail.html?id={strategy_id}&checkout=cancel"
            if kind == "strategy_purchase"
            else f"{settings.SITE_URL}/index_1.html?checkout=cancel"
        ),
        # Collects the payer's email without requiring an account -
        # stored against the device/user row for future re-engagement
        # (e.g. a "your pass expires tomorrow" email), never required to
        # export.
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
    ('export_credit' | 'day_pass' | 'strategy_purchase'), or None if this
    event wasn't one we act on (main.py should still 200 those - Stripe
    sends many event types to one webhook URL, ignoring the rest is
    normal)."""
    import db  # local import: avoids a circular import at module load time

    if event["type"] != "checkout.session.completed":
        return None

    session = event["data"]["object"]
    if session.get("payment_status") != "paid":
        return None

    metadata = session.get("metadata") or {}
    kind = metadata.get("kind")
    if kind not in ("export_credit", "day_pass", "strategy_purchase"):
        # Shouldn't happen for sessions we created, but a malformed/
        # unrelated event should never crash the webhook (Stripe would
        # just retry it forever).
        return None

    email = None
    customer_details = session.get("customer_details") or {}
    email = customer_details.get("email")

    amount_total = session.get("amount_total") or 0
    currency = session.get("currency") or "usd"
    stripe_event_id = event["id"]
    session_id = session["id"]

    if kind == "export_credit":
        # Credited to the ACCOUNT (user_entitlements.exports_available),
        # never the device - checkout has required login since 2026-09-03
        # (see create_checkout_session), so user_id is always present for
        # any session created after that. device_id stays on the
        # billing_events row as an audit trail of which browser paid.
        user_id = metadata.get("user_id")
        device_id = metadata.get("device_id") or session.get("client_reference_id")
        if not user_id or not device_id:
            return None
        await db.grant_export_credits(
            device_id, 1, stripe_event_id, session_id, amount_total, currency, email, user_id=user_id
        )
    elif kind == "strategy_purchase":
        # Granted to the logged-in user_id, not device_id - see
        # create_checkout_session()/LoginRequired above (2026-08-31: this
        # used to be optional/device-based like export_credit still is,
        # changed so ownership survives clearing cookies or switching
        # devices). device_id is still recorded (metadata always carries
        # it - see create_checkout_session) purely for the audit trail in
        # billing_events, never used to decide ownership.
        user_id = metadata.get("user_id") or session.get("client_reference_id")
        strategy_id = metadata.get("strategy_id")
        device_id = metadata.get("device_id")
        if not user_id or not strategy_id:
            return None
        await db.grant_strategy_purchase(
            user_id, strategy_id, stripe_event_id, session_id, amount_total, currency, email, device_id=device_id
        )
    else:
        # day_pass: granted to the logged-in user_id, not device_id - see
        # create_checkout_session()/LoginRequired above.
        user_id = metadata.get("user_id") or session.get("client_reference_id")
        if not user_id:
            return None
        await db.grant_day_pass(
            user_id, settings.PASS_DURATION_DAYS, stripe_event_id, session_id, amount_total, currency, email
        )
    return kind
