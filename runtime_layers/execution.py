"""Canonical signal-to-EA/API contracts with no network or market-data effects."""
from __future__ import annotations

from typing import Dict

from signal_schema import BLOCKED, ERROR, NO_SIGNAL, SIGNAL, create_signal
from runtime_layers.common import _first_float, _iso_timestamp, _safe_float

def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "ok", "pass", "passed", "win", "wins"}

def _rr_metrics(
    direction: str,
    entry: float,
    sl: float,
    tp_final: float,
    min_rr: float,
) -> Dict:
    """Return risk/reward/RR and pass/fail state. Pure adapter math; no market decision."""
    direction = str(direction or "").upper()
    entry = _safe_float(entry)
    sl = _safe_float(sl)
    tp_final = _safe_float(tp_final)

    if direction == "BUY":
        risk = entry - sl
        reward = tp_final - entry
    elif direction == "SELL":
        risk = sl - entry
        reward = entry - tp_final
    else:
        risk = 0.0
        reward = 0.0

    rr = reward / risk if risk > 0 else 0.0
    return {
        "risk_points": round(risk, 3) if risk > 0 else 0.0,
        "reward_points": round(reward, 3) if reward > 0 else 0.0,
        "rr": round(rr, 3) if rr > 0 else 0.0,
        "rr_ok": bool(risk > 0 and reward > 0 and rr >= min_rr),
        "min_rr": min_rr,
    }

def _any_truthy(data: Dict, keys: list[str]) -> bool:
    return any(_truthy(data.get(key)) for key in keys)

def _engine_v4_gate_state(signal: Dict, direction: str) -> Dict:
    """
    Gate Telegram/EA OPEN using the zone-first engine_v4 setup contract.
    If no engine_v4 overlay exists, keep gates permissive for non-engine WAIT paths.
    """
    engine = dict(signal.get("engine_v4", {}) or {})
    direction = str(direction or signal.get("decision", {}).get("action", "NONE")).upper()

    if not engine:
        return {
            "zone_ok": True,
            "setup_ok": True,
            "vsa_gate_ok": True,
            "setup_state": "NO_ENGINE_V4",
        }

    if direction == "BUY":
        zone_ok = _any_truthy(engine, [
            "PRZ_Support", "Pine_PRZ_Support", "Pine_PRZ_Support_Touch",
            "In_PRZ_Support", "BB_Lower_Zone", "Near_BB_Lower",
            "Buy_Killzone_072_088", "V4_Support_Zone",
            "v4_entry_zone", "zone_confluence", "bb_prz_confluence",
        ])
        pa_ok = _any_truthy(engine, [
            "HA_Bull", "HA_Bull_Reversal", "HA_Green_1", "HA_Green_2_CF",
            "Bullish_Pinbar", "pa_bull_confirmed", "PA_Bull_Confirmed",
        ])
        vsa_ok = _any_truthy(engine, [
            "VSA_Buy_Wins", "vsa_buy_wins", "VSA_BUY_WINS",
            "VSA_Buy_Pressure", "vsa_buy_pressure",
        ]) and not _any_truthy(engine, ["V4_Block_Buy_At_Upper"])
        setup_ok = _any_truthy(engine, ["V4_Buy_Setup", "V4_BUY_SETUP", "BUY_SETUP", "cf_confirmed"]) or str(engine.get("setup_state", "")).upper() in {"BUY_SETUP", "BUY_CF_READY"} or (zone_ok and pa_ok and vsa_ok)
        setup_state = "BUY_SETUP" if setup_ok else "BUY_BLOCKED"
    elif direction == "SELL":
        zone_ok = _any_truthy(engine, [
            "PRZ_Resistance", "Pine_PRZ_Resistance", "Pine_PRZ_Resistance_Touch",
            "In_PRZ_Resistance", "BB_Upper_Zone", "Near_BB_Upper",
            "V4_Resistance_Zone",
            "v4_entry_zone", "zone_confluence", "bb_prz_confluence",
        ])
        pa_ok = _any_truthy(engine, [
            "HA_Bear", "HA_Bear_Reversal", "HA_Red_1", "HA_Red_2_CF",
            "Bearish_Pinbar", "pa_bear_confirmed", "PA_Bear_Confirmed",
        ])
        vsa_ok = _any_truthy(engine, [
            "VSA_Sell_Wins", "vsa_sell_wins", "VSA_SELL_WINS",
            "VSA_Sell_Pressure", "vsa_sell_pressure",
        ]) and not _any_truthy(engine, ["V4_Block_Sell_At_Lower"])
        setup_ok = _any_truthy(engine, ["V4_Sell_Setup", "V4_SELL_SETUP", "SELL_SETUP"]) or str(engine.get("setup_state", "")).upper() in {"SELL_SETUP", "SELL_CF_READY"} or (zone_ok and pa_ok and vsa_ok)
        setup_state = "SELL_SETUP" if setup_ok else "SELL_BLOCKED"
    else:
        zone_ok = setup_ok = vsa_ok = False
        setup_state = "NO_DIRECTION"

    return {
        "zone_ok": bool(zone_ok),
        "setup_ok": bool(setup_ok),
        "vsa_gate_ok": bool(vsa_ok),
        "setup_state": setup_state,
    }

