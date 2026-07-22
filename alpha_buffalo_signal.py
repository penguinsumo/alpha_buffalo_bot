# Railway deployment revision: 20260721T063218Z
from __future__ import annotations

import html
import json
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request, Response

from decision_engine import DecisionEngine
from scenario_scanner import ScenarioScanner
from signal_composer import SignalComposer
from signal_schema import BLOCKED, ERROR, NO_SIGNAL, SIGNAL, create_signal
from session_clock import SessionClock
from telegram_guard import (
    TELEGRAM_DISCLAIMER,
    ensure_telegram_disclaimer,
    guarded_telegram_post,
    telegram_market_is_open,
)
from engine_v4.session_gate import GateResult, SessionGate
from execution_lifecycle import execution_lifecycle
from pine_signal_bridge import (
    PineSignalBridge,
    PineSignalError,
    build_pine_api_payload,
    pine_signal_bridge,
)

# Engine V4 baseline imports. Keep these globals available for _run_engine_v4_baseline().
ENGINE_V4_IMPORT_ERROR = None
try:
    from engine_v4.indicators import add_indicators
    from engine_v4.router import SignalRouter
    from engine_v4.final_gate import FinalGate
    from engine_v4.harmonic_bias_gate import evaluate_harmonic_bias
    from engine_v4.buy_engine import BuySignalEngine
    from engine_v4.sell_engine import SellSignalEngine
except Exception as exc:  # pragma: no cover - runtime diagnostic path
    ENGINE_V4_IMPORT_ERROR = exc
    add_indicators = None
    SignalRouter = None
    FinalGate = None
    evaluate_harmonic_bias = None
    BuySignalEngine = None
    SellSignalEngine = None


app = FastAPI(title="Alpha Buffalo v12 API Adapter", version="12.0.0")

SIGNAL_LOOP_INTERVAL_SECONDS = int(os.getenv("SIGNAL_LOOP_INTERVAL_SECONDS", "60"))
LATEST_SIGNAL_CACHE: dict = {}
LATEST_SIGNAL_LOCK = threading.Lock()

TF_FETCH_TTL_SECONDS = {
    "5min": int(os.getenv("TF_5M_TTL_SECONDS", "45")),
    "15min": int(os.getenv("TF_15M_TTL_SECONDS", "180")),
    "1h": int(os.getenv("TF_1H_TTL_SECONDS", "900")),
    "4h": int(os.getenv("TF_4H_TTL_SECONDS", "1800")),
}
TF_DATA_CACHE: dict = {}
TF_CACHE_LOCK = threading.Lock()
_SIGNAL_LOOP_STARTED = False

SYMBOL_DEFAULT = os.getenv("ALPHA_SYMBOL", "XAU/USD")
PUBLIC_SYMBOL_DEFAULT = os.getenv("ALPHA_PUBLIC_SYMBOL", "XAUUSD")
TWELVEDATA_API_KEY = (
    os.getenv("TWELVEDATA_API_KEY")
    or os.getenv("TWELVE_API_KEY")
    or os.getenv("TWELVE_DATA_API_KEY")
    or os.getenv("TWELVEDATA_KEY")
    or ""
)
API_LICENSE_KEY = os.getenv("ALPHA_API_KEY", os.getenv("LICENSE_KEY", "DEMO123"))
SIGNAL_SOURCE = os.getenv("ALPHA_SIGNAL_SOURCE", "PYTHON").strip().upper()
if SIGNAL_SOURCE not in {"PYTHON", "PINE", "HYBRID"}:
    SIGNAL_SOURCE = "PYTHON"
