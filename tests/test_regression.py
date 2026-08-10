"""
Regression tests for the StrategyIR refactor (parse_strategy / render_mql5 /
render_csharp) - see HANDOFF.md for why this shape exists.

`tests/fixtures/*.json` are Blockly-serialized workspace payloads captured
BEFORE the IR refactor (generate_mql5(WorkspaceConfig) directly). The
matching `*.mq5.expected` / `*.readme.expected` files are that pre-refactor
output. These tests assert the post-refactor pipeline
(parse_strategy -> render_mql5 / generate_readme_mql5) still produces byte-
identical output (module docstring's "Generated: <timestamp>" line excepted),
and that render_csharp() produces syntactically sane C# for the same inputs.

Run with:
    pytest tests/test_regression.py -v
"""

import json
import re
from pathlib import Path

import pytest

from main import (
    WorkspaceConfig,
    parse_strategy,
    render_mql5,
    render_csharp,
    generate_readme_mql5,
    generate_readme_csharp,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = [p.stem for p in FIXTURES_DIR.glob("*.json")]


def _normalize(text: str) -> str:
    """Strip the "Generated: <timestamp>" line so comparisons aren't
    sensitive to when the test happens to run."""
    return re.sub(r"Generated: .*", "Generated: <NORMALIZED>", text)


def _load_fixture(name: str) -> WorkspaceConfig:
    with open(FIXTURES_DIR / f"{name}.json", encoding="utf-8") as f:
        return WorkspaceConfig(**json.load(f))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_mql5_output_unchanged_by_ir_refactor(name):
    config = _load_fixture(name)
    ir = parse_strategy(config)
    actual = render_mql5(ir)

    expected = (FIXTURES_DIR / f"{name}.mq5.expected").read_text(encoding="utf-8")

    assert _normalize(actual) == _normalize(expected)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_readme_output_unchanged_by_ir_refactor(name):
    config = _load_fixture(name)
    ir = parse_strategy(config)
    actual = generate_readme_mql5(ir)
    expected = (FIXTURES_DIR / f"{name}.readme.expected").read_text(encoding="utf-8")
    assert _normalize(actual) == _normalize(expected)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_csharp_output_is_syntactically_sane(name):
    """Not a real compile (no cTrader Automate available here - see
    HANDOFF.md) - just the cheap structural checks that would catch a
    broken template: balanced braces/parens, and no leftover <<TOKEN>>
    placeholders from the .replace()-based substitution."""
    config = _load_fixture(name)
    ir = parse_strategy(config)
    cs = render_csharp(ir)

    assert cs.count("{") == cs.count("}")
    assert cs.count("(") == cs.count(")")
    assert "<<" not in cs and ">>" not in cs
    assert "namespace cAlgo.Robots" in cs
    assert "class ScratchForTradersBot" in cs

    # README should also render without error and mention every rule's asset.
    readme = generate_readme_csharp(ir)
    for rule in ir.rules:
        assert rule.asset in readme