def _apply_engine_v4_signal(signal: Dict, engine_signal: Dict | None) -> Dict:
    """Overlay v4 baseline trade output onto v12 composed payload."""
    if not engine_signal:
        signal["status"] = NO_SIGNAL
        signal["direction"] = None
        signal["reason"] = "No BUY or SELL engine conditions met"
        return signal

    engine_status = str(engine_signal.get("status", SIGNAL)).upper()
    direction = str(engine_signal.get("direction", "NONE")).upper()
    entry = _safe_float(engine_signal.get("entry_price", engine_signal.get("entry")))
    sl = _safe_float(engine_signal.get("sl_price", engine_signal.get("sl")))
    tp1 = _safe_float(engine_signal.get("tp1_price", engine_signal.get("tp1")))
    tp_final = _safe_float(engine_signal.get("tp2_price", engine_signal.get("tp")))

    if engine_status != SIGNAL:
        signal["status"] = BLOCKED
        signal["direction"] = direction if direction in {"BUY", "SELL"} else None
        signal["entry_price"] = entry or None
        signal["sl_price"] = sl or None
        signal["tp1_price"] = tp1 or None
        signal["tp2_price"] = tp_final or None
        signal["reason"] = str(engine_signal.get("reason", "Engine candidate blocked"))
        signal["engine_v4"] = {
            key: _safe_float(value) if isinstance(value, float) else value
            for key, value in engine_signal.items()
            if key != "timestamp"
        }
        return signal

    if direction not in {"BUY", "SELL"}:
        signal["status"] = BLOCKED
        signal["direction"] = None
        signal["reason"] = "INVALID_ENGINE_DIRECTION"
        return signal

    if entry <= 0 or sl <= 0 or tp_final <= 0:
        signal["status"] = BLOCKED
        signal["direction"] = direction
        signal["reason"] = "MISSING_ENGINE_PRICE_LEVELS"
        return signal

    if direction == "BUY" and not (sl < entry < tp_final):
        signal["status"] = BLOCKED
        signal["direction"] = direction
        signal["reason"] = "INVALID_BUY_LEVELS"
        return signal
    if direction == "SELL" and not (tp_final < entry < sl):
        signal["status"] = BLOCKED
        signal["direction"] = direction
        signal["reason"] = "INVALID_SELL_LEVELS"
        return signal

    entry_mode = engine_signal.get("entry_mode") or f"V4_{direction}_BASE"
    exit_mode = engine_signal.get("exit_mode") or ("V4_BB_UPPER" if direction == "BUY" else "V4_BB_LOWER")

    quality_score = int(engine_signal.get("v5_quality_score", 0) or 0)
    confidence = 0.78 if quality_score >= 4 else 0.70
    score = 8 if quality_score >= 4 else 6
    grade = "STRONG_TRADE" if quality_score >= 4 else "VALID_TRADE"

    reason_parts = [
        "ENGINE_V4_BASELINE",
        f"direction={direction}",
        f"entry_mode={entry_mode}",
        f"exit_mode={exit_mode}",
    ]
    if engine_signal.get("v5_basis"):
        reason_parts.append(f"v5_basis={engine_signal.get('v5_basis')}")
    if engine_signal.get("session_quality_gate"):
        reason_parts.append(f"session_gate={engine_signal.get('session_quality_gate')}")

    signal["decision"] = {
        "action": direction,
        "confidence": confidence,
        "score": score,
        "reason": "|".join(reason_parts),
        "grade": grade,
    }
    signal["status"] = SIGNAL
    signal["direction"] = direction
    signal["entry_price"] = entry
    signal["sl_price"] = sl
    signal["tp1_price"] = tp1 or tp_final
    signal["tp2_price"] = tp_final
    signal["score"] = score
    signal["reason"] = "|".join(reason_parts)
    signal["timestamp"] = _iso_timestamp(engine_signal.get("timestamp") or signal.get("timestamp"))
    signal["entry"] = entry
    signal["sl"] = sl
    signal["tp_final"] = tp_final
    signal["entry_mode"] = entry_mode
    signal["setup_state"] = engine_signal.get("setup_state", "UNKNOWN")
    signal["scenario_state"] = engine_signal.get("scenario_state") or engine_signal.get("setup_state")
    signal["journey_state"] = engine_signal.get("journey_state")
    signal["v4_state"] = engine_signal.get("v4_state")
    signal["v5_state"] = engine_signal.get("v5_state")
    signal["engine_stages"] = engine_signal.get("engine_stages")
    signal["order_policy"] = engine_signal.get("order_policy")
    signal["entry_rr"] = engine_signal.get("entry_rr")
    signal["rr_ok"] = engine_signal.get("rr_ok")
    signal["zone_confluence"] = engine_signal.get("zone_confluence")
    signal["bb_prz_confluence"] = engine_signal.get("bb_prz_confluence")
    signal["v4_entry_zone"] = engine_signal.get("v4_entry_zone")
    signal["vsa_gate"] = engine_signal.get("vsa_gate")
    signal["selected_age_bars"] = engine_signal.get("selected_age_bars")
    signal["selected_idx"] = engine_signal.get("selected_idx")
    signal["exit_mode"] = exit_mode
    signal["be_policy"] = engine_signal.get("be_policy") or ("PROFIT_0_15" if direction == "BUY" else "CURRENT_BBMID_LOW")
    signal["trail_policy"] = engine_signal.get("trail_policy") or ("TRAIL_FACTOR_0_9995" if direction == "BUY" else "NONE")
    signal["max_bars"] = int(engine_signal.get("max_bars", 40) or 40)
    signal["v5_quality_score"] = quality_score
    signal["v5_quality_grade"] = engine_signal.get("v5_quality_grade", "BASE")
    signal["v5_basis"] = engine_signal.get("v5_basis", "BASE")
    signal["session_quality_gate"] = engine_signal.get("session_quality_gate", "BUY_TIMING_GATE" if direction == "BUY" else "UNKNOWN")
    signal["sell_dot_reason"] = engine_signal.get("sell_dot_reason", "UNKNOWN")
    signal["target_source"] = engine_signal.get("target_source")
    signal["tp_mode"] = engine_signal.get("tp_mode")
    signal["target_contract"] = engine_signal.get("target_contract")
    signal["harmonic_role"] = engine_signal.get("harmonic_role")
    signal["harmonic_target_price"] = engine_signal.get("harmonic_target_price")
    signal["harmonic_target_eligible"] = bool(
        engine_signal.get("harmonic_target_eligible")
    )
    signal["next_prz_low"] = engine_signal.get("next_prz_low")
    signal["next_prz_high"] = engine_signal.get("next_prz_high")

    for _key in (
        "scenario_state", "journey_state", "v4_state", "v5_state",
        "engine_stages", "order_policy", "trade_management", "break_prediction",
        "bos_confirmed", "vsa_gate", "vsa_pressure_delta", "checkpoint_price",
        "approach_break_zone", "setup_state", "target_source", "tp_mode",
        "target_contract", "harmonic_role", "harmonic_target_price",
        "harmonic_target_eligible", "next_prz_low", "next_prz_high",
    ):
        if engine_signal.get(_key) is not None:
            signal[_key] = engine_signal.get(_key)
    signal["engine_v4"] = {
        key: _safe_float(value) if isinstance(value, float) else value
        for key, value in engine_signal.items()
        if key != "timestamp"
    }

    return signal

