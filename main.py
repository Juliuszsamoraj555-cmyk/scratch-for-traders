"""
AlgoPuzzle - Backend
------------------------------
FastAPI service that receives a serialized Blockly workspace (JSON) and
exports it as an executable trading strategy for either MetaTrader 5
(.mq5 Expert Advisor) or cTrader (.cs cBot), packaged with a README.txt
into a downloadable .zip.

Architecture: the Blockly JSON is parsed & validated once into a small,
platform-agnostic StrategyIR (see `parse_strategy()`), and each export
target has its own renderer (`render_mql5()` / `render_csharp()`) that
walks that SAME intermediate representation. What a condition/operand/
action MEANS lives in exactly one place; a renderer only decides how to
express it in a given language. See docs/HANDOFF.md for the story of why this
shape was chosen (in short: two renderers independently re-implementing
"what does this Blockly tree mean" is exactly the kind of two-places-that-
can-silently-disagree bug this project has hit before).

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

import io
import itertools
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import billing
import db
from device_identity import get_client_ip, get_device_id
from supabase_auth import get_current_user
from settings import settings

# --------------------------------------------------------------------------
# FastAPI app setup
# --------------------------------------------------------------------------

app = FastAPI(title="AlgoPuzzle - Strategy Generator", version="0.2.0")

# CORS restricted to known origins (settings.ALLOWED_ORIGINS, see
# .env.example) - required as soon as `allow_credentials=True` is set,
# since browsers reject a wildcard "*" origin alongside credentialed
# (cookie-bearing) requests. The device-identity cookie (see
# device_identity.py) is what needs this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Pydantic models describing the serialized Blockly workspace (API contract)
# --------------------------------------------------------------------------
# The frontend performs its own custom serialization (see index_1.html) that
# walks the Blockly block tree and emits this compact, backend-friendly
# JSON shape -- rather than relying on Blockly's generic XML/JSON dump.
#
# These models validate the WIRE FORMAT only. `parse_strategy()` further
# down converts a validated WorkspaceConfig into the platform-agnostic
# StrategyIR that the actual renderers consume - the two are kept as
# separate types on purpose, so the API contract and the internal
# representation used by code generation are free to diverge later
# without one dragging the other along.

class Operand(BaseModel):
    """One side of a comparison, OR a Stop-Loss/Take-Profit distance value:
    an indicator, candle/volume data, a literal number/pips/percent risk
    value, or a MULTIPLY node combining two nested operands (e.g. `2 * ATR(14)`)."""
    kind: Literal["MA", "RSI", "MACD", "BANDS", "CANDLE", "ATR", "STOCH", "VOLUME", "NUMBER", "MULTIPLY", "RISK_VALUE"]
    period: Optional[int] = None          # for MA / RSI / BANDS / ATR
    ma_type: Optional[str] = None         # MODE_SMA / MODE_EMA               (MA)
    deviation: Optional[float] = None     # standard-deviation multiplier     (BANDS)
    band: Optional[str] = None            # UPPER / MIDDLE / LOWER            (BANDS)
    line: Optional[str] = None            # MAIN / HIST                      (MACD)
    candle_type: Optional[str] = None     # CURRENT / PREV_OPEN / PREV_CLOSE / PREV_HIGH / PREV_LOW (CANDLE)
    k_period: Optional[int] = None        # %K period                         (STOCH)
    d_period: Optional[int] = None        # %D period                         (STOCH)
    slowing: Optional[int] = None         # slowing                           (STOCH)
    stoch_line: Optional[str] = None      # K / D                             (STOCH)
    volume_bar: Optional[str] = None      # CURRENT / PREVIOUS                (VOLUME)
    unit: Optional[str] = None            # PIPS / PERCENT / PRICE            (RISK_VALUE)
    value: Optional[float] = None         # for NUMBER / RISK_VALUE
    left: Optional["Operand"] = None      # for MULTIPLY
    right: Optional["Operand"] = None     # for MULTIPLY

    @field_validator("period")
    @classmethod
    def period_must_be_positive(cls, v):
        if v is not None and v < 1:
            raise ValueError("Indicator period must be >= 1")
        return v

    @field_validator("deviation")
    @classmethod
    def deviation_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Bollinger Band deviation must be > 0")
        return v


Operand.model_rebuild()


class ComparisonNode(BaseModel):
    """A leaf condition: compare two operands directly."""
    type: Literal["comparison"] = "comparison"
    left: Operand
    operator: Literal[">", "<", "=="]
    right: Operand


class LogicalNode(BaseModel):
    """A branch condition: combine two nested conditions with AND / OR.
    `left` and `right` may themselves be ComparisonNode or LogicalNode,
    which lets the block builder nest arbitrarily deep condition trees."""
    type: Literal["logical"] = "logical"
    operator: Literal["AND", "OR"]
    left: "ConditionNode"
    right: "ConditionNode"


ConditionNode = Annotated[Union[ComparisonNode, LogicalNode], Field(discriminator="type")]
LogicalNode.model_rebuild()


class TradeAction(BaseModel):
    direction: Literal["BUY", "SELL"]
    lot: float = Field(gt=0)
    # Stop Loss / Take Profit are dynamic operand trees. The recommended leaf
    # is a RISK_VALUE (a plain number interpreted as Pips or Percent-of-entry-
    # price, converted to a real price distance at compile/run time), but any
    # operand tree works - e.g. `2 * ATR(14)` for volatility-based sizing, or
    # a raw NUMBER treated as an already-in-price-units distance (advanced use).
    # None means "no SL/TP".
    sl: Optional[Operand] = None
    tp: Optional[Operand] = None


class RuleConfig(BaseModel):
    """One independent "IF (Strategy Rule)" block: its own asset, timeframe,
    condition and actions. A workspace can contain more than one of these -
    each is exported as its own self-contained trigger inside the SAME
    generated EA/cBot (own indicator handles, own position tracking, own
    magic number - see StrategyIR/RuleIR and each renderer)."""
    asset: str
    timeframe: str
    condition: ConditionNode
    actions: List[TradeAction]
    # How many separate "rounds" of the THEN actions can be open on this
    # symbol at once. 1 (default) reproduces the original behaviour: a new
    # signal is ignored entirely while any position this EA opened is still
    # open. >1 lets a later signal open more trades on top of still-open
    # ones, up to this many concurrent rounds.
    max_positions: int = Field(default=1, ge=1, le=100)

    @field_validator("actions")
    @classmethod
    def at_least_one_action(cls, v):
        if not v:
            raise ValueError("At least one trade action is required")
        return v

    @field_validator("asset")
    @classmethod
    def asset_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Asset is required")
        return v

    @field_validator("timeframe")
    @classmethod
    def timeframe_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Timeframe is required")
        return v


class WorkspaceConfig(BaseModel):
    """The full wire payload: one or more independent strategy rules,
    always a list (even for a single rule) so there's exactly one shape to
    reason about instead of a singular/plural split."""
    rules: List[RuleConfig]

    @field_validator("rules")
    @classmethod
    def at_least_one_rule(cls, v):
        if not v:
            raise ValueError('At least one "IF (Strategy Rule)" block is required')
        return v


# --------------------------------------------------------------------------
# Strategy IR (platform-agnostic) + parsing/validation
# --------------------------------------------------------------------------
# Every renderer (MQL5, C#/cTrader, and whatever comes next) consumes ONLY
# these dataclasses - never the Blockly JSON or the Pydantic API models
# directly. Adding a new export target should mean "write a new
# render_xxx(ir)", never "re-implement the Blockly-tree walk a second time
# and hope it stays in sync with the first one".

VALID_TIMEFRAMES = {
    "PERIOD_M1", "PERIOD_M5", "PERIOD_M15", "PERIOD_M30",
    "PERIOD_H1", "PERIOD_H4", "PERIOD_D1", "PERIOD_W1", "PERIOD_MN1",
}
VALID_MA_TYPES = {"MODE_SMA", "MODE_EMA"}
VALID_BANDS = {"UPPER", "MIDDLE", "LOWER"}
VALID_MACD_LINES = {"MAIN", "HIST"}
VALID_CANDLE_TYPES = {"CURRENT", "PREV_OPEN", "PREV_CLOSE", "PREV_HIGH", "PREV_LOW"}
VALID_STOCH_LINES = {"K", "D"}
VALID_VOLUME_BARS = {"CURRENT", "PREVIOUS"}
VALID_RISK_UNITS = {"PIPS", "PERCENT", "PRICE"}

# MACD is always computed with the standard 12 / 26 / 9 parameters, per spec.
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

# iBands() buffer layout: 0 = BASE_LINE (middle), 1 = UPPER_BAND, 2 = LOWER_BAND
_BANDS_BUFFER_INDEX = {"MIDDLE": 0, "UPPER": 1, "LOWER": 2}

# iStochastic() buffer layout: 0 = MAIN_LINE (%K), 1 = SIGNAL_LINE (%D)
_STOCH_BUFFER_INDEX = {"K": 0, "D": 1}

# Placeholder substituted with the actual entry-price variable name inside
# each renderer's action-block builder once it's known (Ask for BUY, Bid
# for SELL). Lets RISK_VALUE(unit=PERCENT) express "X% of entry price" even
# though the operand tree is built before we know which action/direction
# it belongs to. Shared across renderers so this trick isn't reinvented
# per platform.
_ENTRY_PRICE_PLACEHOLDER = "__ENTRY_PRICE__"


class StrategyValidationError(Exception):
    """A strategy failed validation. Deliberately not an HTTPException -
    this layer has no FastAPI/HTTP dependency, so parse_strategy() can be
    reused outside a web request (tests, a future CLI, ...). The API
    endpoints below catch this and translate it to HTTPException(400)."""


@dataclass
class OperandIR:
    kind: str
    period: Optional[int] = None
    ma_type: Optional[str] = None
    deviation: Optional[float] = None
    band: Optional[str] = None
    line: Optional[str] = None
    candle_type: Optional[str] = None
    k_period: Optional[int] = None
    d_period: Optional[int] = None
    slowing: Optional[int] = None
    stoch_line: Optional[str] = None
    volume_bar: Optional[str] = None
    unit: Optional[str] = None
    value: Optional[float] = None
    left: Optional["OperandIR"] = None
    right: Optional["OperandIR"] = None


@dataclass
class ComparisonIR:
    left: OperandIR
    operator: str
    right: OperandIR


@dataclass
class LogicalIR:
    operator: str
    left: "ConditionIR"
    right: "ConditionIR"


ConditionIR = Union[ComparisonIR, LogicalIR]


@dataclass
class ActionIR:
    direction: str  # "BUY" / "SELL"
    lot: float
    sl: Optional[OperandIR]
    tp: Optional[OperandIR]


@dataclass
class RuleIR:
    """One independent trigger: platform-agnostic form of a RuleConfig, plus
    the fields every renderer needs to keep multiple rules from stepping on
    each other inside one generated file - `rule_index` (for unique
    variable/handle naming) and `magic` (a distinct MT5/MT4 magic number /
    cTrader label so each rule's positions can be told apart, even when two
    rules trade the same asset)."""
    asset: str
    timeframe: str  # MT5-style constant, e.g. "PERIOD_M1" - each renderer maps this to its own timeframe type
    condition: ConditionIR
    actions: List[ActionIR]
    max_positions: int = 1
    rule_index: int = 0
    magic: int = 0


@dataclass
class StrategyIR:
    rules: List[RuleIR]


def _validate_operand(op: Operand) -> None:
    if op.kind == "MA" and (op.ma_type not in VALID_MA_TYPES or not op.period):
        raise StrategyValidationError("Invalid Moving Average configuration")
    if op.kind == "RSI" and not op.period:
        raise StrategyValidationError("Invalid RSI configuration")
    if op.kind == "ATR" and not op.period:
        raise StrategyValidationError("Invalid ATR configuration")
    if op.kind == "BANDS" and (op.band not in VALID_BANDS or not op.period or not op.deviation):
        raise StrategyValidationError("Invalid Bollinger Bands configuration")
    if op.kind == "STOCH" and (
        op.stoch_line not in VALID_STOCH_LINES or not op.k_period or not op.d_period or not op.slowing
    ):
        raise StrategyValidationError("Invalid Stochastic Oscillator configuration")
    if op.kind == "MACD" and op.line not in VALID_MACD_LINES:
        raise StrategyValidationError("Invalid MACD configuration")
    if op.kind == "CANDLE" and op.candle_type not in VALID_CANDLE_TYPES:
        raise StrategyValidationError("Invalid Candle Data configuration")
    if op.kind == "VOLUME" and op.volume_bar not in VALID_VOLUME_BARS:
        raise StrategyValidationError("Invalid Volume configuration")
    if op.kind == "RISK_VALUE" and (op.unit not in VALID_RISK_UNITS or op.value is None or op.value < 0):
        raise StrategyValidationError("Invalid risk value (Pips/Percent/Price) configuration")
    if op.kind == "NUMBER" and op.value is None:
        raise StrategyValidationError("Number block is missing a value")
    if op.kind == "MULTIPLY":
        if op.left is None or op.right is None:
            raise StrategyValidationError("A Multiply block is missing one of its two inputs")
        _validate_operand(op.left)
        _validate_operand(op.right)


def _validate_condition(node: "ConditionNode") -> None:
    """Recursively validate every operand in a (possibly nested) condition tree."""
    if node.type == "comparison":
        _validate_operand(node.left)
        _validate_operand(node.right)
    else:  # logical
        _validate_condition(node.left)
        _validate_condition(node.right)


def _operand_to_ir(op: Optional[Operand]) -> Optional[OperandIR]:
    if op is None:
        return None
    return OperandIR(
        kind=op.kind, period=op.period, ma_type=op.ma_type, deviation=op.deviation,
        band=op.band, line=op.line, candle_type=op.candle_type, k_period=op.k_period,
        d_period=op.d_period, slowing=op.slowing, stoch_line=op.stoch_line,
        volume_bar=op.volume_bar, unit=op.unit, value=op.value,
        left=_operand_to_ir(op.left), right=_operand_to_ir(op.right),
    )


def _condition_to_ir(node: "ConditionNode") -> ConditionIR:
    if node.type == "comparison":
        return ComparisonIR(left=_operand_to_ir(node.left), operator=node.operator, right=_operand_to_ir(node.right))
    return LogicalIR(operator=node.operator, left=_condition_to_ir(node.left), right=_condition_to_ir(node.right))


def _action_to_ir(action: TradeAction) -> ActionIR:
    return ActionIR(direction=action.direction, lot=action.lot, sl=_operand_to_ir(action.sl), tp=_operand_to_ir(action.tp))


# Magic numbers assigned to rules start here (rule 0 -> 100000, rule 1 ->
# 100001, ...) - large and distinctive enough to be unlikely to collide
# with another EA's own magic number on the same account, and derived
# directly from rule position so it's stable across re-exports of the same
# workspace (no randomness to make diffing two generated files harder).
_MAGIC_NUMBER_BASE = 100000


def parse_strategy(config: WorkspaceConfig) -> StrategyIR:
    """Validate a Blockly-derived WorkspaceConfig and convert it into the
    platform-agnostic StrategyIR every renderer consumes. This is the ONE
    place that interprets what a Blockly-serialized strategy means - both
    render_mql5() and render_csharp() (and any future renderer) work from
    its output, never from the raw JSON or the Pydantic models again.

    A workspace can contain more than one independent "IF" rule (see
    RuleConfig) - each becomes its own RuleIR with a distinct magic number,
    and every renderer generates one combined EA/cBot that runs all of them
    side by side, each managing its own positions independently."""
    rules: List[RuleIR] = []
    for i, rule_config in enumerate(config.rules):
        if rule_config.timeframe not in VALID_TIMEFRAMES:
            raise StrategyValidationError(f"Rule {i + 1} ({rule_config.asset}): unsupported timeframe: {rule_config.timeframe}")

        _validate_condition(rule_config.condition)
        for action in rule_config.actions:
            if action.sl is not None:
                _validate_operand(action.sl)
            if action.tp is not None:
                _validate_operand(action.tp)

        rules.append(RuleIR(
            asset=rule_config.asset,
            timeframe=rule_config.timeframe,
            condition=_condition_to_ir(rule_config.condition),
            actions=[_action_to_ir(a) for a in rule_config.actions],
            max_positions=rule_config.max_positions,
            rule_index=i,
            magic=_MAGIC_NUMBER_BASE + i,
        ))

    return StrategyIR(rules=rules)


# Indicator-shaped operand kinds, as opposed to plain values (NUMBER,
# RISK_VALUE) or structural ones (MULTIPLY, CANDLE, VOLUME) - what
# _summarize_strategy() below reports as "indicator_kinds" is restricted
# to this set, since "does anyone actually use Stochastic" is a much
# more useful analytics question than "does anyone compare against a
# raw number" (every strategy does, trivially).
_INDICATOR_OPERAND_KINDS = {"MA", "RSI", "MACD", "BANDS", "ATR", "STOCH"}


def _collect_operand_kinds(op: Optional[OperandIR], kinds: set) -> None:
    if op is None:
        return
    kinds.add(op.kind)
    _collect_operand_kinds(op.left, kinds)
    _collect_operand_kinds(op.right, kinds)


def _collect_condition_kinds(node: ConditionIR, kinds: set) -> None:
    if isinstance(node, ComparisonIR):
        _collect_operand_kinds(node.left, kinds)
        _collect_operand_kinds(node.right, kinds)
    else:
        _collect_condition_kinds(node.left, kinds)
        _collect_condition_kinds(node.right, kinds)


def _summarize_strategy(ir: StrategyIR) -> dict:
    """Export analytics metadata (see export_log.strategy_meta in
    schema.sql, and export_asset_popularity for the view that reads it) -
    derived directly from the already-parsed, already-validated
    StrategyIR, NEVER from the raw client request body. That's
    deliberate: it means this can't be spoofed by editing the request
    JSON, and can never drift from what the export actually contained,
    since it's the exact same object the renderer itself consumes.
    Called once per successful export (see the generate_* endpoints
    below), right after parse_strategy() succeeds."""
    assets: List[str] = []
    timeframes: List[str] = []
    indicator_kinds: set = set()
    directions: set = set()
    max_positions: set = set()
    uses_sl = False
    uses_tp = False

    for rule in ir.rules:
        if rule.asset not in assets:
            assets.append(rule.asset)
        if rule.timeframe not in timeframes:
            timeframes.append(rule.timeframe)
        max_positions.add(rule.max_positions)

        condition_kinds: set = set()
        _collect_condition_kinds(rule.condition, condition_kinds)
        indicator_kinds |= condition_kinds & _INDICATOR_OPERAND_KINDS

        for action in rule.actions:
            directions.add(action.direction)
            if action.sl is not None:
                uses_sl = True
                sl_kinds: set = set()
                _collect_operand_kinds(action.sl, sl_kinds)
                indicator_kinds |= sl_kinds & _INDICATOR_OPERAND_KINDS
            if action.tp is not None:
                uses_tp = True
                tp_kinds: set = set()
                _collect_operand_kinds(action.tp, tp_kinds)
                indicator_kinds |= tp_kinds & _INDICATOR_OPERAND_KINDS

    return {
        "assets": assets,
        "timeframes": timeframes,
        "rule_count": len(ir.rules),
        "indicator_kinds": sorted(indicator_kinds),
        "directions": sorted(directions),
        "uses_sl": uses_sl,
        "uses_tp": uses_tp,
        "max_positions": sorted(max_positions),
    }


def _describe_operand(op: Optional[OperandIR]) -> str:
    if op is None:
        return "none"
    if op.kind == "NUMBER":
        return f"{op.value}"
    if op.kind == "MA":
        return f"MA({op.period}, {op.ma_type.replace('MODE_', '')})"
    if op.kind == "RSI":
        return f"RSI({op.period})"
    if op.kind == "ATR":
        return f"ATR({op.period})"
    if op.kind == "MACD":
        return f"MACD {op.line}"
    if op.kind == "BANDS":
        return f"Bollinger {op.band}({op.period}, {op.deviation})"
    if op.kind == "CANDLE":
        return op.candle_type
    if op.kind == "STOCH":
        return f"Stochastic %{op.stoch_line}({op.k_period},{op.d_period},{op.slowing})"
    if op.kind == "VOLUME":
        return f"Volume[{'0 (current)' if op.volume_bar == 'CURRENT' else '1 (previous)'}]"
    if op.kind == "RISK_VALUE":
        unit_label = {"PIPS": "pips", "PERCENT": "% of entry price", "PRICE": "price units"}[op.unit]
        return f"{op.value} {unit_label}"
    if op.kind == "MULTIPLY":
        return f"({_describe_operand(op.left)} * {_describe_operand(op.right)})"
    return "?"


def _describe_actions(rule: "RuleIR") -> str:
    return ", ".join(
        f"{a.direction} ({a.lot} lots, SL: {_describe_operand(a.sl)}, TP: {_describe_operand(a.tp)})"
        for a in rule.actions
    )


def _describe_positions(rule: "RuleIR") -> str:
    return "only one round open at a time" if rule.max_positions == 1 else f"up to {rule.max_positions} rounds open at once"


# ============================================================================
# MQL5 (MetaTrader 5) renderer
# ============================================================================

class _MqlBuiltOperand:
    """Everything needed to plug one operand into the generated .mq5 file."""

    def __init__(self, global_decl: str = "", init_code: str = "",
                 copy_code: str = "", value_expr: str = "", handle_vars: Optional[List[str]] = None):
        self.global_decl = global_decl     # top-level `int handle = INVALID_HANDLE;` line(s), or ""
        self.init_code = init_code         # OnInit() handle creation, or ""
        self.copy_code = copy_code         # OnTick() CopyBuffer() calls, or ""
        self.value_expr = value_expr       # the MQL5 expression representing this operand's value
        self.handle_vars = handle_vars or []  # variable name(s) to IndicatorRelease()


def _mql5_handle_error_check(handle_var: str, label: str) -> str:
    return (
        f"   if({handle_var} == INVALID_HANDLE)\n"
        f"     {{\n"
        f'      Print("Failed to create {label} indicator handle. Error: ", GetLastError());\n'
        f"      return(INIT_FAILED);\n"
        f"     }}\n"
    )


def _mql5_copy_buffer_block(handle_var: str, buffer_index: int, arr_var: str, val_var: str) -> str:
    return (
        f"   double {arr_var}[];\n"
        f"   ArraySetAsSeries({arr_var}, true);\n"
        f"   if(CopyBuffer({handle_var}, {buffer_index}, 0, 1, {arr_var}) < 1)\n"
        f"     {{\n"
        f'      Print("Failed to copy indicator buffer. Error: ", GetLastError());\n'
        f"      return;\n"
        f"     }}\n"
        f"   double {val_var} = {arr_var}[0];\n"
    )


def _mql5_build_operand(operand: OperandIR, index: int, timeframe: str, counter, symbol_var: str) -> _MqlBuiltOperand:
    """Translate one OperandIR into MQL5 declarations/init/copy code and the
    expression that represents its live value in OnTick(). `counter` is a
    shared itertools.count() (shared across every rule in the whole file,
    not just this one) so every operand - including nested ones inside a
    MULTIPLY, and across different rules - gets its own unique variable
    names. `symbol_var` is the per-rule TradeSymbol_N variable this operand
    reads its price/indicator data from."""

    if operand.kind == "NUMBER":
        return _MqlBuiltOperand(value_expr=f"{operand.value}")

    if operand.kind == "RISK_VALUE":
        if operand.unit == "PRICE":
            # Already a raw price distance - passed through unchanged
            # (advanced/legacy use, e.g. an exact 0.0050 distance).
            return _MqlBuiltOperand(value_expr=f"{operand.value}")
        if operand.unit == "PIPS":
            # Converted using a pip-size helper that accounts for 3/5-digit
            # brokers (where 1 pip = 10 points) vs 2/4-digit brokers
            # (where 1 pip = 1 point).
            return _MqlBuiltOperand(value_expr=f"({operand.value} * PipSize({symbol_var}))")
        if operand.unit == "PERCENT":
            # Resolved against the real entry price once the action-block
            # builder knows it (Ask for BUY / Bid for SELL).
            return _MqlBuiltOperand(value_expr=f"(({operand.value} / 100.0) * {_ENTRY_PRICE_PLACEHOLDER})")
        raise StrategyValidationError(f"Unsupported risk value unit: {operand.unit}")

    if operand.kind == "CANDLE":
        expr_map = {
            "CURRENT": f"SymbolInfoDouble({symbol_var}, SYMBOL_BID)",
            "PREV_OPEN": f"iOpen({symbol_var}, {timeframe}, 1)",
            "PREV_CLOSE": f"iClose({symbol_var}, {timeframe}, 1)",
            "PREV_HIGH": f"iHigh({symbol_var}, {timeframe}, 1)",
            "PREV_LOW": f"iLow({symbol_var}, {timeframe}, 1)",
        }
        return _MqlBuiltOperand(value_expr=expr_map[operand.candle_type])

    if operand.kind == "VOLUME":
        bar_index = 0 if operand.volume_bar == "CURRENT" else 1
        return _MqlBuiltOperand(value_expr=f"(double)iVolume({symbol_var}, {timeframe}, {bar_index})")

    if operand.kind == "MA":
        handle_var = f"h_ma_{index}"
        arr_var, val_var = f"buf_ma_{index}", f"val_ma_{index}"
        init_code = (
            f"   {handle_var} = iMA({symbol_var}, {timeframe}, {operand.period}, 0, "
            f"{operand.ma_type}, PRICE_CLOSE);\n" + _mql5_handle_error_check(handle_var, "MA")
        )
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=_mql5_copy_buffer_block(handle_var, 0, arr_var, val_var),
            value_expr=val_var,
            handle_vars=[handle_var],
        )

    if operand.kind == "RSI":
        handle_var = f"h_rsi_{index}"
        arr_var, val_var = f"buf_rsi_{index}", f"val_rsi_{index}"
        init_code = (
            f"   {handle_var} = iRSI({symbol_var}, {timeframe}, {operand.period}, PRICE_CLOSE);\n"
            + _mql5_handle_error_check(handle_var, "RSI")
        )
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=_mql5_copy_buffer_block(handle_var, 0, arr_var, val_var),
            value_expr=val_var,
            handle_vars=[handle_var],
        )

    if operand.kind == "ATR":
        handle_var = f"h_atr_{index}"
        arr_var, val_var = f"buf_atr_{index}", f"val_atr_{index}"
        init_code = (
            f"   {handle_var} = iATR({symbol_var}, {timeframe}, {operand.period});\n"
            + _mql5_handle_error_check(handle_var, "ATR")
        )
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=_mql5_copy_buffer_block(handle_var, 0, arr_var, val_var),
            value_expr=val_var,
            handle_vars=[handle_var],
        )

    if operand.kind == "BANDS":
        handle_var = f"h_bands_{index}"
        arr_var, val_var = f"buf_bands_{index}", f"val_bands_{index}"
        buffer_index = _BANDS_BUFFER_INDEX[operand.band]
        init_code = (
            f"   {handle_var} = iBands({symbol_var}, {timeframe}, {operand.period}, 0, "
            f"{operand.deviation}, PRICE_CLOSE);\n" + _mql5_handle_error_check(handle_var, "Bollinger Bands")
        )
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=_mql5_copy_buffer_block(handle_var, buffer_index, arr_var, val_var),
            value_expr=val_var,
            handle_vars=[handle_var],
        )

    if operand.kind == "STOCH":
        handle_var = f"h_stoch_{index}"
        arr_var, val_var = f"buf_stoch_{index}", f"val_stoch_{index}"
        buffer_index = _STOCH_BUFFER_INDEX[operand.stoch_line]
        init_code = (
            f"   {handle_var} = iStochastic({symbol_var}, {timeframe}, {operand.k_period}, "
            f"{operand.d_period}, {operand.slowing}, MODE_SMA, STO_LOWHIGH);\n"
            + _mql5_handle_error_check(handle_var, "Stochastic")
        )
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=_mql5_copy_buffer_block(handle_var, buffer_index, arr_var, val_var),
            value_expr=val_var,
            handle_vars=[handle_var],
        )

    if operand.kind == "MACD":
        handle_var = f"h_macd_{index}"
        init_code = (
            f"   {handle_var} = iMACD({symbol_var}, {timeframe}, {MACD_FAST}, {MACD_SLOW}, "
            f"{MACD_SIGNAL}, PRICE_CLOSE);\n" + _mql5_handle_error_check(handle_var, "MACD")
        )
        main_arr, main_val = f"buf_macd_main_{index}", f"val_macd_main_{index}"
        if operand.line == "MAIN":
            copy_code = _mql5_copy_buffer_block(handle_var, 0, main_arr, main_val)
            value_expr = main_val
        else:  # HIST: MT5's iMACD only exposes MAIN (0) and SIGNAL (1) buffers,
            # so the histogram is computed as their difference.
            signal_arr, signal_val = f"buf_macd_signal_{index}", f"val_macd_signal_{index}"
            copy_code = (
                _mql5_copy_buffer_block(handle_var, 0, main_arr, main_val)
                + _mql5_copy_buffer_block(handle_var, 1, signal_arr, signal_val)
            )
            value_expr = f"({main_val} - {signal_val})"
        return _MqlBuiltOperand(
            global_decl=f"int {handle_var} = INVALID_HANDLE;",
            init_code=init_code,
            copy_code=copy_code,
            value_expr=value_expr,
            handle_vars=[handle_var],
        )

    if operand.kind == "MULTIPLY":
        left_built = _mql5_build_operand(operand.left, next(counter), timeframe, counter, symbol_var)
        right_built = _mql5_build_operand(operand.right, next(counter), timeframe, counter, symbol_var)
        return _MqlBuiltOperand(
            global_decl="\n".join(x for x in (left_built.global_decl, right_built.global_decl) if x),
            init_code=left_built.init_code + right_built.init_code,
            copy_code=left_built.copy_code + right_built.copy_code,
            value_expr=f"(({left_built.value_expr}) * ({right_built.value_expr}))",
            handle_vars=left_built.handle_vars + right_built.handle_vars,
        )

    raise StrategyValidationError(f"Unsupported operand kind: {operand.kind}")


def _mql5_comparison_expression(left: _MqlBuiltOperand, operator: str, right: _MqlBuiltOperand) -> str:
    if operator == "==":
        # Doubles should never be compared with strict equality; use a
        # small tolerance instead.
        return f"(MathAbs(({left.value_expr}) - ({right.value_expr})) < 0.00001)"
    return f"(({left.value_expr}) {operator} ({right.value_expr}))"


def _mql5_action_block(action: ActionIR, index: int,
                        built_sl: Optional[_MqlBuiltOperand], built_tp: Optional[_MqlBuiltOperand],
                        symbol_var: str, magic_var: str) -> str:
    lot = round(action.lot, 2)
    # Resolve the "% of entry price" placeholder now that we know the local
    # variable name (entryPrice) that will hold Ask (BUY) or Bid (SELL).
    sl_expr = (built_sl.value_expr if built_sl is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")
    tp_expr = (built_tp.value_expr if built_tp is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")

    if action.direction == "BUY":
        open_call = f"trade.Buy(lotToTrade, {symbol_var}, 0, 0, 0, \"AlgoPuzzle\")"
        entry_price_line = f"double entryPrice = SymbolInfoDouble({symbol_var}, SYMBOL_ASK);"
        # BUY positions are exited by SELLING, which fills at Bid - so the
        # virtual SL/TP levels must be measured from Bid, not the Ask entry
        # price. Otherwise, if the spread is wider than the SL distance
        # itself, the position would appear to be "already past" its stop
        # the instant it opens, purely from the spread - not from any real
        # adverse price move.
        exit_ref_price_line = f"double exitRefPrice = SymbolInfoDouble({symbol_var}, SYMBOL_BID);"
        fail_label = "BUY"
        direction_const = "1"
    else:
        open_call = f"trade.Sell(lotToTrade, {symbol_var}, 0, 0, 0, \"AlgoPuzzle\")"
        entry_price_line = f"double entryPrice = SymbolInfoDouble({symbol_var}, SYMBOL_BID);"
        # SELL positions are exited by BUYING, which fills at Ask - same
        # reasoning as above, mirrored.
        exit_ref_price_line = f"double exitRefPrice = SymbolInfoDouble({symbol_var}, SYMBOL_ASK);"
        fail_label = "SELL"
        direction_const = "-1"

    return (
        f"      // Action {index + 1}: Open {fail_label}\n"
        f"      {{\n"
        f"         // Lot from the builder is a target - snapped to this broker's actual\n"
        f"         // volume step/min/max, which vary widely broker to broker.\n"
        f"         double lotToTrade = NormalizeLot({symbol_var}, {lot});\n"
        f"         {entry_price_line}\n"
        f"         {exit_ref_price_line}\n"
        f"         double slDistance = {sl_expr};\n"
        f"         double tpDistance = {tp_expr};\n"
        f"         // This broker/account rejects server-side attached SL/TP as\n"
        f"         // \"invalid stops\" regardless of value, timing, or format (a known\n"
        f"         // quirk with some regulated brokers). So SL/TP is enforced by the\n"
        f"         // EA itself instead: no stops are sent to the server at all - the\n"
        f"         // levels are stored here and watched every tick, closing the\n"
        f"         // position manually when price crosses them. See the top of\n"
        f"         // OnTick() below. Note: MT5's position list won't show a Stop\n"
        f"         // Loss / Take Profit value, since none is registered with the\n"
        f"         // broker - the EA is managing the exit itself.\n"
        f"         // Magic number + filling mode are set right before sending, since a\n"
        f"         // single shared `trade` object is reused across every rule in this\n"
        f"         // file - each rule's orders must be tagged with ITS OWN magic number\n"
        f"         // so positions from different rules are never confused with each\n"
        f"         // other (see the Max-simultaneous-positions accounting below).\n"
        f"         trade.SetExpertMagicNumber({magic_var});\n"
        f"         trade.SetTypeFillingBySymbol({symbol_var});\n"
        f"         if(!{open_call})\n"
        f"           {{\n"
        f'            Print("{fail_label} order failed. Retcode: ", trade.ResultRetcode(), '
        f'" - ", trade.ResultRetcodeDescription());\n'
        f"           }}\n"
        f"         else\n"
        f"           {{\n"
        f"            // Track this position - even with no SL/TP - both so its virtual\n"
        f"            // stop can be enforced (see RegisterVirtualStop()) and so the\n"
        f"            // \"Max simultaneous\" position limit above can see it. A THEN block\n"
        f"            // can contain more than one action, so this is tracked by its own\n"
        f"            // MT5 position id rather than a single shared variable.\n"
        f"            ulong openedPositionId = ResolveOpenedPositionId();\n"
        f'            if(openedPositionId == 0)\n'
        f"              {{\n"
        f'               Print("Could not resolve the position id opened by this {fail_label} - it will not count toward the simultaneous-position limit or have virtual SL/TP enforced.");\n'
        f"              }}\n"
        f"            else\n"
        f"              {{\n"
        f"               // RegisterVirtualStop() derives the actual SL/TP levels itself,\n"
        f"               // blending this round's exit-side reference price with any\n"
        f"               // earlier round(s) already tracked under the same position id\n"
        f"               // (e.g. a netting account merging this order into an existing\n"
        f"               // position) - see its definition above for why that matters.\n"
        f"               RegisterVirtualStop(openedPositionId, {symbol_var}, {magic_var}, exitRefPrice, slDistance, tpDistance, {direction_const}, lotToTrade);\n"
        f"              }}\n"
        f"           }}\n"
        f"      }}\n"
    )


def _mql5_build_condition(node: ConditionIR, timeframe: str, counter, symbol_var: str) -> "tuple[str, List[_MqlBuiltOperand]]":
    """Recursively compile a (possibly nested) IR condition tree into a
    single MQL5 boolean expression, plus the flat list of every indicator
    operand encountered along the way (for declarations/init/copy code)."""
    if isinstance(node, ComparisonIR):
        left_built = _mql5_build_operand(node.left, next(counter), timeframe, counter, symbol_var)
        right_built = _mql5_build_operand(node.right, next(counter), timeframe, counter, symbol_var)
        expr = _mql5_comparison_expression(left_built, node.operator, right_built)
        return expr, [left_built, right_built]

    # logical: recurse into both branches, then join with && / ||
    left_expr, left_ops = _mql5_build_condition(node.left, timeframe, counter, symbol_var)
    right_expr, right_ops = _mql5_build_condition(node.right, timeframe, counter, symbol_var)
    mql_op = "&&" if node.operator == "AND" else "||"
    return f"({left_expr} {mql_op} {right_expr})", left_ops + right_ops


def render_mql5(ir: StrategyIR) -> str:
    """Turn a StrategyIR into a complete .mq5 Expert Advisor source file.
    Every rule in ir.rules gets its own symbol/timeframe/indicator handles/
    round-counter/magic number and runs side by side inside ONE combined
    OnInit()/OnDeinit()/OnTick() - see _mql5_action_block()'s magic-number
    comment and CountTrackedForMagic() for how rules stay independent even
    when two of them trade the same asset."""

    counter = itertools.count()  # shared file-wide so no two rules' variable names collide

    global_decls_parts: List[str] = []
    init_parts: List[str] = []
    release_handle_vars: List[str] = []
    ontick_parts: List[str] = []

    for rule in ir.rules:
        i = rule.rule_index
        symbol_var = f"TradeSymbol_{i}"
        timeframe_var = f"TradeTimeframe_{i}"
        magic_var = f"MagicNumber_{i}"
        max_pos_var = f"MaxSimultaneousPositions_{i}"
        rounds_var = f"g_openRoundsSinceFlat_{i}"

        comparison_expr, condition_ops = _mql5_build_condition(rule.condition, rule.timeframe, counter, symbol_var)

        built_sl_by_action: dict = {}
        built_tp_by_action: dict = {}
        extra_ops: List[_MqlBuiltOperand] = []
        for ai, action in enumerate(rule.actions):
            if action.sl is not None:
                built_sl_by_action[ai] = _mql5_build_operand(action.sl, next(counter), rule.timeframe, counter, symbol_var)
                extra_ops.append(built_sl_by_action[ai])
            if action.tp is not None:
                built_tp_by_action[ai] = _mql5_build_operand(action.tp, next(counter), rule.timeframe, counter, symbol_var)
                extra_ops.append(built_tp_by_action[ai])

        built_operands = condition_ops + extra_ops
        rule_handles = "\n".join(b.global_decl for b in built_operands if b.global_decl)
        rule_init = "".join(b.init_code for b in built_operands if b.init_code)
        rule_copy = "".join(b.copy_code for b in built_operands if b.copy_code)
        for b in built_operands:
            release_handle_vars.extend(b.handle_vars)

        actions_body = "".join(
            _mql5_action_block(a, ai, built_sl_by_action.get(ai), built_tp_by_action.get(ai), symbol_var, magic_var)
            for ai, a in enumerate(rule.actions)
        )

        global_decls_parts.append(
            f"//--- Rule {i + 1}: {rule.asset} on {rule.timeframe.replace('PERIOD_', '')}. All indicator, price\n"
            f"//--- and trade calls for this rule use {symbol_var} explicitly, so it always\n"
            f"//--- trades this asset regardless of which chart the EA is attached to.\n"
            f"string {symbol_var} = \"{rule.asset}\";\n"
            f"ENUM_TIMEFRAMES {timeframe_var} = {rule.timeframe};\n"
            f"const int {magic_var} = {rule.magic};\n"
            f"const int {max_pos_var} = {rule.max_positions};\n"
            f"int {rounds_var} = 0;\n"
            + (rule_handles + "\n" if rule_handles else "")
            + "\n"
        )

        init_parts.append(
            f"   // --- Rule {i + 1} ({rule.asset}) ---\n"
            f"   {{\n"
            f"      // {symbol_var} may differ from the symbol of the chart this EA is\n"
            f"      // attached to (by design), and the exact name configured in the\n"
            f"      // builder may not exist as-is on this specific broker (suffix/prefix\n"
            f"      // conventions vary widely) - ResolveBrokerSymbol() finds the right\n"
            f"      // name and force-selects it into Market Watch.\n"
            f"      string resolved{i} = ResolveBrokerSymbol({symbol_var});\n"
            f'      if(resolved{i} == "")\n'
            f"        {{\n"
            f'         Print("Rule {i + 1}: no symbol matching \\"", {symbol_var}, "\\" was found on this broker. ",\n'
            f'               "Check Market Watch -> Symbols for the exact name this broker uses and edit {symbol_var} near the top of this file.");\n'
            f"         return(INIT_FAILED);\n"
            f"        }}\n"
            f"      {symbol_var} = resolved{i};\n"
            f"{rule_init}"
            f'      Print("AlgoPuzzle EA - Rule {i + 1} initialized on ", {symbol_var}, " (magic ", {magic_var}, ")");\n'
            f"      //--- Diagnostic info: if orders ever get rejected with \"invalid stops\"\n"
            f"      //--- or similar, these values show exactly what this symbol/account\n"
            f"      //--- requires, instead of having to guess.\n"
            f"      long stopsLevelPoints{i}  = SymbolInfoInteger({symbol_var}, SYMBOL_TRADE_STOPS_LEVEL);\n"
            f"      long freezeLevelPoints{i} = SymbolInfoInteger({symbol_var}, SYMBOL_TRADE_FREEZE_LEVEL);\n"
            f"      double point{i}           = SymbolInfoDouble({symbol_var}, SYMBOL_POINT);\n"
            f"      double tickSize{i}        = SymbolInfoDouble({symbol_var}, SYMBOL_TRADE_TICK_SIZE);\n"
            f"      long tradeMode{i}         = SymbolInfoInteger({symbol_var}, SYMBOL_TRADE_MODE);\n"
            f"      long fillingModes{i}      = SymbolInfoInteger({symbol_var}, SYMBOL_FILLING_MODE);\n"
            f'      Print("Rule {i + 1} symbol trading diagnostics for ", {symbol_var}, ":");\n'
            f'      Print("  SYMBOL_TRADE_STOPS_LEVEL (points): ", stopsLevelPoints{i},\n'
            f'            "  (min. distance = ", stopsLevelPoints{i} * point{i}, " price units)");\n'
            f'      Print("  SYMBOL_TRADE_FREEZE_LEVEL (points): ", freezeLevelPoints{i});\n'
            f'      Print("  SYMBOL_POINT: ", point{i}, "   SYMBOL_TRADE_TICK_SIZE: ", tickSize{i},\n'
            f'            "   SYMBOL_DIGITS: ", (int)SymbolInfoInteger({symbol_var}, SYMBOL_DIGITS));\n'
            f'      Print("  SYMBOL_TRADE_MODE: ", tradeMode{i}, "  (0=disabled,1=longonly,2=shortonly,3=closeonly,4=full)");\n'
            f'      Print("  SYMBOL_FILLING_MODE flags: ", fillingModes{i}, "  (1=FOK,2=IOC,3=both)");\n'
            f"     }}\n"
        )

        ontick_parts.append(
            f"\n   // ===================== Rule {i + 1}: {rule.asset} =====================\n"
            f"   {{\n"
            f"      // Reset THIS rule's own round counter once ITS OWN tracked positions\n"
            f"      // have all closed - independent of every other rule's positions (see\n"
            f"      // CountTrackedForMagic() - matters most when two rules share a symbol).\n"
            f"      if(CountTrackedForMagic({magic_var}) == 0)\n"
            f"         {rounds_var} = 0;\n\n"
            f"      static datetime lastBarTime_{i} = 0;\n"
            f"      datetime currentBarTime_{i} = iTime({symbol_var}, {timeframe_var}, 0);\n"
            f"      if(currentBarTime_{i} != lastBarTime_{i})\n"
            f"        {{\n"
            f"         lastBarTime_{i} = currentBarTime_{i};\n"
            f"{rule_copy}"
            f"         bool signalTriggered_{i} = {comparison_expr};\n"
            f"         if(signalTriggered_{i})\n"
            f"           {{\n"
            f"            if({rounds_var} < {max_pos_var})\n"
            f"              {{\n"
            f"               {rounds_var}++;\n"
            f"{actions_body}"
            f"              }}\n"
            f"           }}\n"
            f"        }}\n"
            f"     }}\n"
        )

    global_decls = "".join(global_decls_parts)
    init_body = "".join(init_parts)
    release_body = "\n".join(f"   IndicatorRelease({v});" for v in release_handle_vars)
    if not release_body:
        release_body = "   // Nothing to release."
    ontick_body = "".join(ontick_parts)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rule_count = len(ir.rules)

    template = f"""//+------------------------------------------------------------------+
