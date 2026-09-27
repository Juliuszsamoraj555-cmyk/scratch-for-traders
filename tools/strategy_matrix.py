"""
Combinatorial strategy generator for compile verification.

Full cartesian coverage (every operand kind x every parameter variant x
every role x every timeframe x every operator x ...) would be tens of
thousands of strategies - not testable in finite time, and mostly
redundant (once "BANDS UPPER works as a comparison operand on M15" is
confirmed, testing it again on M30 barely adds information).

Instead this uses "each-choice" combinatorial coverage: every operand
kind, every meaningful parameter variant of each kind (MA type, BANDS
line, MACD line, STOCH line, CANDLE type, VOLUME bar, RISK_VALUE unit),
every timeframe, every comparison/logical operator, both trade
directions, and both "Positions" modes each appear in at least one
generated strategy - cycling through the other axes round-robin so
nothing ends up tested in total isolation from everything else either.
Plus a handful of structural cases (nested logical trees, MULTIPLY
nesting, multi-action, no-SL/no-TP) that aren't covered by round-robin
alone.

Produces WorkspaceConfig-shaped dicts (the Blockly export JSON format,
`{"rules": [...]}` - see main.py) - consumed by tools/verify_mt5_compile.py
/ tools/verify_mt4_compile.py / tools/verify_ctrader_build.py via --matrix,
or usable standalone via generate_matrix(). Almost every entry is a single
rule (wrapped in a one-element "rules" list at the very end of
generate_matrix(), so the individual strategies below didn't all need
rewriting when "rules" became a list) - a couple of entries near the end
are genuinely multi-rule, covering that combined-export path too.
"""

from typing import Dict, List, Tuple

TIMEFRAMES = ["PERIOD_M1", "PERIOD_M5", "PERIOD_M15", "PERIOD_M30", "PERIOD_H1", "PERIOD_H4", "PERIOD_D1"]
COMPARISON_OPERATORS = [">", "<", "=="]
LOGICAL_OPERATORS = ["AND", "OR"]
DIRECTIONS = ["BUY", "SELL"]

# Every operand kind, with every parameter variant worth covering
# separately (the axis that actually changes generated code, not every
# numeric period - period=14 vs period=20 exercises the same code path).
OPERAND_VARIANTS: List[Tuple[str, dict]] = [
    ("ma_sma", {"kind": "MA", "period": 20, "ma_type": "MODE_SMA"}),
    ("ma_ema", {"kind": "MA", "period": 50, "ma_type": "MODE_EMA"}),
    ("rsi", {"kind": "RSI", "period": 14}),
    ("macd_main", {"kind": "MACD", "line": "MAIN"}),
    ("macd_hist", {"kind": "MACD", "line": "HIST"}),
    ("bands_upper", {"kind": "BANDS", "period": 20, "deviation": 2, "band": "UPPER"}),
    ("bands_middle", {"kind": "BANDS", "period": 20, "deviation": 2, "band": "MIDDLE"}),
    ("bands_lower", {"kind": "BANDS", "period": 20, "deviation": 2.5, "band": "LOWER"}),
    ("atr", {"kind": "ATR", "period": 14}),
    ("stoch_k", {"kind": "STOCH", "k_period": 5, "d_period": 3, "slowing": 3, "stoch_line": "K"}),
    ("stoch_d", {"kind": "STOCH", "k_period": 8, "d_period": 4, "slowing": 2, "stoch_line": "D"}),
    ("volume_current", {"kind": "VOLUME", "volume_bar": "CURRENT"}),
    ("volume_previous", {"kind": "VOLUME", "volume_bar": "PREVIOUS"}),
    ("candle_current", {"kind": "CANDLE", "candle_type": "CURRENT"}),
    ("candle_prev_open", {"kind": "CANDLE", "candle_type": "PREV_OPEN"}),
    ("candle_prev_close", {"kind": "CANDLE", "candle_type": "PREV_CLOSE"}),
    ("candle_prev_high", {"kind": "CANDLE", "candle_type": "PREV_HIGH"}),
    ("candle_prev_low", {"kind": "CANDLE", "candle_type": "PREV_LOW"}),
    ("number", {"kind": "NUMBER", "value": 1.2345}),
]