def _python_trade_management_contract(direction: str, signal: Dict, setup_info: Dict) -> Dict:
    """
    Python-side trade management contract.
    EA must execute only; all HA/VSA/BOS/BE/TP-route decisions stay in Python.
    """
    engine_v4 = signal.get("engine_v4", {}) or {}
    existing = signal.get("trade_management") or engine_v4.get("trade_management") or {}

    if isinstance(existing, dict):
        management = dict(existing)
    elif existing:
        management = {"legacy_value": existing}
    else:
        management = {}

    management.setdefault("managed_by", "PYTHON_CLOUD")
    management.setdefault("ea_role", "EXECUTION_ONLY")
    management["ha_tf"] = "5M_CLOSED_BARS"
    management["m5_fallback"] = "HOLD_USE_SL_TP_TIMEOUT"
    management["ha_trailing_activation"] = "AFTER_TP1_AND_BE_ONLY"
    management.setdefault("bos_required_always", False)
    management["bos_required_for_tp2"] = True
    management["harmonic_role"] = "POST_BOS_TP2_AT_NEXT_PRZ_ONLY"

    if direction == "BUY":
        management.setdefault("entry_source", "VSA_DEMAND_WALL_REACTION")
        management.setdefault("entry_confirmation", "REACTION_CONFIRM_OR_BULLISH_PINBAR")
        management.setdefault("visual_sl_source", "VSA_WALL_LOW")
        management.setdefault("vsa_wall_low", _safe_float(engine_v4.get("vsa_wall_low")))
        management.setdefault("vsa_wall_high", _safe_float(engine_v4.get("vsa_wall_high")))
        management.setdefault("sl_rule", "BELOW_VSA_WALL_LOW")
        management.setdefault("tp_route", {
            "tp1": "V4_REACTION_CHECKPOINT_BEFORE_BOS",
            "tp2": "HARMONIC_D_IF_OVERLAPS_NEXT_SUPPLY_PRZ_AFTER_BOS",
        })
        management["ha_close_all_if"] = "TWO_CLOSED_HA5_RED_AFTER_BE"
        management["move_be_if"] = "TP1_REACHED"
        management.setdefault("bos_pullback_entry", "BOS_UP_PULLBACK_HA15_GREEN")
        management.setdefault("add_layer_rule", "BOS_UP_PULLBACK_HA15_GREEN")

    elif direction == "SELL":
        management.setdefault("entry_source", "VSA_SUPPLY_WALL_REJECTION")
        management.setdefault("entry_confirmation", "REACTION_CONFIRM_OR_BEARISH_PINBAR")
        management.setdefault("visual_sl_source", "VSA_WALL_HIGH")
        management.setdefault("vsa_wall_low", _safe_float(engine_v4.get("vsa_wall_low")))
        management.setdefault("vsa_wall_high", _safe_float(engine_v4.get("vsa_wall_high")))
        management.setdefault("sl_rule", "ABOVE_VSA_WALL_HIGH")
        management.setdefault("tp_route", {
            "tp1": "V4_REACTION_CHECKPOINT_BEFORE_BOS",
            "tp2": "HARMONIC_D_IF_OVERLAPS_NEXT_DEMAND_PRZ_AFTER_BOS",
        })
        management["ha_close_all_if"] = "TWO_CLOSED_HA5_GREEN_AFTER_BE"
        management["move_be_if"] = "TP1_REACHED"
        management.setdefault("bos_add_layer", "M15_BOS_BODY_CLOSE_DOWN_OR_RETEST_REJECTION")
        management.setdefault("layer_1", "VSA_SUPPLY_WALL_OR_PRZ_RESISTANCE_REJECTION")
        management.setdefault("layer_2", "M15_BOS_DOWN_OR_BOS_RETEST_REJECTION")

    else:
        management.setdefault("entry_source", "NONE")
        management.setdefault("visual_sl_source", "NONE")
        management.setdefault("tp_route", {})

    return management