python_signal_bridge = PineSignalBridge(
    os.getenv(
        "ALPHA_PYTHON_BRIDGE_STATE_FILE",
        "/tmp/alpha_buffalo_python_bridge.json",
    ),
    accepted_source="PYTHON",
    command_prefix="PYTHON",
    command_owner="PYTHON_CLOUD",
    open_ttl_env="ALPHA_PYTHON_OPEN_TTL_SECONDS",
    close_ttl_env="ALPHA_PYTHON_CLOSE_TTL_SECONDS",
)
# Harmonic is production guidance, never a mandatory entry prerequisite.
# A separate, intentionally named switch keeps the historical hard gate
# available only for controlled research/backtests.  The deprecated
# ALPHA_REQUIRE_HARMONIC_BIAS value is deliberately ignored so a stale Railway
# variable cannot silently block every live order again.
REQUIRE_HARMONIC_BIAS = os.getenv(
    "ALPHA_STRICT_HARMONIC_RESEARCH_MODE", "false"
).lower() in {"1", "true", "yes", "on"}
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_IDS = [
    chat_id.strip()
    for chat_id in (
        os.getenv("NOTIFY_IDS")
        or os.getenv("TELEGRAM_CHAT_IDS")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    ).split(",")
    if chat_id.strip()
]
# Only the confirmed-BOS/WAIT-CF state may emit a waiting message.  The old
# TELEGRAM_NOTIFY_WAIT flag is intentionally ignored because it represented
# noisy generic WAIT updates in previous deployments.
TELEGRAM_NOTIFY_WAIT = os.getenv("TELEGRAM_NOTIFY_CONFIRMATION", "true").lower() in {"1", "true", "yes", "on"}
TELEGRAM_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "5"))
TRADE_MIN_RR = float(os.getenv("TRADE_MIN_RR", "1.5"))
TELEGRAM_MIN_RR = float(os.getenv("TELEGRAM_MIN_RR", str(TRADE_MIN_RR)))
TELEGRAM_NOTIFY_TREND_UPDATE = os.getenv("TELEGRAM_NOTIFY_TREND_UPDATE", "true").lower() in {"1", "true", "yes", "on"}
TELEGRAM_TREND_STATE_FILE = os.getenv(
    "TELEGRAM_TREND_STATE_FILE",
    "/tmp/alpha_buffalo_trend_update_state.json",
).strip()
TELEGRAM_PINE_MONITOR_ENABLED = os.getenv("TELEGRAM_PINE_MONITOR_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
TELEGRAM_PINE_MONITOR_INTERVAL_SECONDS = max(
    60,
    int(os.getenv("TELEGRAM_PINE_MONITOR_INTERVAL_SECONDS", "300")),
)
TELEGRAM_MAX_OPEN_SIGNAL_AGE_SECONDS = max(
    60,
    int(os.getenv("TELEGRAM_MAX_OPEN_SIGNAL_AGE_SECONDS", "1800")),
)
LAST_TELEGRAM_SIGNAL_KEY = ""
LAST_TELEGRAM_TREND_UPDATE_KEY = ""
LAST_TELEGRAM_TREND_STATE_LOADED = False
LAST_TELEGRAM_CONFIRM_KEY = ""
LAST_TELEGRAM_LOCK = threading.Lock()
TELEGRAM_DELIVERY_STATUS = {
    "last_attempt_at": "",
    "last_success_at": "",
    "last_error": "",
}


def _market_open_gate() -> Dict:
    session_state = SessionClock().get()
    block_reason = "MARKET_CLOSED" if session_state.session == "CLOSED" else ""

    return {
        "market_open": not bool(block_reason),
        "block_reason": block_reason,
        "session_state": session_state,
        "timestamp": session_state.timestamp,
    }


def _market_closed_payload(symbol: str, public_symbol: str, gate: Dict) -> Dict:
    session_state = gate["session_state"]
    timestamp = str(gate.get("timestamp") or datetime.now(timezone.utc).isoformat())
    block_reason = str(gate.get("block_reason") or "MARKET_CLOSED")
    signal_id = f"{public_symbol}-{timestamp}-MARKET_CLOSED".replace(":", "").replace("/", "")

    return {
        "status": NO_SIGNAL,
        "direction": None,
        "entry_price": None,
        "sl_price": None,
        "tp1_price": None,
        "tp2_price": None,
        "score": 0,
        "reason": block_reason,
        "symbol": public_symbol,
        "generated_at": timestamp,
        "signal": {
            "status": NO_SIGNAL,
            "direction": None,
            "reason": block_reason,
            "timestamp": timestamp,
            "decision": {
                "action": "NONE",
                "grade": "WAIT",
                "score": 0,
                "confidence": 0.0,
                "reason": block_reason,
            },
            "gates": {
                "session": session_state.session,
                "liquidity": session_state.liquidity,
                "market_open": False,
                "block_reason": block_reason,
            },
            "blueprint": {
                "symbol": public_symbol,
                "session": session_state.session,
                "is_valid": False,
                "validation_errors": [block_reason],
            },
        },
        "ea": {
            "signal_id": signal_id,
            "symbol": public_symbol,
            "action": "WAIT",
            "execution_state": "BLOCKED",
            "direction": "NONE",
            "entry": 0.0,
            "sl": 0.0,
            "tp_final": 0.0,
            "risk_pct": 0.0,
            "levels_ready": False,
            "directional_levels_ok": False,
            "max_bars": 0,
            "rr": 0.0,
            "rr_ok": False,
            "risk_points": 0.0,
            "reward_points": 0.0,
            "min_rr": TRADE_MIN_RR,
            "zone_ok": False,
            "setup_ok": False,
            "vsa_gate_ok": False,
            "setup_state": "MARKET_CLOSED",
            "scenario_state": "MARKET_CLOSED",
            "journey_state": "NONE",
            "trade_management": {
                "managed_by": "PYTHON_CLOUD",
                "ea_role": "EXECUTION_ONLY",
                "entry_source": "NONE",
                "visual_sl_source": "NONE",
                "tp_route": {},
            },
            "visual_sl_source": "NONE",
            "tp_route": {},
            "plan_lifecycle": {
                "plan_id": signal_id,
                "plan_status": "NONE",
                "action_source": "PYTHON_CLOUD",
                "ea_may_open_from_armed": False,
                "ea_open_rule": "ONLY_ACTION_OPEN_AND_EXECUTION_STATE_READY",
                "python_controls_cancel": True,
                "cancel_if": [block_reason, "SESSION_EXPIRED"],
                "ready_checks": {
                    "setup_ok": False,
                    "zone_ok": False,
                    "vsa_gate_ok": False,
                    "rr_ok": False,
                    "levels_ready": False,
                    "directional_levels_ok": False,
                },
            },
            "command_owner": "PYTHON_CLOUD",
            "ea_role": "EXECUTION_ONLY",
            "ea_execute_only": True,
            "session": session_state.session,
            "entry_mode": "MARKET_CLOSED_GUARD",
            "exit_mode": "NONE",
            "confidence": 0.0,
            "score": 0.0,
            "grade": "WAIT",
            "reason": block_reason,
            "block_reason": block_reason,
            "market_open": False,
        },
    }


def verify_license(key: str) -> bool:
    return bool(key) and key == API_LICENSE_KEY


def fetch_twelvedata(symbol: str, interval: str, outputsize: int = 200) -> pd.DataFrame:
    if not TWELVEDATA_API_KEY:
        raise HTTPException(status_code=500, detail="MISSING_TWELVEDATA_API_KEY")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }

    response = requests.get(url, params=params, timeout=15)

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"DATA_FETCH_HTTP_{response.status_code}")

    data = response.json()

    if data.get("status") == "error":
        raise HTTPException(status_code=502, detail=data.get("message", "TWELVEDATA_ERROR"))

    values = data.get("values", [])
    if not values:
        raise HTTPException(status_code=502, detail="EMPTY_MARKET_DATA")

    df = pd.DataFrame(values)

    required = ["open", "high", "low", "close"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise HTTPException(status_code=502, detail=f"MISSING_MARKET_COLUMNS:{missing}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.sort_values("datetime")

    df = df.reset_index(drop=True)

    if len(df) < 50:
        raise HTTPException(status_code=502, detail=f"INSUFFICIENT_MARKET_DATA:{interval}:{len(df)}")

    return df


def _fetch_cached_tf(symbol: str, interval: str, outputsize: int = 200) -> pd.DataFrame:
    ttl = TF_FETCH_TTL_SECONDS.get(interval, 180)
    key = f"{symbol}:{interval}"
    now = time.time()

    stale_df = None
    stale_age = None

    with TF_CACHE_LOCK:
        cached = TF_DATA_CACHE.get(key)
        if cached:
            stale_df = cached.get("df")
            cached_ts = float(cached.get("ts", 0))
            stale_age = now - cached_ts
            if stale_df is not None and stale_age < ttl:
                return stale_df.copy()

    try:
        df = fetch_twelvedata(symbol, interval, outputsize=outputsize)
    except Exception as exc:
        if stale_df is not None:
            print(
                f"AlphaBuffalo TF cache fallback | interval={interval} age={int(stale_age or 0)}s error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return stale_df.copy()
        raise

    with TF_CACHE_LOCK:
        TF_DATA_CACHE[key] = {"df": df.copy(), "ts": time.time()}

    print(f"AlphaBuffalo TF fetch | interval={interval} ttl={ttl}s rows={len(df)}", flush=True)
    return df


def fetch_multi_tf(symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_4h = _fetch_cached_tf(symbol, "4h")
    df_1h = _fetch_cached_tf(symbol, "1h")
    df_15m = _fetch_cached_tf(symbol, "15min")
    return df_4h, df_1h, df_15m


def _harmonic_gate_context(blueprint) -> Dict:
    """Normalize ScenarioBlueprint into the one V4/V5 bias contract."""
    if blueprint is None:
        return {
            "found": False,
            "direction": "NONE",
            "state": "MISSING",
            "pattern": "",
            "source": "NONE",
            "tunnel_state": "NONE",
        }
    return {
        "found": bool(getattr(blueprint, "harmonic_is_real", False)),
        "direction": str(getattr(blueprint, "harmonic_direction", "NONE") or "NONE").upper(),
        "approach_direction": str(getattr(blueprint, "harmonic_approach_direction", "NONE") or "NONE").upper(),
        "state": str(getattr(blueprint, "harmonic_state", "NONE") or "NONE").upper(),
        "pattern": str(getattr(blueprint, "harmonic_pattern", "") or ""),
        "source": str(getattr(blueprint, "harmonic_source", "NONE") or "NONE"),
        "source_tf": str(getattr(blueprint, "harmonic_source_tf", "NONE") or "NONE"),
        "pattern_state": str(getattr(blueprint, "harmonic_pattern_state", "NONE") or "NONE"),
        "projection_mode": str(getattr(blueprint, "harmonic_projection_mode", "NONE") or "NONE"),
        "execution_authority": bool(getattr(blueprint, "harmonic_execution_authority", True)),
        "tunnel_broken": bool(getattr(blueprint, "harmonic_tunnel_broken", False)),
        "candidate_patterns": list(getattr(blueprint, "harmonic_candidate_patterns", []) or []),
        "current_xad": _safe_float(getattr(blueprint, "harmonic_current_xad", 0.0)),
        "current_bcd": _safe_float(getattr(blueprint, "harmonic_current_bcd", 0.0)),
        "next_xad": _safe_float(getattr(blueprint, "harmonic_next_xad", 0.0)),
        "d_point": _safe_float(getattr(blueprint, "harmonic_d_point", 0.0)),
        "prz_low": _safe_float(getattr(blueprint, "harmonic_prz_low", 0.0)),
        "prz_high": _safe_float(getattr(blueprint, "harmonic_prz_high", 0.0)),
        "current_price": _safe_float(getattr(blueprint, "current_price", 0.0)),
        "tunnel_state": str(getattr(blueprint, "tunnel_state", "NONE") or "NONE").upper(),
        "tunnel_valid": bool(getattr(blueprint, "tunnel_valid", False)),
        "bos_eligible": bool(getattr(blueprint, "harmonic_bos_eligible", False)),
        "bos_state": str(getattr(blueprint, "harmonic_bos_state", "WAIT_BOS") or "WAIT_BOS"),
        "bos_sources": list(getattr(blueprint, "harmonic_bos_sources", []) or []),
        "bos_direction": str(getattr(blueprint, "harmonic_bos_direction", "NONE") or "NONE"),
        "bos_primary_timeframe": str(
            getattr(blueprint, "harmonic_bos_primary_timeframe", "NONE") or "NONE"
        ),
    }


def _live_harmonic_gate_context(public_symbol: str) -> Dict:
    """Read the Newday harmonic map with current cached multi-TF location."""
    clean_symbol = str(public_symbol or PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    default_clean = str(PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    data_symbol = SYMBOL_DEFAULT if clean_symbol == default_clean else public_symbol
    df_4h, df_1h, df_15m = fetch_multi_tf(data_symbol)
    blueprint = ScenarioScanner().scan(
        df_4h,
        df_1h,
        df_15m,
        symbol=clean_symbol,
    )
    return _harmonic_gate_context(blueprint)


def _pine_entry_permission(direction: str, public_symbol: str) -> GateResult:
    """Check time/risk for Pine OPEN; harmonic is optional context by default."""
    direction = str(direction or "").upper()
    if direction not in {"BUY", "SELL"}:
        return GateResult(False, "INVALID_ENTRY_DIRECTION")
    if FinalGate is None:
        return GateResult(False, "FINAL_GATE_UNAVAILABLE")

    harmonic_context = None
    if REQUIRE_HARMONIC_BIAS:
        try:
            harmonic_context = _live_harmonic_gate_context(public_symbol)
        except Exception as exc:
            print(
                "AlphaBuffalo strict harmonic entry gate failed | "
                f"symbol={public_symbol} direction={direction} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
            return GateResult(False, "HARMONIC_CONTEXT_UNAVAILABLE")

    clock = SessionClock()
    risk_permissions = execution_lifecycle.risk_permissions(
        str(public_symbol or PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    )
    return FinalGate(clock).evaluate(
        clock.get(),
        direction,
        **risk_permissions,
        harmonic_context=harmonic_context,
        require_harmonic=REQUIRE_HARMONIC_BIAS,
    )


def fetch_management_m5(symbol: str) -> pd.DataFrame | None:
    """Fetch M5 for active-position management without breaking entry scans."""
    try:
        return _fetch_cached_tf(symbol, "5min", outputsize=250)
    except Exception as exc:
        print(f"AlphaBuffalo M5 management unavailable | {type(exc).__name__}: {exc}", flush=True)
        return None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_float(*values, default: float = 0.0) -> float:
    for value in values:
        parsed = _safe_float(value, default=0.0)
        if parsed != 0.0:
            return parsed
    return default


def _fmt_price(value) -> str:
    parsed = _safe_float(value)
    return f"{parsed:.2f}" if parsed else "-"


def _clean_text(value, default: str = "-") -> str:
    text = str(value if value not in (None, "") else default)
    return html.escape(text, quote=False)


def _rr_metrics(direction: str, entry: float, sl: float, tp_final: float) -> Dict:
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
        "rr_ok": bool(risk > 0 and reward > 0 and rr >= TRADE_MIN_RR),
        "min_rr": TRADE_MIN_RR,
    }


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "ok", "pass", "passed", "win", "wins"}


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


def _format_time_pair(value) -> str:
    raw = str(value or "").strip()
    dt = None
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            dt = None
    if dt is None:
        return raw or "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    bkk = utc.astimezone(timezone(timedelta(hours=7)))
    return f"{utc:%a %d %b %Y | %H:%M UTC} / {bkk:%H:%M BKK}"


def _telegram_enabled() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_IDS)


def _telegram_signal_key(payload: Dict) -> str:
    ea = payload.get("ea", {}) or {}
    signal = payload.get("signal", {}) or {}
    return "|".join([
        str(ea.get("signal_id", "")),
        str(ea.get("action", "")),
        str(ea.get("execution_state", "")),
        str(ea.get("direction", "")),
        str(ea.get("entry", "")),
        str(ea.get("sl", "")),
        str(ea.get("tp_final", "")),
        str(signal.get("v5_quality_score", "")),
    ])


def _telegram_open_signal_is_fresh(payload: Dict) -> bool:
    """Prevent an old cached OPEN from being re-sent after a service restart."""
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    raw = (
        ea.get("received_at")
        or signal.get("timestamp")
        or payload.get("generated_at")
        or ""
    )
    try:
        parsed = pd.to_datetime(raw, utc=True).to_pydatetime()
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return -60.0 <= age <= TELEGRAM_MAX_OPEN_SIGNAL_AGE_SECONDS



def _calc_rr(direction: str, entry: float, sl: float, tp: float) -> Dict[str, float | bool]:
    direction = str(direction or "").upper()
    if direction == "BUY":
        risk = entry - sl
        reward = tp - entry
    elif direction == "SELL":
        risk = sl - entry
        reward = entry - tp
    else:
        risk = 0.0
        reward = 0.0
    rr = reward / risk if risk > 0 else 0.0
    return {
        "risk_points": float(risk),
        "reward_points": float(reward),
        "rr": float(rr),
        "rr_ok": bool(rr >= TRADE_MIN_RR),
        "min_rr": float(TRADE_MIN_RR),
    }


def _format_signal_time(value: str) -> str:
    try:
        ts = pd.to_datetime(value, utc=True)
        bkk = ts.tz_convert("Asia/Bangkok")
        return f"{ts.strftime('%a %d %b %Y | %H:%M UTC')} / {bkk.strftime('%H:%M BKK')}"
    except Exception:
        return str(value or "-")


def _format_public_trade_time(value: str) -> str:
    try:
        ts = pd.to_datetime(value, utc=True).tz_convert("Asia/Bangkok")
        return ts.strftime("%a %d %b %Y | %H:%M")
    except Exception:
        return str(value or "-")


def _public_side(direction: str) -> tuple[str, str]:
    direction = str(direction or "NONE").upper()
    if direction == "BUY":
        return "🟢", "BUY"
    if direction == "SELL":
        return "🔴", "SELL"
    return "⚪", "WAIT"


def _price_zone(center: float, direction: str) -> str:
    if center <= 0:
        return "-"
    width = max(2.0, center * 0.00025)
    if str(direction).upper() == "SELL":
        low, high = center - width, center
    else:
        low, high = center, center + width
    return f"{low:.1f} - {high:.1f}"


def _public_targets(direction: str, entry: float, tp_final: float, engine: Dict) -> tuple[float, float]:
    direction = str(direction or "").upper()
    tp1 = _first_float(engine.get("tp1"), engine.get("signal_tp"), engine.get("bb_lower_tp"))
    if direction == "BUY":
        if tp1 <= entry or tp1 > tp_final:
            tp1 = entry + (tp_final - entry) * 0.5
    elif direction == "SELL":
        if tp1 >= entry or tp1 < tp_final:
            tp1 = entry - (entry - tp_final) * 0.5
    else:
        tp1 = 0.0
    return tp1, tp_final


def _has_tp2_opportunity(payload: Dict, direction: str, tp1: float, tp2: float) -> bool:
    """Show TP2 only when the plan has structural/PRZ continuation evidence.

    The targets may still exist internally for risk management.  Telegram uses
    one TP for a short reaction trade and TP1/TP2 only for a runner that can
    plausibly continue through a PRZ after aligned BOS/CHoCH evidence.
    """
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    engine = dict(signal.get("engine_v4", {}) or {})
    blueprint = signal.get("blueprint", {}) or {}
    mode = str(
        ea.get("telegram_tp_mode")
        or signal.get("telegram_tp_mode")
        or ea.get("tp_mode")
        or signal.get("tp_mode")
        or ""
    ).upper()
    if mode in {"SINGLE", "SINGLE_TP", "SHORT", "SCALP"}:
        return False
    if mode in {"DUAL", "TP1_TP2", "RUNNER", "CONTINUATION"}:
        return True

    direction = str(direction or "").upper()
    if direction not in {"BUY", "SELL"} or tp1 <= 0 or tp2 <= 0 or abs(tp2 - tp1) < 1e-9:
        return False

    bos_direction = str(
        blueprint.get("harmonic_bos_direction")
        or _deep_get(blueprint, ["harmonic", "bos_direction"], "")
        or engine.get("bos_direction")
        or ""
    ).upper()
    aligned_bos = bool(
        ea.get("bos_confirmed")
        or signal.get("bos_confirmed")
        or engine.get("bos_confirmed")
        or blueprint.get("bos_confirmed")
        or blueprint.get("choch_confirmed")
        or blueprint.get("harmonic_bos_eligible")
        or _deep_get(blueprint, ["harmonic", "bos_eligible"], False)
    ) and bos_direction in {"", "NONE", "MIXED", direction}

    journey = str(
        ea.get("journey_state")
        or signal.get("journey_state")
        or engine.get("journey_state")
        or ""
    ).upper()
    continuation_state = direction in journey and any(
        token in journey for token in ("V5", "BOS", "CHOCH", "CONTINU")
    )
    target_source = str(
        ea.get("target_source")
        or signal.get("target_source")
        or engine.get("target_source")
        or ""
    ).upper()
    prz_route = "PRZ" in target_source and target_source not in {"PRZ_REACTION", "NEAREST_PRZ"}
    return bool(aligned_bos or continuation_state or prz_route)


def format_telegram_signal(payload: Dict) -> str:
    """Format either the short one-TP alert or the structural TP1/TP2 alert."""
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    engine = dict(signal.get("engine_v4", {}) or {})

    direction = str(ea.get("direction", "NONE")).upper()
    side_icon, side_label = _public_side(direction)
    entry = _safe_float(ea.get("entry"))
    sl = _safe_float(ea.get("sl"))
    tp = _safe_float(ea.get("tp_final"))
    engine.setdefault("tp1", ea.get("tp1"))
    tp1, tp2 = _public_targets(direction, entry, tp, engine)
    timestamp = signal.get("timestamp") or ea.get("received_at") or ea.get("signal_id") or ""
    score = int(_safe_float(ea.get("score") or signal.get("score") or _deep_get(signal, ["decision", "score"], 0)))
    session = ea.get("session") or _deep_get(signal, ["gates", "session"], "-")

    if not _has_tp2_opportunity(payload, direction, tp1, tp2):
        return "\n".join([
            f"{side_icon} <b>SESSION SIGNAL FIRING</b>",
            "━━━━━━━━━━━━━━━━━",
            f"📌 {_clean_text(symbol)} {_clean_text(side_label)}",
            f"🎯 Entry  : {entry:,.2f}",
            f"🛡️ SL     : {sl:,.2f}",
            f"🏆 TP     : {tp1:,.2f}",
            f"{'📈' if direction == 'BUY' else '📉'} Score  : {score}/10",
            f"🕐 Session: {_clean_text(session)}",
            "━━━━━━━━━━━━━━━━━",
            "🤖 EA executing...",
            TELEGRAM_DISCLAIMER,
        ])

    return "\n".join([
        f"{side_icon} <b>ALPHA BUFFALO</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Asset    : <b>{_clean_text(symbol)}</b>",
        f"📊 Type     : {side_icon} {_clean_text(side_label)}",
        f"🎯 Entry    : ~{entry:,.2f}",
        f"🛡️ SL Zone  : {_price_zone(sl, direction)}",
        f"🎯 TP1      : {tp1:,.1f}  (M15 ~30min)",
        f"🎯 TP2      : {tp2:,.1f}  (H1  ~2hr)",
        f"⏰ {_clean_text(_format_public_trade_time(timestamp))}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "✅ EA Executing",
        TELEGRAM_DISCLAIMER,
    ])


def _telegram_market_is_open(
    payload: Dict | None = None,
    now: datetime | None = None,
) -> bool:
    """Fail closed for weekend/holiday/intraday closure or a CLOSED payload."""
    payload = payload or {}
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    payload_session = str(
        ea.get("session") or _deep_get(signal, ["gates", "session"], "") or ""
    ).upper()

    return telegram_market_is_open(payload_session=payload_session, now=now)


def send_telegram_message(text: str, *, test_mode: bool = False) -> bool:
    if test_mode and not str(text).startswith("🧪 <b>TEST"):
        return False
    attempted_at = datetime.now(timezone.utc).isoformat()
    with LAST_TELEGRAM_LOCK:
        TELEGRAM_DELIVERY_STATUS["last_attempt_at"] = attempted_at
        TELEGRAM_DELIVERY_STATUS["last_error"] = ""
    if not test_mode and not _telegram_market_is_open():
        with LAST_TELEGRAM_LOCK:
            TELEGRAM_DELIVERY_STATUS["last_error"] = "MARKET_CLOSED"
        return False
    if not _telegram_enabled():
        disabled_reason = (
            "TELEGRAM_DISABLED:"
            f"token_set={bool(TELEGRAM_TOKEN)}:chat_ids={len(TELEGRAM_CHAT_IDS)}"
        )
        with LAST_TELEGRAM_LOCK:
            TELEGRAM_DELIVERY_STATUS["last_error"] = disabled_reason
        print(
            "AlphaBuffalo Telegram disabled | "
            f"token_set={bool(TELEGRAM_TOKEN)} chat_ids={len(TELEGRAM_CHAT_IDS)}",
            flush=True,
        )
        return False

    text = ensure_telegram_disclaimer(text)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = False
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            response = guarded_telegram_post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=TELEGRAM_TIMEOUT_SECONDS,
                allow_closed_test=test_mode,
            )
            if response is None:
                with LAST_TELEGRAM_LOCK:
                    TELEGRAM_DELIVERY_STATUS["last_error"] = "TELEGRAM_GUARD_BLOCKED"
                return False
            if response.status_code == 200:
                ok = True
            else:
                error = f"HTTP_{response.status_code}:{response.text[:160]}"
                with LAST_TELEGRAM_LOCK:
                    TELEGRAM_DELIVERY_STATUS["last_error"] = error
                print(f"AlphaBuffalo Telegram send failed | chat_id={chat_id} status={response.status_code} body={response.text[:160]}", flush=True)
        except Exception as exc:
            with LAST_TELEGRAM_LOCK:
                TELEGRAM_DELIVERY_STATUS["last_error"] = f"{type(exc).__name__}:{exc}"
            print(f"AlphaBuffalo Telegram send error | chat_id={chat_id} {type(exc).__name__}: {exc}", flush=True)
    if ok:
        with LAST_TELEGRAM_LOCK:
            TELEGRAM_DELIVERY_STATUS["last_success_at"] = datetime.now(timezone.utc).isoformat()
            TELEGRAM_DELIVERY_STATUS["last_error"] = ""
    return ok



def _deep_get(data: Dict, path: list[str], default=None):
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur in (None, "") else cur


def _trend_update_key(payload: Dict) -> str:
    """Match Clean-V5: notify only when session or a confirmed TF trend changes."""
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    price_action = blueprint.get("price_action", {}) or {}
    session = str(
        ea.get("session")
        or _deep_get(signal, ["gates", "session"], "")
        or SessionClock().get().session
    ).strip().upper()
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    bias = _trend_bias_label(signal)
    states = []
    for key, fallback in (
        ("m15_phase", ""),
        ("h1_phase", blueprint.get("trend_h1", "")),
        ("h4_phase", blueprint.get("trend_h4", "")),
    ):
        raw = price_action.get(key) or fallback
        states.append(_public_trend_state(raw, bias))
    return "|".join([str(symbol).upper(), session or "UNKNOWN", *states])


def _load_last_trend_update_key() -> str:
    """Restore the last delivered session so a process restart does not resend it."""
    global LAST_TELEGRAM_TREND_UPDATE_KEY, LAST_TELEGRAM_TREND_STATE_LOADED
    if LAST_TELEGRAM_TREND_STATE_LOADED:
        return LAST_TELEGRAM_TREND_UPDATE_KEY

    LAST_TELEGRAM_TREND_STATE_LOADED = True
    if not TELEGRAM_TREND_STATE_FILE:
        return LAST_TELEGRAM_TREND_UPDATE_KEY
    try:
        data = json.loads(Path(TELEGRAM_TREND_STATE_FILE).read_text(encoding="utf-8"))
        LAST_TELEGRAM_TREND_UPDATE_KEY = str(data.get("last_trend_update_key") or "")
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    return LAST_TELEGRAM_TREND_UPDATE_KEY


def _persist_last_trend_update_key(key: str) -> None:
    if not TELEGRAM_TREND_STATE_FILE:
        return
    try:
        path = Path(TELEGRAM_TREND_STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "last_trend_update_key": key,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        print(
            "AlphaBuffalo trend state persistence warning | "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


def _trend_zone_label(signal: Dict, ea: Dict) -> str:
    engine = signal.get("engine_v4", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    reason = str(ea.get("reason") or "")

    if _truthy(engine.get("In_Pine_PRZ_Support")) or _truthy(engine.get("V4_Buy_Setup")) or _truthy(engine.get("V4_Block_Sell_At_Lower")):
        return "PRZ Support / BB Lower"
    if _truthy(engine.get("In_Pine_PRZ_Resistance")) or _truthy(engine.get("V4_Sell_Setup")) or _truthy(engine.get("V4_Block_Buy_At_Upper")):
        return "PRZ Resistance / BB Upper"

    prz_state = _deep_get(blueprint, ["prz_layers", "state"], "") or blueprint.get("prz_state") or ""
    if str(prz_state).upper() == "ACTIVE" or "prz=ACTIVE" in reason:
        return "PRZ Active"
    if str(prz_state):
        return str(prz_state)
    return "No Man's Land"


def _zone_range(low: float, high: float) -> str:
    if low > 0 and high > 0:
        lo, hi = sorted([low, high])
        return f"{lo:,.1f} - {hi:,.1f}"
    return "Waiting"


def _public_zone_line(signal: Dict) -> str:
    engine = signal.get("engine_v4", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    htf_prz = _deep_get(blueprint, ["prz_layers", "htf"], {}) or {}
    resist = _zone_range(
        _safe_float(
            engine.get("prz_resistance_low")
            or engine.get("Pine_PRZ_Resistance_Low")
            or htf_prz.get("resistance_low")
        ),
        _safe_float(
            engine.get("prz_resistance_high")
            or engine.get("Pine_PRZ_Resistance_High")
            or htf_prz.get("resistance_high")
        ),
    )
    support = _zone_range(
        _safe_float(
            engine.get("prz_support_low")
            or engine.get("Pine_PRZ_Support_Low")
            or htf_prz.get("support_low")
        ),
        _safe_float(
            engine.get("prz_support_high")
            or engine.get("Pine_PRZ_Support_High")
            or htf_prz.get("support_high")
        ),
    )
    return f"Resist = {resist}     Support = {support}"


def _trend_setup_label(signal: Dict, ea: Dict) -> str:
    """Return only an executable setup, never a location/candidate direction.

    The Pine monitor is watch-only.  A blocked BUY candidate at Demand PRZ may
    still leave ``ea.direction=BUY`` in diagnostics, so direction is public only
    when the adapter is actually ready to OPEN it.
    """
    action = str(ea.get("action") or "WAIT").upper()
    state = str(ea.get("execution_state") or "").upper()
    direction = str(ea.get("direction") or "").upper()
    if action == "OPEN" and state in {"READY", "EXECUTING", "OPEN"} and direction in {"BUY", "SELL"}:
        return direction
    return "WAIT"


def _direction_from_text(value) -> str:
    raw = str(value or "").upper().replace("_", " ")
    if any(token in raw for token in ("SELL", "DOWN", "BEAR")):
        return "SELL"
    if any(token in raw for token in ("BUY", "UP", "BULL")):
        return "BUY"
    return "NONE"


def _trend_bias_label(signal: Dict) -> str:
    """Resolve a read-only directional bias from confirmed HA and MTF state."""
    blueprint = signal.get("blueprint", {}) or {}
    price_action = blueprint.get("price_action", {}) or {}

    buy_score = 0
    sell_score = 0
    for value in (
        price_action.get("m15_phase"),
        price_action.get("h1_phase"),
        price_action.get("h4_phase"),
        price_action.get("m15_delta"),
        price_action.get("h1_delta"),
        price_action.get("h4_delta"),
        blueprint.get("trend_h1"),
        blueprint.get("trend_h4"),
    ):
        side = _direction_from_text(value)
        buy_score += int(side == "BUY")
        sell_score += int(side == "SELL")

    # H1 HA is the asymmetric entry source used by Pine for SELL, so it gets
    # more weight than a lower-timeframe candidate or PRZ location label.
    if _truthy(price_action.get("ha_h1_bearish")):
        sell_score += 3
    if _truthy(price_action.get("ha_h1_bullish")):
        buy_score += 3
    if _truthy(price_action.get("ha_m15_bearish")):
        sell_score += 1
    if _truthy(price_action.get("ha_m15_bullish")):
        buy_score += 1

    watch_side = _direction_from_text(price_action.get("watch_bias"))
    buy_score += int(watch_side == "BUY")
    sell_score += int(watch_side == "SELL")

    if sell_score >= 3 and sell_score > buy_score:
        return "SELL"
    if buy_score >= 3 and buy_score > sell_score:
        return "BUY"
    return "NEUTRAL"


def _trend_location_label(signal: Dict) -> str:
    engine = signal.get("engine_v4", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    current_price = _safe_float(blueprint.get("current_price"))
    htf_prz = _deep_get(blueprint, ["prz_layers", "htf"], {}) or {}

    in_support = (
        _truthy(engine.get("In_Pine_PRZ_Support"))
        or _truthy(engine.get("V4_Block_Sell_At_Lower"))
    )
    in_resistance = (
        _truthy(engine.get("In_Pine_PRZ_Resistance"))
        or _truthy(engine.get("V4_Block_Buy_At_Upper"))
    )
    support_low = _safe_float(htf_prz.get("support_low"))
    support_high = _safe_float(htf_prz.get("support_high"))
    resistance_low = _safe_float(htf_prz.get("resistance_low"))
    resistance_high = _safe_float(htf_prz.get("resistance_high"))
    if current_price > 0 and support_low > 0 and support_high > 0:
        lo, hi = sorted((support_low, support_high))
        in_support = in_support or lo <= current_price <= hi
    if current_price > 0 and resistance_low > 0 and resistance_high > 0:
        lo, hi = sorted((resistance_low, resistance_high))
        in_resistance = in_resistance or lo <= current_price <= hi

    if in_support and not in_resistance:
        return "DEMAND PRZ"
    if in_resistance and not in_support:
        return "SUPPLY PRZ"
    if in_support and in_resistance:
        return "OVERLAP / WAIT"
    return "MID / WAIT LOCATION"


def _public_setup_label(setup: str) -> str:
    setup = str(setup or "").upper()
    if setup.startswith("BUY"):
        return "🟢 BUY"
    if setup.startswith("SELL"):
        return "🔴 SELL"
    return "🟡 WAIT"


def _public_bias_label(bias: str) -> str:
    bias = str(bias or "NEUTRAL").upper()
    if bias == "BUY":
        return "🟢 BUY"
    if bias == "SELL":
        return "🔴 SELL"
    return "🟡 NEUTRAL"


def _public_watch_label(setup: str, bias: str, location: str) -> str:
    setup = str(setup or "").upper()
    if setup.startswith("BUY"):
        return "CONFIRMED 🟢 BUY"
    if setup.startswith("SELL"):
        return "CONFIRMED 🔴 SELL"

    bias = str(bias or "NEUTRAL").upper()
    location = str(location or "").upper()
    if bias == "SELL" and location == "DEMAND PRZ":
        return "SELL BIAS — DEMAND TARGET / BUY UNCONFIRMED"
    if bias == "BUY" and location == "SUPPLY PRZ":
        return "BUY BIAS — SUPPLY TARGET / SELL UNCONFIRMED"
    if bias == "SELL":
        return "WAIT LOCATION — 🔴 SELL BIAS"
    if bias == "BUY":
        return "WAIT LOCATION — 🟢 BUY BIAS"
    return "WAIT LOCATION — NEUTRAL"


def _trend_vsa_label(signal: Dict, ea: Dict) -> str:
    engine = signal.get("engine_v4", {}) or {}
    if _truthy(engine.get("VSA_Buy_Wins")):
        return "BUY > SELL"
    if _truthy(engine.get("VSA_Sell_Wins")):
        return "SELL > BUY"
    gate = ea.get("vsa_gate") or ("PASS" if ea.get("vsa_gate_ok") else "WAIT")
    return str(gate)


def _trend_line(signal: Dict, key: str, fallback: str = "-") -> str:
    blueprint = signal.get("blueprint", {}) or {}
    price_action = blueprint.get("price_action", {}) or {}
    value = price_action.get(key) or blueprint.get(key) or fallback
    return str(value).replace("_", " ")


def _public_trend_state(value, fallback_direction: str = "NONE") -> str:
    raw = str(value or "").upper().replace("_", " ")
    direction = _direction_from_text(raw)
    if direction == "NONE":
        direction = str(fallback_direction or "NONE").upper()
    phase = "Pullback" if "PULLBACK" in raw or "RETRACE" in raw else "Impulse"
    if any(token in raw for token in ("RANGE", "SIDEWAY", "NEUTRAL", "WAIT")):
        return "Range ⚪"
    if direction == "BUY":
        return f"{phase} 🟢"
    if direction == "SELL":
        return f"{phase} 🔴"
    return "Range ⚪"


def format_telegram_trend_update(payload: Dict) -> str:
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    blueprint = signal.get("blueprint", {}) or {}

    price = _safe_float(
        blueprint.get("current_price")
        or _deep_get(blueprint, ["price_action", "current_price"])
        or ea.get("entry")
    )
    session = ea.get("session") or _deep_get(signal, ["gates", "session"], "-")
    bias = _trend_bias_label(signal)
    watch_icon = "🟢" if bias == "BUY" else "🔴" if bias == "SELL" else "⚪"
    watch_side = "B" if bias == "BUY" else "S" if bias == "SELL" else "WAIT"
    m15 = _public_trend_state(_trend_line(signal, "m15_phase", ""), bias)
    h1 = _public_trend_state(
        _trend_line(signal, "h1_phase", blueprint.get("trend_h1", "")), bias
    )
    h4 = _public_trend_state(
        _trend_line(signal, "h4_phase", blueprint.get("trend_h4", "")), bias
    )

    return "\n".join([
        f"📊 <b>{_clean_text(symbol)} TREND UPDATE</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 Session : {_clean_text(session)}",
        f"💰 Price   : {price:,.2f}",
        "",
        f"{'📈' if '🟢' in m15 else '📉' if '🔴' in m15 else '➡️'} M15  : {_clean_text(m15)}",
        f"{'📈' if '🟢' in h1 else '📉' if '🔴' in h1 else '➡️'} H1   : {_clean_text(h1)}",
        f"{'📈' if '🟢' in h4 else '📉' if '🔴' in h4 else '➡️'} H4   : {_clean_text(h4)}",
        "",
        f"👀 Watch for {watch_icon} {watch_side} Setup...",
        "━━━━━━━━━━━━━━━━━━━━━",
        TELEGRAM_DISCLAIMER,
    ])


def _bos_confirmation_context(payload: Dict) -> tuple[bool, str, float, int]:
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    harmonic = blueprint.get("harmonic", {}) or {}
    eligible = bool(
        ea.get("bos_confirmed")
        or signal.get("bos_confirmed")
        or blueprint.get("bos_confirmed")
        or blueprint.get("harmonic_bos_eligible")
        or harmonic.get("bos_eligible")
    )
    direction = str(
        blueprint.get("harmonic_bos_direction")
        or harmonic.get("bos_direction")
        or ea.get("direction")
        or "NONE"
    ).upper()
    if direction not in {"BUY", "SELL"}:
        direction = _trend_bias_label(signal)
    price = _safe_float(
        blueprint.get("current_price")
        or _deep_get(blueprint, ["price_action", "current_price"], 0)
        or ea.get("entry")
    )
    score = int(_safe_float(ea.get("score") or signal.get("score") or _deep_get(signal, ["decision", "score"], 0)))
    return eligible and direction in {"BUY", "SELL"}, direction, price, score


def format_telegram_confirmation(payload: Dict) -> str:
    _, direction, price, score = _bos_confirmation_context(payload)
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    icon, label = _public_side(direction)
    score_icon = "📈" if direction == "BUY" else "📉"
    return "\n".join([
        "🎯 <b>BOS CONFIRMED — ENTRY READY</b>",
        "━━━━━━━━━━━━━━━━━",
        f"{icon} {label[0]}  {_clean_text(symbol)}",
        f"💰 BOS @ {price:,.2f}",
        f"{score_icon} Score: {score}/10",
        "⚡ Signal กำลังประมวลผล...",
        TELEGRAM_DISCLAIMER,
    ])


def build_telegram_test_messages(direction: str = "SELL") -> list[str]:
    """Build the four public templates without creating an EA command."""
    direction = str(direction or "SELL").upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("TEST_DIRECTION_MUST_BE_BUY_OR_SELL")
    is_buy = direction == "BUY"
    entry = 3992.32
    sl = 3979.70 if is_buy else 4004.99
    tp1 = 3998.60 if is_buy else 3986.00
    tp2 = 4005.00 if is_buy else 3979.70
    phase = "IMPULSE UP" if is_buy else "IMPULSE DOWN"
    timestamp = datetime.now(timezone.utc).isoformat()
    base = {
        "symbol": PUBLIC_SYMBOL_DEFAULT,
        "signal": {
            "timestamp": timestamp,
            "score": 5,
            "gates": {"session": "NY"},
            "blueprint": {
                "current_price": 3983.98,
                "trend_h1": "UP" if is_buy else "DOWN",
                "trend_h4": "UP" if is_buy else "DOWN",
                "harmonic_bos_eligible": True,
                "harmonic_bos_direction": direction,
                "harmonic_bos_sources": ["M15"],
                "price_action": {
                    "m15_phase": phase,
                    "h1_phase": phase,
                    "h4_phase": phase,
                },
            },
        },
        "ea": {
            "action": "OPEN",
            "execution_state": "READY",
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp_final": tp2,
            "score": 5,
            "session": "NY",
        },
    }

    trend = format_telegram_trend_update(base)
    short_payload = json.loads(json.dumps(base))
    short_payload["ea"]["telegram_tp_mode"] = "SINGLE_TP"
    short = format_telegram_signal(short_payload)
    runner_payload = json.loads(json.dumps(base))
    runner_payload["ea"]["telegram_tp_mode"] = "TP1_TP2"
    runner = format_telegram_signal(runner_payload)
    confirm_payload = json.loads(json.dumps(base))
    confirm_payload["ea"]["action"] = "WAIT"
    confirm_payload["ea"]["direction"] = "NONE"
    confirm = format_telegram_confirmation(confirm_payload)

    labels = (
        "TREND UPDATE",
        "SHORT TRADE — TP เดียว",
        "PRZ/BOS RUNNER — TP1/TP2",
        "WAIT CONFIRMATION",
    )
    bodies = (trend, short, runner, confirm)
    return [
        "\n".join([
            f"🧪 <b>TEST {index}/4 — {label}</b>",
            "⚠️ ทดสอบระบบเท่านั้น ไม่ใช่สัญญาณจริง",
            "",
            body,
        ])
        for index, (label, body) in enumerate(zip(labels, bodies), start=1)
    ]


def maybe_broadcast_confirmation(payload: Dict) -> bool:
    """Send one WAIT-CF message per active confirmed BOS, never on every poll."""
    global LAST_TELEGRAM_CONFIRM_KEY
    if not TELEGRAM_NOTIFY_WAIT or not _telegram_market_is_open(payload) or not _telegram_enabled():
        return False
    ea = payload.get("ea", {}) or {}
    eligible, direction, price, _ = _bos_confirmation_context(payload)
    if not eligible or str(ea.get("action") or "WAIT").upper() == "OPEN":
        if not eligible:
            with LAST_TELEGRAM_LOCK:
                LAST_TELEGRAM_CONFIRM_KEY = ""
        return False
    signal = payload.get("signal", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    sources = blueprint.get("harmonic_bos_sources") or _deep_get(
        blueprint, ["harmonic", "bos_sources"], []
    )
    key = "|".join([
        str(payload.get("symbol") or PUBLIC_SYMBOL_DEFAULT).upper(),
        direction,
        ",".join(sources) if isinstance(sources, list) else str(sources),
    ])
    with LAST_TELEGRAM_LOCK:
        if key == LAST_TELEGRAM_CONFIRM_KEY:
            return False
        previous = LAST_TELEGRAM_CONFIRM_KEY
        LAST_TELEGRAM_CONFIRM_KEY = key
    sent = send_telegram_message(format_telegram_confirmation(payload))
    if not sent:
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_CONFIRM_KEY == key:
                LAST_TELEGRAM_CONFIRM_KEY = previous
    return sent


def maybe_broadcast_trend_update(payload: Dict) -> bool:
    """Send once when the session changes, matching Clean-V5 behavior."""
    global LAST_TELEGRAM_TREND_UPDATE_KEY
    if not _telegram_market_is_open(payload):
        return False

    if not TELEGRAM_NOTIFY_TREND_UPDATE:
        return False
    if not _telegram_enabled():
        return False

    key = _trend_update_key(payload)
    with LAST_TELEGRAM_LOCK:
        _load_last_trend_update_key()
        if key == LAST_TELEGRAM_TREND_UPDATE_KEY:
            return False
        previous_key = LAST_TELEGRAM_TREND_UPDATE_KEY
        # Reserve this transition before the network call so parallel loops
        # cannot publish the same session snapshot twice.
        LAST_TELEGRAM_TREND_UPDATE_KEY = key

    sent = send_telegram_message(format_telegram_trend_update(payload))
    if sent:
        with LAST_TELEGRAM_LOCK:
            _persist_last_trend_update_key(key)
    else:
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_TREND_UPDATE_KEY == key:
                LAST_TELEGRAM_TREND_UPDATE_KEY = previous_key
    return sent

def maybe_broadcast_signal(payload: Dict) -> bool:
    """Broadcast an accepted OPEN once; lifecycle/fill/debug events stay silent."""
    global LAST_TELEGRAM_SIGNAL_KEY

    if not _telegram_market_is_open(payload):
        return False

    ea = payload.get("ea", {}) or {}
    signal = payload.get("signal", {}) or {}
    action = str(ea.get("action", "WAIT")).upper()

    if action != "OPEN":
        return False

    if not _telegram_open_signal_is_fresh(payload):
        print(
            "AlphaBuffalo Telegram stale OPEN suppressed | "
            f"signal_id={ea.get('signal_id')}",
            flush=True,
        )
        return False
    rr = _safe_float(ea.get("rr"))
    if rr < TELEGRAM_MIN_RR:
        return False

    if not bool(ea.get("directional_levels_ok")):
        return False
    if not bool(ea.get("levels_ready")):
        return False
    if not bool(ea.get("rr_ok")):
        return False
    if not bool(ea.get("setup_ok", True)):
        return False
    if not bool(ea.get("zone_ok", True)):
        return False
    if not bool(ea.get("vsa_gate_ok", True)):
        return False

    signal_key = _telegram_signal_key(payload)
    with LAST_TELEGRAM_LOCK:
        if signal_key and signal_key == LAST_TELEGRAM_SIGNAL_KEY:
            return False
        previous_key = LAST_TELEGRAM_SIGNAL_KEY
        LAST_TELEGRAM_SIGNAL_KEY = signal_key

    sent = send_telegram_message(format_telegram_signal(payload))
    if not sent:
        # Do not permanently deduplicate a delivery that never reached Telegram.
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_SIGNAL_KEY == signal_key:
                LAST_TELEGRAM_SIGNAL_KEY = previous_key
    return sent


def _df_with_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize TwelveData dataframe for engine_v4 without changing fetch contract."""
    out = df.copy()

    if isinstance(out.index, pd.DatetimeIndex):
        out = out.sort_index()
        return out

    if "datetime" not in out.columns:
        raise ValueError("ENGINE_V4_REQUIRES_DATETIME_COLUMN")

    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).set_index("datetime").sort_index()

    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")

    return out


def _iso_timestamp(value) -> str:
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value or "")
    except Exception:
        return ""



def _log_engine_v4_debug(message: str) -> None:
    """Best-effort runtime trace for engine_v4 selection. Never blocks trading loop."""
    try:
        print(f"AlphaBuffalo engine_v4 | {message}", flush=True)
    except Exception:
        pass



def _ensure_engine_v4_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return OHLC dataframe with DatetimeIndex for engine_v4/session logic."""
    if df is None or df.empty:
        return df

    out = df.copy()

    if isinstance(out.index, pd.DatetimeIndex):
        out = out.sort_index()
        return out

    dt_series = None
    for col in ("datetime", "timestamp", "time", "date", "date_time"):
        if col in out.columns:
            dt_series = pd.to_datetime(out[col], errors="coerce", utc=True)
            break

    if dt_series is None:
        parsed_index = pd.to_datetime(out.index, errors="coerce", utc=True)
        if not pd.isna(parsed_index).all():
            dt_series = parsed_index

    if dt_series is None or pd.isna(dt_series).all():
        # Last-resort runtime fallback: preserve row order and let engine_v4 run.
        # Real TwelveData payloads should normally have datetime/timestamp columns.
        dt_series = pd.date_range(
            end=pd.Timestamp.now(tz="UTC").floor("15min"),
            periods=len(out),
            freq="15min",
        )

    out.index = pd.DatetimeIndex(dt_series)
    out = out[~out.index.isna()].sort_index()
    out.index.name = "datetime"
    return out

def _run_engine_v4_baseline(
    df_15m: pd.DataFrame,
    symbol: str = PUBLIC_SYMBOL_DEFAULT,
    blueprint=None,
) -> Dict | None:

    if add_indicators is None or SignalRouter is None or FinalGate is None or BuySignalEngine is None or SellSignalEngine is None:
        print(
            "AlphaBuffalo engine_v4 | none reason=IMPORT_ERROR "
            f"type={type(ENGINE_V4_IMPORT_ERROR).__name__ if ENGINE_V4_IMPORT_ERROR else 'Unknown'} "
            f"error={ENGINE_V4_IMPORT_ERROR}"
        )
        return None
    """Run engine_v4 and log why it did/did not produce a V4 entry."""
    ENGINE_V4_TRACE_FIELDS = [
        "close", "BB_Lower", "BB_Upper", "Near_BB_Lower", "Near_BB_Upper",
        "In_Pine_PRZ_Support", "In_Pine_PRZ_Resistance",
        "BB_PRZ_Support_Confluence", "BB_PRZ_Resistance_Confluence",
        "Pine_PA_Bull_Confirmed", "Pine_PA_Bear_Confirmed",
        "VSA_Buy_Pressure", "VSA_Sell_Pressure", "VSA_Buy_Wins", "VSA_Sell_Wins",
        "In_Session_Kivanc_Buy_Zone", "In_Session_Kivanc_Sell_Zone",
        "Deep_Buy_PRZ_Context", "Deep_Sell_PRZ_Context",
        "Deep_Buy_Wall_Candidate", "Deep_Sell_Wall_Candidate",
        "Deep_Buy_Reclaim_Active", "Deep_Sell_Reclaim_Active",
        "Deep_Buy_Reclaim_Trigger", "Deep_Sell_Reclaim_Trigger", "Kivanc_Scenario_State",
        "V4_Buy_Entry_Zone", "V4_Sell_Entry_Zone", "V4_Buy_Setup", "V4_Sell_Setup",
        "V4_Block_Sell_At_Lower", "V4_Block_Buy_At_Upper", "CHoCH_Bull", "CHoCH_Bear",
    ]
    try:
        if df_15m is None or getattr(df_15m, "empty", True):
            _log_engine_v4_debug("none reason=EMPTY_DF")
            return None

        df = _ensure_engine_v4_datetime_index(df_15m)
        df = add_indicators(df)
        session_clock = SessionClock()
        risk_permissions = execution_lifecycle.risk_permissions(symbol)
        harmonic_context = _harmonic_gate_context(blueprint) if blueprint else None
        if REQUIRE_HARMONIC_BIAS and evaluate_harmonic_bias is not None:
            harmonic_direction = str(
                (harmonic_context or {}).get("direction", "NONE")
            ).upper()
            harmonic_state = str(
                (harmonic_context or {}).get("state", "NONE")
            ).upper()
            approach_direction = str(
                (harmonic_context or {}).get("approach_direction", "NONE")
            ).upper()
            requested_direction = (
                approach_direction
                if harmonic_state == "FORMING" and approach_direction in {"BUY", "SELL"}
                else
                harmonic_direction
                if harmonic_direction in {"BUY", "SELL"}
                else "BUY"
            )
            bias_gate = evaluate_harmonic_bias(
                requested_direction,
                harmonic_context,
                require_harmonic=True,
            )
            if not bias_gate.allowed:
                blocked_direction = (
                    harmonic_direction
                    if harmonic_direction in {"BUY", "SELL"}
                    else None
                )
                return {
                    "status": BLOCKED,
                    "direction": blocked_direction,
                    "reason": bias_gate.reason,
                    "harmonic_bias": bias_gate.to_dict(),
                }
        routed = SignalRouter(
            clock=session_clock,
            gate=FinalGate(session_clock),
            buy_engine=BuySignalEngine(),
            sell_engine=SellSignalEngine(),
        ).process(
            df,
            **risk_permissions,
            harmonic_context=harmonic_context,
            require_harmonic=REQUIRE_HARMONIC_BIAS,
        )
        signal = routed[0] if routed else None

        tail = df.tail(int(os.getenv("ENGINE_V4_LOOKBACK_BARS", "6")))
        last = tail.iloc[-1]
        flags = {}
        for field in ENGINE_V4_TRACE_FIELDS:
            if field in last:
                value = last.get(field)
                try:
                    if hasattr(value, "item"):
                        value = value.item()
                except Exception:
                    pass
                flags[field] = value

        counts = {}
        for field in [
            "BB_PRZ_Support_Confluence", "BB_PRZ_Resistance_Confluence",
            "V4_Buy_Entry_Zone", "V4_Sell_Entry_Zone", "V4_Buy_Setup", "V4_Sell_Setup",
            "Pine_PA_Bull_Confirmed", "Pine_PA_Bear_Confirmed", "VSA_Buy_Wins", "VSA_Sell_Wins",
        ]:
            if field in tail:
                try:
                    counts[field] = int(tail[field].fillna(False).astype(bool).sum())
                except Exception:
                    counts[field] = "ERR"

        if signal:
            _log_engine_v4_debug(
                "selected "
                f"direction={signal.get('direction')} entry_mode={signal.get('entry_mode')} "
                f"setup={signal.get('setup_state')} journey={signal.get('journey_state')} "
                f"age={signal.get('selected_age_bars')} bb_prz={signal.get('bb_prz_confluence')} "
                f"counts={counts} last={flags}"
            )
        else:
            _log_engine_v4_debug(f"none reason=NO_RECENT_V4_SETUP counts={counts} last={flags}")
        return signal
    except Exception as exc:
        _log_engine_v4_debug(f"none reason=EXCEPTION type={type(exc).__name__} error={exc}")
        return None


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

    for _key in (
        "scenario_state", "journey_state", "trade_management", "break_prediction",
        "bos_confirmed", "vsa_gate", "vsa_pressure_delta", "checkpoint_price",
        "approach_break_zone", "setup_state",
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

    if direction == "BUY":
        management.setdefault("entry_source", "VSA_DEMAND_WALL_REACTION")
        management.setdefault("entry_confirmation", "REACTION_CONFIRM_OR_BULLISH_PINBAR")
        management.setdefault("visual_sl_source", "VSA_WALL_LOW")
        management.setdefault("vsa_wall_low", _safe_float(engine_v4.get("vsa_wall_low")))
        management.setdefault("vsa_wall_high", _safe_float(engine_v4.get("vsa_wall_high")))
        management.setdefault("sl_rule", "BELOW_VSA_WALL_LOW")
        management.setdefault("tp_route", {
            "tp1": "UPPER_BB_CHECKPOINT",
            "tp2": "NEXT_PRZ",
            "tp3": "HARMONIC_ROUTE_OR_NEXT_HARMONIC_PRZ",
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
            "tp1": "LOWER_BB_CHECKPOINT",
            "tp2": "NEXT_PRZ",
            "tp3": "HARMONIC_ROUTE_OR_NEXT_HARMONIC_PRZ",
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
    elif trade_direction_ok and setup_ok and zone_ok and vsa_gate_ok:
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
            "vsa_gate_ok": vsa_gate_ok,
            "rr_ok": rr_ok,
            "levels_ready": levels_ready,
            "directional_levels_ok": directional_levels_ok,
        },
    }


def build_ea_payload(symbol: str, signal: Dict) -> Dict:
    """
    EA execution payload.
    Adapter-only: ไม่ตัดสินใจตลาดใหม่ ไม่คำนวณ PRZ/BOS/TP/SL เอง
    RR/setup/VSA gates only convert low-quality candidates to WAIT before EA/Telegram.
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

    rr_info = _rr_metrics(direction, entry, sl, tp_final)
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
        and vsa_gate_ok
        else "WATCH"
    )

    action = "OPEN" if execution_state == "READY" else "WAIT"

    reason = str(decision.get("reason", "") or "")
    blocked_reasons = []
    if not rr_ok and trade_direction_ok and levels_ready and directional_levels_ok:
        blocked_reasons.append(f"rr={rr_info['rr']:.2f}<min_rr={TRADE_MIN_RR:.2f}")
    if not setup_ok:
        blocked_reasons.append(f"setup={setup_info['setup_state']}")
    if not zone_ok:
        blocked_reasons.append("zone_not_confirmed")
    if not vsa_gate_ok:
        blocked_reasons.append("vsa_gate_not_confirmed")
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
        "trade_management": trade_management,
        "break_prediction": signal.get("break_prediction") or (signal.get("engine_v4", {}) or {}).get("break_prediction"),
        "bos_confirmed": bool(signal.get("bos_confirmed") or (signal.get("engine_v4", {}) or {}).get("bos_confirmed")),
        "vsa_gate": signal.get("vsa_gate") or (signal.get("engine_v4", {}) or {}).get("vsa_gate"),
        "checkpoint_price": _safe_float(signal.get("checkpoint_price") or (signal.get("engine_v4", {}) or {}).get("checkpoint_price")),

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
        "signal": signal,
        "ea": ea,
    }


def _latest_market_price(df: pd.DataFrame | None) -> float:
    if df is None or getattr(df, "empty", True) or "close" not in df:
        return 0.0
    return _safe_float(df["close"].iloc[-1])


def _attach_execution_lifecycle(
    *,
    data_symbol: str,
    public_symbol: str,
    df_15m: pd.DataFrame,
    ea: Dict,
) -> Dict:
    """Attach Python-owned fill/TP/BE/HA5 state without changing entry logic."""
    payload = dict(ea)
    payload["risk_permissions"] = execution_lifecycle.risk_permissions(public_symbol)
    position = execution_lifecycle.position(public_symbol)
    payload["position"] = position

    if not position:
        payload["management_command"] = {
            "action": "HOLD",
            "reason": "NO_ACTIVE_POSITION",
            "symbol": public_symbol,
        }
        return payload

    command = execution_lifecycle.evaluate(
        public_symbol,
        _latest_market_price(df_15m),
        fetch_management_m5(data_symbol),
    )
    payload["action"] = "WAIT"
    payload["execution_state"] = "MANAGING"
    payload["reason"] = "ACTIVE_POSITION|" + str(command.get("reason", "MANAGING"))
    payload["management_command"] = command
    lifecycle = dict(payload.get("plan_lifecycle") or {})
    lifecycle["plan_status"] = "MANAGING"
    lifecycle["ea_may_open_from_armed"] = False
    payload["plan_lifecycle"] = lifecycle
    return payload

def run_pipeline(symbol: str = SYMBOL_DEFAULT, public_symbol: str = PUBLIC_SYMBOL_DEFAULT) -> Dict:
    try:
        market_gate = _market_open_gate()
        if not market_gate["market_open"]:
            return _market_closed_payload(
                symbol=symbol,
                public_symbol=public_symbol,
                gate=market_gate,
            )

        df_4h, df_1h, df_15m = fetch_multi_tf(symbol)

        scanner = ScenarioScanner()
        blueprint = scanner.scan(df_4h, df_1h, df_15m, symbol=public_symbol)

        session_clock = SessionClock()
        session_state = session_clock.get()
        blueprint.session = session_state.session

        engine = DecisionEngine()
        decision = engine.evaluate(blueprint)

        composer = SignalComposer(session_clock=session_clock)
        signal = composer.compose(
            blueprint=blueprint,
            decision=decision,
            symbol=public_symbol,
        )

        # Production baseline overlay:
        # v12 scanner/blueprint stays intact, but proven engine_v4 BUY/SELL baseline
        # becomes the actual trade source when it produces confirmed levels.
        engine_v4_signal = _run_engine_v4_baseline(
            df_15m,
            public_symbol,
            blueprint=blueprint,
        )
        signal = _apply_engine_v4_signal(signal, engine_v4_signal)
        ea = build_ea_payload(public_symbol, signal)
        ea = _attach_execution_lifecycle(
            data_symbol=symbol,
            public_symbol=public_symbol,
            df_15m=df_15m,
            ea=ea,
        )
        return build_api_signal_response(public_symbol, signal, ea)
    except Exception as exc:
        error_signal = {
            "status": ERROR,
            "direction": None,
            "reason": f"{type(exc).__name__}: {exc}",
            "decision": {
                "action": "NONE",
                "confidence": 0.0,
                "score": 0,
                "reason": f"PIPELINE_ERROR:{type(exc).__name__}",
                "grade": "ERROR",
            },
            "gates": {"blueprint_valid": False, "session": ""},
            "blueprint": {"is_valid": False},
        }
        ea = build_ea_payload(public_symbol, error_signal)
        return build_api_signal_response(public_symbol, error_signal, ea)


def _set_latest_signal(payload: dict) -> None:
    with LATEST_SIGNAL_LOCK:
        LATEST_SIGNAL_CACHE.clear()
        LATEST_SIGNAL_CACHE.update(payload)


def _get_latest_signal() -> dict:
    with LATEST_SIGNAL_LOCK:
        return dict(LATEST_SIGNAL_CACHE)


def _publish_python_entry_command(payload: Dict) -> Dict:
    """Queue one confirmed Python OPEN for the execution-only EA."""
    if SIGNAL_SOURCE not in {"PYTHON", "HYBRID"}:
        return {"action": "HOLD", "reason": "PYTHON_SIGNAL_MODE_DISABLED"}

    ea = dict(payload.get("ea") or {})
    if ea.get("action") != "OPEN" or ea.get("execution_state") != "READY":
        return {"action": "HOLD", "reason": "NO_READY_PYTHON_SIGNAL"}

    command_payload = {
        "status": "SIGNAL",
        "source": "PYTHON",
        "strategy": "ALPHABUFF_V12_BASELINE",
        "action": "OPEN",
        "direction": ea.get("direction"),
        "symbol": ea.get("symbol") or payload.get("symbol") or PUBLIC_SYMBOL_DEFAULT,
        "signal_id": ea.get("signal_id"),
        "entry_price": ea.get("entry"),
        "exit_price": None,
        "sl_price": ea.get("sl"),
        "tp1_price": ea.get("tp1"),
        "tp2_price": ea.get("tp_final"),
        "score": ea.get("score", 0),
        "target_source": ea.get("target_source", "PYTHON_BASELINE"),
        "reason": ea.get("reason", "PYTHON_FINAL_SIGNAL"),
        "timeframe": "15",
    }
    try:
        return python_signal_bridge.ingest(command_payload)
    except PineSignalError as exc:
        print(
            "AlphaBuffalo Python command queue blocked | "
            f"signal_id={ea.get('signal_id')} reason={exc}",
            flush=True,
        )
        return {"action": "HOLD", "reason": str(exc)}


def _cloud_signal_loop() -> None:
    print(f"AlphaBuffalo cloud signal loop started | interval={SIGNAL_LOOP_INTERVAL_SECONDS}s", flush=True)
    while True:
        try:
            payload = run_pipeline()
            _set_latest_signal(payload)
            queued = _publish_python_entry_command(payload)
            maybe_broadcast_signal(payload)
            maybe_broadcast_trend_update(payload)
            maybe_broadcast_confirmation(payload)
            decision = payload.get("signal", {}).get("decision", {})
            ea = payload.get("ea", {})
            print(
                f"AlphaBuffalo cloud scan | action={decision.get('action')} "
                f"grade={decision.get('grade')} score={decision.get('score')} "
                f"ea={ea.get('action')} state={ea.get('execution_state')} "
                f"queue={queued.get('action')} queue_reason={queued.get('reason', '')}",
                flush=True,
            )
        except Exception as exc:
            print(f"AlphaBuffalo cloud scan error | {type(exc).__name__}: {exc}", flush=True)
        time.sleep(SIGNAL_LOOP_INTERVAL_SECONDS)


def _pine_monitor_payload() -> Dict:
    """Build a read-only market update without giving Python command ownership."""
    payload = run_pipeline()
    monitor = dict(payload)
    monitor["source"] = "PINE_MONITOR"
    monitor["generated_at"] = datetime.now(timezone.utc).isoformat()
    signal = dict(monitor.get("signal") or {})
    signal["timestamp"] = monitor["generated_at"]
    monitor["signal"] = signal
    ea = dict(monitor.get("ea") or {})
    candidate_direction = str(ea.get("direction") or "").upper()
    if candidate_direction in {"BUY", "SELL"}:
        ea["monitor_candidate_direction"] = candidate_direction
    ea["direction"] = "NONE"
    ea["action"] = "WAIT"
    ea["execution_state"] = "WATCH"
    ea["command_owner"] = "PINE_TRADINGVIEW"
    ea["reason"] = "PINE_MONITOR_ONLY"
    monitor["ea"] = ea
    return monitor


def _pine_monitor_loop() -> None:
    """Keep Telegram observable in Pine mode without manufacturing trades."""
    print(
        "AlphaBuffalo Pine Telegram monitor started | "
        f"interval={TELEGRAM_PINE_MONITOR_INTERVAL_SECONDS}s",
        flush=True,
    )
    while True:
        try:
            if TELEGRAM_NOTIFY_TREND_UPDATE and _telegram_market_is_open():
                monitor = _pine_monitor_payload()
                maybe_broadcast_trend_update(monitor)
                maybe_broadcast_confirmation(monitor)
        except Exception as exc:
            print(
                "AlphaBuffalo Pine Telegram monitor error | "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
        time.sleep(TELEGRAM_PINE_MONITOR_INTERVAL_SECONDS)


@app.on_event("startup")
def _start_cloud_signal_loop() -> None:
    global _SIGNAL_LOOP_STARTED
    if _SIGNAL_LOOP_STARTED:
        return
    _SIGNAL_LOOP_STARTED = True
    if SIGNAL_SOURCE == "PINE":
        print(
            "AlphaBuffalo Pine signal mode | Python trade loop disabled; "
            "Telegram monitor remains active",
            flush=True,
        )
        if TELEGRAM_PINE_MONITOR_ENABLED:
            worker = threading.Thread(
                target=_pine_monitor_loop,
                name="alpha-pine-telegram-monitor",
                daemon=True,
            )
            worker.start()
        return
    worker = threading.Thread(target=_cloud_signal_loop, name="alpha-cloud-signal-loop", daemon=True)
    worker.start()



@app.head("/")
def root_head():
    return Response(status_code=200)


@app.head("/health")
def health_head():
    return Response(status_code=200)

@app.get("/")
def root():
    return {
        "service": "Alpha Buffalo",
        "version": "v12-core",
        "status": "ok",
        "signal_source": SIGNAL_SOURCE,
    }


@app.get("/health")
def health():
    return {
        "status": "alive",
        "version": "v12-core",
        "signal_source": SIGNAL_SOURCE,
        "timestamp": time.time(),
    }


@app.get("/telegram/status")
def telegram_status(key: str = "", symbol: str = PUBLIC_SYMBOL_DEFAULT):
    """Safe observability endpoint; never exposes the bot token or license."""
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")
    public_symbol = str(symbol or PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    with LAST_TELEGRAM_LOCK:
        delivery = dict(TELEGRAM_DELIVERY_STATUS)
    pending = (
        pine_signal_bridge.pending_command(public_symbol)
        if SIGNAL_SOURCE == "PINE"
        else python_signal_bridge.pending_command(public_symbol)
    )
    return {
        "status": "ok",
        "signal_source": SIGNAL_SOURCE,
        "telegram_enabled": _telegram_enabled(),
        "chat_ids_count": len(TELEGRAM_CHAT_IDS),
        "pine_monitor_enabled": TELEGRAM_PINE_MONITOR_ENABLED,
        "trend_update_enabled": TELEGRAM_NOTIFY_TREND_UPDATE,
        "market_open": _telegram_market_is_open(),
        "last_delivery": delivery,
        "pending_action": pending.get("action", "HOLD"),
        "pending_reason": pending.get("reason", "NO_PENDING_COMMAND"),
    }


@app.post("/telegram/test")
async def telegram_test(request: Request):
    """Send four unmistakable TEST templates; never touches EA command state."""
    body = await request.json()
    if not verify_license(str(body.get("key") or "")):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")
    direction = str(body.get("direction") or "SELL").upper()
    try:
        messages = build_telegram_test_messages(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    results = [send_telegram_message(message, test_mode=True) for message in messages]
    if not all(results):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TELEGRAM_TEST_DELIVERY_INCOMPLETE",
                "sent_count": sum(results),
                "expected_count": len(results),
            },
        )
    return {
        "status": "sent",
        "test_only": True,
        "direction": direction,
        "sent_count": len(results),
        "ea_command_created": False,
    }


@app.get("/signal/latest")
def signal_latest(key: str = "", symbol: str = SYMBOL_DEFAULT):
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = symbol.replace("/", "")
    if SIGNAL_SOURCE == "PINE":
        cached = _get_latest_signal()
        if cached and cached.get("symbol") == public_symbol:
            return cached
        return {
            **create_signal(status=NO_SIGNAL, reason="WAITING_FOR_PINE_SIGNAL"),
            "symbol": public_symbol,
            "source": "PINE",
            "ea": {
                "action": "WAIT",
                "execution_state": "WATCH",
                "direction": "NONE",
                "ea_role": "EXECUTION_ONLY",
                "ea_execute_only": True,
            },
        }

    market_gate = _market_open_gate()
    if not market_gate["market_open"]:
        payload = _market_closed_payload(symbol=symbol, public_symbol=public_symbol, gate=market_gate)
        _set_latest_signal(payload)
        return payload

    cached = _get_latest_signal()
    if cached and cached.get("symbol") == public_symbol:
        return cached

    payload = run_pipeline(symbol=symbol, public_symbol=public_symbol)
    _set_latest_signal(payload)
    _publish_python_entry_command(payload)
    return payload


@app.get("/execution/state")
def execution_state(key: str = "", symbol: str = PUBLIC_SYMBOL_DEFAULT):
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")
    public_symbol = symbol.replace("/", "")
    return {
        "status": "ok",
        "symbol": public_symbol,
        "position": execution_lifecycle.position(public_symbol),
        "entry_command": (
            python_signal_bridge.pending_command(public_symbol)
            if SIGNAL_SOURCE in {"PYTHON", "HYBRID"}
            else pine_signal_bridge.pending_command(public_symbol)
        ),
        "pending_command": execution_lifecycle.pending_command(public_symbol),
        "risk_permissions": execution_lifecycle.risk_permissions(public_symbol),
    }


@app.post("/execution/fill")
async def execution_fill(request: Request):
    """EA confirms a fill while the configured source keeps command ownership."""
    body = await request.json()
    if not verify_license(str(body.get("key", ""))):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = str(body.get("symbol") or PUBLIC_SYMBOL_DEFAULT).replace("/", "")
    signal_id = str(body.get("signal_id") or "")
    ticket = str(body.get("ticket") or "")
    fill_price = _safe_float(body.get("fill_price"))
    if not ticket or fill_price <= 0:
        raise HTTPException(status_code=400, detail="TICKET_AND_FILL_PRICE_REQUIRED")

    cached = _get_latest_signal()
    plan = cached.get("ea", {}) if cached.get("symbol") == public_symbol else {}

    if SIGNAL_SOURCE in {"PINE", "HYBRID"}:
        durable_pine_plan = pine_signal_bridge.pending_command(public_symbol)
        if (
            durable_pine_plan.get("action") == "OPEN"
            and str(durable_pine_plan.get("signal_id") or "") == signal_id
        ):
            plan = {
                "signal_id": durable_pine_plan.get("signal_id"),
                "action": "OPEN",
                "execution_state": "READY",
                "direction": durable_pine_plan.get("direction"),
                "sl": durable_pine_plan.get("sl"),
                "tp1": durable_pine_plan.get("tp1"),
                "tp_final": durable_pine_plan.get("tp_final"),
            }

    if SIGNAL_SOURCE in {"PYTHON", "HYBRID"} and (
        plan.get("action") != "OPEN"
        or str(plan.get("signal_id") or "") != signal_id
    ):
        durable_python_plan = python_signal_bridge.pending_command(public_symbol)
        if (
            durable_python_plan.get("action") == "OPEN"
            and str(durable_python_plan.get("signal_id") or "") == signal_id
        ):
            plan = {
                "signal_id": durable_python_plan.get("signal_id"),
                "action": "OPEN",
                "execution_state": "READY",
                "direction": durable_python_plan.get("direction"),
                "sl": durable_python_plan.get("sl"),
                "tp1": durable_python_plan.get("tp1"),
                "tp_final": durable_python_plan.get("tp_final"),
            }

    if (
        not signal_id
        or signal_id != str(plan.get("signal_id") or "")
        or plan.get("action") != "OPEN"
        or plan.get("execution_state") != "READY"
    ):
        raise HTTPException(status_code=409, detail="NO_MATCHING_READY_PLAN")

    try:
        position = execution_lifecycle.register_fill(
            symbol=public_symbol,
            signal_id=signal_id,
            ticket=ticket,
            direction=str(plan.get("direction") or ""),
            entry=fill_price,
            sl=_safe_float(plan.get("sl")),
            tp1=_safe_float(plan.get("tp1")),
            tp2=_safe_float(plan.get("tp_final")),
            max_bars=int(plan.get("max_bars", 40) or 40),
            filled_at=body.get("filled_at"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "status": "accepted",
        "position": position,
        # Fill/lifecycle noise is intentionally excluded from public Telegram.
        "telegram_notified": False,
    }


@app.get("/execution/python/command")
def execution_python_command(
    key: str = "",
    symbol: str = SYMBOL_DEFAULT,
    client_id: str = "RAILWAY_PYTHON_V1",
    account_id: str = "",
    balance: float = 0.0,
    equity: float = 0.0,
    day_start_equity: float = 0.0,
):
    """Dedicated command lane for the isolated Railway Python EA.

    The account telemetry is intentionally read-only. It is accepted so the
    EA can report its execution context without letting account data influence
    signal direction or price levels.
    """
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = symbol.replace("/", "")
    consumer = (client_id or "RAILWAY_PYTHON_V1").strip()

    if SIGNAL_SOURCE != "PYTHON":
        return {
            "status": "ok",
            "source": "PYTHON",
            "consumer": consumer,
            "command": {
                "action": "HOLD",
                "reason": "PYTHON_SIGNAL_MODE_DISABLED",
            },
        }

    if not execution_lifecycle.has_active(public_symbol):
        command = python_signal_bridge.pending_command(public_symbol)
        return {
            "status": "ok",
            "source": "PYTHON",
            "consumer": consumer,
            "command": command,
        }

    pending = execution_lifecycle.pending_command(public_symbol)
    if pending.get("action") != "HOLD":
        return {
            "status": "ok",
            "source": "LIFECYCLE",
            "consumer": consumer,
            "command": pending,
        }

    data_symbol = SYMBOL_DEFAULT if public_symbol.upper().startswith("XAUUSD") else symbol
    df_15m = _fetch_cached_tf(data_symbol, "15min")
    command = execution_lifecycle.evaluate(
        public_symbol,
        _latest_market_price(df_15m),
        fetch_management_m5(data_symbol),
    )
    return {
        "status": "ok",
        "source": "LIFECYCLE",
        "consumer": consumer,
        "command": command,
    }


@app.get("/execution/command")
def execution_command(key: str = "", symbol: str = SYMBOL_DEFAULT):
    """EA polls this endpoint and executes only the returned command."""
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = symbol.replace("/", "")

    # Python production commands have a dedicated consumer lane. Returning a
    # HOLD here prevents an older Pine/Cloud EA on the same symbol from racing
    # the isolated RailwayPythonEA for the same durable OPEN or lifecycle ACK.
    if SIGNAL_SOURCE == "PYTHON":
        return {
            "status": "ok",
            "source": "PYTHON",
            "command": {
                "action": "HOLD",
                "reason": "USE_DEDICATED_PYTHON_ENDPOINT",
            },
        }

    pine_command = {"action": "HOLD", "reason": "PINE_SIGNAL_MODE_DISABLED"}

    if SIGNAL_SOURCE in {"PINE", "HYBRID"}:
        pine_command = pine_signal_bridge.pending_command(public_symbol)
        if pine_command.get("action") != "HOLD":
            return {"status": "ok", "source": "PINE", "command": pine_command}

    if not execution_lifecycle.has_active(public_symbol):
        if SIGNAL_SOURCE == "PINE":
            return {"status": "ok", "source": "PINE", "command": pine_command}
        python_command = python_signal_bridge.pending_command(public_symbol)
        return {"status": "ok", "source": "PYTHON", "command": python_command}

    pending = execution_lifecycle.pending_command(public_symbol)
    if pending.get("action") != "HOLD":
        return {"status": "ok", "source": "LIFECYCLE", "command": pending}

    data_symbol = SYMBOL_DEFAULT if public_symbol.upper().startswith("XAUUSD") else symbol
    df_15m = _fetch_cached_tf(data_symbol, "15min")
    command = execution_lifecycle.evaluate(
        public_symbol,
        _latest_market_price(df_15m),
        fetch_management_m5(data_symbol),
    )
    return {"status": "ok", "source": "LIFECYCLE", "command": command}


@app.post("/execution/ack")
async def execution_ack(request: Request):
    """EA ACK makes partial/BE and close transitions durable and retry-safe."""
    body = await request.json()
    if not verify_license(str(body.get("key", ""))):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = str(body.get("symbol") or PUBLIC_SYMBOL_DEFAULT).replace("/", "")
    command_id = str(body.get("command_id") or "")

    if pine_signal_bridge.owns(command_id):
        try:
            result = pine_signal_bridge.acknowledge(
                symbol=public_symbol,
                command_id=command_id,
                success=body.get("success") is True,
            )
        except PineSignalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        close_state = None
        if body.get("success") is True and result.get("action") == "CLOSE_ALL":
            close_state = execution_lifecycle.close_external(
                public_symbol,
                r_multiple=_safe_float(body.get("r_multiple")),
            )

        promoted_command = result.get("promoted_command")
        telegram_notified = False
        if isinstance(promoted_command, dict):
            promoted_payload = build_pine_api_payload(promoted_command)
            _set_latest_signal(promoted_payload)
            telegram_notified = maybe_broadcast_signal(promoted_payload)

        return {
            "status": "accepted",
            "source": "PINE",
            "result": result,
            "position": close_state,
            "next_command": promoted_command,
            "telegram_notified": telegram_notified,
        }

    if python_signal_bridge.owns(command_id):
        try:
            result = python_signal_bridge.acknowledge(
                symbol=public_symbol,
                command_id=command_id,
                success=body.get("success") is True,
            )
        except PineSignalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return {
            "status": "accepted",
            "source": "PYTHON",
            "result": result,
            "position": execution_lifecycle.position(public_symbol),
            "telegram_notified": False,
        }

    try:
        result = execution_lifecycle.acknowledge(
            symbol=public_symbol,
            command_id=command_id,
            success=body.get("success") is True,
            remaining_pct=body.get("remaining_pct"),
            r_multiple=_safe_float(body.get("r_multiple")),
            acknowledged_at=body.get("acknowledged_at"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "accepted", "result": result}


@app.get("/signal/scenarios")
def signal_scenarios(key: str = "", symbol: str = SYMBOL_DEFAULT):
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    df_4h, df_1h, df_15m = fetch_multi_tf(symbol)

    scanner = ScenarioScanner()
    blueprint = scanner.scan(df_4h, df_1h, df_15m, symbol=symbol.replace("/", ""))

    return {
        "status": "ok",
        "symbol": symbol.replace("/", ""),
        "blueprint": blueprint.to_dict(),
    }


@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    if SIGNAL_SOURCE not in {"PINE", "HYBRID"}:
        raise HTTPException(status_code=409, detail="PINE_SIGNAL_MODE_DISABLED")

    payload = await request.json()

    key = payload.get("key", "")
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    action = str(payload.get("action") or "").upper()
    direction = str(payload.get("direction") or "").upper()
    signal_id = str(payload.get("signal_id") or "")
    print(
        "AlphaBuffalo Pine webhook received | "
        f"action={action or 'MISSING'} direction={direction or 'MISSING'} "
        f"signal_id={signal_id or 'MISSING'}",
        flush=True,
    )
    if action in {"OPEN", "ENTRY"} and not _market_open_gate()["market_open"]:
        raise HTTPException(status_code=409, detail="MARKET_CLOSED")

    effective_payload = dict(payload)
    public_symbol = str(payload.get("symbol") or PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    reverse_blocked_reason = ""

    if action in {"OPEN", "ENTRY"}:
        entry_gate = _pine_entry_permission(payload.get("direction"), public_symbol)
        if not entry_gate.allowed:
            print(
                "AlphaBuffalo Pine webhook blocked | "
                f"direction={direction} signal_id={signal_id} "
                f"reason={entry_gate.reason} telegram_notified=False",
                flush=True,
            )
            raise HTTPException(status_code=409, detail=entry_gate.reason)

    # CLOSE must always pass.  A requested reverse is a new entry and therefore
    # must pass the same time/risk/harmonic gate; if it fails, keep the close
    # command but strip only the reverse leg.
    reverse_direction = str(payload.get("reverse_direction") or "").upper()
    if action in {"CLOSE", "EXIT", "CLOSE_ALL"} and reverse_direction:
        reverse_gate = _pine_entry_permission(reverse_direction, public_symbol)
        if not reverse_gate.allowed:
            reverse_blocked_reason = reverse_gate.reason
            for field in list(effective_payload):
                if str(field).startswith("reverse_"):
                    effective_payload.pop(field, None)

    try:
        command = pine_signal_bridge.ingest(effective_payload)
    except PineSignalError as exc:
        print(
            "AlphaBuffalo Pine webhook invalid | "
            f"action={action} direction={direction} signal_id={signal_id} "
            f"reason={exc}",
            flush=True,
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if reverse_blocked_reason:
        command["reverse_blocked_reason"] = reverse_blocked_reason

    if command.get("action") == "HOLD":
        return {
            "status": "accepted",
            "source": "PINE",
            "duplicate": True,
            "command": command,
            "telegram_notified": False,
        }

    public_payload = build_pine_api_payload(command)
    _set_latest_signal(public_payload)
    telegram_notified = maybe_broadcast_signal(public_payload)
    print(
        "AlphaBuffalo Pine webhook accepted | "
        f"command={command.get('action')} direction={command.get('direction')} "
        f"signal_id={command.get('signal_id')} telegram_notified={telegram_notified}",
        flush=True,
    )
    return {
        "status": "accepted",
        "source": "PINE",
        "command": command,
        "signal": public_payload,
        "telegram_notified": telegram_notified,
    }
