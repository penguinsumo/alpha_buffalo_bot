from __future__ import annotations

import html
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request, Response, Response

from decision_engine import DecisionEngine
from scenario_scanner import ScenarioScanner
from signal_composer import SignalComposer
from session_clock import SessionClock
from engine_v4.session_gate import SessionGate

# Engine V4 baseline imports. Keep these globals available for _run_engine_v4_baseline().
ENGINE_V4_IMPORT_ERROR = None
try:
    from engine_v4.indicators import add_indicators
    from engine_v4.router import SignalRouter
    from engine_v4.final_gate import FinalGate
    from engine_v4.buy_engine import BuySignalEngine
    from engine_v4.sell_engine import SellSignalEngine
except Exception as exc:  # pragma: no cover - runtime diagnostic path
    ENGINE_V4_IMPORT_ERROR = exc
    add_indicators = None
    SignalRouter = None
    FinalGate = None
    BuySignalEngine = None
    SellSignalEngine = None


app = FastAPI(title="Alpha Buffalo v12 API Adapter", version="12.0.0")

SIGNAL_LOOP_INTERVAL_SECONDS = int(os.getenv("SIGNAL_LOOP_INTERVAL_SECONDS", "60"))
LATEST_SIGNAL_CACHE: dict = {}
LATEST_SIGNAL_LOCK = threading.Lock()

TF_FETCH_TTL_SECONDS = {
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
TELEGRAM_NOTIFY_WAIT = os.getenv("TELEGRAM_NOTIFY_WAIT", "false").lower() in {"1", "true", "yes", "on"}
TELEGRAM_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "5"))
TRADE_MIN_RR = float(os.getenv("TRADE_MIN_RR", "1.5"))
TELEGRAM_MIN_RR = float(os.getenv("TELEGRAM_MIN_RR", str(TRADE_MIN_RR)))
TELEGRAM_NOTIFY_TREND_UPDATE = os.getenv("TELEGRAM_NOTIFY_TREND_UPDATE", "true").lower() in {"1", "true", "yes", "on"}
LAST_TELEGRAM_SIGNAL_KEY = ""
LAST_TELEGRAM_H1_UPDATE_KEY = ""
LAST_TELEGRAM_LOCK = threading.Lock()


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
    engine = signal.get("engine_v4", {}) or {}
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
        ])
        pa_ok = _any_truthy(engine, [
            "HA_Bull", "HA_Bull_Reversal", "HA_Green_1", "HA_Green_2_CF",
            "Bullish_Pinbar", "pa_bull_confirmed", "PA_Bull_Confirmed",
        ])
        vsa_ok = _any_truthy(engine, [
            "VSA_Buy_Wins", "vsa_buy_wins", "VSA_BUY_WINS",
            "VSA_Buy_Pressure", "vsa_buy_pressure",
        ]) and not _any_truthy(engine, ["V4_Block_Buy_At_Upper"])
        setup_ok = _any_truthy(engine, ["V4_Buy_Setup", "V4_BUY_SETUP", "BUY_SETUP", "cf_confirmed"]) or (zone_ok and pa_ok and vsa_ok)
        setup_state = "BUY_SETUP" if setup_ok else "BUY_BLOCKED"
    elif direction == "SELL":
        zone_ok = _any_truthy(engine, [
            "PRZ_Resistance", "Pine_PRZ_Resistance", "Pine_PRZ_Resistance_Touch",
            "In_PRZ_Resistance", "BB_Upper_Zone", "Near_BB_Upper",
            "V4_Resistance_Zone",
        ])
        pa_ok = _any_truthy(engine, [
            "HA_Bear", "HA_Bear_Reversal", "HA_Red_1", "HA_Red_2_CF",
            "Bearish_Pinbar", "pa_bear_confirmed", "PA_Bear_Confirmed",
        ])
        vsa_ok = _any_truthy(engine, [
            "VSA_Sell_Wins", "vsa_sell_wins", "VSA_SELL_WINS",
            "VSA_Sell_Pressure", "vsa_sell_pressure",
        ]) and not _any_truthy(engine, ["V4_Block_Sell_At_Lower"])
        setup_ok = _any_truthy(engine, ["V4_Sell_Setup", "V4_SELL_SETUP", "SELL_SETUP"]) or (zone_ok and pa_ok and vsa_ok)
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

