"""
Tests for email verification at purchase time (2026-09-19): every checkout
endpoint refuses an unverified account with 403 `email_not_verified`, and
POST /api/account/verify-email is the only way to become verified.

Nothing here may touch the real database or Supabase - .env points at the
production project. The autouse fixture makes any accidental real
connection fail loudly instead of quietly reading or writing production.

Run with:
    pytest tests/test_email_verification.py -v
"""
import urllib.error

import pytest
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

import db
import main
import supabase_auth
from device_identity import get_device_id


USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _never_touch_production(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test tried to open a real database connection")
    monkeypatch.setattr(db, "_get_pool", boom)
    # DATABASE_URL must look configured so the code paths under test run, but
    # nothing may use it: every db function used below is replaced.
    monkeypatch.setattr(main.settings, "DATABASE_URL", "postgresql://test-only", raising=False)
    # The gate ships dark (off unless REQUIRE_VERIFIED_EMAIL=1); these tests are
    # about how it behaves once it is on.
    monkeypatch.setattr(main.settings, "REQUIRE_VERIFIED_EMAIL", True, raising=False)
    # The export/pass checkouts only exist in paid mode (settings.FREE_MODE
    # refuses them with 409), which is the mode this gate protects.
    monkeypatch.setattr(main.settings, "FREE_MODE", False, raising=False)
    main.app.dependency_overrides[get_device_id] = lambda: "device-test"
    main._VERIFY_FAILURES.clear()
    yield
    main.app.dependency_overrides.clear()
    main._VERIFY_FAILURES.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


class Recorder:
    """Stands in for db + supabase so a test can see what was called."""
    def __init__(self):
        self.marked = []
        self.verified = False
        self.email = "trader@example.com"


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()

    async def is_verified(uid):
        return r.verified

    async def mark(uid):
        r.marked.append(uid)

    async def get_email(uid):
        return r.email

    monkeypatch.setattr(main.db, "is_email_verified", is_verified)
    monkeypatch.setattr(main.db, "mark_email_verified", mark)
    monkeypatch.setattr(main.db, "get_user_email", get_email)
    return r


def logged_in_as(monkeypatch, uid):
    monkeypatch.setattr(main, "get_current_user", lambda request: uid)


# ---------------------------------------------------------------- the gate

@pytest.mark.parametrize("path,body", [
    ("/api/billing/checkout/export", None),
    ("/api/billing/checkout/pass", None),
    ("/api/billing/checkout/strategy", {"strategy_id": "anything"}),
])
def test_unverified_account_cannot_start_any_checkout(client, rec, monkeypatch, path, body):
    logged_in_as(monkeypatch, USER)
    called = []
    monkeypatch.setattr(main.billing, "create_checkout_session", lambda *a, **k: called.append(1) or "https://stripe.test/x")
    rec.verified = False

    res = client.post(path, json=body) if body else client.post(path)

    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "email_not_verified"
    assert called == [], "Stripe must not be contacted for an unverified account"


@pytest.mark.parametrize("path,body", [
    ("/api/billing/checkout/export", None),
    ("/api/billing/checkout/pass", None),
    ("/api/billing/checkout/strategy", {"strategy_id": "anything"}),
])
def test_verified_account_reaches_stripe(client, rec, monkeypatch, path, body):
    logged_in_as(monkeypatch, USER)
    monkeypatch.setattr(main.billing, "create_checkout_session", lambda *a, **k: "https://stripe.test/x")
    rec.verified = True

    res = client.post(path, json=body) if body else client.post(path)

    assert res.status_code == 200
    assert res.json() == {"url": "https://stripe.test/x"}


@pytest.mark.parametrize("path,body", [
    ("/api/billing/checkout/export", None),
    ("/api/billing/checkout/pass", None),
    ("/api/billing/checkout/strategy", {"strategy_id": "anything"}),
])
def test_the_gate_is_dark_by_default_so_checkout_is_unchanged(client, rec, monkeypatch, path, body):
    """The deploy ships with REQUIRE_VERIFIED_EMAIL unset: an unverified account
    must reach Stripe exactly as it did before this feature existed, and the
    database must not even be asked."""
    monkeypatch.setattr(main.settings, "REQUIRE_VERIFIED_EMAIL", False, raising=False)
    logged_in_as(monkeypatch, USER)
    rec.verified = False

    async def must_not_be_called(uid):
        raise AssertionError("the gate must not touch the database while it is off")
    monkeypatch.setattr(main.db, "is_email_verified", must_not_be_called)
    monkeypatch.setattr(main.billing, "create_checkout_session", lambda *a, **k: "https://stripe.test/x")

    res = client.post(path, json=body) if body else client.post(path)

    assert res.status_code == 200
    assert res.json() == {"url": "https://stripe.test/x"}


def test_anonymous_request_still_gets_the_login_error_not_the_verify_error(client, rec, monkeypatch):
    logged_in_as(monkeypatch, None)

    def needs_login(*a, **k):
        raise main.billing.LoginRequired("log in first")
    monkeypatch.setattr(main.billing, "create_checkout_session", needs_login)

    res = client.post("/api/billing/checkout/pass")

    assert res.status_code == 401


def test_free_export_and_signup_paths_never_ask_for_verification(rec):
    """Guard against the gate creeping onto the free flow: exports are spent
    through _require_export_entitlement, which must not know about it."""
    import inspect
    assert "verified" not in inspect.getsource(main._require_export_entitlement)


# ------------------------------------------------------ POST /verify-email

def post_code(client, code):
    return client.post("/api/account/verify-email", json={"code": code})


def test_right_code_marks_the_account_verified(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    monkeypatch.setattr(main, "verify_email_otp", lambda email, code: USER)

    res = post_code(client, "123456")

    assert res.status_code == 200 and res.json() == {"verified": True}
    assert rec.marked == [USER]


def test_code_is_checked_against_this_accounts_email(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    seen = {}
    def fake(email, code):
        seen.update(email=email, code=code)
        return USER
    monkeypatch.setattr(main, "verify_email_otp", fake)

    post_code(client, " 123 456 ")

    assert seen == {"email": "trader@example.com", "code": "123456"}


def test_wrong_code_is_rejected_and_nothing_is_marked(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    monkeypatch.setattr(main, "verify_email_otp", lambda email, code: None)

    res = post_code(client, "000000")

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "invalid_code"
    assert rec.marked == []


def test_a_code_that_belongs_to_another_account_is_rejected(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    monkeypatch.setattr(main, "verify_email_otp", lambda email, code: OTHER)

    res = post_code(client, "123456")

    assert res.status_code == 400
    assert rec.marked == []


@pytest.mark.parametrize("bad", ["", "abc123", "12", "12345678901", "1234 5x"])
def test_malformed_codes_never_reach_supabase(client, rec, monkeypatch, bad):
    logged_in_as(monkeypatch, USER)
    monkeypatch.setattr(main, "verify_email_otp", lambda *a: pytest.fail("should not be called"))

    assert post_code(client, bad).status_code == 400


def test_login_is_required(client, rec, monkeypatch):
    logged_in_as(monkeypatch, None)

    assert post_code(client, "123456").status_code == 401


def test_guessing_is_throttled_after_five_wrong_codes(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    calls = []
    monkeypatch.setattr(main, "verify_email_otp", lambda email, code: calls.append(1) and None)

    statuses = [post_code(client, "000000").status_code for _ in range(7)]

    assert statuses[:5] == [400] * 5
    assert statuses[5:] == [429, 429]
    assert len(calls) == 5, "throttled attempts must not reach Supabase"


def test_a_success_clears_earlier_failures(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    answers = iter([None, None, USER])
    monkeypatch.setattr(main, "verify_email_otp", lambda email, code: next(answers))

    for _ in range(2):
        assert post_code(client, "000000").status_code == 400
    assert post_code(client, "123456").status_code == 200
    assert main._VERIFY_FAILURES.get(USER) is None


def test_supabase_outage_is_a_503_not_a_wrong_code(client, rec, monkeypatch):
    logged_in_as(monkeypatch, USER)
    def down(email, code):
        raise supabase_auth.OtpServiceError("down")
    monkeypatch.setattr(main, "verify_email_otp", down)

    res = post_code(client, "123456")

    assert res.status_code == 503
    assert main._VERIFY_FAILURES.get(USER) in (None, []), "an outage must not count as a wrong guess"


# --------------------------------------------- supabase_auth.verify_email_otp

class FakeResponse:
    def __init__(self, body):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def patch_urlopen(monkeypatch, behaviour):
    monkeypatch.setattr(supabase_auth._urlrequest, "urlopen", behaviour)
    monkeypatch.setattr(supabase_auth.settings, "SUPABASE_URL", "https://x.supabase.test", raising=False)
    monkeypatch.setattr(supabase_auth.settings, "SUPABASE_ANON_KEY", "anon", raising=False)


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def test_otp_success_returns_the_user_id(monkeypatch):
    patch_urlopen(monkeypatch, lambda req, timeout: FakeResponse(b'{"access_token":"t","user":{"id":"abc"}}'))
    assert supabase_auth.verify_email_otp("a@b.c", "123456") == "abc"


def test_otp_sends_the_documented_verify_request(monkeypatch):
    seen = {}
    def capture(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = req.data
        seen["apikey"] = req.get_header("Apikey")
        return FakeResponse(b'{"user":{"id":"abc"}}')
    patch_urlopen(monkeypatch, capture)

    supabase_auth.verify_email_otp("a@b.c", "123456")

    assert seen["url"] == "https://x.supabase.test/auth/v1/verify"
    assert seen["apikey"] == "anon"
    assert b'"type": "email"' in seen["body"] and b'"token": "123456"' in seen["body"]


@pytest.mark.parametrize("code", [400, 401, 403, 422])
def test_otp_wrong_or_expired_code_is_none(monkeypatch, code):
    def raise_it(req, timeout):
        raise http_error(code)
    patch_urlopen(monkeypatch, raise_it)
    assert supabase_auth.verify_email_otp("a@b.c", "000000") is None


def test_otp_429_is_rate_limited(monkeypatch):
    def raise_it(req, timeout):
        raise http_error(429)
    patch_urlopen(monkeypatch, raise_it)
    with pytest.raises(supabase_auth.OtpRateLimited):
        supabase_auth.verify_email_otp("a@b.c", "000000")


@pytest.mark.parametrize("exc", [http_error(500), urllib.error.URLError("no route"), TimeoutError()])
def test_otp_infrastructure_failure_is_a_service_error(monkeypatch, exc):
    def raise_it(req, timeout):
        raise exc
    patch_urlopen(monkeypatch, raise_it)
    with pytest.raises(supabase_auth.OtpServiceError):
        supabase_auth.verify_email_otp("a@b.c", "000000")


def test_otp_without_supabase_config_is_a_service_error(monkeypatch):
    monkeypatch.setattr(supabase_auth.settings, "SUPABASE_URL", None, raising=False)
    with pytest.raises(supabase_auth.OtpServiceError):
        supabase_auth.verify_email_otp("a@b.c", "123456")


# ---------------------------------------------------- db fail-open behaviour

def test_missing_column_fails_open_so_a_forgotten_migration_cannot_block_sales(monkeypatch, capsys):
    class Boom:
        def __enter__(self):
            raise pg_errors.UndefinedColumn("column email_verified_at does not exist")
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(db, "_conn", lambda: Boom())

    assert db._is_email_verified_sync(USER) is True
    assert "email_verified_at is missing" in capsys.readouterr().out