def _python_plan_lifecycle_contract(
    *,
    signal_id: str,
    action: str,
    execution_state: str,
    direction: str,
    trade_direction_ok: bool,
    setup_ok: bool,
    zone_ok: bool,
    vsa_gate_ok: bool,
    rr_ok: bool,
    levels_ready: bool,
    directional_levels_ok: bool,
) -> Dict:
    """
    Plan lifecycle is controlled by Python.
    EA may display/store ARMED plans, but may open only READY + OPEN.
    """
    if action == "OPEN" and execution_state == "READY":
        plan_status = "READY"
    elif trade_direction_ok and setup_ok and zone_ok:
        plan_status = "ARMED"
    elif trade_direction_ok:
        plan_status = "WATCH"
    else:
        plan_status = "NONE"

    cancel_if = [
        "PYTHON_SENDS_CANCELLED",
        "PYTHON_SENDS_EXPIRED",
        "ZONE_INVALIDATED",
        "VSA_WALL_BROKEN",
        "HA15_OPPOSITE_CLOSE_ALL",
        "SESSION_EXPIRED",
    ]

    if not rr_ok:
        cancel_if.append("RR_BELOW_MIN")
    if not levels_ready or not directional_levels_ok:
        cancel_if.append("LEVELS_INVALID")

    return {
        "plan_id": signal_id,
        "plan_status": plan_status,
        "action_source": "PYTHON_CLOUD",
        "ea_may_open_from_armed": False,
        "ea_open_rule": "ONLY_ACTION_OPEN_AND_EXECUTION_STATE_READY",
        "python_controls_cancel": True,
        "cancel_if": cancel_if,
        "ready_checks": {
            "setup_ok": setup_ok,
            "zone_ok": zone_ok,
            "vsa_bonus": vsa_gate_ok,
            "rr_ok": rr_ok,
            "levels_ready": levels_ready,
            "directional_levels_ok": directional_levels_ok,
        },
    }