//|                                              strategy.mq5         |
//|         Auto-generated by "AlgoPuzzle" (No-Code Builder) |
//|         Generated: {generated_at}                                 |
//|         {rule_count} strategy rule(s) combined into this one EA    |
//|                                                                     |
//|  WARNING: Always test on a Demo account before going live.         |
//+------------------------------------------------------------------+
#property copyright "AlgoPuzzle"
#property link      ""
#property version   "1.00"

#include <Trade\\Trade.mqh>

//--- Trading object - shared across every rule below. Each rule tags its
//--- own orders with its own magic number right before sending (see
//--- _mql5_action_block's SetExpertMagicNumber call), so one CTrade
//--- instance is enough; positions are still told apart correctly.
CTrade trade;

//--- Virtual Stop Loss / Take Profit state. This broker/account rejects
//--- server-side attached SL/TP outright (a known quirk with some
//--- regulated brokers), so instead of relying on the server to enforce
//--- stops, the EA itself watches price every tick and closes the
//--- position manually when it crosses these levels.
//--- A single THEN block can contain more than one action (e.g. two BUYs
//--- at different sizes, or an opposite-direction hedge on a hedging-mode
//--- account), and this EA can run more than one independent rule at once,
//--- so stops are tracked per-position - keyed by MT5 position id, the
//--- only identifier that stays valid across both netting and hedging
//--- account modes - in these parallel arrays, rather than in a single
//--- shared variable. g_vsSymbol/g_vsMagic record which symbol to check
//--- the price against and which rule this position belongs to.
ulong  g_vsPositionId[];
string g_vsSymbol[];
int    g_vsMagic[];
double g_vsSL[];
double g_vsTP[];
int    g_vsDirection[];  // 1 = BUY, -1 = SELL
//--- Volume-weighted exit-side reference price and total volume registered
//--- so far for each tracked position id, and the running total volume.
//--- On a netting account, adding a 2nd (or 3rd, ...) round to the SAME
//--- symbol doesn't open a separate position - MT5 merges it into the one
//--- existing position at a new blended average price. This applies both
//--- to a single rule opening multiple rounds AND to two DIFFERENT rules
//--- that happen to trade the same symbol on a netting account - MT5 does
//--- not distinguish them by magic number for this. Re-deriving SL/TP from
//--- just the latest round's price (like this EA originally did) would
//--- silently discard the earlier round's own reference price entirely, and
//--- the resulting SL/TP distance from the position's *actual* combined
//--- cost basis would end up smaller or larger than configured - see
//--- RegisterVirtualStop(). Deliberately NOT using MT5's own
//--- POSITION_PRICE_OPEN for this: that is the entry-side (Ask for BUY)
//--- price, and anchoring SL/TP to it would reintroduce the exact "spread
//--- looks like an instant stop-out" bug the exit-side design avoids
//--- (see exitRefPrice above) - so this average is tracked independently,
//--- on the exit side, ourselves.
double g_vsExitRef[];
double g_vsVolume[];