def format_telegram_signal(payload: Dict) -> str:
    """Clean V5 Telegram message. No raw debug/reason spam."""
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}

    direction = str(ea.get("direction", "NONE")).upper()
    side_icon = "🟢 Δ+" if direction == "BUY" else "🔴 Δ-" if direction == "SELL" else "⚪ Δ"
    entry_mode = ea.get("entry_mode") or signal.get("entry_mode") or "V4_SESSION"
    entry = _safe_float(ea.get("entry"))
    sl = _safe_float(ea.get("sl"))
    tp = _safe_float(ea.get("tp_final"))
    rr = _safe_float(ea.get("rr"))
    risk = _safe_float(ea.get("risk_points"))
    reward = _safe_float(ea.get("reward_points"))

    quality_score = int(ea.get("v5_quality_score", 0) or 0)
    quality_grade = ea.get("v5_quality_grade") or "UNKNOWN"
    quality_basis = ea.get("v5_basis") or signal.get("v5_basis") or "UNKNOWN"
    session = ea.get("session") or "-"
    timestamp = signal.get("timestamp") or ea.get("signal_id") or ""

    return "\n".join([
        f"{side_icon} <b>ALPHA BUFFALO V5</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Asset    : <b>{_clean_text(symbol)}</b>",
        f"📊 Side     : {_clean_text(direction)}",
        f"📊 Type     : {_clean_text(entry_mode)}",
        f"🎯 Score    : {_safe_float(ea.get('score')):.1f}",
        f"🎯 Entry    : ~{entry:,.2f}",
        f"🛡️ SL       : {sl:,.2f}",
        f"🎯 TP       : {tp:,.2f}",
        f"⚖️ RR       : {rr:.2f}R | Risk {risk:.2f} | Reward {reward:.2f}",
        f"🕐 Session  : {_clean_text(session)}",
        f"🧠 Quality  : {quality_score} / {_clean_text(quality_grade)} / {_clean_text(quality_basis)}",
        f"⏰ Time     : {_clean_text(_format_time_pair(timestamp))}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "✅ EA Executing",
        "⚠️ Not financial advice. Trade at your own risk.",
    ])

def send_telegram_message(text: str) -> bool:
    if not _telegram_enabled():
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = False
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                ok = True
            else:
                print(f"AlphaBuffalo Telegram send failed | chat_id={chat_id} status={response.status_code} body={response.text[:160]}", flush=True)
        except Exception as exc:
            print(f"AlphaBuffalo Telegram send error | chat_id={chat_id} {type(exc).__name__}: {exc}", flush=True)
    return ok



def _deep_get(data: Dict, path: list[str], default=None):
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur in (None, "") else cur


def _h1_update_key(payload: Dict) -> str:
    signal = payload.get("signal", {}) or {}
    raw = str(signal.get("timestamp") or payload.get("generated_at") or "")
    dt = None
    try:
        if raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    h1 = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    return f"{symbol}-{h1.isoformat()}"


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


def _trend_setup_label(signal: Dict, ea: Dict) -> str:
    engine = signal.get("engine_v4", {}) or {}
    setup = ea.get("scenario_state") or ea.get("setup_state") or signal.get("scenario_state") or signal.get("setup_state")
    if setup:
        return str(setup)
    if _truthy(engine.get("V4_Buy_Setup")):
        return "BUY_SETUP"
    if _truthy(engine.get("V4_Sell_Setup")):
        return "SELL_SETUP"
    watch_bias = _deep_get(signal, ["blueprint", "watch_bias"], "") or _deep_get(signal, ["blueprint", "prz_layers", "routing"], "")
    return str(watch_bias or "WAIT")


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
    zone = _trend_zone_label(signal, ea)
    setup = _trend_setup_label(signal, ea)
    vsa = _trend_vsa_label(signal, ea)
    bos = ea.get("break_prediction") or ea.get("journey_state") or ("CONFIRMED" if ea.get("bos_confirmed") else "Waiting")

    watch = "Wait"
    if str(setup).upper().startswith("BUY") or "SUPPORT" in zone.upper():
        watch = "Δ+ BUY if CF/BOS confirms"
    elif str(setup).upper().startswith("SELL") or "RESISTANCE" in zone.upper():
        watch = "Δ- SELL if CF/BOS confirms"

    timestamp = signal.get("timestamp") or payload.get("generated_at") or ""

    return "\n".join([
        "📊 <b>XAUUSD TREND UPDATE</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 Session : {_clean_text(session)}",
        f"💰 Price   : {price:,.2f}",
        "",
        f"🧭 Zone    : {_clean_text(zone)}",
        f"⚡ Setup   : {_clean_text(setup)}",
        f"🧠 VSA     : {_clean_text(vsa)}",
        f"🚦 BOS     : {_clean_text(bos)}",
        "",
        f"➡️ M15     : {_clean_text(_trend_line(signal, 'm15_phase', 'Reaction/Watch'))}",
        f"📈 H1      : {_clean_text(_trend_line(signal, 'h1_phase', blueprint.get('trend_h1', '-')))}",
        f"📉 H4      : {_clean_text(_trend_line(signal, 'h4_phase', blueprint.get('trend_h4', '-')))}",
        "",
        f"👀 Watch   : {_clean_text(watch)}",
        f"⏰ Time    : {_clean_text(_format_time_pair(timestamp))}",
        "━━━━━━━━━━━━━━━━━━━━━",
    ])


