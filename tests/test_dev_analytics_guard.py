"""
Analytics written from a developer's own machine must never reach the
production database (2026-09-20: local testing of a feature wrote 60 rows of
`guide_step_done`, plus page-view style events, into the real analytics_events
table, because a local backend reads .env and .env points at production).

`_is_dev_request` decides from Origin / Referer; the analytics beacon, the
strategy save log and the Strategy-of-the-Week download log all honour it.
Entitlement writes (exports, purchases) deliberately do not.

Nothing here may touch a real database: the autouse fixture makes any real
connection raise.

Run with:
    pytest tests/test_dev_analytics_guard.py -v
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import main
from device_identity import get_device_id

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "simple_single.json").read_text())
PROD = "https://algopuzzle.com"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test tried to open a real database connection")
    monkeypatch.setattr(db, "_get_pool", boom)
    monkeypatch.setattr(main.settings, "DATABASE_URL", "postgresql://test-only", raising=False)
    monkeypatch.delenv("LOG_DEV_ANALYTICS", raising=False)
    main.app.dependency_overrides[get_device_id] = lambda: "device-test"
    yield
    main.app.dependency_overrides.clear()


class Calls:
    def __init__(self):
        self.analytics, self.saves, self.downloads = [], [], []


@pytest.fixture
def calls(monkeypatch):
    c = Calls()

    async def log_event(device_id, user_id, event_type, metadata, path):
        c.analytics.append(event_type)

    async def log_save(**kw):
        c.saves.append(kw["strategy_id"])

    async def log_download(**kw):
        c.downloads.append(kw["strategy_id"])

    async def no_pass(uid):
        return False

    monkeypatch.setattr(main.db, "log_analytics_event", log_event)
    monkeypatch.setattr(main.db, "log_strategy_save", log_save)
    monkeypatch.setattr(main.db, "log_sotw_download", log_download)
    monkeypatch.setattr(main.db, "has_active_pass", no_pass)
    monkeypatch.setattr(main, "get_current_user", lambda request: None)
    return c


@pytest.fixture
def client():
    return TestClient(main.app)


def beacon(client, headers=None, event="builder_opened"):
    return client.post("/api/analytics/event", json={"event_type": event, "metadata": None, "path": "/index_1.html"}, headers=headers or {})


# ------------------------------------------------------------ the decision

@pytest.mark.parametrize("headers", [
    {"Origin": "http://localhost:8080"},
    {"Origin": "http://127.0.0.1:8080"},
    {"Origin": "http://localhost"},
    {"Origin": "http://[::1]:8080"},
    {"Origin": "null"},                                   # a file:// page
    {"Referer": "http://localhost:8080/index_1.html"},
    {"Referer": "http://127.0.0.1:5500/index_1.html?x=1"},
    {"Origin": PROD, "Referer": "http://localhost:8080/"},  # any one local header is enough
])
def test_local_requests_are_recognised(client, calls, headers):
    assert beacon(client, headers).status_code == 204
    assert calls.analytics == []


@pytest.mark.parametrize("headers", [
    {"Origin": PROD},
    {"Origin": "https://algopuzzle-frontend-staging.onrender.com"},
    {"Referer": PROD + "/index_1.html"},
    {},                                                    # no browser headers at all
])
def test_real_traffic_is_still_recorded(client, calls, headers):
    assert beacon(client, headers).status_code == 204
    assert calls.analytics == ["builder_opened"]


def test_a_hostname_that_merely_contains_localhost_is_not_treated_as_local(client, calls):
    beacon(client, {"Origin": "https://notlocalhost.example.com"})
    beacon(client, {"Origin": "https://localhost.evil.example"})
    assert len(calls.analytics) == 2


def test_the_opt_in_switch_turns_it_off(client, calls, monkeypatch):
    monkeypatch.setenv("LOG_DEV_ANALYTICS", "1")
    beacon(client, {"Origin": "http://localhost:8080"})
    assert calls.analytics == ["builder_opened"]


# ----------------------------------------------------- the other two loggers

def test_strategy_save_log_skips_local_saves(client, calls):
    res = client.post("/api/strategies/log-save", json={"strategy_id": "s1", "strategy_name": "x", "config": FIXTURE},
                      headers={"Origin": "http://localhost:8080"})
    assert res.json() == {"logged": False}
    assert calls.saves == []


def test_strategy_save_log_records_real_saves(client, calls):
    res = client.post("/api/strategies/log-save", json={"strategy_id": "s1", "strategy_name": "x", "config": FIXTURE},
                      headers={"Origin": PROD})
    assert res.json() == {"logged": True}
    assert calls.saves == ["s1"]


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


@pytest.mark.anyio
async def _download(origin):
    await main._log_sotw_download_best_effort("some-id", "mt5", "device-test", FakeRequest({"origin": origin} if origin else {}))


def test_marketplace_download_log_skips_local_downloads(calls):
    import asyncio
    asyncio.run(_download("http://localhost:8080"))
    assert calls.downloads == []
    asyncio.run(_download(PROD))
    assert calls.downloads == ["some-id"]