//--- Per-rule strategy configuration (from the visual block builder) -----
{global_decls}

//+------------------------------------------------------------------+
//| Converts 1 "pip" to a price distance for the given symbol.        |
//| Standard convention: on 3 or 5-digit ("fractional pip") symbols,  |
//| 1 pip = 10 points; on 2 or 4-digit symbols, 1 pip = 1 point.      |
//+------------------------------------------------------------------+
double PipSize(string symbol)
  {{
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(digits == 3 || digits == 5)
      return(point * 10.0);
   return(point);
  }}

//+------------------------------------------------------------------+
//| Snaps a price to the broker's actual tradeable price increment    |
//| (SYMBOL_TRADE_TICK_SIZE), not just the symbol's decimal digits.   |
//| Some brokers reject SL/TP as "invalid stops" if the price isn't  |
//| an exact multiple of the tick size, even when the distance from  |
//| the current price is otherwise perfectly valid.                  |
//+------------------------------------------------------------------+
double NormalizeToTick(string symbol, double price)
  {{
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0)
      tickSize = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double snapped = MathRound(price / tickSize) * tickSize;
   return(NormalizeDouble(snapped, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)));
  }}

//+------------------------------------------------------------------+
//| Resolves the broker's actual symbol name for a base asset name,   |
//| so this EA works on any broker regardless of that broker's symbol |
//| naming convention (e.g. "EURUSD" vs "EURUSD.a" vs "EURUSDm" vs    |
//| "#EURUSD" vs "EURUSD_i" - every broker does this differently).    |
//| Tries an exact match first; if that isn't found in the broker's   |
//| symbol list, scans every available symbol for one containing the  |
//| base name and uses the first match. Returns "" if nothing matches |
//| at all, meaning this asset genuinely isn't offered by the broker. |
//+------------------------------------------------------------------+
string ResolveBrokerSymbol(string baseName)
  {{
   if(SymbolSelect(baseName, true))
      return(baseName);

   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {{
      string candidate = SymbolName(i, false);
      if(StringFind(candidate, baseName) >= 0 && SymbolSelect(candidate, true))
        {{
         Print("Exact symbol \\"", baseName, "\\" not found on this broker - using \\"",
               candidate, "\\" instead (closest match).");
         return(candidate);
        }}
     }}
   return("");
  }}

//+------------------------------------------------------------------+
//| Snaps a lot size to the broker's actual SYMBOL_VOLUME_STEP and    |
//| clamps it into [SYMBOL_VOLUME_MIN, SYMBOL_VOLUME_MAX]. Brokers    |
//| disagree wildly on these (0.01 vs 0.1 min lot, different steps),  |
//| so the lot value chosen in the builder is a target, not a         |
//| guarantee - this is what actually gets sent to the server.        |
//+------------------------------------------------------------------+
double NormalizeLot(string symbol, double lot)
  {{
   double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0)
      lotStep = 0.01;
   double normalized = MathRound(lot / lotStep) * lotStep;
   if(minLot > 0)
      normalized = MathMax(minLot, normalized);
   if(maxLot > 0)
      normalized = MathMin(maxLot, normalized);
   return(NormalizeDouble(normalized, 3));
  }}

//+------------------------------------------------------------------+
//| Resolves the MT5 position id that the trade just executed via     |
//| `trade` opened, or added volume to. Going through the resulting   |
//| deal's DEAL_POSITION_ID (rather than assuming the order ticket or |
//| PositionSelect(symbol)) is what makes this correct on BOTH a      |
//| netting account (one position per symbol - a second same-symbol   |
//| order adds to it, sharing the same position id) and a hedging     |
//| account (each order can open a fully separate position).          |
//+------------------------------------------------------------------+
ulong ResolveOpenedPositionId()
  {{
   ulong dealTicket = trade.ResultDeal();
   if(dealTicket > 0 && HistoryDealSelect(dealTicket))
      return((ulong)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID));
   return(0);
  }}