def maybe_broadcast_trend_update(payload: Dict) -> None:
    """Send one compact market-state update per H1 hour. Never opens or bypasses trade gates."""
    global LAST_TELEGRAM_H1_UPDATE_KEY
    if not TELEGRAM_NOTIFY_TREND_UPDATE:
        return
    if not _telegram_enabled():
        return

    key = _h1_update_key(payload)
    with LAST_TELEGRAM_LOCK:
        if key == LAST_TELEGRAM_H1_UPDATE_KEY:
            return
        LAST_TELEGRAM_H1_UPDATE_KEY = key

    send_telegram_message(format_telegram_trend_update(payload))

def maybe_broadcast_signal(payload: Dict) -> None:
    """Broadcast only Clean V5 OPEN signals that passed RR/levels/setup/VSA gates."""
    global LAST_TELEGRAM_SIGNAL_KEY

    ea = payload.get("ea", {}) or {}
    signal = payload.get("signal", {}) or {}
    action = str(ea.get("action", "WAIT")).upper()

    # No Telegram for WAIT by default. Even if TELEGRAM_NOTIFY_WAIT=true, never send invalid low-quality trade alerts.
    if action != "OPEN":
        return

    rr = _safe_float(ea.get("rr"))
    if rr < TELEGRAM_MIN_RR:
        return

    if not bool(ea.get("directional_levels_ok")):
        return
    if not bool(ea.get("levels_ready")):
        return
    if not bool(ea.get("rr_ok")):
        return
    if not bool(ea.get("setup_ok", True)):
        return
    if not bool(ea.get("zone_ok", True)):
        return
    if not bool(ea.get("vsa_gate_ok", True)):
        return

    signal_key = _telegram_signal_key(payload)
    with LAST_TELEGRAM_LOCK:
        if signal_key and signal_key == LAST_TELEGRAM_SIGNAL_KEY:
            return
        LAST_TELEGRAM_SIGNAL_KEY = signal_key

    send_telegram_message(format_telegram_signal(payload))

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

