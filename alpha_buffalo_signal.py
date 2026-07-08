from __future__ import annotations

import html
import os
import time
import threading
from typing import Dict, Tuple

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request, Response, Response

from decision_engine import DecisionEngine
from scenario_scanner import ScenarioScanner
from signal_composer import SignalComposer
from session_clock import SessionClock


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
LAST_TELEGRAM_SIGNAL_KEY = ""
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


def format_telegram_signal(payload: Dict) -> str:
    """Clean V5-style Telegram message, adapted to v12 nested signal + ea payload."""
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    decision = signal.get("decision", {}) or {}

    action = str(ea.get("action", "WAIT")).upper()
    state = str(ea.get("execution_state", "WATCH")).upper()
    direction = str(ea.get("direction", "NONE")).upper()
    emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"

    entry_mode = ea.get("entry_mode") or signal.get("entry_mode") or "V12_DECISION"
    exit_mode = ea.get("exit_mode") or signal.get("exit_mode") or "NONE"
    quality_score = int(ea.get("v5_quality_score", 0) or 0)
    quality_grade = ea.get("v5_quality_grade") or "UNKNOWN"
    quality_basis = ea.get("v5_basis") or "UNKNOWN"
    session_gate = ea.get("session_quality_gate") or "UNKNOWN"
    reason = ea.get("reason") or decision.get("reason") or "-"

    return "\n".join([
        f"{emoji} <b>Alpha Buffalo</b>",
        f"📊 <b>{_clean_text(symbol)}</b> | {_clean_text(direction)} | {_clean_text(action)}/{_clean_text(state)}",
        f"🎯 Score: {_safe_float(ea.get('score')):.1f} | Grade: {_clean_text(ea.get('grade') or decision.get('grade'))}",
        f"💰 Entry: {_fmt_price(ea.get('entry'))}",
        f"📈 TP: {_fmt_price(ea.get('tp_final'))}",
        f"🛡️ SL: {_fmt_price(ea.get('sl'))}",
        f"🧭 Session: {_clean_text(ea.get('session'))}",
        f"⚙️ Entry: {_clean_text(entry_mode)} | Exit: {_clean_text(exit_mode)}",
        f"🧠 V5 Quality: {quality_score} / {_clean_text(quality_grade)} / {_clean_text(quality_basis)}",
        f"🚦 Gate: {_clean_text(session_gate)} | Levels: {_clean_text(ea.get('directional_levels_ok'))}",
        f"📝 {_clean_text(reason)}",
        f"⏱️ {_clean_text(signal.get('timestamp') or ea.get('signal_id'))}",
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


def maybe_broadcast_signal(payload: Dict) -> None:
    """Send only actionable OPEN by default; WAIT can be enabled with TELEGRAM_NOTIFY_WAIT=true."""
    global LAST_TELEGRAM_SIGNAL_KEY

    ea = payload.get("ea", {}) or {}
    action = str(ea.get("action", "WAIT")).upper()
    if action != "OPEN" and not TELEGRAM_NOTIFY_WAIT:
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


def _run_engine_v4_baseline(df_15m: pd.DataFrame) -> Dict | None:
    """
    Restore proven BUY/SELL baseline from sell-micro-v4-2.

    Root lifecycle preserved:
    - Harmonic/PRZ remains context, not score/bucket here.
    - This engine only produces V4/V5 trading baseline signal when confirmed.
    - EA payload mapping remains adapter-only in build_ea_payload().
    """
    try:
        from engine_v4.indicators import add_indicators
        from engine_v4.router import SignalRouter
        from engine_v4.final_gate import FinalGate
        from engine_v4.buy_engine import BuySignalEngine
        from engine_v4.sell_engine import SellSignalEngine

        df = _df_with_datetime_index(df_15m)
        df = add_indicators(df).dropna()

        if len(df) < 60:
            return None

        clock = SessionClock()
        router = SignalRouter(
            clock=clock,
            gate=FinalGate(clock),
            buy_engine=BuySignalEngine(),
            sell_engine=SellSignalEngine(),
        )
        signals = router.process(df)
        if not signals:
            return None

        def rank(sig: Dict) -> tuple:
            direction_rank = 1 if str(sig.get("direction", "")).upper() == "SELL" else 0
            quality = int(sig.get("v5_quality_score", 0) or 0)
            rr = _safe_float(sig.get("entry_rr"), 0.0)
            return (quality, direction_rank, rr)

        return max(signals, key=rank)

    except Exception as exc:
        print(f"AlphaBuffalo engine_v4 baseline error | {type(exc).__name__}: {exc}", flush=True)
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
    signal["exit_mode"] = exit_mode
    signal["be_policy"] = engine_signal.get("be_policy") or ("PROFIT_0_15" if direction == "BUY" else "CURRENT_BBMID_LOW")
    signal["trail_policy"] = engine_signal.get("trail_policy") or ("TRAIL_FACTOR_0_9995" if direction == "BUY" else "NONE")
    signal["max_bars"] = int(engine_signal.get("max_bars", 40) or 40)
    signal["v5_quality_score"] = quality_score
    signal["v5_quality_grade"] = engine_signal.get("v5_quality_grade", "BASE")
    signal["v5_basis"] = engine_signal.get("v5_basis", "BASE")
    signal["session_quality_gate"] = engine_signal.get("session_quality_gate", "BUY_TIMING_GATE" if direction == "BUY" else "UNKNOWN")
    signal["sell_dot_reason"] = engine_signal.get("sell_dot_reason", "UNKNOWN")
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
    """
    decision = signal.get("decision", {}) or {}
    gates = signal.get("gates", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}

    plan_a = blueprint.get("plan_a", {}) or {}
    plan_b = blueprint.get("plan_b", {}) or {}

    direction = str(decision.get("action", "NONE")).upper()
    timestamp = str(signal.get("timestamp") or blueprint.get("timestamp") or "")

    current_price = _safe_float(blueprint.get("current_price"))

    # อ่านทั้ง v12 nested plan และ legacy flat fields
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

    execution_state = (
        "READY"
        if blueprint_valid and trade_direction_ok and levels_ready and directional_levels_ok
        else "WATCH"
    )

    action = "OPEN" if execution_state == "READY" else "WAIT"

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
        "reason": decision.get("reason", ""),
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