//+------------------------------------------------------------------+
//| Registers a just-opened (or just-added-to) position's virtual     |
//| SL/TP. If this position id is already tracked - meaning this      |
//| round was merged into an existing position, which is exactly what |
//| happens on a netting account - exitRefPrice and lot are blended   |
//| into the running volume-weighted average for that position        |
//| BEFORE recomputing SL/TP from slDistance/tpDistance, so the        |
//| levels always reflect the position's true combined cost basis,    |
//| not just whichever round most recently overwrote them. `symbol`   |
//| and `magic` are stored alongside so the OnTick() watch loop can   |
//| check the right symbol's price, and so each rule can count only   |
//| ITS OWN tracked positions (see CountTrackedForMagic()) - this     |
//| matters because two DIFFERENT rules trading the SAME symbol on a  |
//| netting account will still merge into this one entry, exactly    |
//| like two rounds of the same rule do (see the README note on this).|
//+------------------------------------------------------------------+
void RegisterVirtualStop(ulong positionId, string symbol, int magic, double exitRefPrice,
                          double slDistance, double tpDistance, int direction, double lot)
  {{
   int idx = -1;
   for(int i = 0; i < ArraySize(g_vsPositionId); i++)
     {{
      if(g_vsPositionId[i] == positionId)
        {{
         idx = i;
         break;
        }}
     }}
   if(idx < 0)
     {{
      idx = ArraySize(g_vsPositionId);
      ArrayResize(g_vsPositionId, idx + 1);
      ArrayResize(g_vsSymbol, idx + 1);
      ArrayResize(g_vsMagic, idx + 1);
      ArrayResize(g_vsSL, idx + 1);
      ArrayResize(g_vsTP, idx + 1);
      ArrayResize(g_vsDirection, idx + 1);
      ArrayResize(g_vsExitRef, idx + 1);
      ArrayResize(g_vsVolume, idx + 1);
      g_vsPositionId[idx] = positionId;
      g_vsSymbol[idx]     = symbol;
      g_vsMagic[idx]      = magic;
      g_vsExitRef[idx]    = 0;
      g_vsVolume[idx]     = 0;
     }}

   double totalVolume = g_vsVolume[idx] + lot;
   double blendedExitRef = (totalVolume > 0)
      ? (g_vsExitRef[idx] * g_vsVolume[idx] + exitRefPrice * lot) / totalVolume
      : exitRefPrice;
   g_vsExitRef[idx]   = blendedExitRef;
   g_vsVolume[idx]    = totalVolume;
   g_vsDirection[idx] = direction;
   g_vsSL[idx] = (slDistance <= 0) ? 0 :
      NormalizeToTick(symbol, (direction == 1) ? (blendedExitRef - slDistance) : (blendedExitRef + slDistance));
   g_vsTP[idx] = (tpDistance <= 0) ? 0 :
      NormalizeToTick(symbol, (direction == 1) ? (blendedExitRef + tpDistance) : (blendedExitRef - tpDistance));
  }}

//+------------------------------------------------------------------+
//| Stops tracking one entry (position closed, or its virtual stop    |
//| just fired) - swap-removes it to avoid shifting the whole array.  |
//+------------------------------------------------------------------+
void RemoveVirtualStop(int idx)
  {{
   int last = ArraySize(g_vsPositionId) - 1;
   g_vsPositionId[idx] = g_vsPositionId[last];
   g_vsSymbol[idx]     = g_vsSymbol[last];
   g_vsMagic[idx]      = g_vsMagic[last];
   g_vsSL[idx]         = g_vsSL[last];
   g_vsTP[idx]         = g_vsTP[last];
   g_vsDirection[idx]  = g_vsDirection[last];
   g_vsExitRef[idx]    = g_vsExitRef[last];
   g_vsVolume[idx]     = g_vsVolume[last];
   ArrayResize(g_vsPositionId, last);
   ArrayResize(g_vsSymbol, last);
   ArrayResize(g_vsMagic, last);
   ArrayResize(g_vsSL, last);
   ArrayResize(g_vsTP, last);
   ArrayResize(g_vsDirection, last);
   ArrayResize(g_vsExitRef, last);
   ArrayResize(g_vsVolume, last);
  }}

//+------------------------------------------------------------------+
//| Counts how many currently-tracked positions belong to one rule    |
//| (by magic number) - used to reset that rule's own "rounds since   |
//| flat" counter independently of every other rule.                  |
//+------------------------------------------------------------------+
int CountTrackedForMagic(int magic)
  {{
   int count = 0;
   for(int i = 0; i < ArraySize(g_vsMagic); i++)
      if(g_vsMagic[i] == magic)
         count++;
   return(count);
  }}

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
  {{
{init_body}
   return(INIT_SUCCEEDED);
  }}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {{
{release_body}
  }}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
  {{
   //--- Virtual Stop Loss / Take Profit monitor: runs on EVERY tick (not
   //--- gated to new bars), since price can cross a stop level at any
   //--- moment, not just on a bar close. This never sends SL/TP to the
   //--- broker's server at all - it watches the live price itself and
   //--- closes each tracked position directly, which works regardless of
   //--- whether this broker/account accepts server-side attached stops.
   //--- Shared across every rule below - iterates every tracked position
   //--- independently (a THEN block can contain more than one, and there
   //--- can be more than one rule), walking backwards since
   //--- RemoveVirtualStop() swap-removes from the array. g_vsSymbol[i]
   //--- (not a single global symbol) is used for the price check, since
   //--- different rules can track positions on different symbols.
   for(int i = ArraySize(g_vsPositionId) - 1; i >= 0; i--)
     {{
      if(!PositionSelectByTicket(g_vsPositionId[i]))
        {{
         // Position no longer exists (closed some other way) - stop tracking it.
         RemoveVirtualStop(i);
         continue;
        }}

      double checkPrice = (g_vsDirection[i] == 1)
         ? SymbolInfoDouble(g_vsSymbol[i], SYMBOL_BID)   // BUY exits by selling, at Bid
         : SymbolInfoDouble(g_vsSymbol[i], SYMBOL_ASK);  // SELL exits by buying, at Ask

      bool hitSL = (g_vsSL[i] > 0) &&
         ((g_vsDirection[i] == 1) ? (checkPrice <= g_vsSL[i]) : (checkPrice >= g_vsSL[i]));
      bool hitTP = (g_vsTP[i] > 0) &&
         ((g_vsDirection[i] == 1) ? (checkPrice >= g_vsTP[i]) : (checkPrice <= g_vsTP[i]));

      if(hitSL || hitTP)
        {{
         if(trade.PositionClose(g_vsPositionId[i]))
           {{
            Print(hitSL ? "Virtual Stop Loss triggered - position closed." : "Virtual Take Profit triggered - position closed.");
            RemoveVirtualStop(i);
           }}
         else
           {{
            Print("Virtual SL/TP close failed. Retcode: ", trade.ResultRetcode(), " - ", trade.ResultRetcodeDescription());
           }}
        }}
     }}
{ontick_body}
  }}
//+------------------------------------------------------------------+
"""
    return template


def generate_readme_mql5(ir: StrategyIR) -> str:
    rule_summaries = "\n\n".join(
        f"Rule {r.rule_index + 1} (magic number {r.magic})\n"
        f"- Asset:      {r.asset}\n"
        f"- Timeframe:  {r.timeframe}\n"
        f"- Actions:    {_describe_actions(r)}\n"
        f"- Positions:  {_describe_positions(r)}"
        for r in ir.rules
    )
    assets_line = ", ".join(dict.fromkeys(r.asset for r in ir.rules))  # de-duplicated, order-preserved
    _summary_heading = f"Strategy summary ({len(ir.rules)} rule{'s' if len(ir.rules) != 1 else ''})"
    _multi_rule_heading = f"NOTE ON RUNNING {len(ir.rules)} RULES IN ONE EA"
    multi_rule_note = "" if len(ir.rules) == 1 else f"""
{_multi_rule_heading}
{"-" * len(_multi_rule_heading)}
This file combines {len(ir.rules)} independent strategy rules into a single
Expert Advisor. Each runs on its own schedule, evaluates its own condition,
and tracks its own open positions completely independently (tagged with
its own magic number, listed above) - one rule misfiring or hitting its
position limit never blocks another.

If two rules trade the SAME asset on an account in NETTING mode (as
opposed to Hedging), be aware that MT5 itself only allows ONE net position
per symbol - the platform will merge both rules' orders into that single
position no matter what magic number is attached, exactly the way multiple
rounds from a single rule already blend together (see the Stop Loss / Take
Profit note below). On a Hedging account, or when rules trade different
assets, each rule's positions stay fully independent.
"""

    return f"""ALGOPUZZLE - Setup Guide
===================================

Your strategy has been compiled into a MetaTrader 5 Expert Advisor
(strategy.mq5). Follow the steps below to install and run it.

{_summary_heading}
{"-" * len(_summary_heading)}
{rule_summaries}
{multi_rule_note}
NOTE ON STOP LOSS / TAKE PROFIT
--------------------------------
SL/TP are entered as Pips or Percent-of-entry-price (converted to the real
price distance automatically), or as a dynamic expression like "2 x ATR(14)"
so your stop adapts to current volatility. A value of "none" means no Stop
Loss / Take Profit is attached to that action.

STEP 1 - Open MetaTrader 5
---------------------------
Launch the MetaTrader 5 desktop application and log into your
Demo or Live trading account. (Always start with a Demo account -
see IMPORTANT SAFETY NOTES at the bottom.)

STEP 2 - Open your Data Folder
--------------------------------
In the top menu, click:  File -> Open Data Folder
This opens a Windows Explorer / Finder window showing MT5's internal
files.

STEP 3 - Copy the Expert Advisor
-----------------------------------
Inside the Data Folder, navigate to:  MQL5 -> Experts
Paste the "strategy.mq5" file from this zip into that folder.

STEP 4 - Refresh the Navigator panel
----------------------------------------
Back in MetaTrader 5, open the "Navigator" panel (Ctrl+N) if it isn't
already open. MT5 does NOT automatically notice files added to the
Experts folder while it's running - right-click anywhere inside the
"Expert Advisors" section of the Navigator and choose "Refresh".
"strategy" should now appear in the list. (If it still doesn't, double-
check the file really landed in MQL5\\Experts and not a subfolder.)

STEP 5 - Compile the Expert Advisor
--------------------------------------
Right-click "strategy" in the Navigator and select "Modify" - this
opens the MetaEditor with the code loaded. Press F7 (or click the
"Compile" button). Watch the "Errors" tab at the bottom: it must read
"0" errors before you continue - warnings are fine, errors are not.
Close the MetaEditor once compilation succeeds.

STEP 6 - Attach the EA to a Chart
------------------------------------
Open a chart for ANY symbol/timeframe (it doesn't have to match any of the
rules below - the EA always trades exactly the asset(s)/timeframe(s) shown
in the strategy summary above, regardless of which chart it's attached to:
{assets_line}). Drag "strategy" from the Navigator panel onto the chart. In
the dialog that appears, open the "Common" tab and make sure "Allow Algo
Trading" is checked, then click OK.

STEP 7 - Enable Algo Trading
--------------------------------
Click the "Algo Trading" button in the main MT5 toolbar so it turns
green/highlighted. This is a global switch - without it, no Expert
Advisor on any chart will trade, no matter how it's configured.

STEP 8 - Confirm it's actually running
-------------------------------------------
Open the "Toolbox" panel at the bottom of the terminal (Ctrl+T) and
click the "Experts" tab. Within a few seconds you should see one
initialization line PER RULE, e.g.:
    AlgoPuzzle EA - Rule 1 initialized on {ir.rules[0].asset} (magic {ir.rules[0].magic})
followed by a block of diagnostic lines (symbol digits, stop level,
filling mode, etc) for that rule - repeated once per rule above. If you
see all of them, the EA is live and evaluating every rule on its own
timeframe. If you see a red error line instead, read it - it usually
explains exactly what's wrong (e.g. a symbol name mismatch, or Algo
Trading not enabled).

A NOTE ON WHAT YOU'LL SEE IN THE "TRADE" TAB
-------------------------------------------------
When a position opens, MT5's own Trade tab will show it with NO Stop
Loss or Take Profit value filled in - even though you configured one.
This is expected, not a bug: this EA manages SL/TP itself internally
(see NOTE ON STOP LOSS / TAKE PROFIT above) rather than registering
them with the broker's server, so nothing appears in that column. The
"Experts" tab will print "Virtual Stop Loss triggered" or "Virtual
Take Profit triggered" when the EA closes a position for you.

IMPORTANT SAFETY NOTES
-----------------------
- Always test this Expert Advisor on a Demo account first.
- This build does not include backtesting validation - review the
  generated strategy.mq5 code and run it through MT5's Strategy
  Tester before using real funds.
- Past performance and rule-based logic do not guarantee future
  results. Trade responsibly.