def _run_engine_v4_baseline(df_15m: pd.DataFrame) -> Dict | None:

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
        routed = SignalRouter(
            clock=session_clock,
            gate=FinalGate(session_clock),
            buy_engine=BuySignalEngine(),
            sell_engine=SellSignalEngine(),
        ).process(df)
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
        return signal

    direction = str(engine_signal.get("direction", "NONE")).upper()
    if direction not in {"BUY", "SELL"}:
        return signal

    entry = _safe_float(engine_signal.get("entry"))
    sl = _safe_float(engine_signal.get("sl"))
    tp_final = _safe_float(engine_signal.get("tp"))

    if entry <= 0 or sl <= 0 or tp_final <= 0:
        return signal

    if direction == "BUY" and not (sl < entry < tp_final):
        return signal
    if direction == "SELL" and not (tp_final < entry < sl):
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

    direction = str(decision.get("action", "NONE")).upper()
    timestamp = str(signal.get("timestamp") or blueprint.get("timestamp") or "")

    current_price = _safe_float(blueprint.get("current_price"))

    entry = _first_float(
        signal.get("entry"),
        blueprint.get("entry"),
        plan_a.get("entry"),
        plan_b.get("entry"),
        blueprint.get("plan_a_entry"),
        blueprint.get("plan_b_entry"),
        current_price,
    )

    sl = _first_float(
        signal.get("sl"),
        blueprint.get("sl"),
        plan_a.get("sl"),
        plan_b.get("sl"),
        blueprint.get("plan_a_sl"),
        blueprint.get("plan_b_sl"),
    )

    tp_final = _first_float(
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

    blueprint_valid = bool(gates.get("blueprint_valid", blueprint.get("is_valid", False)))
    trade_direction_ok = direction in {"BUY", "SELL"}
    levels_ready = entry > 0 and sl > 0 and tp_final > 0

    if direction == "BUY":
        directional_levels_ok = sl < entry < tp_final
    elif direction == "SELL":
        directional_levels_ok = tp_final < entry < sl
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

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "action": action,
        "execution_state": execution_state,
        "direction": direction if trade_direction_ok else "NONE",

        "entry": entry,
        "sl": sl,
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
        "trade_management": signal.get("trade_management") or (signal.get("engine_v4", {}) or {}).get("trade_management"),
        "break_prediction": signal.get("break_prediction") or (signal.get("engine_v4", {}) or {}).get("break_prediction"),
        "bos_confirmed": bool(signal.get("bos_confirmed") or (signal.get("engine_v4", {}) or {}).get("bos_confirmed")),
        "vsa_gate": signal.get("vsa_gate") or (signal.get("engine_v4", {}) or {}).get("vsa_gate"),
        "checkpoint_price": _safe_float(signal.get("checkpoint_price") or (signal.get("engine_v4", {}) or {}).get("checkpoint_price")),

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

def run_pipeline(symbol: str = SYMBOL_DEFAULT, public_symbol: str = PUBLIC_SYMBOL_DEFAULT) -> Dict:
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
    engine_v4_signal = _run_engine_v4_baseline(df_15m)
    signal = _apply_engine_v4_signal(signal, engine_v4_signal)

    return {
        "status": "ok",
        "symbol": public_symbol,
        "signal": signal,
        "ea": build_ea_payload(public_symbol, signal),
    }


def _set_latest_signal(payload: dict) -> None:
    with LATEST_SIGNAL_LOCK:
        LATEST_SIGNAL_CACHE.clear()
        LATEST_SIGNAL_CACHE.update(payload)


def _get_latest_signal() -> dict:
    with LATEST_SIGNAL_LOCK:
        return dict(LATEST_SIGNAL_CACHE)


def _cloud_signal_loop() -> None:
    print(f"AlphaBuffalo cloud signal loop started | interval={SIGNAL_LOOP_INTERVAL_SECONDS}s", flush=True)
    while True:
        try:
            payload = run_pipeline()
            _set_latest_signal(payload)
            maybe_broadcast_signal(payload)
            maybe_broadcast_trend_update(payload)
            decision = payload.get("signal", {}).get("decision", {})
            ea = payload.get("ea", {})
            print(
                f"AlphaBuffalo cloud scan | action={decision.get('action')} "
                f"grade={decision.get('grade')} score={decision.get('score')} "
                f"ea={ea.get('action')} state={ea.get('execution_state')}",
                flush=True,
            )
        except Exception as exc:
            print(f"AlphaBuffalo cloud scan error | {type(exc).__name__}: {exc}", flush=True)
        time.sleep(SIGNAL_LOOP_INTERVAL_SECONDS)


@app.on_event("startup")
def _start_cloud_signal_loop() -> None:
    global _SIGNAL_LOOP_STARTED
    if _SIGNAL_LOOP_STARTED:
        return
    _SIGNAL_LOOP_STARTED = True
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
    }


@app.get("/health")
def health():
    return {
        "status": "alive",
        "version": "v12-core",
        "timestamp": time.time(),
    }


@app.get("/signal/latest")
def signal_latest(key: str = "", symbol: str = SYMBOL_DEFAULT):
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = symbol.replace("/", "")
    cached = _get_latest_signal()
    if cached and cached.get("symbol") == public_symbol:
        return cached

    payload = run_pipeline(symbol=symbol, public_symbol=public_symbol)
    _set_latest_signal(payload)
    return payload


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
    payload = await request.json()

    key = payload.get("key", "")
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    symbol = payload.get("symbol", SYMBOL_DEFAULT)
    public_symbol = symbol.replace("/", "")

    return run_pipeline(symbol=symbol, public_symbol=public_symbol)