def build_ea_payload(symbol: str, signal: Dict, *, min_rr: float) -> Dict:
    """
    EA execution payload.
    Adapter-only: ไม่ตัดสินใจตลาดใหม่ ไม่คำนวณ PRZ/BOS/TP/SL เอง
    Levels/RR/setup are checked once here before EA/Telegram. VSA is evidence
    metadata and trade-management context, never a second hard entry gate.
    """
    decision = signal.get("decision", {}) or {}
    gates = signal.get("gates", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}

    plan_a = blueprint.get("plan_a", {}) or {}
    plan_b = blueprint.get("plan_b", {}) or {}

    signal_status = str(signal.get("status", NO_SIGNAL)).upper()
    direction = str(signal.get("direction") or decision.get("action", "NONE")).upper()
    timestamp = str(signal.get("timestamp") or blueprint.get("timestamp") or "")

    current_price = _safe_float(blueprint.get("current_price"))

    entry = _first_float(
        signal.get("entry_price"),
        signal.get("entry"),
        blueprint.get("entry"),
        plan_a.get("entry"),
        plan_b.get("entry"),
        blueprint.get("plan_a_entry"),
        blueprint.get("plan_b_entry"),
        current_price,
    )

    sl = _first_float(
        signal.get("sl_price"),
        signal.get("sl"),
        blueprint.get("sl"),
        plan_a.get("sl"),
        plan_b.get("sl"),
        blueprint.get("plan_a_sl"),
        blueprint.get("plan_b_sl"),
    )

    tp_final = _first_float(
        signal.get("tp2_price"),
        signal.get("tp_final"),
        signal.get("tp"),
        blueprint.get("tp_final"),
        blueprint.get("tp"),
        plan_a.get("tp"),
        plan_b.get("tp2"),
        plan_b.get("tp1"),
        blueprint.get("plan_a_tp"),
        blueprint.get("plan_b_tp2"),
        blueprint.get("plan_b_tp1"),
    )

    tp1 = _first_float(
        signal.get("tp1_price"),
        signal.get("tp1"),
        (signal.get("engine_v4", {}) or {}).get("tp1_price"),
        tp_final,
    )

    blueprint_valid = bool(gates.get("blueprint_valid", blueprint.get("is_valid", False)))
    trade_direction_ok = signal_status == SIGNAL and direction in {"BUY", "SELL"}
    levels_ready = entry > 0 and sl > 0 and tp1 > 0 and tp_final > 0

    if direction == "BUY":
        directional_levels_ok = sl < entry < tp1 <= tp_final
    elif direction == "SELL":
        directional_levels_ok = tp_final <= tp1 < entry < sl
    else:
        directional_levels_ok = False

    rr_info = _rr_metrics(direction, entry, sl, tp_final, min_rr)
    setup_info = _engine_v4_gate_state(signal, direction)

    rr_ok = bool(rr_info["rr_ok"])
    setup_ok = bool(setup_info["setup_ok"])
    zone_ok = bool(setup_info["zone_ok"])
    vsa_gate_ok = bool(setup_info["vsa_gate_ok"])

    execution_state = (
        "READY"
        if blueprint_valid
        and trade_direction_ok
        and levels_ready
        and directional_levels_ok
        and rr_ok
        and setup_ok
        and zone_ok
        else "WATCH"
    )

    action = "OPEN" if execution_state == "READY" else "WAIT"

    reason = str(decision.get("reason", "") or "")
    blocked_reasons = []
    if not rr_ok and trade_direction_ok and levels_ready and directional_levels_ok:
        blocked_reasons.append(f"rr={rr_info['rr']:.2f}<min_rr={min_rr:.2f}")
    if not setup_ok:
        blocked_reasons.append(f"setup={setup_info['setup_state']}")
    if not zone_ok:
        blocked_reasons.append("zone_not_confirmed")
    if blocked_reasons:
        reason = "|".join([part for part in [reason, *blocked_reasons] if part])

    signal_id = f"{symbol}-{timestamp}-{direction}".replace(":", "").replace("/", "")

    trade_management = _python_trade_management_contract(direction, signal, setup_info)
    plan_lifecycle = _python_plan_lifecycle_contract(
        signal_id=signal_id,
        action=action,
        execution_state=execution_state,
        direction=direction,
        trade_direction_ok=trade_direction_ok,
        setup_ok=setup_ok,
        zone_ok=zone_ok,
        vsa_gate_ok=vsa_gate_ok,
        rr_ok=rr_ok,
        levels_ready=levels_ready,
        directional_levels_ok=directional_levels_ok,
    )

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "action": action,
        "execution_state": execution_state,
        "direction": direction if trade_direction_ok else "NONE",

        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp_final": tp_final,
        "risk_pct": _safe_float(signal.get("risk_pct"), 0.0075),
        "levels_ready": levels_ready,
        "directional_levels_ok": directional_levels_ok,
        "max_bars": int(signal.get("max_bars", 40)),

        "rr": rr_info["rr"],
        "rr_ok": rr_ok,
        "risk_points": rr_info["risk_points"],
        "reward_points": rr_info["reward_points"],
        "min_rr": rr_info["min_rr"],

        "zone_ok": zone_ok,
        "setup_ok": setup_ok,
        "vsa_gate_ok": vsa_gate_ok,
        "setup_state": setup_info["setup_state"],
        "scenario_state": signal.get("scenario_state") or (signal.get("engine_v4", {}) or {}).get("scenario_state"),
        "journey_state": signal.get("journey_state") or (signal.get("engine_v4", {}) or {}).get("journey_state"),
        "v4_state": signal.get("v4_state") or (signal.get("engine_v4", {}) or {}).get("v4_state"),
        "v5_state": signal.get("v5_state") or (signal.get("engine_v4", {}) or {}).get("v5_state"),
        "engine_stages": signal.get("engine_stages") or (signal.get("engine_v4", {}) or {}).get("engine_stages") or {},
        "order_policy": signal.get("order_policy") or (signal.get("engine_v4", {}) or {}).get("order_policy") or "V4_OPEN_ONCE_V5_MANAGE_EXISTING",
        "trade_management": trade_management,
        "break_prediction": signal.get("break_prediction") or (signal.get("engine_v4", {}) or {}).get("break_prediction"),
        "bos_confirmed": bool(signal.get("bos_confirmed") or (signal.get("engine_v4", {}) or {}).get("bos_confirmed")),
        "vsa_gate": signal.get("vsa_gate") or (signal.get("engine_v4", {}) or {}).get("vsa_gate"),
        "checkpoint_price": _safe_float(signal.get("checkpoint_price") or (signal.get("engine_v4", {}) or {}).get("checkpoint_price")),
        "target_source": signal.get("target_source") or (signal.get("engine_v4", {}) or {}).get("target_source") or "V4_SCALP_CHECKPOINT",
        "tp_mode": signal.get("tp_mode") or (signal.get("engine_v4", {}) or {}).get("tp_mode") or "SINGLE_TP",
        "target_contract": signal.get("target_contract") or (signal.get("engine_v4", {}) or {}).get("target_contract") or {},
        "harmonic_role": signal.get("harmonic_role") or (signal.get("engine_v4", {}) or {}).get("harmonic_role") or "POST_BOS_TP2_ONLY",
        "harmonic_target_price": _safe_float(signal.get("harmonic_target_price") or (signal.get("engine_v4", {}) or {}).get("harmonic_target_price")),
        "harmonic_target_eligible": bool(signal.get("harmonic_target_eligible") or (signal.get("engine_v4", {}) or {}).get("harmonic_target_eligible")),
        "next_prz_low": _safe_float(signal.get("next_prz_low") or (signal.get("engine_v4", {}) or {}).get("next_prz_low")),
        "next_prz_high": _safe_float(signal.get("next_prz_high") or (signal.get("engine_v4", {}) or {}).get("next_prz_high")),

        "visual_sl_source": trade_management.get("visual_sl_source", "NONE"),
        "tp_route": trade_management.get("tp_route", {}),
        "plan_lifecycle": plan_lifecycle,
        "command_owner": "PYTHON_CLOUD",
        "ea_role": "EXECUTION_ONLY",
        "ea_execute_only": True,

        "session": gates.get("session", ""),
        "entry_mode": signal.get("entry_mode", "V12_DECISION"),
        "exit_mode": signal.get("exit_mode", "NONE"),
        "be_policy": signal.get("be_policy", "NONE"),
        "trail_policy": signal.get("trail_policy", "NONE"),

        "v5_quality_score": int(signal.get("v5_quality_score", 0) or 0),
        "v5_quality_grade": signal.get("v5_quality_grade", "UNKNOWN"),
        "v5_basis": signal.get("v5_basis", "UNKNOWN"),

        "session_quality_gate": signal.get("session_quality_gate", "UNKNOWN"),
        "sell_dot_reason": signal.get("sell_dot_reason", "UNKNOWN"),

        "confidence": _safe_float(decision.get("confidence")),
        "score": _safe_float(decision.get("score")),
        "grade": decision.get("grade", ""),
        "reason": reason,
    }