Generated by AlgoPuzzle (MVP).
"""


# ============================================================================
# C# / cTrader (cAlgo) renderer
# ============================================================================
# Mirrors render_mql5() feature-for-feature (same IR, same business rules),
# but uses idiomatic cAlgo patterns rather than a literal MQL5
# transliteration:
#   - event-driven bar handling (Bars.BarOpened) instead of manual iTime()
#     polling every tick;
#   - Dictionary<long, VirtualStopState> keyed by the Position object cAlgo
#     hands back directly from ExecuteMarketOrder(), instead of MQL5's
#     DEAL_POSITION_ID lookup dance;
#   - Symbol.PipSize / Symbol.NormalizeVolumeInUnits() from the cAlgo API
#     instead of hand-rolled PipSize()/NormalizeLot() helpers.
# The netting-account SL/TP blending logic (RegisterVirtualStop) is a direct
# port of the MQL5 version's logic, since cAlgo has no built-in equivalent.
#
# NOT YET VERIFIED against a real cTrader Automate compile (Ctrl+B) - see
# docs/HANDOFF.md. Written from the documented, stable parts of the cAlgo API;
# treat as needing that manual verification pass before live use.

CTRADER_TIMEFRAME = {
    "PERIOD_M1": "TimeFrame.Minute",
    "PERIOD_M5": "TimeFrame.Minute5",
    "PERIOD_M15": "TimeFrame.Minute15",
    "PERIOD_M30": "TimeFrame.Minute30",
    "PERIOD_H1": "TimeFrame.Hour",
    "PERIOD_H4": "TimeFrame.Hour4",
    "PERIOD_D1": "TimeFrame.Daily",
    "PERIOD_W1": "TimeFrame.Weekly",
    "PERIOD_MN1": "TimeFrame.Monthly",
}


class _CsBuiltOperand:
    """Everything needed to plug one operand into the generated .cs file."""

    def __init__(self, field_decl: str = "", init_code: str = "", value_expr: str = ""):
        self.field_decl = field_decl   # private field declaration(s), or ""
        self.init_code = init_code     # OnStart() indicator creation, or ""
        self.value_expr = value_expr   # C# expression representing this operand's live value
        # No separate "copy" step like MQL5's CopyBuffer - cAlgo indicator
        # results (.Result.LastValue etc.) are always live, read directly.


def _csharp_build_operand(operand: OperandIR, index: int, counter, symbol_var: str, bars_var: str) -> _CsBuiltOperand:
    """Translate one OperandIR into cAlgo field/init code and the C#
    expression representing its live value. Mirrors _mql5_build_operand()
    one-for-one - same operand kinds, same semantics, different target API.
    `symbol_var`/`bars_var` are the per-rule _tradeSymbolN/_tradeBarsN
    fields this operand reads its price/indicator data from."""

    if operand.kind == "NUMBER":
        return _CsBuiltOperand(value_expr=f"{operand.value}")

    if operand.kind == "RISK_VALUE":
        if operand.unit == "PRICE":
            return _CsBuiltOperand(value_expr=f"{operand.value}")
        if operand.unit == "PIPS":
            # cAlgo's Symbol exposes PipSize directly - no fractional-pip
            # digit-counting helper needed like MQL5's PipSize().
            return _CsBuiltOperand(value_expr=f"({operand.value} * {symbol_var}.PipSize)")
        if operand.unit == "PERCENT":
            return _CsBuiltOperand(value_expr=f"(({operand.value} / 100.0) * {_ENTRY_PRICE_PLACEHOLDER})")
        raise StrategyValidationError(f"Unsupported risk value unit: {operand.unit}")

    if operand.kind == "CANDLE":
        expr_map = {
            "CURRENT": f"{symbol_var}.Bid",
            "PREV_OPEN": f"{bars_var}.OpenPrices.Last(1)",
            "PREV_CLOSE": f"{bars_var}.ClosePrices.Last(1)",
            "PREV_HIGH": f"{bars_var}.HighPrices.Last(1)",
            "PREV_LOW": f"{bars_var}.LowPrices.Last(1)",
        }
        return _CsBuiltOperand(value_expr=expr_map[operand.candle_type])

    if operand.kind == "VOLUME":
        bar_index = 0 if operand.volume_bar == "CURRENT" else 1
        return _CsBuiltOperand(value_expr=f"{bars_var}.TickVolumes.Last({bar_index})")

    if operand.kind == "MA":
        field = f"_ma{index}"
        ma_type = "MovingAverageType.Exponential" if operand.ma_type == "MODE_EMA" else "MovingAverageType.Simple"
        return _CsBuiltOperand(
            field_decl=f"private MovingAverage {field} = null!;",
            init_code=f"            {field} = Indicators.MovingAverage({bars_var}.ClosePrices, {operand.period}, {ma_type});\n",
            value_expr=f"{field}.Result.LastValue",
        )

    if operand.kind == "RSI":
        field = f"_rsi{index}"
        return _CsBuiltOperand(
            field_decl=f"private RelativeStrengthIndex {field} = null!;",
            init_code=f"            {field} = Indicators.RelativeStrengthIndex({bars_var}.ClosePrices, {operand.period});\n",
            value_expr=f"{field}.Result.LastValue",
        )

    if operand.kind == "ATR":
        field = f"_atr{index}"
        return _CsBuiltOperand(
            field_decl=f"private AverageTrueRange {field} = null!;",
            init_code=f"            {field} = Indicators.AverageTrueRange({operand.period}, MovingAverageType.Simple);\n",
            value_expr=f"{field}.Result.LastValue",
        )

    if operand.kind == "BANDS":
        field = f"_bands{index}"
        line_map = {"MIDDLE": "Main", "UPPER": "Top", "LOWER": "Bottom"}
        return _CsBuiltOperand(
            field_decl=f"private BollingerBands {field} = null!;",
            init_code=f"            {field} = Indicators.BollingerBands({bars_var}.ClosePrices, {operand.period}, {operand.deviation}, MovingAverageType.Simple);\n",
            value_expr=f"{field}.{line_map[operand.band]}.LastValue",
        )

    if operand.kind == "STOCH":
        field = f"_stoch{index}"
        line_map = {"K": "PercentK", "D": "PercentD"}
        # cAlgo's StochasticOscillator(kPeriods, kSlowing, dPeriods, maType)
        # param order differs from MT5's iStochastic(k, d, slowing, ...) -
        # mapped so operand.k_period/slowing/d_period keep their meaning.
        return _CsBuiltOperand(
            field_decl=f"private StochasticOscillator {field} = null!;",
            init_code=f"            {field} = Indicators.StochasticOscillator({operand.k_period}, {operand.slowing}, {operand.d_period}, MovingAverageType.Simple);\n",
            value_expr=f"{field}.{line_map[operand.stoch_line]}.LastValue",
        )

    if operand.kind == "MACD":
        field = f"_macd{index}"
        # cAlgo's MacdCrossOver(source, longCycle, shortCycle, signalPeriods)
        # exposes MACD/Signal/Histogram directly - no manual main-minus-
        # signal subtraction needed like the MQL5 side has to do.
        init_code = f"            {field} = Indicators.MacdCrossOver({bars_var}.ClosePrices, {MACD_SLOW}, {MACD_FAST}, {MACD_SIGNAL});\n"
        value_expr = f"{field}.MACD.LastValue" if operand.line == "MAIN" else f"{field}.Histogram.LastValue"
        return _CsBuiltOperand(field_decl=f"private MacdCrossOver {field} = null!;", init_code=init_code, value_expr=value_expr)

    if operand.kind == "MULTIPLY":
        left_built = _csharp_build_operand(operand.left, next(counter), counter, symbol_var, bars_var)
        right_built = _csharp_build_operand(operand.right, next(counter), counter, symbol_var, bars_var)
        return _CsBuiltOperand(
            field_decl="\n".join(x for x in (left_built.field_decl, right_built.field_decl) if x),
            init_code=left_built.init_code + right_built.init_code,
            value_expr=f"(({left_built.value_expr}) * ({right_built.value_expr}))",
        )

    raise StrategyValidationError(f"Unsupported operand kind: {operand.kind}")


def _csharp_comparison_expression(left: _CsBuiltOperand, operator: str, right: _CsBuiltOperand) -> str:
    if operator == "==":
        return f"(Math.Abs(({left.value_expr}) - ({right.value_expr})) < 0.00001)"
    return f"(({left.value_expr}) {operator} ({right.value_expr}))"


def _csharp_build_condition(node: ConditionIR, counter, symbol_var: str, bars_var: str) -> "tuple[str, List[_CsBuiltOperand]]":
    if isinstance(node, ComparisonIR):
        left_built = _csharp_build_operand(node.left, next(counter), counter, symbol_var, bars_var)
        right_built = _csharp_build_operand(node.right, next(counter), counter, symbol_var, bars_var)
        expr = _csharp_comparison_expression(left_built, node.operator, right_built)
        return expr, [left_built, right_built]

    left_expr, left_ops = _csharp_build_condition(node.left, counter, symbol_var, bars_var)
    right_expr, right_ops = _csharp_build_condition(node.right, counter, symbol_var, bars_var)
    cs_op = "&&" if node.operator == "AND" else "||"
    return f"({left_expr} {cs_op} {right_expr})", left_ops + right_ops


def _csharp_action_block(action: ActionIR, index: int,
                          built_sl: Optional[_CsBuiltOperand], built_tp: Optional[_CsBuiltOperand],
                          symbol_var: str, symbol_name_var: str, rule_index: int, label: str) -> str:
    lot = round(action.lot, 2)
    sl_expr = (built_sl.value_expr if built_sl is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")
    tp_expr = (built_tp.value_expr if built_tp is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")

    if action.direction == "BUY":
        trade_type = "TradeType.Buy"
        entry_price_line = f"double entryPrice = {symbol_var}.Ask;"
        exit_ref_price_line = f"double exitRefPrice = {symbol_var}.Bid;"
        fail_label = "BUY"
        direction_const = "1"
    else:
        trade_type = "TradeType.Sell"
        entry_price_line = f"double entryPrice = {symbol_var}.Bid;"
        exit_ref_price_line = f"double exitRefPrice = {symbol_var}.Ask;"
        fail_label = "SELL"
        direction_const = "-1"

    return (
        f"                // Action {index + 1}: Open {fail_label}\n"
        f"                {{\n"
        f"                    // Lot from the builder is a target - converted to cAlgo's\n"
        f"                    // volume-in-units and snapped to this broker's actual step/min/max.\n"
        f"                    double lotToTrade = {symbol_var}.NormalizeVolumeInUnits({symbol_var}.QuantityToVolumeInUnits({lot}), RoundingMode.ToNearest);\n"
        f"                    {entry_price_line}\n"
        f"                    {exit_ref_price_line}\n"
        f"                    double slDistance = {sl_expr};\n"
        f"                    double tpDistance = {tp_expr};\n"
        f"                    // Same virtual SL/TP design as the MT5 export (see RegisterVirtualStop\n"
        f"                    // above): never attached to the order itself, watched every tick\n"
        f"                    // instead, so behaviour stays identical across both export targets.\n"
        f"                    // The label (last param) tags this position as belonging to THIS\n"
        f"                    // rule - cAlgo's equivalent of a magic number - so it's never\n"
        f"                    // confused with another rule's positions, even on the same symbol.\n"
        f"                    var result = ExecuteMarketOrder({trade_type}, {symbol_name_var}, lotToTrade, \"{label}\");\n"
        f"                    if (!result.IsSuccessful)\n"
        f"                    {{\n"
        f'                        Print("{fail_label} order failed: {{0}}", result.Error);\n'
        f"                    }}\n"
        f"                    else\n"
        f"                    {{\n"
        f"                        RegisterVirtualStop(result.Position.Id, {symbol_var}, {rule_index}, exitRefPrice, slDistance, tpDistance, {direction_const}, lotToTrade);\n"
        f"                    }}\n"
        f"                }}\n"
    )


# The C# template is a plain (non-f) string with <<TOKEN>> placeholders,
# substituted via .replace() below - deliberately NOT an f-string. This
# project already hit a real bug once from f-string brace/quote escaping
# rules silently eating characters meant for the generated file's own
# syntax (see docs/HANDOFF.md); <<TOKEN>> placeholders can't collide with C#'s
# own `{`/`}` at all, so that whole class of mistake isn't possible here.
_CSHARP_TEMPLATE = """// -----------------------------------------------------------------
// AlgoPuzzleBot.cs
// Auto-generated by "AlgoPuzzle" (No-Code Builder)
// Generated: <<GENERATED_AT>>
//
// WARNING: Always test on a Demo account before going live.
// -----------------------------------------------------------------
using System;
using System.Collections.Generic;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class AlgoPuzzleBot : Robot
    {
        // Per-rule strategy configuration (from the visual block builder) -
        // each rule uses its own _tradeSymbolN/_tradeBarsN explicitly
        // (never the Robot's own Symbol/Bars/MarketSeries, which are tied
        // to whatever chart this cBot is attached to), so every rule always
        // trades the asset chosen for it in the builder, regardless of
        // which chart the cBot happens to be running on. Same design
        // intent as the MT5 export's TradeSymbol.
<<PER_RULE_FIELDS>>
        // Per-position virtual Stop Loss / Take Profit state, keyed by
        // cAlgo's own Position.Id. Unlike MQL5, ExecuteMarketOrder() hands
        // this back directly (TradeResult.Position.Id) - no separate
        // lookup needed. On a Netted account, cAlgo itself merges
        // same-symbol orders into one position and returns the SAME id for
        // each round - even across TWO DIFFERENT rules that happen to
        // share a symbol, since cAlgo does not distinguish by Label for
        // this - so RegisterVirtualStop() below blends them exactly like
        // the MT5 export does for netting accounts (see its comment). This
        // works correctly for both Hedged and Netted Account.AccountType
        // without needing to branch on it explicitly.
        private class VirtualStopState
        {
            public Symbol Symbol = null!;
            public int RuleIndex;
            public double ExitRef;
            public double Volume;
            public double SL;
            public double TP;
            public int Direction; // 1 = Buy, -1 = Sell
        }
        private readonly Dictionary<long, VirtualStopState> _trackedStops = new Dictionary<long, VirtualStopState>();

        // Indicator handles
<<FIELD_DECLS>>

        protected override void OnStart()
        {
            Positions.Closed += OnPositionsClosed;

<<INIT_BODY>>
        }

        // Resolves the broker's actual symbol name for a base asset name -
        // see the comment on TradeSymbolBaseName above for why this exists
        // and its verification status.
        private string? ResolveBrokerSymbol(string baseName)
        {
            if (Symbols.Exists(baseName))
                return baseName;

            foreach (var candidate in Symbols)
            {
                if (candidate.IndexOf(baseName, StringComparison.OrdinalIgnoreCase) >= 0 && Symbols.Exists(candidate))
                {
                    Print("Exact symbol \\"{0}\\" not found on this broker - using \\"{1}\\" instead (closest match).", baseName, candidate);
                    return candidate;
                }
            }
            return null;
        }

        // Snaps a price to the symbol's tradeable tick size - same purpose
        // as the MT5 export's NormalizeToTick().
        private double NormalizeToTick(Symbol symbol, double price)
        {
            double tickSize = symbol.TickSize;
            if (tickSize <= 0)
                tickSize = symbol.PipSize / 10.0;
            double snapped = Math.Round(price / tickSize) * tickSize;
            return Math.Round(snapped, symbol.Digits);
        }

        // Counts how many currently-tracked positions belong to one rule -
        // used to reset that rule's own "rounds since flat" counter
        // independently of every other rule.
        private int CountTrackedForRule(int ruleIndex)
        {
            return _trackedStops.Values.Count(s => s.RuleIndex == ruleIndex);
        }

        // Registers a just-opened (or just-added-to) position's virtual
        // SL/TP, blending this round's exit-side reference price into the
        // running volume-weighted average for that position id first - a
        // direct port of the MT5 export's RegisterVirtualStop() (see its
        // comment there for the full reasoning on why the blend matters
        // and why it's NOT anchored to the entry-side price). `symbol` and
        // `ruleIndex` are stored so OnTick() checks the right symbol's
        // price and so each rule can count only its own positions.
        private void RegisterVirtualStop(long positionId, Symbol symbol, int ruleIndex, double exitRefPrice, double slDistance, double tpDistance, int direction, double volume)
        {
            VirtualStopState state;
            if (!_trackedStops.TryGetValue(positionId, out state))
            {
                state = new VirtualStopState { Symbol = symbol, RuleIndex = ruleIndex, ExitRef = 0, Volume = 0 };
                _trackedStops[positionId] = state;
            }

            double totalVolume = state.Volume + volume;
            double blendedExitRef = totalVolume > 0
                ? (state.ExitRef * state.Volume + exitRefPrice * volume) / totalVolume
                : exitRefPrice;

            state.ExitRef = blendedExitRef;
            state.Volume = totalVolume;
            state.Direction = direction;
            state.SL = slDistance <= 0 ? 0 : NormalizeToTick(symbol, direction == 1 ? blendedExitRef - slDistance : blendedExitRef + slDistance);
            state.TP = tpDistance <= 0 ? 0 : NormalizeToTick(symbol, direction == 1 ? blendedExitRef + tpDistance : blendedExitRef - tpDistance);
        }

        // Cleans up tracked state for a position that closed by any means
        // (our own virtual stop below, a manual close, a margin call, ...),
        // and resets THAT position's own rule's round counter once nothing
        // of its is left open - independent of every other rule.
        private void OnPositionsClosed(PositionClosedEventArgs args)
        {
            VirtualStopState closedState;
            if (_trackedStops.TryGetValue(args.Position.Id, out closedState))
            {
                int ruleIndex = closedState.RuleIndex;
                _trackedStops.Remove(args.Position.Id);
                if (CountTrackedForRule(ruleIndex) == 0)
                {
                    _roundsSinceFlat[ruleIndex] = 0;
                }
            }
        }

        protected override void OnTick()
        {
            // Virtual Stop Loss / Take Profit monitor: runs on EVERY tick
            // (not gated to new bars), since price can cross a stop level
            // at any moment. Never attaches SL/TP to the order itself -
            // watches live price and closes the position directly, exactly
            // like the MT5 export (see its OnTick() for the full reasoning).
            // Shared across every rule below - state.Symbol (not a single
            // field) is used for the price check, since different rules
            // can track positions on different symbols.
            foreach (var kvp in _trackedStops.ToList())
            {
                long positionId = kvp.Key;
                var state = kvp.Value;
                var position = Positions.FirstOrDefault(p => p.Id == positionId);
                if (position == null)
                {
                    _trackedStops.Remove(positionId);
                    continue;
                }

                double checkPrice = state.Direction == 1 ? state.Symbol.Bid : state.Symbol.Ask;
                bool hitSL = state.SL > 0 && (state.Direction == 1 ? checkPrice <= state.SL : checkPrice >= state.SL);
                bool hitTP = state.TP > 0 && (state.Direction == 1 ? checkPrice >= state.TP : checkPrice <= state.TP);

                if (hitSL || hitTP)
                {
                    var closeResult = ClosePosition(position);
                    if (closeResult.IsSuccessful)
                    {
                        Print(hitSL ? "Virtual Stop Loss triggered - position closed." : "Virtual Take Profit triggered - position closed.");
                    }
                    else
                    {
                        Print("Virtual SL/TP close failed: {0}", closeResult.Error);
                    }
                }
            }
        }

<<BAR_OPENED_HANDLERS>>
    }
}
"""


def render_csharp(ir: StrategyIR) -> str:
    """Turn a StrategyIR into a complete cTrader cBot (.cs) source file.
    See the module comment above this function for the design rationale.
    Every rule in ir.rules gets its own symbol/bars/round-counter/label and
    runs side by side inside ONE combined OnStart()/OnTick() - same overall
    approach as render_mql5()/render_mql4(), adapted to cAlgo's per-symbol
    Bars.BarOpened event model (one handler method per rule instead of one
    shared polling loop) and array-indexed per-rule state (idiomatic C#,
    instead of the Nth-suffixed field-per-rule naming the MQL renderers use)."""

    for rule in ir.rules:
        if rule.timeframe not in CTRADER_TIMEFRAME:
            raise StrategyValidationError(f"Rule {rule.rule_index + 1} ({rule.asset}): unsupported timeframe: {rule.timeframe}")

    counter = itertools.count()  # shared file-wide so no two rules' field names collide

    field_lines: List[str] = []
    init_parts: List[str] = []
    handler_parts: List[str] = []
    base_names: List[str] = []
    max_positions_values: List[str] = []

    for rule in ir.rules:
        i = rule.rule_index
        symbol_var = f"_tradeSymbol[{i}]"
        bars_var = f"_tradeBars[{i}]"
        symbol_name_var = f"TradeSymbolName[{i}]"
        base_name_var = f"TradeSymbolBaseName[{i}]"
        label = f"AlgoPuzzle_Rule{i}"

        comparison_expr, condition_ops = _csharp_build_condition(rule.condition, counter, symbol_var, bars_var)

        built_sl_by_action: dict = {}
        built_tp_by_action: dict = {}
        extra_ops: List[_CsBuiltOperand] = []
        for ai, action in enumerate(rule.actions):
            if action.sl is not None:
                built_sl_by_action[ai] = _csharp_build_operand(action.sl, next(counter), counter, symbol_var, bars_var)
                extra_ops.append(built_sl_by_action[ai])
            if action.tp is not None:
                built_tp_by_action[ai] = _csharp_build_operand(action.tp, next(counter), counter, symbol_var, bars_var)
                extra_ops.append(built_tp_by_action[ai])

        built_operands = condition_ops + extra_ops
        for b in built_operands:
            if b.field_decl:
                field_lines.extend(b.field_decl.split("\n"))
        rule_init = "".join(b.init_code for b in built_operands if b.init_code)

        actions_body = "".join(
            _csharp_action_block(a, ai, built_sl_by_action.get(ai), built_tp_by_action.get(ai), symbol_var, symbol_name_var, i, label)
            for ai, a in enumerate(rule.actions)
        )

        base_names.append(f'"{rule.asset}"')
        max_positions_values.append(str(rule.max_positions))

        init_parts.append(
            f"            // --- Rule {i + 1} ({rule.asset}) ---\n"
            f"            {{\n"
            f"                // {base_name_var} may not exist as-is on this broker.\n"
            f"                // ResolveBrokerSymbol() tries an exact match first, then falls\n"
            f"                // back to scanning for a close match - same shape as the MT5\n"
            f"                // export's ResolveBrokerSymbol(). It is NOT confirmed whether\n"
            f"                // cTrader brokers append suffixes the way some MT5 white-labels\n"
            f"                // do - this fallback is cheap insurance either way, but treat it\n"
            f"                // as unverified until checked against a real cTrader account.\n"
            f"                {symbol_name_var} = ResolveBrokerSymbol({base_name_var});\n"
            f"                if ({symbol_name_var} == null)\n"
            f"                {{\n"
            f'                    Print("Rule {i + 1}: no symbol matching \\"{{0}}\\" was found on this broker. Check the Symbol list for the exact name this broker uses and edit TradeSymbolBaseName near the top of this file.", {base_name_var});\n'
            f"                    Stop();\n"
            f"                    return;\n"
            f"                }}\n"
            f"                {symbol_var} = Symbols.GetSymbol({symbol_name_var});\n"
            f"                {bars_var} = MarketData.GetBars({CTRADER_TIMEFRAME[rule.timeframe]}, {symbol_name_var});\n"
            f"                {bars_var}.BarOpened += OnTradeBarOpened{i};\n"
            f"{rule_init}"
            f'                Print("AlgoPuzzle cBot - Rule {i + 1} initialized on {{0}}", {symbol_name_var});\n'
            f"            }}\n"
        )

        handler_parts.append(
            f"        // Strategy logic for Rule {i + 1} only evaluates once per new bar on\n"
            f"        // its own configured timeframe - cAlgo's Bars.BarOpened event does\n"
            f"        // this natively, unlike MQL5 which has to compare iTime() by hand\n"
            f"        // every tick. Each rule subscribes its own instance of this handler\n"
            f"        // to its own {bars_var}, so rules on different timeframes each fire\n"
            f"        // only on their own bar closes.\n"
            f"        private void OnTradeBarOpened{i}(BarOpenedEventArgs obj)\n"
            f"        {{\n"
            f"            bool signalTriggered{i} = {comparison_expr};\n\n"
            f"            if (signalTriggered{i})\n"
            f"            {{\n"
            f"                // \"Positions\" setting from the IF block - same round-based\n"
            f"                // gate as the MT5 export, scoped to this rule only.\n"
            f"                if (_roundsSinceFlat[{i}] < MaxSimultaneousPositions[{i}])\n"
            f"                {{\n"
            f"                    _roundsSinceFlat[{i}]++;\n"
            f"{actions_body}"
            f"                }}\n"
            f"            }}\n"
            f"        }}\n"
        )

    field_decls = "\n".join(f"        {line}" for line in field_lines) if field_lines else "        // No indicators required for this strategy."
    rule_count = len(ir.rules)

    per_rule_fields = (
        f"        private static readonly string[] TradeSymbolBaseName = new string[] {{ {', '.join(base_names)} }};\n"
        f"        private readonly string[] TradeSymbolName = new string[{rule_count}];\n"
        f"        private readonly Symbol[] _tradeSymbol = new Symbol[{rule_count}];\n"
        f"        private readonly Bars[] _tradeBars = new Bars[{rule_count}];\n"
        f"\n"
        f"        // How many separate \"rounds\" of THEN actions are currently open per\n"
        f"        // rule, and each rule's own configured limit (the \"Positions\" field on\n"
        f"        // its IF block). Resets to 0 once every position THAT RULE opened has\n"
        f"        // actually closed (see OnPositionsClosed below), so the next signal can\n"
        f"        // open up to MaxSimultaneousPositions[i] rounds again for that rule.\n"
        f"        private readonly int[] _roundsSinceFlat = new int[{rule_count}];\n"
        f"        private static readonly int[] MaxSimultaneousPositions = new int[] {{ {', '.join(max_positions_values)} }};\n"
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    replacements = {
        "<<GENERATED_AT>>": generated_at,
        "<<PER_RULE_FIELDS>>": per_rule_fields,
        "<<FIELD_DECLS>>": field_decls,
        "<<INIT_BODY>>": "".join(init_parts),
        "<<BAR_OPENED_HANDLERS>>": "".join(handler_parts),
    }
    result = _CSHARP_TEMPLATE
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def generate_readme_csharp(ir: StrategyIR) -> str:
    rule_summaries = "\n\n".join(
        f"Rule {r.rule_index + 1} (label AlgoPuzzle_Rule{r.rule_index})\n"
        f"- Asset:      {r.asset}\n"
        f"- Timeframe:  {r.timeframe}\n"
        f"- Actions:    {_describe_actions(r)}\n"
        f"- Positions:  {_describe_positions(r)}"
        for r in ir.rules
    )
    assets_line = ", ".join(dict.fromkeys(r.asset for r in ir.rules))  # de-duplicated, order-preserved
    _summary_heading = f"Strategy summary ({len(ir.rules)} rule{'s' if len(ir.rules) != 1 else ''})"
    _multi_rule_heading = f"NOTE ON RUNNING {len(ir.rules)} RULES IN ONE cBOT"
    multi_rule_note = "" if len(ir.rules) == 1 else f"""
{_multi_rule_heading}
{"-" * len(_multi_rule_heading)}
This file combines {len(ir.rules)} independent strategy rules into a single
cBot. Each runs on its own schedule, evaluates its own condition, and
tracks its own open positions completely independently (tagged with its
own label, listed above) - one rule misfiring or hitting its position
limit never blocks another.

