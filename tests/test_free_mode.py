"""
Tests for free mode (settings.FREE_MODE, 2026-09-24): exports need no
account and deduct nothing, /api/billing/status reports free_mode so the
frontend hides every upgrade element, and the export/pass checkouts are
refused - while marketplace checkout keeps working. With FREE_MODE off the
original paid behaviour (401 without login) must come back unchanged.

Nothing here may touch the real database - .env points at production.

Run with:
    pytest tests/test_free_mode.py -v
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import main
from device_identity import get_device_id


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "multiple_positions.json").read_text(encoding="utf-8"))
PROD_ORIGIN = {"Origin": "https://algopuzzle.app"}


@pytest.fixture(autouse=True)
def _never_touch_production(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test tried to open a real database connection")
    monkeypatch.setattr(db, "_get_pool", boom)
    monkeypatch.setattr(main.settings, "DATABASE_URL", "postgresql://test-only", raising=False)
    monkeypatch.setattr(main.settings, "REQUIRE_VERIFIED_EMAIL", False, raising=False)
    monkeypatch.setattr(main, "get_current_user", lambda request: None)
    main.app.dependency_overrides[get_device_id] = lambda: "device-test"
    yield
    main.app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def logged(monkeypatch):
    calls = []

    async def log_free(device_id, user_id, platform, meta=None):
        calls.append((device_id, user_id, platform))

    async def must_not_run(*a, **k):
        raise AssertionError("free mode must not touch the paid entitlement")

    monkeypatch.setattr(main.db, "log_free_mode_export", log_free)
    monkeypatch.setattr(main.db, "consume_account_export", must_not_run)
    monkeypatch.setattr(main.db, "has_active_pass", must_not_run)
    return calls


@pytest.mark.parametrize("path,platform", [
    ("/api/generate", "mt5"), ("/api/generate/ctrader", "ctrader"), ("/api/generate/mt4", "mt4"),
])
def test_free_mode_exports_without_login_every_time(client, logged, monkeypatch, path, platform):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)
    for _ in range(5):  # well past the old 2-free limit
        res = client.post(path, json=FIXTURE, headers=PROD_ORIGIN)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/zip"
    assert logged == [("device-test", None, platform)] * 5


def test_free_mode_export_survives_a_logging_failure(client, monkeypatch):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)

    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(main.db, "log_free_mode_export", broken)
    assert client.post("/api/generate", json=FIXTURE, headers=PROD_ORIGIN).status_code == 200


def test_free_mode_does_not_log_localhost_exports(client, logged, monkeypatch):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)
    res = client.post("/api/generate", json=FIXTURE, headers={"Origin": "http://localhost:8080"})
    assert res.status_code == 200
    assert logged == []


def test_free_mode_status_flag(client, monkeypatch):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)
    body = client.get("/api/billing/status").json()
    assert body["free_mode"] is True


@pytest.mark.parametrize("path", ["/api/billing/checkout/export", "/api/billing/checkout/pass"])
def test_free_mode_refuses_export_and_pass_checkout(client, monkeypatch, path):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)
    called = []
    monkeypatch.setattr(main.billing, "create_checkout_session", lambda *a, **k: called.append(1) or "https://stripe.test/x")
    res = client.post(path)
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "free_mode"
    assert called == []


def test_free_mode_keeps_marketplace_checkout(client, monkeypatch):
    monkeypatch.setattr(main.settings, "FREE_MODE", True, raising=False)
    monkeypatch.setattr(main.billing, "create_checkout_session", lambda *a, **k: "https://stripe.test/x")
    res = client.post("/api/billing/checkout/strategy", json={"strategy_id": "anything"})
    assert res.status_code == 200
    assert res.json() == {"url": "https://stripe.test/x"}


def test_paid_mode_is_unchanged(client, monkeypatch):
    monkeypatch.setattr(main.settings, "FREE_MODE", False, raising=False)
    res = client.post("/api/generate", json=FIXTURE, headers=PROD_ORIGIN)
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "login_required"
    assert client.get("/api/billing/status").json()["free_mode"] is False