# RISK_VALUE only makes sense in an SL/TP slot (its PERCENT unit resolves
# against the entry price placeholder, which only action blocks provide).
RISK_VARIANTS: List[Tuple[str, dict]] = [
    ("risk_pips", {"kind": "RISK_VALUE", "value": 30, "unit": "PIPS"}),
    ("risk_percent", {"kind": "RISK_VALUE", "value": 1.5, "unit": "PERCENT"}),
    ("risk_price", {"kind": "RISK_VALUE", "value": 0.0050, "unit": "PRICE"}),
]

# A few representative MULTIPLY nestings - both legs matter (indicator x
# indicator, number x indicator, and one nested two levels deep).
MULTIPLY_VARIANTS: List[Tuple[str, dict]] = [
    ("multiply_num_atr", {"kind": "MULTIPLY", "left": {"kind": "NUMBER", "value": 2}, "right": {"kind": "ATR", "period": 14}}),
    ("multiply_num_rsi", {"kind": "MULTIPLY", "left": {"kind": "NUMBER", "value": 0.5}, "right": {"kind": "RSI", "period": 21}}),
    ("multiply_nested", {
        "kind": "MULTIPLY",
        "left": {"kind": "NUMBER", "value": 1.5},
        "right": {"kind": "MULTIPLY", "left": {"kind": "NUMBER", "value": 2}, "right": {"kind": "ATR", "period": 10}},
    }),
]

ALL_SL_TP_VARIANTS = RISK_VARIANTS + MULTIPLY_VARIANTS + [("no_stop", None)]


def _cycle(seq, i):
    return seq[i % len(seq)]


def _base_action(direction: str, sl, tp, lot: float = 0.1) -> dict:
    return {"direction": direction, "lot": lot, "sl": sl, "tp": tp}