If two rules trade the SAME asset on a Netted account, be aware that
cTrader itself only allows ONE net position per symbol - it will merge
both rules' orders into that single position no matter what label is
attached, exactly the way multiple rounds from a single rule already
blend together (see the Stop Loss / Take Profit note below). On a Hedged
account, or when rules trade different assets, each rule's positions stay
fully independent.
"""

    return f"""ALGOPUZZLE - cTrader Setup Guide
=============================================

Your strategy has been compiled into a cTrader cBot
(AlgoPuzzleBot.cs). Follow the steps below to install and run it.

{_summary_heading}
{"-" * len(_summary_heading)}
{rule_summaries}
{multi_rule_note}
NOTE ON STOP LOSS / TAKE PROFIT
--------------------------------
SL/TP are entered as Pips or Percent-of-entry-price (converted to the real
price distance automatically), or as a dynamic expression like "2 x ATR(14)"
so your stop adapts to current volatility. A value of "none" means no Stop
Loss / Take Profit is attached to that action. Like the MT5 version, SL/TP
are enforced by the cBot itself (watched every tick) rather than attached
to the order.

STEP 1 - Open cTrader
-----------------------
Launch the cTrader desktop application and log into your Demo or Live
trading account. (Always start with a Demo account - see IMPORTANT
SAFETY NOTES at the bottom.)

STEP 2 - Open cTrader Automate
---------------------------------
Click "Automate" in the left-hand navigation to open the cBot workspace.

STEP 3 - Create a new cBot
------------------------------
Click "New cBot" (or the "+" button), give it a name, and it will open
in the built-in code editor with a default template.

STEP 4 - Paste the generated code
-------------------------------------
Select all the template code in the editor and replace it entirely with
the contents of "AlgoPuzzleBot.cs" from this zip.

STEP 5 - Build
------------------
Press Ctrl+B (or click "Build"). Check the Build Result panel at the
bottom - it must show 0 errors before continuing. If there are errors,
they are most likely due to a cAlgo API version mismatch between your
cTrader version and the one this was generated against - review the
specific error message; it usually points at exactly which call needs
adjusting.

STEP 6 - Add the cBot to a Chart
-------------------------------------
Open a chart for ANY symbol/timeframe (it doesn't have to match any of the
rules below - the cBot always trades exactly the asset(s)/timeframe(s)
shown in the strategy summary above, regardless of which chart it's added
to: {assets_line}). In that chart's toolbar, click the "Add cBot" icon and
choose "AlgoPuzzleBot" from the list, then click "Add to chart". It
appears as a new panel on the chart, not started yet.

STEP 7 - Start the cBot
---------------------------
Click "Start" on the cBot panel you just added. You'll be asked to pick
an instance type:
  - "Local instance" runs on your own machine - simplest to use, but
    only trades while cTrader stays open and connected.
  - "Cloud instance" runs on cTrader's own servers, 24/7, even with
    your computer off - better once you trust the strategy, unnecessary
    for a first Demo test.
For a first test, Local is simplest. Click Start to confirm.

STEP 8 - Confirm it's actually running
-------------------------------------------
At the bottom of the platform, next to Positions / Orders / History,
open the "Log" tab. Within a few seconds after starting you should see
one initialization line PER RULE, e.g.:
    AlgoPuzzle cBot - Rule 1 initialized on {ir.rules[0].asset}