def build_api_signal_response(symbol: str, signal: Dict, ea: Dict) -> Dict:
    """Expose one API schema for BUY, SELL, no-signal, blocked, and error states."""
    signal_status = str(signal.get("status", NO_SIGNAL)).upper()
    direction = str(signal.get("direction") or ea.get("direction") or "").upper()
    if direction not in {"BUY", "SELL"}:
        direction = None

    engine = signal.get("engine_v4", {}) or {}
    entry = _first_float(signal.get("entry_price"), ea.get("entry"))
    sl = _first_float(signal.get("sl_price"), ea.get("sl"))
    tp2 = _first_float(signal.get("tp2_price"), ea.get("tp_final"))
    tp1 = _first_float(
        signal.get("tp1_price"),
        engine.get("tp1_price"),
        engine.get("tp1"),
        tp2,
    )

    if signal_status == ERROR:
        status = ERROR
    elif signal_status == BLOCKED:
        status = BLOCKED
    elif direction is None:
        status = NO_SIGNAL
    elif signal_status == SIGNAL and ea.get("action") == "OPEN":
        status = SIGNAL
    else:
        status = BLOCKED

    response_reason = (
        signal.get("reason")
        if status in {NO_SIGNAL, ERROR}
        else ea.get("reason") or signal.get("reason")
    )
    contract = create_signal(
        status=status,
        direction=direction,
        entry_price=entry,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        score=signal.get("score", ea.get("score", 0)),
        reason=response_reason or "No signal",
    )
    return {
        **contract,
        "symbol": symbol,
        "source": "PYTHON",
        "engine_stages": (
            ea.get("engine_stages")
            or signal.get("engine_stages")
            or engine.get("engine_stages")
            or {}
        ),
        "signal": signal,
        "ea": ea,
    }