# --- Wave 1 blocks (new indicators, parameters, shifts, timeframe
# overrides, math, and the new condition types). Same each-choice idea:
# every new kind and every line/parameter variant that changes generated
# code appears at least once.
WAVE1_OPERAND_VARIANTS: List[Tuple[str, dict]] = [
    ("ma_smma", {"kind": "MA", "period": 13, "ma_type": "MODE_SMMA", "applied_price": "PRICE_MEDIAN"}),
    ("ma_lwma_typical", {"kind": "MA", "period": 21, "ma_type": "MODE_LWMA", "applied_price": "PRICE_TYPICAL"}),
    ("ma_ema_open_shift2", {"kind": "MA", "period": 9, "ma_type": "MODE_EMA", "applied_price": "PRICE_OPEN", "shift": 2}),
    ("ma_sma_weighted_h4", {"kind": "MA", "period": 50, "ma_type": "MODE_SMA", "applied_price": "PRICE_WEIGHTED", "timeframe": "PERIOD_H4"}),
    ("rsi_high_d1", {"kind": "RSI", "period": 14, "applied_price": "PRICE_HIGH", "timeframe": "PERIOD_D1"}),
    ("bands_low_price", {"kind": "BANDS", "period": 20, "deviation": 2, "band": "UPPER", "applied_price": "PRICE_LOW", "shift": 1}),
    ("macd_signal_custom", {"kind": "MACD", "line": "SIGNAL", "macd_fast": 8, "macd_slow": 21, "macd_signal": 5}),
    ("macd_hist_shift", {"kind": "MACD", "line": "HIST", "shift": 1}),
    ("macd_main_custom_h1", {"kind": "MACD", "line": "MAIN", "macd_fast": 5, "macd_slow": 35, "macd_signal": 5, "timeframe": "PERIOD_H1"}),
    ("atr_shift_h4", {"kind": "ATR", "period": 14, "shift": 1, "timeframe": "PERIOD_H4"}),
    ("stoch_shift", {"kind": "STOCH", "k_period": 14, "d_period": 3, "slowing": 3, "stoch_line": "D", "shift": 3}),
    ("cci", {"kind": "CCI", "period": 20}),
    ("cci_close_shift", {"kind": "CCI", "period": 14, "applied_price": "PRICE_CLOSE", "shift": 1}),
    ("wpr", {"kind": "WPR", "period": 14}),
    ("sar", {"kind": "SAR", "sar_step": 0.02, "sar_max": 0.2}),
    ("sar_custom_m30", {"kind": "SAR", "sar_step": 0.01, "sar_max": 0.1, "timeframe": "PERIOD_M30"}),
    ("momentum", {"kind": "MOMENTUM", "period": 14}),
    ("momentum_median", {"kind": "MOMENTUM", "period": 10, "applied_price": "PRICE_MEDIAN", "shift": 1}),
    ("stddev", {"kind": "STDDEV", "period": 20}),
    ("mfi", {"kind": "MFI", "period": 14}),
    ("envelopes_upper", {"kind": "ENVELOPES", "period": 14, "deviation": 0.1, "band": "UPPER", "ma_type": "MODE_SMA"}),
    ("envelopes_lower_ema", {"kind": "ENVELOPES", "period": 20, "deviation": 0.25, "band": "LOWER", "ma_type": "MODE_EMA"}),
    ("demarker", {"kind": "DEMARKER", "period": 14}),
    ("bulls", {"kind": "BULLS", "period": 13}),
    ("bears", {"kind": "BEARS", "period": 13, "shift": 1}),
    ("ao", {"kind": "AO"}),
    ("ac", {"kind": "AC", "shift": 1}),
    ("adx_main", {"kind": "ADX", "period": 14, "line": "ADX"}),
    ("adx_plus", {"kind": "ADX", "period": 14, "line": "PLUS_DI"}),
    ("adx_minus_h1", {"kind": "ADX", "period": 10, "line": "MINUS_DI", "timeframe": "PERIOD_H1"}),
    ("ichimoku_tenkan", {"kind": "ICHIMOKU", "line": "TENKAN"}),
    ("ichimoku_kijun", {"kind": "ICHIMOKU", "line": "KIJUN", "tenkan": 7, "kijun": 22, "senkou_b": 44}),
    ("ichimoku_senkou_a", {"kind": "ICHIMOKU", "line": "SENKOU_A"}),
    ("ichimoku_senkou_b_h4", {"kind": "ICHIMOKU", "line": "SENKOU_B", "timeframe": "PERIOD_H4"}),
    ("alligator_jaw", {"kind": "ALLIGATOR", "line": "JAW"}),
    ("alligator_teeth", {"kind": "ALLIGATOR", "line": "TEETH"}),
    ("alligator_lips", {"kind": "ALLIGATOR", "line": "LIPS", "shift": 1}),
    ("highest", {"kind": "HIGHEST", "period": 20}),
    ("lowest_d1", {"kind": "LOWEST", "period": 5, "timeframe": "PERIOD_D1"}),
    ("candle_current_shift", {"kind": "CANDLE", "candle_type": "CURRENT", "shift": 2}),
    ("candle_prev_high_shift", {"kind": "CANDLE", "candle_type": "PREV_HIGH", "shift": 4}),
    ("candle_prev_close_h1", {"kind": "CANDLE", "candle_type": "PREV_CLOSE", "timeframe": "PERIOD_H1"}),
    ("volume_prev_shift", {"kind": "VOLUME", "volume_bar": "PREVIOUS", "shift": 1}),
    ("pips", {"kind": "PIPS", "value": 15}),
    ("arith_minus", {"kind": "ARITH", "op": "-", "left": {"kind": "CANDLE", "candle_type": "CURRENT"}, "right": {"kind": "MA", "period": 20, "ma_type": "MODE_SMA"}}),
    ("arith_plus_shifted", {"kind": "ARITH", "op": "+", "shift": 1, "left": {"kind": "MA", "period": 20, "ma_type": "MODE_SMA"}, "right": {"kind": "PIPS", "value": 5}}),
    ("arith_divide", {"kind": "ARITH", "op": "/", "left": {"kind": "ATR", "period": 14}, "right": {"kind": "ATR", "period": 50}}),
    ("arith_times_tf", {"kind": "ARITH", "op": "*", "timeframe": "PERIOD_H4", "left": {"kind": "NUMBER", "value": 1.5}, "right": {"kind": "STDDEV", "period": 20}}),
    ("abs_diff", {"kind": "ABS", "left": {"kind": "ARITH", "op": "-", "left": {"kind": "CANDLE", "candle_type": "PREV_CLOSE"}, "right": {"kind": "CANDLE", "candle_type": "PREV_OPEN"}}}),
]

WAVE1_SL_TP_VARIANTS: List[Tuple[str, dict]] = [
    ("sl_pips_block", {"kind": "PIPS", "value": 25}),
    ("sl_atr_h4_x2", {"kind": "ARITH", "op": "*", "left": {"kind": "NUMBER", "value": 2}, "right": {"kind": "ATR", "period": 14, "timeframe": "PERIOD_H4"}}),
    ("sl_stddev_div", {"kind": "ARITH", "op": "/", "left": {"kind": "STDDEV", "period": 20}, "right": {"kind": "NUMBER", "value": 2}}),
]

