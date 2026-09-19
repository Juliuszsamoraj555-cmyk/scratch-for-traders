"""
Consistency tests for the Strategy of the Week marketplace - the catalog the
pages show (assets/marketplace-data.js), the backend registry that actually
generates the downloads (marketplace_strategies.py), and the billing gate.

Why this exists: on 2026-08-29 a pick was swapped in the catalog and the tier
table but NOT in the config registry, so the id customers were buying
("usdcad-macross-m15") had no config and every download of it 404'd - found
only on 2026-09-19, three weeks after purchases opened. Every weekly rotation
edits these three places by hand or by script; this makes a mismatch fail
loudly at test time instead of at a paying customer's download click.

Run with:
    pytest tests/test_marketplace_registry.py -v
"""

import json
import re
from pathlib import Path

import pytest

import marketplace_strategies as ms
from main import WorkspaceConfig, parse_strategy, render_mql5, render_mql4, render_csharp

ROOT = Path(__file__).resolve().parent.parent
CATALOG_JS = (ROOT / "assets" / "marketplace-data.js").read_text(encoding="utf-8")


def _catalog_entries() -> list[dict]:
    """Every top-level entry of MARKETPLACE_STRATEGIES, reduced to the fields
    these tests need. Anchored on the exact entry shape apply_rotation.py
    writes ('  {' / '    id: ') - a loose `id:` regex also matches
    `strategy_config_id:` (a bug this project has already hit once)."""
    start = CATALOG_JS.index("const MARKETPLACE_STRATEGIES = [\n")
    end = CATALOG_JS.index("\n];\n", start)
    body = "\n" + CATALOG_JS[start:end]
    entries = []
    for chunk in body.split("\n  {\n    id: ")[1:]:
        sid = re.match(r'"([a-z0-9\-]+)"', chunk).group(1)
        entries.append({
            "id": sid,
            "tier": re.search(r'\n    tier: "(\w+)"', chunk).group(1),
            "archived": "\n    archived: true," in chunk,
            "featured": "\n    featured: true," in chunk,
            "config_id": re.search(r'\n    strategy_config_id: "([a-z0-9\-]+)"', chunk).group(1),
            "blockly": json.loads(re.search(r"\n    blockly_state: (\{.*\}),\n", chunk).group(1)),
        })
    return entries


ENTRIES = _catalog_entries()
IDS = [e["id"] for e in ENTRIES]


def test_catalog_parsed_something():
    assert len(ENTRIES) >= 6
    assert len(set(IDS)) == len(IDS), "duplicate ids in the catalog"


def test_catalog_registry_and_tiers_cover_exactly_the_same_ids():
    assert set(IDS) == set(ms.MARKETPLACE_STRATEGY_TIERS), "catalog vs tier table"
    assert set(IDS) == set(ms.MARKETPLACE_STRATEGY_CONFIGS), "catalog vs config registry"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_catalog_tier_matches_backend_tier(entry):
    backend = ms.get_marketplace_strategy_tier(entry["id"])
    if entry["tier"] == "free":
        assert backend == "free"
    else:
        assert backend in ("featured", "standard")
        assert (backend == "featured") == entry["featured"], "featured flag vs $7.99 tier"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_config_id_points_at_a_real_config(entry):
    assert entry["config_id"] == entry["id"]
    assert ms.get_marketplace_strategy_config(entry["config_id"]) is not None


@pytest.mark.parametrize("sid", IDS)
def test_every_config_parses_and_renders_on_all_three_platforms(sid):
    ir = parse_strategy(WorkspaceConfig(**ms.get_marketplace_strategy_config(sid)))
    assert "OnTick" in render_mql5(ir)
    assert "OnTick" in render_mql4(ir) or "start" in render_mql4(ir)
    assert "class" in render_csharp(ir)


def test_archived_flag_matches_backend_archive_set():
    assert {e["id"] for e in ENTRIES if e["archived"]} == set(ms.ARCHIVED_STRATEGY_IDS)


def test_active_lineup_is_one_free_one_featured_and_the_rest_standard():
    active = [e for e in ENTRIES if not e["archived"]]
    assert sum(e["tier"] == "free" for e in active) == 1
    assert sum(e["featured"] for e in active) == 1
    assert len(active) == 6


def test_archived_strategies_cannot_be_bought_but_stay_downloadable():
    import billing

    archived_paid = [e["id"] for e in ENTRIES if e["archived"] and e["tier"] == "paid"]
    assert archived_paid, "expected at least one archived paid strategy after a rotation"
    for sid in archived_paid:
        # Downloads: the registry must still resolve it, or owners lose access.
        assert ms.get_marketplace_strategy_config(sid) is not None
        assert ms.get_marketplace_strategy_tier(sid) in ("featured", "standard")
        # Purchases: billing refuses it. (Stripe config is checked after the id
        # in create_checkout_session, so this needs no Stripe keys to reach.)
        with pytest.raises((ValueError, billing.LoginRequired, billing.BillingNotConfigured)) as exc:
            billing.create_checkout_session("dev", "strategy_purchase", user_id="u", strategy_id=sid)
        if isinstance(exc.value, ValueError):
            assert "no longer on sale" in str(exc.value)


def test_billing_still_sells_active_paid_strategies_past_the_archive_gate():
    """Guards the other direction: the archive gate must not block this week's
    strategies. Without Stripe keys it stops at BillingNotConfigured, which is
    AFTER the archive check - so anything but 'no longer on sale' is a pass."""
    import billing

    active_paid = [e["id"] for e in ENTRIES if not e["archived"] and e["tier"] == "paid"]
    for sid in active_paid:
        try:
            billing.create_checkout_session("dev", "strategy_purchase", user_id="u", strategy_id=sid)
        except ValueError as e:
            assert "no longer on sale" not in str(e), f"{sid} wrongly blocked as archived"
        except Exception:
            pass  # not configured / network - irrelevant to this check