If you see one for every rule above, the cBot is live and evaluating each
rule on its own timeframe. If nothing appears, confirm the instance shows
as "Running" (not just "Added") and that the asset was actually found on
your broker - a "No symbol matching" message in the Log means that rule's
asset name needs adjusting (see the note in the .cs file's OnStart()).

A NOTE ON WHAT YOU'LL SEE IN POSITIONS
-------------------------------------------
When a position opens, cTrader's own Positions tab will show it with NO
Stop Loss or Take Profit value filled in - even though you configured
one. This is expected, not a bug: this cBot manages SL/TP itself
internally (see NOTE ON STOP LOSS / TAKE PROFIT above) rather than
attaching them to the order, so nothing appears in that column. The
"Log" tab will print "Virtual Stop Loss triggered" or "Virtual Take
Profit triggered" when the cBot closes a position for you.

IMPORTANT SAFETY NOTES
-----------------------
- Always test this cBot on a Demo account first.
- This build does not include backtesting validation - run it through
  cTrader's own backtesting before using real funds.
- Past performance and rule-based logic do not guarantee future
  results. Trade responsibly.

Generated by AlgoPuzzle (MVP).
"""


# ============================================================================
# MQL4 (MetaTrader 4) renderer
# ============================================================================
# Mirrors render_mql5() feature-for-feature (same IR, same business rules),
# but simpler in two structural ways that are genuine MQL4 characteristics,
# not shortcuts:
#   - No netting-account SL/TP blending. MT4 has no netting account type at
#     all - every account is effectively "hedging" (every OrderSend() always
#     gets its own independent ticket) - so the volume-weighted blend logic
#     RegisterVirtualStop() needs on MT5 (see its comment there) simply does
#     not apply here. This renderer never generates that branch.
#   - No indicator handle/CopyBuffer bookkeeping. Classic MQL4 indicator
#     functions (iMA, iRSI, iBands, ...) return the value directly from a
#     single call - no handle to create in OnInit(), no buffer to copy in
#     OnTick(), no handle to release in OnDeinit(). Operands are inlined as
#     plain expressions, same as the C# renderer's design but for a
#     different reason (cAlgo caches indicator *objects*; MQL4 just doesn't
#     need caching at all for this).
#
# `#property strict` is INCLUDED here (unlike the MT5 renderer, which
# deliberately removed it - see render_mql5()'s history in docs/HANDOFF.md item
# 1). It's an MQL4-only directive that enables stricter compile-time type
# checking; harmless and additionally useful in MQL4, whereas in MQL5 it is
# simply a no-op.
#
# Ticket tracking is deliberately simpler than MT5's DEAL_POSITION_ID dance:
# OrderSend() returns the ticket directly as its result, no separate lookup
# needed - closer to how cTrader's ExecuteMarketOrder() also hands back the
# Position object immediately (see render_csharp()).

# iBands()/iStochastic()/iMACD() "mode" parameter constant names - MQL4's
# classic indicator functions take one of these named ints, unlike MQL5's
# buffer-index scheme (_BANDS_BUFFER_INDEX / _STOCH_BUFFER_INDEX above),
# though the underlying values line up 1:1 with those same dicts.
_MQL4_BANDS_MODE = {"MIDDLE": "MODE_MAIN", "UPPER": "MODE_UPPER", "LOWER": "MODE_LOWER"}
_MQL4_STOCH_MODE = {"K": "MODE_MAIN", "D": "MODE_SIGNAL"}


def _mql4_build_operand(operand: OperandIR, timeframe: str, symbol_var: str) -> str:
    """Translate one OperandIR directly into an MQL4 expression string - no
    accompanying declarations needed (see module comment above), so unlike
    the MQL5/C# builders this returns a plain string, not a wrapper object.
    `symbol_var` is the per-rule TradeSymbol_N variable this operand reads
    its price/indicator data from."""

    if operand.kind == "NUMBER":
        return f"{operand.value}"

    if operand.kind == "RISK_VALUE":
        if operand.unit == "PRICE":
            return f"{operand.value}"
        if operand.unit == "PIPS":
            return f"({operand.value} * PipSize({symbol_var}))"
        if operand.unit == "PERCENT":
            return f"(({operand.value} / 100.0) * {_ENTRY_PRICE_PLACEHOLDER})"
        raise StrategyValidationError(f"Unsupported risk value unit: {operand.unit}")

    if operand.kind == "CANDLE":
        expr_map = {
            "CURRENT": f"MarketInfo({symbol_var}, MODE_BID)",
            "PREV_OPEN": f"iOpen({symbol_var}, {timeframe}, 1)",
            "PREV_CLOSE": f"iClose({symbol_var}, {timeframe}, 1)",
            "PREV_HIGH": f"iHigh({symbol_var}, {timeframe}, 1)",
            "PREV_LOW": f"iLow({symbol_var}, {timeframe}, 1)",
        }
        return expr_map[operand.candle_type]

    if operand.kind == "VOLUME":
        shift = 0 if operand.volume_bar == "CURRENT" else 1
        return f"(double)iVolume({symbol_var}, {timeframe}, {shift})"

    if operand.kind == "MA":
        return f"iMA({symbol_var}, {timeframe}, {operand.period}, 0, {operand.ma_type}, PRICE_CLOSE, 0)"

    if operand.kind == "RSI":
        return f"iRSI({symbol_var}, {timeframe}, {operand.period}, PRICE_CLOSE, 0)"

    if operand.kind == "ATR":
        return f"iATR({symbol_var}, {timeframe}, {operand.period}, 0)"

    if operand.kind == "BANDS":
        mode = _MQL4_BANDS_MODE[operand.band]
        return f"iBands({symbol_var}, {timeframe}, {operand.period}, {operand.deviation}, 0, PRICE_CLOSE, {mode}, 0)"

    if operand.kind == "STOCH":
        mode = _MQL4_STOCH_MODE[operand.stoch_line]
        return (
            f"iStochastic({symbol_var}, {timeframe}, {operand.k_period}, {operand.d_period}, "
            f"{operand.slowing}, MODE_SMA, 0, {mode}, 0)"
        )

    if operand.kind == "MACD":
        main_expr = f"iMACD({symbol_var}, {timeframe}, {MACD_FAST}, {MACD_SLOW}, {MACD_SIGNAL}, PRICE_CLOSE, MODE_MAIN, 0)"
        if operand.line == "MAIN":
            return main_expr
        # HIST: like MT5's iMACD, MQL4's iMACD only exposes MAIN/SIGNAL -
        # the histogram is their difference, computed the same way.
        signal_expr = f"iMACD({symbol_var}, {timeframe}, {MACD_FAST}, {MACD_SLOW}, {MACD_SIGNAL}, PRICE_CLOSE, MODE_SIGNAL, 0)"
        return f"({main_expr} - {signal_expr})"

    if operand.kind == "MULTIPLY":
        left = _mql4_build_operand(operand.left, timeframe, symbol_var)
        right = _mql4_build_operand(operand.right, timeframe, symbol_var)
        return f"(({left}) * ({right}))"

    raise StrategyValidationError(f"Unsupported operand kind: {operand.kind}")


def _mql4_comparison_expression(left_expr: str, operator: str, right_expr: str) -> str:
    if operator == "==":
        return f"(MathAbs(({left_expr}) - ({right_expr})) < 0.00001)"
    return f"(({left_expr}) {operator} ({right_expr}))"


def _mql4_build_condition(node: ConditionIR, timeframe: str, symbol_var: str) -> str:
    if isinstance(node, ComparisonIR):
        left = _mql4_build_operand(node.left, timeframe, symbol_var)
        right = _mql4_build_operand(node.right, timeframe, symbol_var)
        return _mql4_comparison_expression(left, node.operator, right)

    left = _mql4_build_condition(node.left, timeframe, symbol_var)
    right = _mql4_build_condition(node.right, timeframe, symbol_var)
    mql_op = "&&" if node.operator == "AND" else "||"
    return f"({left} {mql_op} {right})"


def _mql4_action_block(action: ActionIR, index: int, timeframe: str, symbol_var: str, magic_var: str) -> str:
    lot = round(action.lot, 2)
    sl_expr = (_mql4_build_operand(action.sl, timeframe, symbol_var) if action.sl is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")
    tp_expr = (_mql4_build_operand(action.tp, timeframe, symbol_var) if action.tp is not None else "0").replace(_ENTRY_PRICE_PLACEHOLDER, "entryPrice")

    if action.direction == "BUY":
        order_type = "OP_BUY"
        entry_price_line = f"double entryPrice = MarketInfo({symbol_var}, MODE_ASK);"
        exit_ref_price_line = f"double exitRefPrice = MarketInfo({symbol_var}, MODE_BID);"
        fail_label = "BUY"
        direction_const = "1"
    else:
        order_type = "OP_SELL"
        entry_price_line = f"double entryPrice = MarketInfo({symbol_var}, MODE_BID);"
        exit_ref_price_line = f"double exitRefPrice = MarketInfo({symbol_var}, MODE_ASK);"
        fail_label = "SELL"
        direction_const = "-1"

    return (
        f"      // Action {index + 1}: Open {fail_label}\n"
        f"      {{\n"
        f"         // Lot from the builder is a target - snapped to this broker's actual\n"
        f"         // volume step/min/max, which vary widely broker to broker.\n"
        f"         double lotToTrade = NormalizeLot({symbol_var}, {lot});\n"
        f"         {entry_price_line}\n"
        f"         {exit_ref_price_line}\n"
        f"         double slDistance = {sl_expr};\n"
        f"         double tpDistance = {tp_expr};\n"
        f"         // Virtual (EA-managed) SL/TP, same design as the MT5 export - never\n"
        f"         // attached to the order itself, watched every tick instead (see\n"
        f"         // OnTick() below). OrderSend() hands back the new ticket directly, no\n"
        f"         // separate resolution step needed (unlike MT5's ResolveOpenedPositionId()).\n"
        f"         // The magic number (2nd-to-last param) tags this order as belonging to\n"
        f"         // THIS rule, so it's never confused with another rule's orders even when\n"
        f"         // two rules trade the same symbol.\n"
        f"         int ticket = OrderSend({symbol_var}, {order_type}, lotToTrade, entryPrice, "
        f"OrderSlippagePoints, 0, 0, \"AlgoPuzzle\", {magic_var}, 0, clrNONE);\n"
        f"         if(ticket < 0)\n"
        f"           {{\n"
        f'            Print("{fail_label} order failed. Error: ", GetLastError());\n'
        f"           }}\n"
        f"         else\n"
        f"           {{\n"
        f"            RegisterVirtualStop(ticket, {symbol_var}, {magic_var}, exitRefPrice, slDistance, tpDistance, {direction_const});\n"
        f"           }}\n"
        f"      }}\n"
    )


# Plain (non-f) string with <<TOKEN>> placeholders substituted via
# .replace() - same reasoning as the C# template above: avoids any chance
# of repeating the f-string \" vs \\" escaping bug this project already
# hit once in the original MQL5 renderer (see docs/HANDOFF.md item 1).
_MQL4_TEMPLATE = """//+------------------------------------------------------------------+
//|                                              strategy.mq4         |
//|         Auto-generated by "AlgoPuzzle" (No-Code Builder) |
//|         Generated: <<GENERATED_AT>>                                |
//|                                                                     |
//|  WARNING: Always test on a Demo account before going live.         |
//+------------------------------------------------------------------+
#property copyright "AlgoPuzzle"
#property link      ""
#property version   "1.00"
#property strict

//--- Virtual Stop Loss / Take Profit state. MT4 has no netting account
//--- type - every account is effectively "hedging" (each OrderSend() call
//--- always gets its own independent ticket) - so unlike the MT5 export
//--- there is no volume-weighted blending needed here: each ticket's SL/TP
//--- is simply stored directly. A THEN block can still contain more than
//--- one action (e.g. two BUYs at different sizes), and this EA can run
//--- more than one independent rule at once, so stops are tracked
//--- per-ticket in these parallel arrays rather than a single shared
//--- variable, same shape as the MT5/cTrader exports. g_vsSymbol/g_vsMagic
//--- record which symbol to check the price against and which rule this
//--- ticket belongs to.
int    g_vsTicket[];
string g_vsSymbol[];
int    g_vsMagic[];
double g_vsSL[];
double g_vsTP[];
int    g_vsDirection[];  // 1 = BUY, -1 = SELL

//--- Slippage tolerance (in points) for market order execution.
const int OrderSlippagePoints = 5;

//--- Per-rule strategy configuration (from the visual block builder) -----
<<GLOBAL_DECLS>>

//+------------------------------------------------------------------+
//| Converts 1 "pip" to a price distance for the given symbol.        |
//| Standard convention: on 3 or 5-digit ("fractional pip") symbols,  |
//| 1 pip = 10 points; on 2 or 4-digit symbols, 1 pip = 1 point.      |
//+------------------------------------------------------------------+
double PipSize(string symbol)
  {
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double point = MarketInfo(symbol, MODE_POINT);
   if(digits == 3 || digits == 5)
      return(point * 10.0);
   return(point);
  }

//+------------------------------------------------------------------+
//| Snaps a price to the broker's actual tradeable price increment    |
//| (MODE_TICKSIZE), not just the symbol's decimal digits. Some       |
//| brokers reject SL/TP as invalid if the price isn't an exact       |
//| multiple of the tick size, even when the distance from the        |
//| current price is otherwise perfectly valid.                       |
//+------------------------------------------------------------------+
double NormalizeToTick(string symbol, double price)
  {
   double tickSize = MarketInfo(symbol, MODE_TICKSIZE);
   if(tickSize <= 0)
      tickSize = MarketInfo(symbol, MODE_POINT);
   double snapped = MathRound(price / tickSize) * tickSize;
   return(NormalizeDouble(snapped, (int)MarketInfo(symbol, MODE_DIGITS)));
  }

//+------------------------------------------------------------------+
//| Resolves the broker's actual symbol name for a base asset name,   |
//| so this EA works on any broker regardless of that broker's symbol |
//| naming convention (e.g. "EURUSD" vs "EURUSD.a" vs "EURUSDm" etc). |
//| Tries an exact match first; if that isn't found in the broker's   |
//| symbol list, scans every available symbol for one containing the  |
//| base name and uses the first match. Returns "" if nothing matches |
//| at all, meaning this asset genuinely isn't offered by the broker. |
//+------------------------------------------------------------------+
string ResolveBrokerSymbol(string baseName)
  {
   if(SymbolSelect(baseName, true))
      return(baseName);

   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string candidate = SymbolName(i, false);
      if(StringFind(candidate, baseName) >= 0 && SymbolSelect(candidate, true))
        {
         Print("Exact symbol \\"", baseName, "\\" not found on this broker - using \\"",
               candidate, "\\" instead (closest match).");
         return(candidate);
        }
     }
   return("");
  }

//+------------------------------------------------------------------+
//| Snaps a lot size to the broker's actual MODE_LOTSTEP and clamps   |
//| it into [MODE_MINLOT, MODE_MAXLOT]. Brokers disagree wildly on    |
//| these, so the lot value chosen in the builder is a target, not a  |
//| guarantee - this is what actually gets sent to the server.        |
//+------------------------------------------------------------------+
double NormalizeLot(string symbol, double lot)
  {
   double minLot  = MarketInfo(symbol, MODE_MINLOT);
   double maxLot  = MarketInfo(symbol, MODE_MAXLOT);
   double lotStep = MarketInfo(symbol, MODE_LOTSTEP);
   if(lotStep <= 0)
      lotStep = 0.01;
   double normalized = MathRound(lot / lotStep) * lotStep;
   if(minLot > 0)
      normalized = MathMax(minLot, normalized);
   if(maxLot > 0)
      normalized = MathMin(maxLot, normalized);
   return(NormalizeDouble(normalized, 3));
  }

//+------------------------------------------------------------------+
//| Registers a just-opened position's virtual SL/TP, keyed by ticket.|
//| No blending needed here (unlike the MT5 export's RegisterVirtual- |
//| Stop()) - MT4 has no netting accounts, so every ticket is always  |
//| independent already. `symbol`/`magic` are stored alongside so the |
//| OnTick() watch loop checks the right symbol's price, and so each  |
//| rule can count only ITS OWN tracked tickets (CountTrackedForMagic)|
//+------------------------------------------------------------------+
void RegisterVirtualStop(int ticket, string symbol, int magic, double exitRefPrice, double slDistance, double tpDistance, int direction)
  {
   int idx = -1;
   for(int i = 0; i < ArraySize(g_vsTicket); i++)
     {
      if(g_vsTicket[i] == ticket)
        {
         idx = i;
         break;
        }
     }
   if(idx < 0)
     {
      idx = ArraySize(g_vsTicket);
      ArrayResize(g_vsTicket, idx + 1);
      ArrayResize(g_vsSymbol, idx + 1);
      ArrayResize(g_vsMagic, idx + 1);
      ArrayResize(g_vsSL, idx + 1);
      ArrayResize(g_vsTP, idx + 1);
      ArrayResize(g_vsDirection, idx + 1);
      g_vsTicket[idx] = ticket;
      g_vsSymbol[idx] = symbol;
      g_vsMagic[idx]  = magic;
     }
   g_vsDirection[idx] = direction;
   g_vsSL[idx] = (slDistance <= 0) ? 0 :
      NormalizeToTick(symbol, (direction == 1) ? (exitRefPrice - slDistance) : (exitRefPrice + slDistance));
   g_vsTP[idx] = (tpDistance <= 0) ? 0 :
      NormalizeToTick(symbol, (direction == 1) ? (exitRefPrice + tpDistance) : (exitRefPrice - tpDistance));
  }

//+------------------------------------------------------------------+
//| Stops tracking one entry (order closed, or its virtual stop just  |
//| fired) - swap-removes it to avoid shifting the whole array.       |
//+------------------------------------------------------------------+
void RemoveVirtualStop(int idx)
  {
   int last = ArraySize(g_vsTicket) - 1;
   g_vsTicket[idx]    = g_vsTicket[last];
   g_vsSymbol[idx]    = g_vsSymbol[last];
   g_vsMagic[idx]     = g_vsMagic[last];
   g_vsSL[idx]        = g_vsSL[last];
   g_vsTP[idx]        = g_vsTP[last];
   g_vsDirection[idx] = g_vsDirection[last];
   ArrayResize(g_vsTicket, last);
   ArrayResize(g_vsSymbol, last);
   ArrayResize(g_vsMagic, last);
   ArrayResize(g_vsSL, last);
   ArrayResize(g_vsTP, last);
   ArrayResize(g_vsDirection, last);
  }

//+------------------------------------------------------------------+
//| Counts how many currently-tracked tickets belong to one rule (by  |
//| magic number) - used to reset that rule's own "rounds since flat" |
//| counter independently of every other rule.                        |
//+------------------------------------------------------------------+
int CountTrackedForMagic(int magic)
  {
   int count = 0;
   for(int i = 0; i < ArraySize(g_vsMagic); i++)
      if(g_vsMagic[i] == magic)
         count++;
   return(count);
  }

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
  {
<<INIT_BODY>>
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Nothing to release - classic MQL4 indicator functions (iMA, iRSI,
   // ...) don't use handles, unlike MQL5's iXxx()+CopyBuffer() pattern.
  }

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
  {
   //--- Virtual Stop Loss / Take Profit monitor: runs on EVERY tick (not
   //--- gated to new bars). Iterated BACKWARDS deliberately - not just
   //--- because RemoveVirtualStop() swap-removes from our own array, but
   //--- because this is the single most common MQL4 bug of all: closing
   //--- orders while iterating an order list forwards skips every other
   //--- one, since the list shrinks out from under a forward-counting loop.
   //--- Shared across every rule below - g_vsSymbol[i] (not a single
   //--- global symbol) is used for the price check, since different rules
   //--- can track tickets on different symbols.
   for(int i = ArraySize(g_vsTicket) - 1; i >= 0; i--)
     {
      if(!OrderSelect(g_vsTicket[i], SELECT_BY_TICKET) || OrderCloseTime() != 0)
        {
         // Ticket no longer an open order (closed some other way) - stop tracking it.
         RemoveVirtualStop(i);
         continue;
        }

      double checkPrice = (g_vsDirection[i] == 1)
         ? MarketInfo(g_vsSymbol[i], MODE_BID)   // BUY exits by selling, at Bid
         : MarketInfo(g_vsSymbol[i], MODE_ASK);  // SELL exits by buying, at Ask

      bool hitSL = (g_vsSL[i] > 0) &&
         ((g_vsDirection[i] == 1) ? (checkPrice <= g_vsSL[i]) : (checkPrice >= g_vsSL[i]));
      bool hitTP = (g_vsTP[i] > 0) &&
         ((g_vsDirection[i] == 1) ? (checkPrice >= g_vsTP[i]) : (checkPrice <= g_vsTP[i]));

      if(hitSL || hitTP)
        {
         if(OrderClose(g_vsTicket[i], OrderLots(), checkPrice, OrderSlippagePoints, clrNONE))
           {
            Print(hitSL ? "Virtual Stop Loss triggered - position closed." : "Virtual Take Profit triggered - position closed.");
            RemoveVirtualStop(i);
           }
         else
           {
            Print("Virtual SL/TP close failed. Error: ", GetLastError());
           }
        }
     }
<<ONTICK_BODY>>
  }
//+------------------------------------------------------------------+
"""


def render_mql4(ir: StrategyIR) -> str:
    """Turn a StrategyIR into a complete .mq4 Expert Advisor source file.
    See the module comment above this function for the design rationale.
    Every rule in ir.rules gets its own symbol/timeframe/round-counter/
    magic number and runs side by side inside ONE combined OnInit()/
    OnTick() - same overall approach as render_mql5()."""

    global_decls_parts: List[str] = []
    init_parts: List[str] = []
    ontick_parts: List[str] = []

    for rule in ir.rules:
        i = rule.rule_index
        symbol_var = f"TradeSymbol_{i}"
        timeframe_var = f"TradeTimeframe_{i}"
        magic_var = f"MagicNumber_{i}"
        max_pos_var = f"MaxSimultaneousPositions_{i}"
        rounds_var = f"g_openRoundsSinceFlat_{i}"

        comparison_expr = _mql4_build_condition(rule.condition, rule.timeframe, symbol_var)
        actions_body = "".join(
            _mql4_action_block(a, ai, rule.timeframe, symbol_var, magic_var)
            for ai, a in enumerate(rule.actions)
        )

        global_decls_parts.append(
            f"//--- Rule {i + 1}: {rule.asset} on {rule.timeframe.replace('PERIOD_', '')}. All indicator, price\n"
            f"//--- and trade calls for this rule use {symbol_var} explicitly, so it always\n"
            f"//--- trades this asset regardless of which chart the EA is attached to.\n"
            f"string {symbol_var} = \"{rule.asset}\";\n"
            f"int {timeframe_var} = {rule.timeframe};\n"
            f"const int {magic_var} = {rule.magic};\n"
            f"const int {max_pos_var} = {rule.max_positions};\n"
            f"int {rounds_var} = 0;\n\n"
        )

        init_parts.append(
            f"   // --- Rule {i + 1} ({rule.asset}) ---\n"
            f"   {{\n"
            f"      // {symbol_var} may differ from the symbol of the chart this EA is\n"
            f"      // attached to (by design), and the exact name configured in the\n"
            f"      // builder may not exist as-is on this specific broker.\n"
            f"      // ResolveBrokerSymbol() finds the right name and force-selects it\n"
            f"      // into Market Watch.\n"
            f"      string resolved{i} = ResolveBrokerSymbol({symbol_var});\n"
            f'      if(resolved{i} == "")\n'
            f"        {{\n"
            f'         Print("Rule {i + 1}: no symbol matching \\"", {symbol_var}, "\\" was found on this broker. ",\n'
            f'               "Check Market Watch -> Symbols for the exact name this broker uses and edit {symbol_var} near the top of this file.");\n'
            f"         return(INIT_FAILED);\n"
            f"        }}\n"
            f"      {symbol_var} = resolved{i};\n"
            f'      Print("AlgoPuzzle EA - Rule {i + 1} initialized on ", {symbol_var}, " (magic ", {magic_var}, ")");\n'
            f"      //--- Diagnostic info: if orders ever get rejected, these values show\n"
            f"      //--- exactly what this symbol/account requires, instead of guessing.\n"
            f"      double stopsLevelPoints{i}  = MarketInfo({symbol_var}, MODE_STOPLEVEL);\n"
            f"      double freezeLevelPoints{i} = MarketInfo({symbol_var}, MODE_FREEZELEVEL);\n"
            f"      double point{i}             = MarketInfo({symbol_var}, MODE_POINT);\n"
            f"      double tickSize{i}          = MarketInfo({symbol_var}, MODE_TICKSIZE);\n"
            f'      Print("Rule {i + 1} symbol trading diagnostics for ", {symbol_var}, ":");\n'
            f'      Print("  MODE_STOPLEVEL (points): ", stopsLevelPoints{i},\n'
            f'            "  (min. distance = ", stopsLevelPoints{i} * point{i}, " price units)");\n'
            f'      Print("  MODE_FREEZELEVEL (points): ", freezeLevelPoints{i});\n'
            f'      Print("  MODE_POINT: ", point{i}, "   MODE_TICKSIZE: ", tickSize{i},\n'
            f'            "   MODE_DIGITS: ", (int)MarketInfo({symbol_var}, MODE_DIGITS));\n'
            f'      Print("  MODE_TRADEALLOWED: ", MarketInfo({symbol_var}, MODE_TRADEALLOWED));\n'
            f"     }}\n"
        )

        ontick_parts.append(
            f"\n   // ===================== Rule {i + 1}: {rule.asset} =====================\n"
            f"   {{\n"
            f"      // Reset THIS rule's own round counter once ITS OWN tracked tickets\n"
            f"      // have all closed - independent of every other rule's tickets.\n"
            f"      if(CountTrackedForMagic({magic_var}) == 0)\n"
            f"         {rounds_var} = 0;\n\n"
            f"      static datetime lastBarTime_{i} = 0;\n"
            f"      datetime currentBarTime_{i} = iTime({symbol_var}, {timeframe_var}, 0);\n"
            f"      if(currentBarTime_{i} != lastBarTime_{i})\n"
            f"        {{\n"
            f"         lastBarTime_{i} = currentBarTime_{i};\n"
            f"         bool signalTriggered_{i} = {comparison_expr};\n"
            f"         if(signalTriggered_{i})\n"
            f"           {{\n"
            f"            if({rounds_var} < {max_pos_var})\n"
            f"              {{\n"
            f"               {rounds_var}++;\n"
            f"{actions_body}"
            f"              }}\n"
            f"           }}\n"
            f"        }}\n"
            f"     }}\n"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    replacements = {
        "<<GENERATED_AT>>": generated_at,
        "<<GLOBAL_DECLS>>": "".join(global_decls_parts),
        "<<INIT_BODY>>": "".join(init_parts),
        "<<ONTICK_BODY>>": "".join(ontick_parts),
    }
    result = _MQL4_TEMPLATE
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def generate_readme_mql4(ir: StrategyIR) -> str:
    rule_summaries = "\n\n".join(
        f"Rule {r.rule_index + 1} (magic number {r.magic})\n"
        f"- Asset:      {r.asset}\n"
        f"- Timeframe:  {r.timeframe}\n"
        f"- Actions:    {_describe_actions(r)}\n"
        f"- Positions:  {_describe_positions(r)}"
        for r in ir.rules
    )
    assets_line = ", ".join(dict.fromkeys(r.asset for r in ir.rules))  # de-duplicated, order-preserved
    _summary_heading = f"Strategy summary ({len(ir.rules)} rule{'s' if len(ir.rules) != 1 else ''})"
    _multi_rule_heading = f"NOTE ON RUNNING {len(ir.rules)} RULES IN ONE EA"
    multi_rule_note = "" if len(ir.rules) == 1 else f"""
{_multi_rule_heading}
{"-" * len(_multi_rule_heading)}
This file combines {len(ir.rules)} independent strategy rules into a single
Expert Advisor. Each runs on its own schedule, evaluates its own condition,
and tracks its own open positions completely independently (tagged with
its own magic number, listed above) - one rule misfiring or hitting its
position limit never blocks another. Unlike the MT5 version, there is no
netting-account caveat to worry about here: MT4 has no netting account
type at all, so two rules trading the same asset always get fully
independent tickets.
"""

    return f"""ALGOPUZZLE - MT4 Setup Guide
=========================================

Your strategy has been compiled into a MetaTrader 4 Expert Advisor
(strategy.mq4). Follow the steps below to install and run it.

{_summary_heading}
{"-" * len(_summary_heading)}
{rule_summaries}
{multi_rule_note}
NOTE ON STOP LOSS / TAKE PROFIT
--------------------------------
SL/TP are entered as Pips or Percent-of-entry-price (converted to the real
price distance automatically), or as a dynamic expression like "2 x ATR(14)"
so your stop adapts to current volatility. A value of "none" means no Stop
Loss / Take Profit is attached to that action. Like the MT5 version, SL/TP
are enforced by the EA itself (watched every tick) rather than attached
to the order.

STEP 1 - Open MetaTrader 4
---------------------------
Launch the MetaTrader 4 desktop application and log into your
Demo or Live trading account. (Always start with a Demo account -
see IMPORTANT SAFETY NOTES at the bottom.)

STEP 2 - Open your Data Folder
--------------------------------
In the top menu, click:  File -> Open Data Folder
This opens a Windows Explorer / Finder window showing MT4's internal
files. Note: since MT4 build 600 (2014), MetaEditor is shared between
MT4 and MT5 - it automatically compiles with the right compiler based on
the file extension (.mq4 vs .mq5), so no separate install is needed.

STEP 3 - Copy the Expert Advisor
-----------------------------------
Inside the Data Folder, navigate to:  MQL4 -> Experts  (note: MQL4, not
MQL5 - MT4 and MT5 keep entirely separate folders even though they now
share the same MetaEditor).
Paste the "strategy.mq4" file from this zip into that folder.

STEP 4 - Refresh the Navigator panel
----------------------------------------
Back in MetaTrader 4, open the "Navigator" panel (Ctrl+N) if it isn't
already open. MT4 does NOT automatically notice files added to the
Experts folder while it's running - right-click anywhere inside the
"Expert Advisors" section of the Navigator and choose "Refresh".
"strategy" should now appear in the list. (If it still doesn't, double-
check the file really landed in MQL4\\Experts and not a subfolder.)

STEP 5 - Compile the Expert Advisor
--------------------------------------
Right-click "strategy" in the Navigator and select "Modify" - this
opens the MetaEditor with the code loaded. Press F7 (or click the
"Compile" button). Watch the "Errors" tab at the bottom: it must read
"0" errors before you continue - warnings are fine, errors are not.
Close the MetaEditor once compilation succeeds.

STEP 6 - Attach the EA to a Chart
------------------------------------
Open a chart for ANY symbol/timeframe (it doesn't have to match any of the
rules below - the EA always trades exactly the asset(s)/timeframe(s) shown
in the strategy summary above, regardless of which chart it's attached to:
{assets_line}). Drag "strategy" from the Navigator panel onto the chart. In
the dialog that appears, open the "Common" tab and make sure "Allow live
trading" is checked, then click OK.

STEP 7 - Enable Auto Trading
--------------------------------
Click the "Auto Trading" button in the main MT4 toolbar so it turns
green/highlighted. This is a global switch - without it, no Expert
Advisor on any chart will trade, no matter how it's configured.

STEP 8 - Confirm it's actually running
-------------------------------------------
Open the "Terminal" window at the bottom of the platform (Ctrl+T) and
click the "Experts" tab. Within a few seconds you should see one
initialization line PER RULE, e.g.:
    AlgoPuzzle EA - Rule 1 initialized on {ir.rules[0].asset} (magic {ir.rules[0].magic})
followed by a block of diagnostic lines (symbol digits, stop level, etc)
for that rule - repeated once per rule above. If you see all of them, the
EA is live and evaluating every rule on its own timeframe. If you see a
red error line instead, read it - it usually explains exactly what's
wrong (e.g. a symbol name mismatch, or Auto Trading not enabled).

A NOTE ON WHAT YOU'LL SEE IN THE "TRADE" TAB
-------------------------------------------------
When a position opens, MT4's own Trade tab will show it with NO Stop
Loss or Take Profit value filled in - even though you configured one.
This is expected, not a bug: this EA manages SL/TP itself internally
(see NOTE ON STOP LOSS / TAKE PROFIT above) rather than attaching them
to the order, so nothing appears in that column. The "Experts" tab will
print "Virtual Stop Loss triggered" or "Virtual Take Profit triggered"
when the EA closes a position for you.

IMPORTANT SAFETY NOTES
-----------------------
- Always test this Expert Advisor on a Demo account first.
- MT4 has no netting account type - every position this EA opens gets
  its own independent ticket, unlike MT5 where a netting-mode account
  can merge same-symbol orders into one position.
- This build does not include backtesting validation - review the
  generated strategy.mq4 code and run it through MT4's Strategy
  Tester before using real funds.
- Past performance and rule-based logic do not guarantee future
  results. Trade responsibly.

Generated by AlgoPuzzle (MVP).
"""


# --------------------------------------------------------------------------
# API endpoints
# --------------------------------------------------------------------------

def _asset_slug_for_filename(config: WorkspaceConfig) -> str:
    """Mirrors the frontend's assetSlugForFilename() in index_1.html - kept
    in sync deliberately so a filename built here (e.g. a direct API call
    bypassing the browser) looks the same as one built client-side. Config
    no longer has a single top-level asset/timeframe since the multi-rule
    refactor (see RuleConfig) - this derives a slug from however many
    distinct assets the rules actually use."""
    distinct_assets = list(dict.fromkeys(rule.asset.lower() for rule in config.rules))
    if len(distinct_assets) <= 3:
        return "_".join(distinct_assets)
    return f"multi_{len(config.rules)}rules"


async def _require_export_entitlement(
    request: Request, device_id: str, platform: str, strategy_meta: Optional[dict] = None
) -> None:
    """Consumes one unit of export entitlement (free allowance -> paid
    credit -> active day-pass, checked in that order - see
    consume_export_entitlement() in supabase/schema.sql) or raises 402
    Payment Required.

    strategy_meta (see _summarize_strategy() above) is only ever attached
    to the export_log row once the export is actually granted - a 402
    here means nothing was exported, so nothing about the attempted
    strategy is recorded either.

    The client IP is passed through (hashed inside db.py, same as
    get_device_id already does) so the free bucket is also capped per-IP,
    not just per device_id cookie - see IP_FREE_EXPORT_LIMIT in
    settings.py for why a cleared cookie alone isn't enough to loop past
    this anymore.

    If the request carries a valid login token AND that user has active
    pro (a purchased 30-day pass, or a manual grant - see
    has_active_pass() in schema.sql), it wins immediately - checked
    before anything else, since it's meant to work from any of the
    trader's devices, not just this one (see schema.sql's header
    comment).

    Otherwise, if logged in, that account's exports_available bucket is
    checked next (2026-08-29 addition) - a separate, additive pool of
    exports for this account specifically, manually grantable or funded
    by a single-export purchase made while logged in. Only after both of
    those come up empty (or the request isn't logged in at all) does this
    fall through to the anonymous per-device free/paid flow, completely
    unchanged for anyone not logged in.

    If DATABASE_URL isn't configured at all, billing is treated as not
    wired up yet (e.g. local dev without a Supabase project) and every
    export is allowed through - loudly logged so this can never be
    silently true in production by accident."""
    if not settings.DATABASE_URL:
        print(f"[billing] WARNING: DATABASE_URL not set - allowing '{platform}' export with no entitlement check.")
        return

    user_id = get_current_user(request)
    if user_id:
        if await db.has_active_pass(user_id):
            await db.log_user_pass_export(user_id, device_id, platform, strategy_meta)
            return
        if await db.consume_account_export_credit(user_id, device_id, platform, strategy_meta):
            return

    granted, _consumed_from = await db.consume_export(device_id, platform, get_client_ip(request), strategy_meta)
    if not granted:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                # Short on purpose - the paywall modal (see showPaywall()
                # in index_1.html) shows this as a small label above its
                # own heading, with pricing already spelled out on each
                # plan's card, so repeating "$2.00 / $9.99" here as well
                # would just be redundant on top of what the trader is
                # about to read a few lines down.
                "message": f"You've used your {settings.FREE_EXPORT_LIMIT} free exports",
            },
        )


@app.post("/api/generate")
async def generate_expert_advisor(config: WorkspaceConfig, request: Request, device_id: str = Depends(get_device_id)):
    """Receive a serialized Blockly workspace, generate the .mq5 Expert
    Advisor, package it with a README, and stream the .zip back."""

    if config is None:
        raise HTTPException(status_code=400, detail="Empty workspace: build a strategy before exporting.")

    try:
        ir = parse_strategy(config)
        mql5_code = render_mql5(ir)
        readme_text = generate_readme_mql5(ir)
    except StrategyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Entitlement is only consumed once we know the strategy is actually
    # valid - a broken/invalid config never costs the user anything.
    await _require_export_entitlement(request, device_id, "mt5", _summarize_strategy(ir))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("strategy.mq5", mql5_code)
        zf.writestr("README.txt", readme_text)
    buffer.seek(0)

    filename = f"algopuzzle_{_asset_slug_for_filename(config)}.zip"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/generate/ctrader")
async def generate_cbot(config: WorkspaceConfig, request: Request, device_id: str = Depends(get_device_id)):
    """Receive a serialized Blockly workspace, generate a cTrader cBot
    (.cs), package it with a README, and stream the .zip back. Same
    contract shape as /api/generate (MT5) - same request body, same
    zip-download response pattern."""

    if config is None:
        raise HTTPException(status_code=400, detail="Empty workspace: build a strategy before exporting.")

    try:
        ir = parse_strategy(config)
        csharp_code = render_csharp(ir)
        readme_text = generate_readme_csharp(ir)
    except StrategyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _require_export_entitlement(request, device_id, "ctrader", _summarize_strategy(ir))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("AlgoPuzzleBot.cs", csharp_code)
        zf.writestr("README.txt", readme_text)
    buffer.seek(0)

    filename = f"algopuzzle_ctrader_{_asset_slug_for_filename(config)}.zip"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/generate/mt4")
async def generate_expert_advisor_mt4(config: WorkspaceConfig, request: Request, device_id: str = Depends(get_device_id)):
    """Receive a serialized Blockly workspace, generate an MT4 Expert
    Advisor (.mq4), package it with a README, and stream the .zip back.
    Same contract shape as /api/generate (MT5) and /api/generate/ctrader -
    same request body, same zip-download response pattern."""

    if config is None:
        raise HTTPException(status_code=400, detail="Empty workspace: build a strategy before exporting.")

    try:
        ir = parse_strategy(config)
        mql4_code = render_mql4(ir)
        readme_text = generate_readme_mql4(ir)
    except StrategyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _require_export_entitlement(request, device_id, "mt4", _summarize_strategy(ir))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("strategy.mq4", mql4_code)
        zf.writestr("README.txt", readme_text)
    buffer.seek(0)

    filename = f"algopuzzle_mt4_{_asset_slug_for_filename(config)}.zip"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# Site-behavior analytics (2026-08-15 addition) - see analytics_events in
# schema.sql. A fixed, reviewed allowlist of event names - the endpoint
# below silently drops anything not in this set (never a 400) rather than
# letting a client dictate what ends up in the table, so this can't
# accumulate arbitrary junk event names from a buggy or malicious client.
# Add a new event here (and instrument it in index_1.html) rather than
# accepting free-form event_type from the frontend.
# --------------------------------------------------------------------------
ALLOWED_ANALYTICS_EVENTS = {
    "builder_opened",
    "new_strategy_started",
    "strategy_saved",
    "paywall_shown",
    "checkout_started",
    "auth_modal_shown",
    "login_completed",
    "signup_completed",
}

# Generous but bounded - this is lightweight product instrumentation
# (e.g. {"kind": "pass"} or {"platform": "mt5"}), never meant to carry a
# whole strategy or anything large; anything past this is almost
# certainly a bug or abuse, not a legitimate event, so it's replaced
# rather than stored as-is.
_ANALYTICS_METADATA_MAX_CHARS = 2000


class AnalyticsEventIn(BaseModel):
    event_type: str
    metadata: Optional[dict] = None
    path: Optional[str] = None


@app.post("/api/analytics/event", status_code=204)
async def track_event(body: AnalyticsEventIn, request: Request, device_id: str = Depends(get_device_id)):
    """Fire-and-forget site-behavior event logging - see trackEvent() in
    index_1.html for the frontend side. Deliberately tolerant of almost
    anything going wrong here (unknown event_type, oversized metadata, no
    DATABASE_URL configured, even a DB error) - this is instrumentation,
    never a feature anything else depends on, so a bug in it should never
    surface as a visible error to a real trader trying to use the app."""
    if not settings.DATABASE_URL or body.event_type not in ALLOWED_ANALYTICS_EVENTS:
        return

    user_id = get_current_user(request)
    metadata = body.metadata
    if metadata is not None and len(json.dumps(metadata)) > _ANALYTICS_METADATA_MAX_CHARS:
        metadata = {"_truncated": True}
    path = (body.path or "").strip()[:200] or None

    try:
        await db.log_analytics_event(device_id, user_id, body.event_type, metadata, path)
    except Exception as e:
        print(f"[analytics] failed to log event '{body.event_type}': {e}")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Billing (Stripe + anonymous device entitlement)
# --------------------------------------------------------------------------
# No accounts, no login - see device_identity.py. A device pays either
# per export (one-time, ~5 zl) or for a 30-day unlimited pass (one-time,
# ~30 zl); /api/generate* above is what actually spends that entitlement.

@app.get("/api/billing/status")
async def billing_status(request: Request, device_id: str = Depends(get_device_id)):
    """Entitlement snapshot for the current browser - the frontend uses
    this to show 'N free exports left' / paywall state without needing
    to attempt (and fail) an export first. If the request is also logged
    in, pass_active reflects that user's account-wide pass (which can be
    true even on a brand new device that's never bought anything, either
    from a purchased pass or a manual grant - see has_active_pass() in
    schema.sql), and account_exports_available surfaces that same
    account's separate, additive export bucket (2026-08-29 addition) -
    an existing frontend that doesn't read this new field yet keeps
    working exactly as before, it just won't mention this bucket."""
    user_id = get_current_user(request)
    if not settings.DATABASE_URL:
        return {
            "free_exports_used": 0,
            "free_exports_remaining": settings.FREE_EXPORT_LIMIT,
            "paid_export_credits": 0,
            "pass_active": False,
            "pass_expires_at": None,
            "billing_configured": False,
            "logged_in": bool(user_id),
        }
    status = await db.get_entitlement_status(device_id)
    status["billing_configured"] = True
    status["logged_in"] = bool(user_id)
    if user_id:
        account_status = await db.get_account_status(user_id)
        if account_status["pass_active"]:
            status["pass_active"] = True
        status["account_exports_available"] = account_status["exports_available"]
    return status


@app.post("/api/billing/checkout/export")
async def billing_checkout_export(request: Request, device_id: str = Depends(get_device_id)):
    """Creates a Stripe Checkout Session for a single export credit and
    returns its hosted URL for the frontend to redirect to. Anonymous -
    no login required, same as the free tier - but if the caller happens
    to be logged in, that's passed through so the credit lands on their
    account instead of just this device (2026-08-29 addition - see
    billing.create_checkout_session / db.grant_export_credits)."""
    user_id = get_current_user(request)
    try:
        url = billing.create_checkout_session(device_id, "export_credit", user_id=user_id)
    except billing.BillingNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"url": url}