PATTERNS = [
    "BULLISH", "BEARISH", "BULLISH_ENGULFING", "BEARISH_ENGULFING", "DOJI",
    "HAMMER", "SHOOTING_STAR", "INSIDE_BAR", "OUTSIDE_BAR",
]


def _wave1_strategies() -> List[Tuple[str, dict]]:
    out: List[Tuple[str, dict]] = []
    ops = [">", "<", ">=", "<=", "=="]
    number = {"kind": "NUMBER", "value": 50}

    # Every new operand variant on the left of a comparison (new operators
    # cycled in), and every SL/TP variant.
    for i, (name, definition) in enumerate(WAVE1_OPERAND_VARIANTS):
        sl_name, sl_def = _cycle(WAVE1_SL_TP_VARIANTS + RISK_VARIANTS, i)
        out.append((f"w1_left_{name}", {
            "asset": "EURUSD",
            "timeframe": _cycle(TIMEFRAMES, i),
            "condition": {"type": "comparison", "left": definition, "operator": _cycle(ops, i), "right": number},
            "actions": [_base_action(_cycle(DIRECTIONS, i), sl_def, _cycle(WAVE1_SL_TP_VARIANTS, i + 1)[1])],
            "max_positions": 1,
        }))

    ma_fast = {"kind": "MA", "period": 9, "ma_type": "MODE_EMA"}
    ma_slow = {"kind": "MA", "period": 21, "ma_type": "MODE_EMA"}
    for direction in ("ABOVE", "BELOW"):
        for closed in (True, False):
            out.append((f"w1_cross_{direction.lower()}_{'closed' if closed else 'live'}", {
                "asset": "GBPUSD", "timeframe": "PERIOD_H1",
                "condition": {"type": "cross", "left": ma_fast, "direction": direction, "right": ma_slow, "closed_bar": closed},
                "actions": [_base_action("BUY" if direction == "ABOVE" else "SELL", RISK_VARIANTS[0][1], None)],
                "max_positions": 1,
            }))
    # Cross against a constant level and across timeframes.
    out.append(("w1_cross_rsi_level_mtf", {
        "asset": "EURUSD", "timeframe": "PERIOD_M15",
        "condition": {"type": "cross", "left": {"kind": "RSI", "period": 14, "timeframe": "PERIOD_H4"},
                      "direction": "ABOVE", "right": {"kind": "NUMBER", "value": 30}},
        "actions": [_base_action("BUY", WAVE1_SL_TP_VARIANTS[1][1], None)],
        "max_positions": 1,
    }))
    out.append(("w1_cross_price_ichimoku", {
        "asset": "USDJPY", "timeframe": "PERIOD_H1",
        "condition": {"type": "cross", "left": {"kind": "CANDLE", "candle_type": "CURRENT"},
                      "direction": "BELOW", "right": {"kind": "ICHIMOKU", "line": "SENKOU_B"}},
        "actions": [_base_action("SELL", None, RISK_VARIANTS[0][1])],
        "max_positions": 1,
    }))
    out.append(("w1_between", {
        "asset": "EURUSD", "timeframe": "PERIOD_M30",
        "condition": {"type": "between", "value": {"kind": "RSI", "period": 14},
                      "low": {"kind": "NUMBER", "value": 40}, "high": {"kind": "NUMBER", "value": 60}},
        "actions": [_base_action("BUY", RISK_VARIANTS[0][1], RISK_VARIANTS[0][1])],
        "max_positions": 1,
    }))
    out.append(("w1_between_bands", {
        "asset": "EURUSD", "timeframe": "PERIOD_H1",
        "condition": {"type": "between", "value": {"kind": "CANDLE", "candle_type": "PREV_CLOSE"},
                      "low": {"kind": "BANDS", "period": 20, "deviation": 2, "band": "LOWER"},
                      "high": {"kind": "BANDS", "period": 20, "deviation": 2, "band": "UPPER"}},
        "actions": [_base_action("SELL", RISK_VARIANTS[0][1], None)],
        "max_positions": 1,
    }))
    for i, (direction, closed, bars) in enumerate((("RISING", True, 3), ("FALLING", False, 1), ("RISING", False, 5))):
        out.append((f"w1_trend_{direction.lower()}_{bars}", {
            "asset": "EURUSD", "timeframe": _cycle(TIMEFRAMES, i + 2),
            "condition": {"type": "trend", "value": _cycle([ma_slow, {"kind": "ADX", "period": 14, "line": "ADX"}], i),
                          "direction": direction, "bars": bars, "closed_bar": closed},
            "actions": [_base_action(_cycle(DIRECTIONS, i), RISK_VARIANTS[0][1], None)],
            "max_positions": 1,
        }))
    for i, pattern in enumerate(PATTERNS):
        condition = {"type": "pattern", "pattern": pattern, "shift": 1 + (i % 2)}
        if i % 3 == 0:
            condition["timeframe"] = "PERIOD_H4"
        out.append((f"w1_pattern_{pattern.lower()}", {
            "asset": "EURUSD", "timeframe": _cycle(TIMEFRAMES, i),
            "condition": condition,
            "actions": [_base_action(_cycle(DIRECTIONS, i), RISK_VARIANTS[0][1], RISK_VARIANTS[1][1])],
            "max_positions": 1,
        }))
    out.append(("w1_not_and_spread", {
        "asset": "EURUSD", "timeframe": "PERIOD_M5",
        "condition": {
            "type": "logical", "operator": "AND",
            "left": {"type": "not", "inner": {"type": "pattern", "pattern": "DOJI", "shift": 1}},
            "right": {"type": "comparison", "left": {"kind": "SPREAD"}, "operator": "<", "right": {"kind": "NUMBER", "value": 2}},
        },
        "actions": [_base_action("BUY", RISK_VARIANTS[0][1], None)],
        "max_positions": 1,
    }))
    # Several timeframe overrides in one rule, plus two rules on different
    # assets both using overrides (separate extra Bars per rule in cTrader).
    out.append(("w1_multi_rule_mtf", {"rules": [
        {
            "asset": "EURUSD", "timeframe": "PERIOD_M15",
            "condition": {"type": "logical", "operator": "AND",
                          "left": {"type": "comparison", "left": {"kind": "RSI", "period": 14, "timeframe": "PERIOD_H4"}, "operator": ">", "right": {"kind": "NUMBER", "value": 50}},
                          "right": {"type": "cross", "left": ma_fast, "direction": "ABOVE", "right": {"kind": "MA", "period": 50, "ma_type": "MODE_SMA", "timeframe": "PERIOD_D1"}}},
            "actions": [_base_action("BUY", WAVE1_SL_TP_VARIANTS[1][1], None)],
            "max_positions": 2,
        },
        {
            "asset": "GBPUSD", "timeframe": "PERIOD_H1",
            "condition": {"type": "logical", "operator": "OR",
                          "left": {"type": "pattern", "pattern": "BEARISH_ENGULFING", "shift": 1, "timeframe": "PERIOD_H4"},
                          "right": {"type": "comparison", "left": {"kind": "CANDLE", "candle_type": "CURRENT"}, "operator": "<", "right": {"kind": "LOWEST", "period": 20, "timeframe": "PERIOD_H4"}}},
            "actions": [_base_action("SELL", RISK_VARIANTS[0][1], None)],
            "max_positions": 1,
        },
    ]}))
    return out