@app.post("/api/billing/checkout/pass")
async def billing_checkout_pass(request: Request, device_id: str = Depends(get_device_id)):
    """Creates a Stripe Checkout Session for the 30-day unlimited pass
    and returns its hosted URL for the frontend to redirect to. Requires
    login (401 if not) - see billing.LoginRequired."""
    user_id = get_current_user(request)
    try:
        url = billing.create_checkout_session(device_id, "day_pass", user_id=user_id)
    except billing.LoginRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except billing.BillingNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"url": url}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Stripe calls this directly (not the browser) whenever a payment
    event happens - configure this URL in the Stripe dashboard under
    Developers -> Webhooks, subscribed to at least `checkout.session.completed`.

    Verifies the signature against STRIPE_WEBHOOK_SECRET before trusting
    anything in the body - without that check, anyone could POST a fake
    "payment succeeded" event here and grant themselves free credits."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = billing.construct_webhook_event(payload, sig_header)
    except billing.BillingNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        # Covers stripe.error.SignatureVerificationError (bad/missing
        # signature) and malformed-payload errors alike - either way this
        # request is not a trustworthy Stripe event.
        raise HTTPException(status_code=400, detail="Invalid webhook signature or payload.")

    kind = await billing.apply_completed_checkout(event)
    return JSONResponse({"status": "ok", "applied": kind})