def generate_matrix() -> List[Tuple[str, dict]]:
    """Returns [(name, workspace_config_dict), ...]."""
    strategies: List[Tuple[str, dict]] = []

    # --- 1. Every operand variant used as the LEFT side of a comparison,
    # cycling RIGHT operand / operator / timeframe / direction / SL-TP
    # variant so those axes get broad coverage too, not just the featured
    # operand kind.
    for i, (op_name, op_def) in enumerate(OPERAND_VARIANTS):
        right_name, right_def = _cycle(OPERAND_VARIANTS, i + 7)  # offset so RIGHT != LEFT most of the time
        operator = _cycle(COMPARISON_OPERATORS, i)
        timeframe = _cycle(TIMEFRAMES, i)
        direction = _cycle(DIRECTIONS, i)
        sl_name, sl_def = _cycle(ALL_SL_TP_VARIANTS, i)
        tp_name, tp_def = _cycle(ALL_SL_TP_VARIANTS, i + 1)

        strategies.append((
            f"cond_left_{op_name}",
            {
                "asset": "EURUSD",
                "timeframe": timeframe,
                "condition": {"type": "comparison", "left": op_def, "operator": operator, "right": right_def},
                "actions": [_base_action(direction, sl_def, tp_def)],
                "max_positions": 1,
            },
        ))

    # --- 2. Every SL/TP variant (RISK_VALUE units + MULTIPLY nestings)
    # explicitly exercised as BOTH the SL and the TP of an action, so each
    # gets tested in both positions (sl_expr/tp_expr are built by
    # different code paths depending on which action-block sign is used).
    for i, (name, definition) in enumerate(RISK_VARIANTS + MULTIPLY_VARIANTS):
        strategies.append((
            f"sltp_{name}_as_sl",
            {
                "asset": "GBPUSD",
                "timeframe": _cycle(TIMEFRAMES, i + 2),
                "condition": {
                    "type": "comparison",
                    "left": {"kind": "CANDLE", "candle_type": "CURRENT"},
                    "operator": _cycle(COMPARISON_OPERATORS, i),
                    "right": {"kind": "CANDLE", "candle_type": "PREV_CLOSE"},
                },
                "actions": [_base_action(_cycle(DIRECTIONS, i), definition, None)],
                "max_positions": 1,
            },
        ))
        strategies.append((
            f"sltp_{name}_as_tp",
            {
                "asset": "USDJPY",
                "timeframe": _cycle(TIMEFRAMES, i + 4),
                "condition": {
                    "type": "comparison",
                    "left": {"kind": "CANDLE", "candle_type": "CURRENT"},
                    "operator": _cycle(COMPARISON_OPERATORS, i + 1),
                    "right": {"kind": "CANDLE", "candle_type": "PREV_CLOSE"},
                },
                "actions": [_base_action(_cycle(DIRECTIONS, i + 1), None, definition)],
                "max_positions": 1,
            },
        ))

    # --- 3. No SL and no TP at all (both None) - the "no stops attached"
    # path, distinct from "no_stop" appearing only on one side above.
    strategies.append((
        "action_no_sl_no_tp",
        {
            "asset": "EURUSD",
            "timeframe": "PERIOD_M5",
            "condition": {
                "type": "comparison",
                "left": {"kind": "RSI", "period": 14}, "operator": "<",
                "right": {"kind": "NUMBER", "value": 30},
            },
            "actions": [_base_action("BUY", None, None)],
            "max_positions": 1,
        },
    ))

    # --- 4. Nested logical conditions: 2-level AND/OR and a 3-level tree,
    # covering both operators at each nesting depth.
    def leaf(i):
        name, definition = _cycle(OPERAND_VARIANTS, i)
        return {"type": "comparison", "left": definition, "operator": _cycle(COMPARISON_OPERATORS, i), "right": {"kind": "NUMBER", "value": 1.0}}

    for i, op in enumerate(LOGICAL_OPERATORS):
        strategies.append((
            f"logical_2level_{op.lower()}",
            {
                "asset": "EURUSD",
                "timeframe": _cycle(TIMEFRAMES, i),
                "condition": {"type": "logical", "operator": op, "left": leaf(i), "right": leaf(i + 3)},
                "actions": [_base_action(_cycle(DIRECTIONS, i), _cycle(RISK_VARIANTS, i)[1], _cycle(RISK_VARIANTS, i + 1)[1])],
                "max_positions": 1,
            },
        ))

    strategies.append((
        "logical_3level_mixed",
        {
            "asset": "EURUSD",
            "timeframe": "PERIOD_H1",
            "condition": {
                "type": "logical", "operator": "OR",
                "left": {"type": "logical", "operator": "AND", "left": leaf(1), "right": leaf(2)},
                "right": leaf(3),
            },
            "actions": [_base_action("BUY", RISK_VARIANTS[0][1], RISK_VARIANTS[1][1])],
            "max_positions": 1,
        },
    ))

    # --- 5. Multi-action strategies (2-3 actions under one THEN), each
    # action a different direction/SL-TP combo, at various max_positions /
    # "Positions" settings (1 = single, >1 = allow multiple at once).
    for max_positions in (1, 2, 5, 20):
        strategies.append((
            f"multi_action_max_positions_{max_positions}",
            {
                "asset": "EURUSD",
                "timeframe": "PERIOD_M15",
                "condition": {
                    "type": "comparison",
                    "left": {"kind": "MA", "period": 10, "ma_type": "MODE_SMA"},
                    "operator": ">",
                    "right": {"kind": "CANDLE", "candle_type": "PREV_CLOSE"},
                },
                "actions": [
                    _base_action("BUY", RISK_VARIANTS[0][1], RISK_VARIANTS[1][1], lot=0.1),
                    _base_action("SELL", MULTIPLY_VARIANTS[0][1], None, lot=0.05),
                    _base_action("BUY", None, RISK_VARIANTS[2][1], lot=0.2),
                ],
                "max_positions": max_positions,
            },
        ))

    # --- 6. Every timeframe covered explicitly at least once with a
    # trivial strategy (round-robin above already does this, but this
    # makes the coverage claim independently verifiable/obvious).
    for i, tf in enumerate(TIMEFRAMES):
        strategies.append((
            f"timeframe_{tf.lower()}",
            {
                "asset": "EURUSD",
                "timeframe": tf,
                "condition": {
                    "type": "comparison",
                    "left": {"kind": "RSI", "period": 14}, "operator": _cycle(COMPARISON_OPERATORS, i),
                    "right": {"kind": "NUMBER", "value": 50},
                },
                "actions": [_base_action(_cycle(DIRECTIONS, i), RISK_VARIANTS[0][1], RISK_VARIANTS[1][1])],
                "max_positions": 1,
            },
        ))

    # --- 7. Multi-rule strategies: more than one independent "IF" block
    # combined into one export (see main.py's RuleIR/magic-number design).
    # Already in the full {"rules": [...]} wire shape, unlike every entry
    # above - the wrap step below leaves these two untouched.
    strategies.append((
        "multi_rule_two_different_assets",
        {"rules": [
            {
                "asset": "EURUSD", "timeframe": "PERIOD_M15",
                "condition": {"type": "comparison", "left": {"kind": "RSI", "period": 14}, "operator": ">", "right": {"kind": "NUMBER", "value": 70}},
                "actions": [_base_action("BUY", RISK_VARIANTS[0][1], None)],
                "max_positions": 1,
            },
            {
                "asset": "GBPUSD", "timeframe": "PERIOD_H1",
                "condition": {"type": "comparison", "left": {"kind": "CANDLE", "candle_type": "CURRENT"}, "operator": ">", "right": {"kind": "MA", "period": 50, "ma_type": "MODE_SMA"}},
                "actions": [_base_action("SELL", None, MULTIPLY_VARIANTS[0][1])],
                "max_positions": 3,
            },
        ]},
    ))
    strategies.append((
        # Two rules deliberately sharing ONE asset - exercises magic-number/
        # label separation (and, on a netting MT5/cTrader account, the
        # documented same-symbol merge behaviour) independently of the
        # different-assets case above.
        "multi_rule_same_asset_twice",
        {"rules": [
            {
                "asset": "EURUSD", "timeframe": "PERIOD_M5",
                "condition": {"type": "comparison", "left": {"kind": "RSI", "period": 7}, "operator": "<", "right": {"kind": "NUMBER", "value": 30}},
                "actions": [_base_action("BUY", RISK_VARIANTS[1][1], RISK_VARIANTS[0][1])],
                "max_positions": 1,
            },
            {
                "asset": "EURUSD", "timeframe": "PERIOD_M30",
                "condition": {"type": "comparison", "left": {"kind": "STOCH", "k_period": 5, "d_period": 3, "slowing": 3, "stoch_line": "K"}, "operator": ">", "right": {"kind": "NUMBER", "value": 80}},
                "actions": [_base_action("SELL", RISK_VARIANTS[2][1], None)],
                "max_positions": 2,
            },
        ]},
    ))

    strategies.extend(_wave1_strategies())

    # Every entry above section 7 was built in the pre-multi-rule shape (a
    # single rule's fields directly at the top level) - wrap each in the
    # actual {"rules": [...]} wire shape here, in one place, rather than
    # rewriting every append() call site above individually. The two
    # multi-rule entries just above are already in the wrapped shape and
    # pass through unchanged.
    return [
        (name, config if "rules" in config else {"rules": [config]})
        for name, config in strategies
    ]


if __name__ == "__main__":
    matrix = generate_matrix()
    print(f"{len(matrix)} generated strategies:")
    for name, _ in matrix:
        print(f"  {name}")
