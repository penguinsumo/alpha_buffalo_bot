# Railway deployment revision: 20260721T063218Z
from __future__ import annotations

import html
import json
import math
import os
import re
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
from runtime_layers.common import (
    _blueprint_float,
    _engine_v4_scalar,
    _ensure_engine_v4_datetime_index,
    _iso_timestamp,
    _safe_float,
)
from runtime_layers.evidence import (
    _engine_v4_wait_diagnostics,
    _m5_sniper_sweep_overlay,
    _overlay_blueprint_prz_memory,
    _v4_location_evidence_memory,
)
from runtime_layers.execution import (
    _apply_engine_v4_signal,
    build_api_signal_response,
    build_ea_payload as _build_ea_payload,
)
from runtime_layers.harmonic import _harmonic_gate_context
from runtime_layers.newday import newday_bias_for_direction, newday_diagnostic
from fundamental.context import fundamental_bias_for_direction, fundamental_diagnostic
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
    # The strategy only acts on confirmed candles. Fetching faster than the
    # candle cadence spends provider quota without creating fresher evidence.
    "5min": int(os.getenv("TF_5M_TTL_SECONDS", "300")),
    "15min": int(os.getenv("TF_15M_TTL_SECONDS", "900")),
    "1h": int(os.getenv("TF_1H_TTL_SECONDS", "3600")),
    "4h": int(os.getenv("TF_4H_TTL_SECONDS", "14400")),
}
TF_MAX_STALE_SECONDS = {
    # Provider interruptions may use the last closed bars for context, but
    # never indefinitely. These limits are deliberately wider than the normal
    # fetch cadence and narrower than a trading day.
    "5min": int(os.getenv("TF_5M_MAX_STALE_SECONDS", "900")),
    "15min": int(os.getenv("TF_15M_MAX_STALE_SECONDS", "3600")),
    "1h": int(os.getenv("TF_1H_MAX_STALE_SECONDS", "10800")),
    "4h": int(os.getenv("TF_4H_MAX_STALE_SECONDS", "43200")),
}
TF_ENTRY_MAX_AGE_SECONDS = {
    # A confirmed OPEN is stricter than read-only context. M5 is required only
    # when the selected entry mode is an M5 sniper trigger.
    "5min": int(os.getenv("TF_5M_ENTRY_MAX_AGE_SECONDS", "600")),
    "15min": int(os.getenv("TF_15M_ENTRY_MAX_AGE_SECONDS", "1200")),
    "1h": int(os.getenv("TF_1H_ENTRY_MAX_AGE_SECONDS", "7200")),
    "4h": int(os.getenv("TF_4H_ENTRY_MAX_AGE_SECONDS", "28800")),
}
TF_DATA_CACHE: dict = {}
TF_CACHE_LOCK = threading.Lock()
MARKET_DATA_COOLDOWN_LOCK = threading.Lock()
MARKET_DATA_COOLDOWN_UNTIL = 0.0
MARKET_DATA_COOLDOWN_REASON = ""
_SIGNAL_LOOP_STARTED = False

SYMBOL_DEFAULT = os.getenv("ALPHA_SYMBOL", "XAU/USD")
PUBLIC_SYMBOL_DEFAULT = os.getenv("ALPHA_PUBLIC_SYMBOL", "XAUUSD")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
API_LICENSE_KEY = os.getenv("ALPHA_API_KEY", os.getenv("LICENSE_KEY", "DEMO123"))
SIGNAL_SOURCE = os.getenv("ALPHA_SIGNAL_SOURCE", "PYTHON").strip().upper()
if SIGNAL_SOURCE not in {"PYTHON", "PINE", "HYBRID"}:
    SIGNAL_SOURCE = "PYTHON"
PINE_NOTIFICATION_ONLY = os.getenv(
    "ALPHA_PINE_NOTIFICATION_ONLY", "true"
).lower() in {"1", "true", "yes", "on"}
# Pine webhooks may still be accepted for validation/observability, but they
# are silent on Telegram by default.  This keeps the production grouping and
# owner chat focused on the Python/EA decision source.  A dedicated Pine room
# can be re-enabled explicitly for controlled testing.
TELEGRAM_PINE_NOTIFICATIONS_ENABLED = os.getenv(
    "TELEGRAM_PINE_NOTIFICATIONS_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
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
# Harmonic has no entry authority. It becomes a TP2 source only after aligned
# BOS/CHoCH and only when its D projection overlaps the next opposite PRZ.
# Keep the compatibility name false so stale Railway variables cannot restore
# the removed hard gate.
REQUIRE_HARMONIC_BIAS = False
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()


def _parse_telegram_chat_ids(value: str) -> list[str]:
    """Parse a comma-separated destination list without exposing its values."""
    return [chat_id.strip() for chat_id in str(value or "").split(",") if chat_id.strip()]


# Production grouping is owned by the Python engine. Pine must never inherit
# this list because that would mix independent decision sources in one room.
TELEGRAM_CHAT_IDS = _parse_telegram_chat_ids(
    os.getenv("TELEGRAM_GROUP_CHAT_IDS")
    or os.getenv("NOTIFY_IDS")
    or os.getenv("TELEGRAM_CHAT_IDS")
    or os.getenv("TELEGRAM_CHAT_ID")
    or ""
)
TELEGRAM_PINE_CHAT_IDS = _parse_telegram_chat_ids(
    os.getenv("TELEGRAM_PINE_CHAT_IDS")
    or os.getenv("TELEGRAM_PINE_CHAT_ID")
    or ""
)
TELEGRAM_OWNER_CHAT_IDS = _parse_telegram_chat_ids(
    os.getenv("TELEGRAM_OWNER_CHAT_IDS")
    or os.getenv("TELEGRAM_OWNER_CHAT_ID")
    or os.getenv("OWNER_CHAT_ID")
    or os.getenv("ADMIN_ID")
    or ""
)
# Only the confirmed-BOS/WAIT-CF state may emit a waiting message.  The old
# TELEGRAM_NOTIFY_WAIT flag is intentionally ignored because it represented
# noisy generic WAIT updates in previous deployments.
TELEGRAM_NOTIFY_WAIT = os.getenv("TELEGRAM_NOTIFY_CONFIRMATION", "true").lower() in {"1", "true", "yes", "on"}
TELEGRAM_NOTIFY_OWNER_CONTEXT = os.getenv(
    "TELEGRAM_NOTIFY_OWNER_CONTEXT", "true"
).lower() in {"1", "true", "yes", "on"}
TELEGRAM_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "5"))
TRADE_MIN_RR = float(os.getenv("TRADE_MIN_RR", "1.5"))
TELEGRAM_MIN_RR = float(os.getenv("TELEGRAM_MIN_RR", str(TRADE_MIN_RR)))
TELEGRAM_NOTIFY_TREND_UPDATE = os.getenv("TELEGRAM_NOTIFY_TREND_UPDATE", "true").lower() in {"1", "true", "yes", "on"}
TELEGRAM_SIGNAL_ONLY_MODE = os.getenv(
    "TELEGRAM_SIGNAL_ONLY_MODE",
    "true",
).lower() in {"1", "true", "yes", "on"}
TELEGRAM_TREND_MIN_INTERVAL_SECONDS = max(
    300,
    int(os.getenv("TELEGRAM_TREND_MIN_INTERVAL_SECONDS", "3600")),
)
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
LAST_TELEGRAM_TREND_UPDATE_AT: datetime | None = None
LAST_TELEGRAM_H1_CROSS_KEY = ""
LAST_TELEGRAM_TREND_STATE_LOADED = False
LAST_TELEGRAM_CONFIRM_KEY = ""
LAST_TELEGRAM_OWNER_CONTEXT_KEY = ""
LAST_TELEGRAM_LOCK = threading.Lock()
TELEGRAM_DELIVERY_STATUS = {
    "last_attempt_at": "",
    "last_success_at": "",
    "last_error": "",
    "last_audience": "",
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
        detail = f"DATA_FETCH_HTTP_{response.status_code}"
        if response.status_code == 429:
            try:
                provider_message = str(response.json().get("message", "")).lower()
            except (TypeError, ValueError):
                provider_message = ""
            if "for the day" in provider_message or "daily" in provider_message:
                detail = "DATA_FETCH_DAILY_LIMIT"
            elif "minute" in provider_message:
                detail = "DATA_FETCH_MINUTE_LIMIT"
        raise HTTPException(status_code=502, detail=detail)

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


def _tf_cache_directory() -> Path:
    """Use the Railway volume when mounted, otherwise a local temp cache."""
    configured = os.getenv("ALPHA_TF_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    volume_root = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume_root:
        return Path(volume_root) / "alpha_buffalo_tf_cache"
    return Path("/tmp/alpha_buffalo_tf_cache")


def _tf_cache_path(symbol: str, interval: str) -> Path:
    safe_symbol = re.sub(r"[^A-Za-z0-9]+", "_", str(symbol or "UNKNOWN")).strip("_")
    safe_interval = re.sub(r"[^A-Za-z0-9]+", "_", str(interval or "UNKNOWN")).strip("_")
    return _tf_cache_directory() / f"{safe_symbol}_{safe_interval}.json"


def _tag_market_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    age_seconds: float,
    fetched_at: float,
) -> pd.DataFrame:
    tagged = frame.copy()
    tagged.attrs["alpha_data_source"] = str(source)
    tagged.attrs["alpha_data_age_seconds"] = max(0.0, float(age_seconds))
    tagged.attrs["alpha_data_fetched_at"] = float(fetched_at)
    return tagged


def _persist_tf_cache(
    symbol: str,
    interval: str,
    frame: pd.DataFrame,
    fetched_at: float,
) -> None:
    """Best-effort durable candle cache; cache I/O can never break a scan."""
    try:
        path = _tf_cache_path(symbol, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": str(symbol),
            "interval": str(interval),
            "fetched_at": float(fetched_at),
            "records": frame.to_dict(orient="records"),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception as exc:
        print(
            "AlphaBuffalo TF cache persist skipped | "
            f"interval={interval} error={type(exc).__name__}",
            flush=True,
        )


def _load_persisted_tf_cache(
    symbol: str,
    interval: str,
) -> tuple[pd.DataFrame | None, float | None]:
    """Load validated cached candles without allowing malformed files through."""
    try:
        path = _tf_cache_path(symbol, interval)
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0.0))
        records = payload.get("records")
        if fetched_at <= 0 or not isinstance(records, list) or not records:
            return None, None
        frame = pd.DataFrame(records)
        required = ["open", "high", "low", "close"]
        if any(column not in frame.columns for column in required):
            return None, None
        for column in required:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=required)
        if "datetime" in frame.columns:
            frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
            frame = frame.sort_values("datetime")
        frame = frame.reset_index(drop=True)
        if len(frame) < 50:
            return None, None
        return frame, fetched_at
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return None, None
    except Exception as exc:
        print(
            "AlphaBuffalo TF cache load skipped | "
            f"interval={interval} error={type(exc).__name__}",
            flush=True,
        )
        return None, None


def _safe_pipeline_error_code(exc: Exception) -> str:
    """Return a useful error code without ever logging credentials or URLs."""
    def _redact(value: object) -> str:
        message = str(value or "").strip()
        if TWELVEDATA_API_KEY:
            message = message.replace(TWELVEDATA_API_KEY, "[REDACTED]")
        message = re.sub(r"apikey=[^&\s]+", "apikey=[REDACTED]", message, flags=re.I)
        message = re.sub(r"https?://\S+", "[URL_REDACTED]", message)
        return message

    if isinstance(exc, HTTPException):
        detail = _redact(exc.detail)
        if detail:
            return re.sub(r"[^A-Za-z0-9_:.\-]+", "_", detail)[:180]
        return f"HTTP_{exc.status_code}"
    if isinstance(exc, requests.RequestException):
        return f"MARKET_DATA_NETWORK_{type(exc).__name__.upper()}"
    message = _redact(exc)
    clean = re.sub(r"[^A-Za-z0-9_:.\-]+", "_", message).strip("_")
    return f"{type(exc).__name__.upper()}:{clean[:140]}" if clean else type(exc).__name__.upper()


def _tf_epoch_bucket(interval: str, timestamp: float) -> int:
    cadence = max(1, int(TF_FETCH_TTL_SECONDS.get(interval, 180)))
    return int(float(timestamp) // cadence)


def _register_market_data_cooldown(error_code: str, now: float | None = None) -> None:
    """Back off provider requests without delaying recovery after quota reset."""
    global MARKET_DATA_COOLDOWN_UNTIL, MARKET_DATA_COOLDOWN_REASON
    observed_at = float(now if now is not None else time.time())
    code = str(error_code or "").upper()
    if code == "DATA_FETCH_DAILY_LIMIT":
        utc_now = datetime.fromtimestamp(observed_at, tz=timezone.utc)
        next_day = (utc_now + timedelta(days=1)).date()
        retry_at = datetime.combine(
            next_day,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp() + 5.0
    elif code in {"DATA_FETCH_MINUTE_LIMIT", "DATA_FETCH_HTTP_429"}:
        retry_at = (math.floor(observed_at / 60.0) + 1.0) * 60.0 + 2.0
    else:
        return

    with MARKET_DATA_COOLDOWN_LOCK:
        if retry_at >= MARKET_DATA_COOLDOWN_UNTIL:
            MARKET_DATA_COOLDOWN_UNTIL = retry_at
            MARKET_DATA_COOLDOWN_REASON = code


def _active_market_data_cooldown(now: float | None = None) -> tuple[str, float]:
    """Return the active provider backoff reason and retry timestamp."""
    global MARKET_DATA_COOLDOWN_UNTIL, MARKET_DATA_COOLDOWN_REASON
    observed_at = float(now if now is not None else time.time())
    with MARKET_DATA_COOLDOWN_LOCK:
        if MARKET_DATA_COOLDOWN_UNTIL > observed_at:
            return MARKET_DATA_COOLDOWN_REASON, MARKET_DATA_COOLDOWN_UNTIL
        MARKET_DATA_COOLDOWN_UNTIL = 0.0
        MARKET_DATA_COOLDOWN_REASON = ""
    return "", 0.0


def _fetch_cached_tf(symbol: str, interval: str, outputsize: int = 200) -> pd.DataFrame:
    ttl = TF_FETCH_TTL_SECONDS.get(interval, 180)
    max_stale = TF_MAX_STALE_SECONDS.get(interval, max(ttl * 3, ttl))
    key = f"{symbol}:{interval}"
    now = time.time()

    stale_df = None
    stale_age = None
    stale_ts = None

    with TF_CACHE_LOCK:
        cached = TF_DATA_CACHE.get(key)
        if cached:
            stale_df = cached.get("df")
            cached_ts = float(cached.get("ts", 0))
            stale_age = now - cached_ts
            stale_ts = cached_ts
            cached_bucket = int(
                cached.get("bucket", _tf_epoch_bucket(interval, cached_ts))
            )
            current_bucket = _tf_epoch_bucket(interval, now)
            if (
                stale_df is not None
                and stale_age < ttl
                and cached_bucket == current_bucket
            ):
                return _tag_market_frame(
                    stale_df,
                    source="MEMORY_CACHE",
                    age_seconds=stale_age,
                    fetched_at=cached_ts,
                )

    try:
        cooldown_reason, cooldown_until = _active_market_data_cooldown(now)
        if cooldown_reason:
            raise HTTPException(
                status_code=503,
                detail=f"{cooldown_reason}_COOLDOWN_UNTIL_{int(cooldown_until)}",
            )
        df = fetch_twelvedata(symbol, interval, outputsize=outputsize)
    except Exception as exc:
        error_code = _safe_pipeline_error_code(exc)
        _register_market_data_cooldown(error_code, now=now)
        if stale_df is not None and stale_age is not None and stale_age <= max_stale:
            print(
                "AlphaBuffalo TF cache fallback | "
                f"interval={interval} source=MEMORY age={int(stale_age)}s "
                f"error={error_code}",
                flush=True,
            )
            return _tag_market_frame(
                stale_df,
                source="MEMORY_FALLBACK",
                age_seconds=stale_age,
                fetched_at=float(stale_ts or 0.0),
            )

        persisted, persisted_ts = _load_persisted_tf_cache(symbol, interval)
        persisted_age = now - float(persisted_ts or 0.0)
        if persisted is not None and persisted_ts and persisted_age <= max_stale:
            with TF_CACHE_LOCK:
                TF_DATA_CACHE[key] = {
                    "df": persisted.copy(),
                    "ts": float(persisted_ts),
                    "bucket": _tf_epoch_bucket(interval, float(persisted_ts)),
                }
            print(
                "AlphaBuffalo TF cache fallback | "
                f"interval={interval} source=PERSISTED age={int(persisted_age)}s "
                f"error={error_code}",
                flush=True,
            )
            return _tag_market_frame(
                persisted,
                source="PERSISTED_FALLBACK",
                age_seconds=persisted_age,
                fetched_at=float(persisted_ts),
            )
        raise

    fetched_at = time.time()
    with TF_CACHE_LOCK:
        TF_DATA_CACHE[key] = {
            "df": df.copy(),
            "ts": fetched_at,
            "bucket": _tf_epoch_bucket(interval, fetched_at),
        }
    _persist_tf_cache(symbol, interval, df, fetched_at)

    print(f"AlphaBuffalo TF fetch | interval={interval} ttl={ttl}s rows={len(df)}", flush=True)
    return _tag_market_frame(
        df,
        source="LIVE",
        age_seconds=0.0,
        fetched_at=fetched_at,
    )


def _market_data_quality(frames: Dict[str, pd.DataFrame | None]) -> Dict:
    intervals: Dict[str, Dict] = {}
    degraded = False
    for interval, frame in frames.items():
        if frame is None or getattr(frame, "empty", True):
            intervals[interval] = {
                "source": "UNAVAILABLE",
                "age_seconds": None,
                "entry_fresh": False,
            }
            degraded = True
            continue
        source = str(frame.attrs.get("alpha_data_source", "UNKNOWN"))
        age = max(0.0, float(frame.attrs.get("alpha_data_age_seconds", 0.0)))
        fresh_limit = TF_ENTRY_MAX_AGE_SECONDS.get(interval, 0)
        entry_fresh = bool(fresh_limit > 0 and age <= fresh_limit)
        intervals[interval] = {
            "source": source,
            "age_seconds": round(age, 1),
            "entry_fresh": entry_fresh,
        }
        degraded = degraded or source.endswith("_FALLBACK")
    return {
        "status": "DEGRADED" if degraded else "LIVE",
        "degraded": degraded,
        "intervals": intervals,
    }


def _entry_data_fresh(
    engine_signal: Dict | None,
    data_quality: Dict,
) -> tuple[bool, str]:
    """Require fresh entry candles once, after a V4 candidate is selected."""
    intervals = dict(data_quality.get("intervals") or {})
    required = ["15min", "1h", "4h"]
    entry_mode = str((engine_signal or {}).get("entry_mode", "")).upper()
    if "M5" in entry_mode:
        required.append("5min")

    stale = [
        interval
        for interval in required
        if not bool((intervals.get(interval) or {}).get("entry_fresh"))
    ]
    if stale:
        return False, "STALE_ENTRY_DATA:" + ",".join(stale)
    return True, "ENTRY_DATA_FRESH"


def fetch_multi_tf(symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_4h = _fetch_cached_tf(symbol, "4h")
    df_1h = _fetch_cached_tf(symbol, "1h")
    df_15m = _fetch_cached_tf(symbol, "15min")
    return df_4h, df_1h, df_15m


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
    """Check time/risk for Pine OPEN; Harmonic never participates in entry."""
    direction = str(direction or "").upper()
    if direction not in {"BUY", "SELL"}:
        return GateResult(False, "INVALID_ENTRY_DIRECTION")
    if FinalGate is None:
        return GateResult(False, "FINAL_GATE_UNAVAILABLE")

    clock = SessionClock()
    risk_permissions = execution_lifecycle.risk_permissions(
        str(public_symbol or PUBLIC_SYMBOL_DEFAULT).replace("/", "").upper()
    )
    return FinalGate(clock).evaluate(
        clock.get(),
        direction,
        **risk_permissions,
        harmonic_context=None,
        require_harmonic=False,
    )


def fetch_management_m5(symbol: str) -> pd.DataFrame | None:
    """Fetch M5 for active-position management without breaking entry scans."""
    try:
        return _fetch_cached_tf(symbol, "5min", outputsize=250)
    except Exception as exc:
        print(f"AlphaBuffalo M5 management unavailable | {type(exc).__name__}: {exc}", flush=True)
        return None


def _confirmed_h1_indicator_snapshot(df_1h: pd.DataFrame) -> Dict:
    """Return EMA200/RSI14 regimes from closed H1 bars only.

    TwelveData can include a still-forming final candle.  Excluding it keeps
    Telegram crossover events stable and avoids a repainting H1 notification.
    """
    if df_1h is None or getattr(df_1h, "empty", True) or "close" not in df_1h:
        return {}

    close = pd.to_numeric(df_1h["close"], errors="coerce").dropna()
    if len(close) < 16:
        return {}
    confirmed = close.iloc[:-1]
    if len(confirmed) < 15:
        return {}

    ema200 = confirmed.ewm(span=200, adjust=False).mean()
    delta = confirmed.diff()
    average_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    average_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    rsi14 = 100 - (100 / (1 + relative_strength))
    rsi14 = rsi14.mask((average_loss == 0) & (average_gain > 0), 100.0)
    rsi14 = rsi14.mask((average_loss == 0) & (average_gain == 0), 50.0)
    rsi14 = rsi14.fillna(50.0)

    close_now = float(confirmed.iloc[-1])
    ema_now = float(ema200.iloc[-1])
    rsi_now = float(rsi14.iloc[-1])
    ema_regime = "ABOVE_EMA200" if close_now >= ema_now else "BELOW_EMA200"
    rsi_regime = "ABOVE_RSI50" if rsi_now >= 50.0 else "BELOW_RSI50"

    bar_ref = confirmed.index[-1]
    if "datetime" in df_1h.columns:
        bar_ref = df_1h.loc[confirmed.index[-1], "datetime"]
    elif "timestamp" in df_1h.columns:
        bar_ref = df_1h.loc[confirmed.index[-1], "timestamp"]

    return {
        "timeframe": "1H",
        "confirmed_bar": str(bar_ref),
        "close": round(close_now, 6),
        "ema200": round(ema_now, 6),
        "rsi14": round(rsi_now, 4),
        "ema_regime": ema_regime,
        "rsi_regime": rsi_regime,
    }


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


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "ok", "pass", "passed", "win", "wins"}


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


def _telegram_targets(audience: str = "GROUP") -> list[str]:
    """Resolve isolated destinations; silent Pine never falls back anywhere."""
    audience = str(audience or "GROUP").upper()
    if audience == "OWNER":
        return list(TELEGRAM_OWNER_CHAT_IDS)
    if audience == "PINE":
        if not TELEGRAM_PINE_NOTIFICATIONS_ENABLED:
            return []
        candidates = TELEGRAM_PINE_CHAT_IDS or TELEGRAM_OWNER_CHAT_IDS
        group_ids = set(TELEGRAM_CHAT_IDS)
        return [chat_id for chat_id in candidates if chat_id not in group_ids]
    return list(TELEGRAM_CHAT_IDS)


def _telegram_enabled(audience: str = "GROUP") -> bool:
    return bool(TELEGRAM_TOKEN and _telegram_targets(audience))


def _telegram_payload_source(payload: Dict | None = None) -> str:
    payload = payload or {}
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    source = str(
        payload.get("source")
        or signal.get("source")
        or ea.get("command_owner")
        or "UNKNOWN"
    ).strip().upper()
    if source.startswith("PINE"):
        return "PINE"
    if source.startswith("PYTHON"):
        return "PYTHON"
    return source or "UNKNOWN"


def _telegram_payload_audience(payload: Dict | None = None) -> str:
    """Route Pine to a Pine room or owner; Python keeps grouping ownership."""
    return "PINE" if _telegram_payload_source(payload) == "PINE" else "GROUP"


def _telegram_payload_notifications_enabled(payload: Dict | None = None) -> bool:
    """Keep Pine data out of every Telegram destination unless opted in."""
    return (
        _telegram_payload_source(payload) != "PINE"
        or TELEGRAM_PINE_NOTIFICATIONS_ENABLED
    )


def _telegram_signal_key(payload: Dict) -> str:
    ea = payload.get("ea", {}) or {}
    signal = payload.get("signal", {}) or {}
    return "|".join([
        _telegram_payload_source(payload),
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
        engine.get("bos_direction")
        or signal.get("bos_direction")
        or blueprint.get("bos_direction")
        or blueprint.get("choch_direction")
        or direction
    ).upper()
    aligned_bos = bool(
        ea.get("bos_confirmed")
        or signal.get("bos_confirmed")
        or engine.get("bos_confirmed")
        or blueprint.get("bos_confirmed")
        or blueprint.get("choch_confirmed")
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
        f"📊 Type     : {side_icon}",
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


def send_telegram_message(
    text: str,
    *,
    test_mode: bool = False,
    audience: str = "GROUP",
) -> bool:
    if test_mode and not str(text).startswith("🧪 <b>TEST"):
        return False
    attempted_at = datetime.now(timezone.utc).isoformat()
    with LAST_TELEGRAM_LOCK:
        TELEGRAM_DELIVERY_STATUS["last_attempt_at"] = attempted_at
        TELEGRAM_DELIVERY_STATUS["last_error"] = ""
        TELEGRAM_DELIVERY_STATUS["last_audience"] = str(audience or "GROUP").upper()
    if not test_mode and not _telegram_market_is_open():
        with LAST_TELEGRAM_LOCK:
            TELEGRAM_DELIVERY_STATUS["last_error"] = "MARKET_CLOSED"
        return False
    audience = str(audience or "GROUP").upper()
    targets = _telegram_targets(audience)
    if not _telegram_enabled(audience):
        disabled_reason = (
            "TELEGRAM_DISABLED:"
            f"audience={audience}:token_set={bool(TELEGRAM_TOKEN)}:chat_ids={len(targets)}"
        )
        with LAST_TELEGRAM_LOCK:
            TELEGRAM_DELIVERY_STATUS["last_error"] = disabled_reason
        print(
            "AlphaBuffalo Telegram disabled | "
            f"audience={audience} token_set={bool(TELEGRAM_TOKEN)} "
            f"chat_ids={len(targets)}",
            flush=True,
        )
        return False

    text = ensure_telegram_disclaimer(text)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = False
    for chat_id in targets:
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


def _trend_payload_context(payload: Dict) -> Dict:
    """Resolve one complete trend snapshot from the canonical API payload."""
    payload = payload or {}
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    price_action = blueprint.get("price_action", {}) or {}

    price = _first_float(
        blueprint.get("current_price"),
        price_action.get("current_price"),
        payload.get("current_price"),
        payload.get("entry_price"),
        ea.get("entry"),
    )
    session = str(
        ea.get("session")
        or _deep_get(signal, ["gates", "session"], "")
        or blueprint.get("session")
        or ""
    ).strip().upper()
    phases = {
        "m15": (
            price_action.get("m15_phase")
            or blueprint.get("m15_phase")
            or ""
        ),
        "h1": (
            price_action.get("h1_phase")
            or blueprint.get("h1_phase")
            or blueprint.get("trend_h1")
            or ""
        ),
        "h4": (
            price_action.get("h4_phase")
            or blueprint.get("h4_phase")
            or blueprint.get("trend_h4")
            or ""
        ),
    }
    return {
        "payload": payload,
        "signal": signal,
        "ea": ea,
        "blueprint": blueprint,
        "price_action": price_action,
        "price": price,
        "session": session,
        "phases": phases,
    }


def _trend_payload_ready(payload: Dict) -> tuple[bool, str]:
    """Fail closed so an error/fallback payload can never become a trend alert."""
    context = _trend_payload_context(payload)
    signal = context["signal"]
    blueprint = context["blueprint"]
    status = str(
        context["payload"].get("status")
        or signal.get("status")
        or ""
    ).upper()

    if status == ERROR:
        return False, "PIPELINE_ERROR"
    if blueprint.get("is_valid") is False:
        errors = blueprint.get("validation_errors") or []
        return False, "INVALID_BLUEPRINT" + (
            ":" + ",".join(str(value) for value in errors)
            if errors
            else ""
        )
    if context["price"] <= 0:
        return False, "MISSING_PRICE"
    if context["session"] in {"", "-", "UNKNOWN", "CLOSED"}:
        return False, "MISSING_SESSION"

    missing = [
        timeframe.upper()
        for timeframe, value in context["phases"].items()
        if str(value or "").strip().upper() in {"", "-", "NONE", "UNKNOWN"}
    ]
    if missing:
        return False, "MISSING_PHASES:" + ",".join(missing)
    return True, "OK"


def _trend_update_key(payload: Dict) -> str:
    """Build the public trend signature used by hourly/crossover throttling."""
    context = _trend_payload_context(payload)
    signal = context["signal"]
    session = context["session"]
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    bias = _trend_bias_label(signal)
    states = [
        _public_trend_state(context["phases"][timeframe], bias)
        for timeframe in ("m15", "h1", "h4")
    ]
    return "|".join([
        _telegram_payload_source(payload),
        str(symbol).upper(),
        session or "UNKNOWN",
        *states,
    ])


def _h1_cross_key(payload: Dict) -> str:
    signal = payload.get("signal", {}) or {}
    indicators = _deep_get(signal, ["blueprint", "h1_indicators"], {}) or {}
    ema_regime = str(indicators.get("ema_regime") or "").upper()
    rsi_regime = str(indicators.get("rsi_regime") or "").upper()
    if not ema_regime or not rsi_regime:
        return ""
    return f"{ema_regime}|{rsi_regime}"


def _trend_now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_last_trend_update_key() -> str:
    """Restore the last delivered session so a process restart does not resend it."""
    global LAST_TELEGRAM_TREND_UPDATE_KEY
    global LAST_TELEGRAM_TREND_UPDATE_AT
    global LAST_TELEGRAM_H1_CROSS_KEY
    global LAST_TELEGRAM_TREND_STATE_LOADED
    if LAST_TELEGRAM_TREND_STATE_LOADED:
        return LAST_TELEGRAM_TREND_UPDATE_KEY

    LAST_TELEGRAM_TREND_STATE_LOADED = True
    if not TELEGRAM_TREND_STATE_FILE:
        return LAST_TELEGRAM_TREND_UPDATE_KEY
    try:
        data = json.loads(Path(TELEGRAM_TREND_STATE_FILE).read_text(encoding="utf-8"))
        LAST_TELEGRAM_TREND_UPDATE_KEY = str(data.get("last_trend_update_key") or "")
        LAST_TELEGRAM_H1_CROSS_KEY = str(data.get("last_h1_cross_key") or "")
        updated_at = str(data.get("updated_at") or "")
        if updated_at:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            LAST_TELEGRAM_TREND_UPDATE_AT = (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    return LAST_TELEGRAM_TREND_UPDATE_KEY


def _persist_last_trend_update_key(
    key: str,
    *,
    h1_cross_key: str,
    updated_at: datetime,
) -> None:
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
                    "last_h1_cross_key": h1_cross_key,
                    "updated_at": updated_at.astimezone(timezone.utc).isoformat(),
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
    context = _trend_payload_context(payload)
    signal = context["signal"]
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    price = context["price"]
    session = context["session"] or "-"
    bias = _trend_bias_label(signal)
    watch_icon = "🟢" if bias == "BUY" else "🔴" if bias == "SELL" else "⚪"
    watch_side = "B" if bias == "BUY" else "S" if bias == "SELL" else "WAIT"
    m15 = _public_trend_state(context["phases"]["m15"], bias)
    h1 = _public_trend_state(context["phases"]["h1"], bias)
    h4 = _public_trend_state(context["phases"]["h4"], bias)

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


def _h1_prz_confirmation_context(
    payload: Dict,
) -> tuple[bool, str, float, int, float, float]:
    """Expose H1 PRZ location independently from the Harmonic detector."""
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    htf = _deep_get(blueprint, ["prz_layers", "htf"], {}) or {}
    price = _safe_float(
        blueprint.get("current_price")
        or _deep_get(blueprint, ["price_action", "current_price"], 0)
        or ea.get("entry")
    )
    score = int(
        _safe_float(
            ea.get("score")
            or signal.get("score")
            or _deep_get(signal, ["decision", "score"], 0)
        )
    )

    support_low = _safe_float(htf.get("support_low"))
    support_high = _safe_float(htf.get("support_high"))
    resistance_low = _safe_float(htf.get("resistance_low"))
    resistance_high = _safe_float(htf.get("resistance_high"))

    in_support = False
    in_resistance = False
    if price > 0 and support_low > 0 and support_high > 0:
        support_low, support_high = sorted((support_low, support_high))
        in_support = support_low <= price <= support_high
    if price > 0 and resistance_low > 0 and resistance_high > 0:
        resistance_low, resistance_high = sorted((resistance_low, resistance_high))
        in_resistance = resistance_low <= price <= resistance_high

    if in_support and not in_resistance:
        return True, "BUY", price, score, support_low, support_high
    if in_resistance and not in_support:
        return True, "SELL", price, score, resistance_low, resistance_high
    return False, "NONE", price, score, 0.0, 0.0


def _bos_confirmation_context(payload: Dict) -> tuple[bool, str, float, int]:
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    engine = signal.get("engine_v4", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    eligible = bool(
        ea.get("bos_confirmed")
        or signal.get("bos_confirmed")
        or engine.get("bos_confirmed")
        or blueprint.get("bos_confirmed")
        or blueprint.get("choch_confirmed")
    )
    direction = str(
        engine.get("bos_direction")
        or signal.get("bos_direction")
        or blueprint.get("bos_direction")
        or blueprint.get("choch_direction")
        or ea.get("direction")
        or signal.get("direction")
        or engine.get("direction")
        or "NONE"
    ).upper()
    price = _safe_float(
        blueprint.get("current_price")
        or _deep_get(blueprint, ["price_action", "current_price"], 0)
        or ea.get("entry")
    )
    score = int(_safe_float(ea.get("score") or signal.get("score") or _deep_get(signal, ["decision", "score"], 0)))
    return eligible and direction in {"BUY", "SELL"}, direction, price, score


def _confirmation_event_context(payload: Dict) -> Dict:
    prz_ok, prz_direction, price, score, zone_low, zone_high = (
        _h1_prz_confirmation_context(payload)
    )
    if prz_ok:
        return {
            "eligible": True,
            "event": "H1_PRZ",
            "direction": prz_direction,
            "price": price,
            "score": score,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "sources": ["H1_PRZ"],
        }

    bos_ok, bos_direction, price, score = _bos_confirmation_context(payload)
    signal = payload.get("signal", {}) or {}
    blueprint = signal.get("blueprint", {}) or {}
    engine = signal.get("engine_v4", {}) or {}
    sources = (
        engine.get("bos_sources")
        or signal.get("bos_sources")
        or blueprint.get("bos_sources")
        or blueprint.get("choch_sources")
        or []
    )
    return {
        "eligible": bos_ok,
        "event": "BOS",
        "direction": bos_direction,
        "price": price,
        "score": score,
        "zone_low": 0.0,
        "zone_high": 0.0,
        "sources": sources,
    }


def format_telegram_confirmation(payload: Dict) -> str:
    context = _confirmation_event_context(payload)
    direction = str(context.get("direction") or "NONE")
    price = _safe_float(context.get("price"))
    score = int(_safe_float(context.get("score")))
    symbol = payload.get("symbol", SYMBOL_DEFAULT.replace("/", ""))
    icon, label = _public_side(direction)
    score_icon = "📈" if direction == "BUY" else "📉"
    if context.get("event") == "H1_PRZ":
        role = "Demand" if direction == "BUY" else "Supply"
        zone_low = _safe_float(context.get("zone_low"))
        zone_high = _safe_float(context.get("zone_high"))
        return "\n".join([
            "🎯 <b>H1 PRZ — WAIT CONFIRM</b>",
            "━━━━━━━━━━━━━━━━━",
            f"{icon} {label[0]}  {_clean_text(symbol)}",
            f"📍 {role} PRZ: {zone_low:,.2f} - {zone_high:,.2f}",
            f"💰 Price: {price:,.2f}",
            f"{score_icon} Evidence: {score}/10",
            "⚡ รอสัญญาณ CF จาก PA / HA / VSA / BOS",
            TELEGRAM_DISCLAIMER,
        ])
    return "\n".join([
        "🎯 <b>BOS CONFIRMED — ENTRY READY</b>",
        "━━━━━━━━━━━━━━━━━",
        f"{icon} {label[0]}  {_clean_text(symbol)}",
        f"💰 BOS @ {price:,.2f}",
        f"{score_icon} Score: {score}/10",
        "⚡ Signal กำลังประมวลผล...",
        TELEGRAM_DISCLAIMER,
    ])


def _owner_pattern_names(candidates) -> list[str]:
    names: list[str] = []
    for candidate in candidates or []:
        if isinstance(candidate, dict):
            name = (
                candidate.get("pattern")
                or candidate.get("name")
                or candidate.get("selected_pattern")
                or candidate.get("type")
            )
        else:
            name = candidate
        normalized = str(name or "").strip().upper()
        if normalized and normalized not in names:
            names.append(normalized)
    return names[:4]


def _owner_v4_context(payload: Dict) -> Dict:
    """Build an owner-only comparison of V4, PRZ, tunnel, Kivanc and harmonic."""
    signal = payload.get("signal", {}) or {}
    ea = payload.get("ea", {}) or {}
    engine_stages = (
        ea.get("engine_stages")
        or signal.get("engine_stages")
        or (signal.get("engine_v4", {}) or {}).get("engine_stages")
        or {}
    )
    blueprint = signal.get("blueprint", {}) or {}
    diagnostic = signal.get("engine_v4_diagnostics", {}) or {}

    h1_ok, h1_direction, h1_price, _, h1_low, h1_high = (
        _h1_prz_confirmation_context(payload)
    )
    direction = str(diagnostic.get("context_direction") or "NONE").upper()
    if direction not in {"BUY", "SELL"} and h1_direction in {"BUY", "SELL"}:
        direction = h1_direction

    harmonic = blueprint.get("harmonic", {}) or diagnostic.get("harmonic", {}) or {}

    tunnel = _deep_get(blueprint, ["prz_layers", "tunnel_state"], {}) or {}
    if not tunnel:
        raw_tunnel = blueprint.get("tunnel", {}) or {}
        tunnel = {
            **raw_tunnel,
            "state": raw_tunnel.get("state") or "NONE",
            "buy_sweep_armed": False,
            "sell_sweep_armed": False,
            "retest_valid": False,
        }
    if direction not in {"BUY", "SELL"}:
        if _truthy(tunnel.get("buy_sweep_armed")):
            direction = "BUY"
        elif _truthy(tunnel.get("sell_sweep_armed")):
            direction = "SELL"

    m15_touch = bool(diagnostic.get("recent_prz_touch"))
    tunnel_event = any(
        _truthy(tunnel.get(field))
        for field in ("buy_sweep_armed", "sell_sweep_armed", "retest_valid")
    )
    harmonic_found = _truthy(
        harmonic.get("is_real_harmonic")
        if "is_real_harmonic" in harmonic
        else harmonic.get("found")
    )
    candidates = harmonic.get("candidate_patterns") or []
    candidate_names = _owner_pattern_names(candidates)
    kivanc_state = str(
        diagnostic.get("recent_kivanc_state") or "OUTSIDE"
    ).upper()
    kivanc_event = kivanc_state not in {"", "NONE", "OUTSIDE"}
    buy_sniper_armed = bool(diagnostic.get("buy_sniper_armed"))
    sell_sniper_armed = bool(diagnostic.get("sell_sniper_armed"))
    sniper_event = buy_sniper_armed or sell_sniper_armed

    eligible = bool(
        m15_touch
        or h1_ok
        or tunnel_event
        or harmonic_found
        or sniper_event
        or (kivanc_event and direction in {"BUY", "SELL"})
    )

    missing = []
    if direction == "BUY":
        missing = list(diagnostic.get("missing_buy") or [])
    elif direction == "SELL":
        missing = list(diagnostic.get("missing_sell") or [])

    latest = diagnostic.get("latest", {}) or {}
    market_map = blueprint.get("market_close_map", {}) or {}
    map_kivanc = market_map.get("kivanc", {}) or {}
    raw_kivanc_points = [
        (
            "0.618",
            _safe_float(
                map_kivanc.get("fibo_0618") or latest.get("Fib_0618")
            ),
        ),
        (
            "0.720",
            _safe_float(
                map_kivanc.get("fibo_072") or latest.get("Fib_072")
            ),
        ),
        (
            "0.786",
            _safe_float(
                map_kivanc.get("fibo_0786") or latest.get("Fib_0786")
            ),
        ),
        (
            "0.886",
            _safe_float(
                map_kivanc.get("fibo_0886") or latest.get("Fib_0886")
            ),
        ),
        (
            "1.000",
            _safe_float(
                map_kivanc.get("fibo_100") or latest.get("Fib_100")
            ),
        ),
    ]
    kivanc_points = []
    seen_kivanc_values = set()
    for label, value in raw_kivanc_points:
        rounded_value = round(value, 6)
        if value <= 0 or rounded_value in seen_kivanc_values:
            continue
        seen_kivanc_values.add(rounded_value)
        kivanc_points.append({"label": label, "value": value})
    kivanc_levels = [point["value"] for point in kivanc_points]

    if direction == "BUY":
        entry_zone_low = _safe_float(
            latest.get("Pine_PRZ_Support_Low") or h1_low
        )
        entry_zone_high = _safe_float(
            latest.get("Pine_PRZ_Support_High") or h1_high
        )
    elif direction == "SELL":
        entry_zone_low = _safe_float(
            latest.get("Pine_PRZ_Resistance_Low") or h1_low
        )
        entry_zone_high = _safe_float(
            latest.get("Pine_PRZ_Resistance_High") or h1_high
        )
    else:
        entry_zone_low = 0.0
        entry_zone_high = 0.0

    if entry_zone_low > 0 and entry_zone_high > 0:
        entry_zone_low, entry_zone_high = sorted(
            (entry_zone_low, entry_zone_high)
        )
    else:
        entry_zone_low = 0.0
        entry_zone_high = 0.0

    current_price = _safe_float(
        diagnostic.get("current_price")
        or h1_price
        or blueprint.get("current_price")
    )
    entry_watch_candidates = [
        point
        for point in kivanc_points
        if (
            entry_zone_low > 0
            and entry_zone_high > 0
            and entry_zone_low <= point["value"] <= entry_zone_high
        )
    ]
    entry_watch = (
        min(
            entry_watch_candidates,
            key=lambda point: abs(point["value"] - current_price),
        )
        if entry_watch_candidates and current_price > 0
        else entry_watch_candidates[0]
        if entry_watch_candidates
        else None
    )

    pattern = str(
        harmonic.get("selected_pattern")
        or harmonic.get("pattern")
        or ""
    ).strip()
    pattern_state = str(
        harmonic.get("pattern_state") or harmonic.get("state") or "NONE"
    ).upper()
    pattern_tf = str(harmonic.get("source_tf") or "NONE").upper()

    locations = list(diagnostic.get("location_sources") or [])
    if not locations:
        if diagnostic.get("recent_buy_prz_touch"):
            locations.append("M15 DEMAND PRZ TOUCH")
        if diagnostic.get("recent_sell_prz_touch"):
            locations.append("M15 SUPPLY PRZ TOUCH")
    h1_label = f"H1 {'DEMAND' if h1_direction == 'BUY' else 'SUPPLY'} PRZ"
    if h1_ok and h1_label not in locations:
        locations.append(h1_label)

    plan = ea.get("plan_lifecycle", {}) or {}
    ready_checks = plan.get("ready_checks", {}) or {}
    failed_ready_checks = [
        key
        for key, value in ready_checks.items()
        if key != "vsa_bonus" and not bool(value)
    ]
    trigger_source = str(
        diagnostic.get(
            "buy_trigger_source"
            if direction == "BUY"
            else "sell_trigger_source"
            if direction == "SELL"
            else "",
            "NONE",
        )
        or "NONE"
    ).upper()

    return {
        "eligible": eligible,
        "direction": direction,
        "price": current_price,
        "locations": locations,
        "h1_zone_low": h1_low,
        "h1_zone_high": h1_high,
        "v4_status": str(diagnostic.get("status") or "WAIT").upper(),
        "v4_state": str(
            (engine_stages.get("v4") or {}).get("state")
            or signal.get("v4_state")
            or "V4_WAIT_PRZ_TRIGGER"
        ).upper(),
        "v5_state": str(
            (engine_stages.get("v5") or {}).get("state")
            or signal.get("v5_state")
            or (
                f"V5_{direction}_WAIT_BOS_CHOCH"
                if direction in {"BUY", "SELL"}
                else "V5_WAIT_DIRECTION"
            )
        ).upper(),
        "v4_selected": bool(diagnostic.get("v4_selected")),
        "buy_prz_layer_count": int(
            _safe_float(diagnostic.get("buy_prz_layer_count"))
        ),
        "sell_prz_layer_count": int(
            _safe_float(diagnostic.get("sell_prz_layer_count"))
        ),
        "buy_armed": bool(diagnostic.get("buy_armed")),
        "sell_armed": bool(diagnostic.get("sell_armed")),
        "trigger_source": trigger_source,
        "buy_evidence_score": int(
            _safe_float(diagnostic.get("buy_evidence_score"))
        ),
        "sell_evidence_score": int(
            _safe_float(diagnostic.get("sell_evidence_score"))
        ),
        "evidence_min": int(
            _safe_float(diagnostic.get("evidence_min") or 3)
        ),
        "missing": missing,
        "touch_time": str(
            diagnostic.get("buy_touch_time")
            if direction == "BUY"
            else diagnostic.get("sell_touch_time")
            if direction == "SELL"
            else diagnostic.get("latest_bar_time")
            or ""
        ),
        "kivanc_state": kivanc_state,
        "kivanc_levels": kivanc_levels,
        "kivanc_points": kivanc_points,
        "entry_zone_low": entry_zone_low,
        "entry_zone_high": entry_zone_high,
        "entry_watch_level": (
            _safe_float(entry_watch.get("value")) if entry_watch else 0.0
        ),
        "entry_watch_ratio": (
            str(entry_watch.get("label") or "") if entry_watch else ""
        ),
        "entry_watch_status": (
            "WAIT_CF" if entry_watch else "NO_KIVANC_PRZ_OVERLAP"
        ),
        "buy_sniper_armed": buy_sniper_armed,
        "sell_sniper_armed": sell_sniper_armed,
        "sniper_move": _safe_float(
            diagnostic.get("buy_sniper_move")
            if direction == "BUY"
            else diagnostic.get("sell_sniper_move")
            if direction == "SELL"
            else 0.0
        ),
        "sniper_kivanc": _safe_float(
            diagnostic.get("buy_sniper_kivanc")
            if direction == "BUY"
            else diagnostic.get("sell_sniper_kivanc")
            if direction == "SELL"
            else 0.0
        ),
        "sniper_bb": _safe_float(
            diagnostic.get("buy_sniper_bb")
            if direction == "BUY"
            else diagnostic.get("sell_sniper_bb")
            if direction == "SELL"
            else 0.0
        ),
        "sniper_bb_tf": str(
            diagnostic.get("buy_sniper_bb_tf")
            if direction == "BUY"
            else diagnostic.get("sell_sniper_bb_tf")
            if direction == "SELL"
            else "NONE"
        ).upper(),
        "sniper_mode": str(
            diagnostic.get("buy_sniper_mode")
            if direction == "BUY"
            else diagnostic.get("sell_sniper_mode")
            if direction == "SELL"
            else "NONE"
        ).upper(),
        "sniper_point_count": int(
            _safe_float(
                diagnostic.get("buy_sniper_point_count")
                if direction == "BUY"
                else diagnostic.get("sell_sniper_point_count")
                if direction == "SELL"
                else 0
            )
        ),
        "tunnel_state": str(tunnel.get("state") or "NONE").upper(),
        "tunnel_valid": bool(tunnel.get("valid")),
        "tunnel_event": tunnel_event,
        "buy_tunnel_sweep": bool(tunnel.get("buy_sweep_armed")),
        "sell_tunnel_sweep": bool(tunnel.get("sell_sweep_armed")),
        "harmonic_found": harmonic_found,
        "harmonic_pattern": pattern,
        "harmonic_state": pattern_state,
        "harmonic_tf": pattern_tf,
        "candidate_names": candidate_names,
        "ea_action": str(ea.get("action") or "WAIT").upper(),
        "ea_execution_state": str(
            ea.get("execution_state") or "WATCH"
        ).upper(),
        "ea_reason": str(ea.get("reason") or signal.get("reason") or ""),
        "failed_ready_checks": failed_ready_checks,
    }


def format_telegram_owner_v4_context(payload: Dict) -> str:
    """Private diagnostics only; this message never represents an EA order."""
    context = _owner_v4_context(payload)
    direction = str(context.get("direction") or "NONE")
    icon, side = _public_side(direction)
    locations = context.get("locations") or ["WAIT LOCATION"]
    missing = context.get("missing") or []
    tunnel_flags = []
    if context.get("buy_tunnel_sweep"):
        tunnel_flags.append("BUY SWEEP")
    if context.get("sell_tunnel_sweep"):
        tunnel_flags.append("SELL SWEEP")
    if context.get("tunnel_event") and not tunnel_flags:
        tunnel_flags.append("RETEST")

    if context.get("harmonic_found"):
        harmonic_text = " ".join(
            value
            for value in (
                str(context.get("harmonic_pattern") or "XABCD").upper(),
                str(context.get("harmonic_state") or ""),
                f"({context.get('harmonic_tf')})"
                if str(context.get("harmonic_tf") or "NONE") != "NONE"
                else "",
            )
            if value
        )
    else:
        harmonic_text = "NO VALID XABCD"

    candidate_text = (
        ", ".join(context.get("candidate_names") or [])
        or "NO MATCHING PATTERN"
    )
    raw_level_state = str(context.get("kivanc_state") or "").upper()
    level_state = (
        "BUY ARMED"
        if "BUY" in raw_level_state
        else "SELL ARMED"
        if "SELL" in raw_level_state
        else "ACTIVE"
        if raw_level_state not in {"", "NONE", "OUTSIDE", "WAIT"}
        else "WAIT"
    )
    entry_watch_level = _safe_float(context.get("entry_watch_level"))
    entry_zone_low = _safe_float(context.get("entry_zone_low"))
    entry_zone_high = _safe_float(context.get("entry_zone_high"))
    if entry_watch_level > 0:
        entry_watch_text = (
            f"Level {entry_watch_level:,.2f}"
            f" | PRZ {entry_zone_low:,.2f}-{entry_zone_high:,.2f}"
            " | WAIT CF"
        )
    else:
        entry_watch_text = "NO LEVEL / PRZ OVERLAP"
    sniper_side = (
        "BUY ARMED"
        if context.get("buy_sniper_armed")
        else "SELL ARMED"
        if context.get("sell_sniper_armed")
        else "WAIT"
    )
    sniper_text = sniper_side
    if sniper_side != "WAIT":
        sniper_mode = _clean_text(context.get("sniper_mode") or "SINGLE_SWEEP")
        sniper_points = int(_safe_float(context.get("sniper_point_count")))
        sniper_text += (
            f" | {sniper_mode}"
            f" x{max(1, sniper_points)}"
            f" | M5 ${_safe_float(context.get('sniper_move')):,.2f}"
            f" | Level {_safe_float(context.get('sniper_kivanc')):,.2f}"
            f" | BB-{_clean_text(context.get('sniper_bb_tf') or 'NONE')}"
            f" {_safe_float(context.get('sniper_bb')):,.2f}"
        )

    ea_action = str(context.get("ea_action") or "WAIT").upper()
    execution_state = str(
        context.get("ea_execution_state") or "WATCH"
    ).upper()
    failed_checks = context.get("failed_ready_checks") or []
    if ea_action == "OPEN" and execution_state == "READY":
        ea_text = "OPEN — Levels / RR / Risk ผ่านครบ"
    elif context.get("v4_selected"):
        detail = (
            ", ".join(failed_checks)
            or context.get("ea_reason")
            or "ADAPTER WATCH"
        )
        ea_text = f"WAIT — V4 selected; {_clean_text(detail)}"
    elif context.get("buy_armed") or context.get("sell_armed"):
        ea_text = "HOLD — ARMED; รอ HA / Pinbar break / M5 Sniper"
    else:
        ea_text = (
            "HOLD — "
            + _clean_text(
                context.get("ea_reason")
                or "รอ PRZ Layers + Evidence"
            )
        )
    layer_text = (
        f"{int(context.get('buy_prz_layer_count') or 0)} / "
        f"{int(context.get('sell_prz_layer_count') or 0)}"
    )

    message = "\n".join([
        "🔎 <b>V4 OWNER CONTEXT</b>",
        "━━━━━━━━━━━━━━━━━",
        f"📌 {_clean_text(payload.get('symbol') or PUBLIC_SYMBOL_DEFAULT)} | {icon} {_clean_text(side)}",
        f"💰 Price: {_safe_float(context.get('price')):,.2f}",
        f"📍 PRZ: {_clean_text(' + '.join(locations))}",
        f"🧩 V4: {_clean_text(context.get('v4_status'))}"
        f" | Layers B/S {layer_text}",
        f"🧭 Stages: {_clean_text(context.get('v4_state'))}"
        f" | {_clean_text(context.get('v5_state'))}",
        f"📊 Evidence B/S: {int(context.get('buy_evidence_score') or 0)}"
        f" / {int(context.get('sell_evidence_score') or 0)}"
        f" (need {int(context.get('evidence_min') or 3)})",
        f"🎯 M5 Sniper: {_clean_text(sniper_text)}",
        f"🚇 Tunnel: {_clean_text(context.get('tunnel_state'))}"
        + (f" | {_clean_text(', '.join(tunnel_flags))}" if tunnel_flags else ""),
        f"🟡 Level state: {_clean_text(level_state)}",
        f"🎯 Entry cluster: {_clean_text(entry_watch_text)}",
        f"🔷 Harmonic target: {_clean_text(harmonic_text)}"
        " | TARGET ONLY AFTER BOS",
        f"📐 Pattern compare: {_clean_text(candidate_text)}",
        f"⚡ Trigger: {_clean_text(context.get('trigger_source') or 'NONE')}",
        f"⛔ Missing: {_clean_text(', '.join(missing) if missing else 'NONE')}",
        f"🤖 EA: {ea_text}",
        TELEGRAM_DISCLAIMER,
    ])
    # Owner messages must not disclose the proprietary level model even when
    # an upstream diagnostic/reason contains its internal name.
    return re.sub(r"kivanc", "LEVEL", message, flags=re.IGNORECASE)


def maybe_broadcast_owner_v4_context(payload: Dict) -> bool:
    """Send one private state transition; never route V4 internals to groups."""
    global LAST_TELEGRAM_OWNER_CONTEXT_KEY
    if (
        not TELEGRAM_NOTIFY_OWNER_CONTEXT
        or not _telegram_payload_notifications_enabled(payload)
        or not _telegram_market_is_open(payload)
        or not _telegram_enabled("OWNER")
    ):
        return False

    context = _owner_v4_context(payload)
    if not context.get("eligible") or context.get("ea_action") == "OPEN":
        return False

    key = "|".join([
        str(payload.get("symbol") or PUBLIC_SYMBOL_DEFAULT).upper(),
        str(context.get("direction") or "NONE"),
        str(context.get("touch_time") or ""),
        str(context.get("v4_status") or ""),
        str(context.get("buy_evidence_score") or 0),
        str(context.get("sell_evidence_score") or 0),
        str(context.get("kivanc_state") or ""),
        str(context.get("buy_sniper_armed") or False),
        str(context.get("sell_sniper_armed") or False),
        str(context.get("sniper_move") or 0.0),
        str(context.get("tunnel_state") or ""),
        str(context.get("buy_tunnel_sweep") or False),
        str(context.get("sell_tunnel_sweep") or False),
        str(context.get("harmonic_pattern") or ""),
        str(context.get("harmonic_state") or ""),
        ",".join(context.get("candidate_names") or []),
    ])
    with LAST_TELEGRAM_LOCK:
        if key == LAST_TELEGRAM_OWNER_CONTEXT_KEY:
            return False
        previous = LAST_TELEGRAM_OWNER_CONTEXT_KEY
        LAST_TELEGRAM_OWNER_CONTEXT_KEY = key

    sent = send_telegram_message(
        format_telegram_owner_v4_context(payload),
        audience="OWNER",
    )
    if not sent:
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_OWNER_CONTEXT_KEY == key:
                LAST_TELEGRAM_OWNER_CONTEXT_KEY = previous
    return sent


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
                "bos_confirmed": True,
                "bos_direction": direction,
                "bos_sources": ["M15"],
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
    """Send one WAIT-CF event per H1 PRZ entry or confirmed BOS."""
    global LAST_TELEGRAM_CONFIRM_KEY
    audience = _telegram_payload_audience(payload)
    if (
        not TELEGRAM_NOTIFY_WAIT
        or not _telegram_payload_notifications_enabled(payload)
        or not _telegram_market_is_open(payload)
        or not _telegram_enabled(audience)
    ):
        return False
    ea = payload.get("ea", {}) or {}
    context = _confirmation_event_context(payload)
    eligible = bool(context.get("eligible"))
    direction = str(context.get("direction") or "NONE").upper()
    if not eligible or str(ea.get("action") or "WAIT").upper() == "OPEN":
        if not eligible:
            with LAST_TELEGRAM_LOCK:
                LAST_TELEGRAM_CONFIRM_KEY = ""
        return False
    sources = context.get("sources") or []
    key = "|".join([
        _telegram_payload_source(payload),
        str(payload.get("symbol") or PUBLIC_SYMBOL_DEFAULT).upper(),
        str(context.get("event") or "WAIT_CF"),
        direction,
        ",".join(sources) if isinstance(sources, list) else str(sources),
        f"{_safe_float(context.get('zone_low')):.2f}",
        f"{_safe_float(context.get('zone_high')):.2f}",
    ])
    with LAST_TELEGRAM_LOCK:
        if key == LAST_TELEGRAM_CONFIRM_KEY:
            return False
        previous = LAST_TELEGRAM_CONFIRM_KEY
        LAST_TELEGRAM_CONFIRM_KEY = key
    sent = send_telegram_message(
        format_telegram_confirmation(payload),
        audience=audience,
    )
    if not sent:
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_CONFIRM_KEY == key:
                LAST_TELEGRAM_CONFIRM_KEY = previous
    return sent


def maybe_broadcast_trend_update(payload: Dict) -> bool:
    """Send hourly, or immediately when closed-H1 EMA/RSI regime changes."""
    global LAST_TELEGRAM_TREND_UPDATE_KEY
    global LAST_TELEGRAM_TREND_UPDATE_AT
    global LAST_TELEGRAM_H1_CROSS_KEY
    if (
        not _telegram_payload_notifications_enabled(payload)
        or not _telegram_market_is_open(payload)
    ):
        return False

    payload_ready, _ = _trend_payload_ready(payload)
    if not payload_ready:
        return False

    audience = _telegram_payload_audience(payload)
    if not TELEGRAM_NOTIFY_TREND_UPDATE:
        return False
    if not _telegram_enabled(audience):
        return False

    key = _trend_update_key(payload)
    h1_cross_key = _h1_cross_key(payload)
    now = _trend_now_utc()
    with LAST_TELEGRAM_LOCK:
        _load_last_trend_update_key()
        hour_due = (
            LAST_TELEGRAM_TREND_UPDATE_AT is None
            or (now - LAST_TELEGRAM_TREND_UPDATE_AT).total_seconds()
            >= TELEGRAM_TREND_MIN_INTERVAL_SECONDS
        )
        h1_crossed = bool(
            h1_cross_key
            and LAST_TELEGRAM_H1_CROSS_KEY
            and h1_cross_key != LAST_TELEGRAM_H1_CROSS_KEY
        )
        if not hour_due and not h1_crossed:
            return False
        previous_key = LAST_TELEGRAM_TREND_UPDATE_KEY
        previous_at = LAST_TELEGRAM_TREND_UPDATE_AT
        previous_h1_cross_key = LAST_TELEGRAM_H1_CROSS_KEY
        # Reserve this delivery before the network call so parallel loops
        # cannot publish the same hourly/crossover snapshot twice.
        LAST_TELEGRAM_TREND_UPDATE_KEY = key
        LAST_TELEGRAM_TREND_UPDATE_AT = now
        if h1_cross_key:
            LAST_TELEGRAM_H1_CROSS_KEY = h1_cross_key

    sent = send_telegram_message(
        format_telegram_trend_update(payload),
        audience=audience,
    )
    if sent:
        with LAST_TELEGRAM_LOCK:
            _persist_last_trend_update_key(
                key,
                h1_cross_key=LAST_TELEGRAM_H1_CROSS_KEY,
                updated_at=now,
            )
    else:
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_TREND_UPDATE_KEY == key:
                LAST_TELEGRAM_TREND_UPDATE_KEY = previous_key
                LAST_TELEGRAM_TREND_UPDATE_AT = previous_at
                LAST_TELEGRAM_H1_CROSS_KEY = previous_h1_cross_key
    return sent

def maybe_broadcast_signal(payload: Dict) -> bool:
    """Broadcast an accepted OPEN once; lifecycle/fill/debug events stay silent."""
    global LAST_TELEGRAM_SIGNAL_KEY

    audience = _telegram_payload_audience(payload)
    if (
        not _telegram_payload_notifications_enabled(payload)
        or not _telegram_market_is_open(payload)
        or not _telegram_enabled(audience)
    ):
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
    signal_key = _telegram_signal_key(payload)
    with LAST_TELEGRAM_LOCK:
        if signal_key and signal_key == LAST_TELEGRAM_SIGNAL_KEY:
            return False
        previous_key = LAST_TELEGRAM_SIGNAL_KEY
        LAST_TELEGRAM_SIGNAL_KEY = signal_key

    sent = send_telegram_message(
        format_telegram_signal(payload),
        audience=audience,
    )
    if not sent:
        # Do not permanently deduplicate a delivery that never reached Telegram.
        with LAST_TELEGRAM_LOCK:
            if LAST_TELEGRAM_SIGNAL_KEY == signal_key:
                LAST_TELEGRAM_SIGNAL_KEY = previous_key
    return sent


def broadcast_runtime_telegram(payload: Dict) -> bool:
    """Route production Telegram output through the confirmed OPEN lane.

    SIGNAL_ONLY is the production default. Trend, WAIT-CF, owner-context, and
    Pine-monitor formatters remain available for explicit diagnostics/tests,
    but scanner loops cannot publish them while this mode is enabled.
    """
    signal_sent = maybe_broadcast_signal(payload)
    if TELEGRAM_SIGNAL_ONLY_MODE:
        return signal_sent

    maybe_broadcast_trend_update(payload)
    maybe_broadcast_confirmation(payload)
    maybe_broadcast_owner_v4_context(payload)
    return signal_sent


def _log_engine_v4_debug(message: str) -> None:
    """Best-effort runtime trace for engine_v4 selection. Never blocks trading loop."""
    try:
        print(f"AlphaBuffalo engine_v4 | {message}", flush=True)
    except Exception:
        pass


def _run_engine_v4_baseline(
    df_15m: pd.DataFrame,
    symbol: str = PUBLIC_SYMBOL_DEFAULT,
    blueprint=None,
    diagnostics_out: Dict | None = None,
    df_5m: pd.DataFrame | None = None,
    df_1h: pd.DataFrame | None = None,
) -> Dict | None:

    if add_indicators is None or SignalRouter is None or FinalGate is None or BuySignalEngine is None or SellSignalEngine is None:
        if diagnostics_out is not None:
            diagnostics_out.update(
                {
                    "status": "IMPORT_ERROR",
                    "v4_selected": False,
                    "recent_prz_touch": False,
                }
            )
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
        "In_H1_PRZ_Support", "In_H1_PRZ_Resistance",
        "In_PRZ_A_Support", "In_PRZ_A_Resistance",
        "In_PRZ_B_Support", "In_PRZ_B_Resistance",
        "V4_Demand_PRZ_Touch", "V4_Supply_PRZ_Touch",
        "V4_Demand_PRZ_Layer_Count", "V4_Supply_PRZ_Layer_Count",
        "V4_Demand_PRZ_Qualified", "V4_Supply_PRZ_Qualified",
        "V4_Buy_M5_Sniper_Evidence", "V4_Sell_M5_Sniper_Evidence",
        "V4_Buy_M5_Sniper_Move", "V4_Sell_M5_Sniper_Move",
        "V4_Buy_M5_Sniper_Kivanc", "V4_Sell_M5_Sniper_Kivanc",
        "V4_Buy_M5_Sniper_BB", "V4_Sell_M5_Sniper_BB",
        "V4_Buy_M5_Sniper_BB_TF", "V4_Sell_M5_Sniper_BB_TF",
        "V4_Buy_H1_Green_Dot", "V4_Buy_Green_Dot_Trigger",
        "V4_Buy_Location_Memory", "V4_Sell_Location_Memory",
        "V4_Buy_Evidence_Score", "V4_Sell_Evidence_Score",
        "V4_Buy_Armed", "V4_Sell_Armed",
        "V4_M15_Bar_Closed",
        "V4_Buy_HA_Trigger", "V4_Sell_HA_Trigger",
        "V4_Buy_Pinbar_Trigger", "V4_Sell_Pinbar_Trigger",
        "V4_Buy_Sniper_Trigger", "V4_Sell_Sniper_Trigger",
        "V4_Buy_Trigger_Source", "V4_Sell_Trigger_Source",
        "V4_Buy_Memory_Trigger", "V4_Sell_Memory_Trigger",
        "V4_Buy_Entry_Zone", "V4_Sell_Entry_Zone", "V4_Buy_Setup", "V4_Sell_Setup",
        "V4_Block_Sell_At_Lower", "V4_Block_Buy_At_Upper", "CHoCH_Bull", "CHoCH_Bear",
    ]
    try:
        if df_15m is None or getattr(df_15m, "empty", True):
            if diagnostics_out is not None:
                diagnostics_out.update(
                    {
                        "status": "NO_DATA",
                        "v4_selected": False,
                        "recent_prz_touch": False,
                    }
                )
            _log_engine_v4_debug("none reason=EMPTY_DF")
            return None

        df = _ensure_engine_v4_datetime_index(df_15m)
        df = add_indicators(df)
        df = _overlay_blueprint_prz_memory(
            df,
            blueprint,
            lock_bars=int(os.getenv("ENGINE_V4_LOCATION_LOCK_BARS", "4")),
            df_5m=df_5m,
            df_1h=df_1h,
        )
        diagnostics = _engine_v4_wait_diagnostics(
            df,
            blueprint,
            lookback_bars=int(os.getenv("ENGINE_V4_LOOKBACK_BARS", "6")),
        )
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        session_clock = SessionClock()
        risk_permissions = execution_lifecycle.risk_permissions(symbol)
        harmonic_context = _harmonic_gate_context(blueprint) if blueprint else None
        routed = SignalRouter(
            clock=session_clock,
            gate=FinalGate(session_clock),
            buy_engine=BuySignalEngine(),
            sell_engine=SellSignalEngine(),
        ).process(
            df,
            **risk_permissions,
            harmonic_context=harmonic_context,
            require_harmonic=False,
        )
        signal = routed[0] if routed else None
        if diagnostics_out is not None:
            diagnostics_out["v4_selected"] = bool(signal)
            diagnostics_out["selected_direction"] = (
                str(signal.get("direction") or "").upper() if signal else None
            )
            if signal:
                diagnostics_out["status"] = "V4_SELECTED"

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
            "V4_Demand_PRZ_Touch", "V4_Supply_PRZ_Touch",
            "V4_Buy_M5_Sniper_Evidence", "V4_Sell_M5_Sniper_Evidence",
            "V4_Buy_Location_Memory", "V4_Sell_Location_Memory",
            "V4_Buy_Memory_Trigger", "V4_Sell_Memory_Trigger",
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
        if diagnostics_out is not None:
            diagnostics_out.update(
                {
                    "status": "ERROR",
                    "v4_selected": False,
                    "error_type": type(exc).__name__,
                }
            )
        _log_engine_v4_debug(f"none reason=EXCEPTION type={type(exc).__name__} error={exc}")
        return None


def build_ea_payload(symbol: str, signal: Dict) -> Dict:
    """Compatibility facade for the canonical EA execution contract."""
    return _build_ea_payload(symbol, signal, min_rr=TRADE_MIN_RR)


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
        df_5m = fetch_management_m5(symbol)
        data_quality = _market_data_quality(
            {
                "4h": df_4h,
                "1h": df_1h,
                "15min": df_15m,
                "5min": df_5m,
            }
        )

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
        # Operational notification metadata only.  This does not participate
        # in the entry gate and therefore cannot create an EA command.
        signal.setdefault("blueprint", {})["h1_indicators"] = (
            _confirmed_h1_indicator_snapshot(df_1h)
        )

        # Production baseline overlay:
        # v12 scanner/blueprint stays intact, but proven engine_v4 BUY/SELL baseline
        # becomes the actual trade source when it produces confirmed levels.
        engine_v4_diagnostics: Dict = {}
        engine_v4_signal = _run_engine_v4_baseline(
            df_15m,
            public_symbol,
            blueprint=blueprint,
            diagnostics_out=engine_v4_diagnostics,
            df_5m=df_5m,
            df_1h=df_1h,
        )
        if (
            engine_v4_signal
            and str(engine_v4_signal.get("status", "")).upper() == SIGNAL
        ):
            entry_fresh, freshness_reason = _entry_data_fresh(
                engine_v4_signal,
                data_quality,
            )
            if not entry_fresh:
                engine_v4_signal = {
                    **engine_v4_signal,
                    "status": BLOCKED,
                    "reason": freshness_reason,
                }
                engine_v4_diagnostics["data_freshness_block"] = freshness_reason
        signal = _apply_engine_v4_signal(signal, engine_v4_signal)
        # Read-only owner observability. This snapshot can explain a PRZ touch
        # that is still waiting for HA/PA/VSA, but can never create an EA OPEN.
        signal["engine_v4_diagnostics"] = engine_v4_diagnostics
        signal["market_data_quality"] = data_quality
        ea = build_ea_payload(public_symbol, signal)
        ea = _attach_execution_lifecycle(
            data_symbol=symbol,
            public_symbol=public_symbol,
            df_15m=df_15m,
            ea=ea,
        )
        return build_api_signal_response(public_symbol, signal, ea)
    except Exception as exc:
        error_code = _safe_pipeline_error_code(exc)
        print(
            "AlphaBuffalo pipeline error | "
            f"symbol={public_symbol} code={error_code}",
            flush=True,
        )
        error_signal = {
            "status": ERROR,
            "direction": None,
            "reason": error_code,
            "decision": {
                "action": "NONE",
                "confidence": 0.0,
                "score": 0,
                "reason": f"PIPELINE_ERROR:{error_code}",
                "grade": "ERROR",
                "error_code": error_code,
            },
            "gates": {"blueprint_valid": False, "session": ""},
            "blueprint": {"is_valid": False},
            "market_data_quality": {
                "status": "ERROR",
                "degraded": True,
                "error_code": error_code,
                "intervals": {},
            },
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
        signal = dict(payload.get("signal") or {})
        decision = dict(signal.get("decision") or {})
        if str(decision.get("grade", "")).upper() == "ERROR":
            error_code = (
                decision.get("error_code")
                or signal.get("reason")
                or "UNKNOWN_PIPELINE_ERROR"
            )
            return {
                "action": "HOLD",
                "reason": f"PIPELINE_ERROR:{error_code}",
            }
        signal_reason = str(signal.get("reason") or "")
        if signal_reason.startswith("STALE_ENTRY_DATA:"):
            return {"action": "HOLD", "reason": signal_reason}
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
            broadcast_runtime_telegram(payload)
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
    """Optional dedicated-Pine monitoring; disabled in production by default."""
    print(
        "AlphaBuffalo Pine Telegram monitor started | "
        f"interval={TELEGRAM_PINE_MONITOR_INTERVAL_SECONDS}s",
        flush=True,
    )
    while True:
        try:
            if (
                not TELEGRAM_SIGNAL_ONLY_MODE
                and TELEGRAM_NOTIFY_TREND_UPDATE
                and _telegram_market_is_open()
            ):
                monitor = _pine_monitor_payload()
                maybe_broadcast_trend_update(monitor)
                maybe_broadcast_confirmation(monitor)
                maybe_broadcast_owner_v4_context(monitor)
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
            "Telegram output="
            f"{'ENABLED' if TELEGRAM_PINE_NOTIFICATIONS_ENABLED else 'DISABLED'}",
            flush=True,
        )
        if (
            not TELEGRAM_SIGNAL_ONLY_MODE
            and TELEGRAM_PINE_NOTIFICATIONS_ENABLED
            and TELEGRAM_PINE_MONITOR_ENABLED
        ):
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
    latest = _get_latest_signal()
    signal = dict(latest.get("signal") or {})
    decision = dict(signal.get("decision") or {})
    grade = str(decision.get("grade") or "STARTING")
    error_code = (
        decision.get("error_code")
        if grade.upper() == "ERROR"
        else None
    )
    cooldown_reason, cooldown_until = _active_market_data_cooldown()
    return {
        "status": "alive",
        "version": "v12-core",
        "signal_source": SIGNAL_SOURCE,
        "pipeline_grade": grade,
        "pipeline_error": error_code,
        "market_data_cooldown": cooldown_reason or None,
        "market_data_retry_at": (
            datetime.fromtimestamp(cooldown_until, tz=timezone.utc).isoformat()
            if cooldown_until
            else None
        ),
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
    pine_targets = _telegram_targets("PINE")
    owner_targets = _telegram_targets("OWNER")
    pending = (
        pine_signal_bridge.pending_command(public_symbol)
        if SIGNAL_SOURCE == "PINE"
        else python_signal_bridge.pending_command(public_symbol)
    )
    return {
        "status": "ok",
        "signal_source": SIGNAL_SOURCE,
        "telegram_enabled": _telegram_enabled(),
        "pine_telegram_enabled": _telegram_enabled("PINE"),
        "pine_notifications_enabled": TELEGRAM_PINE_NOTIFICATIONS_ENABLED,
        "owner_context_enabled": bool(
            TELEGRAM_NOTIFY_OWNER_CONTEXT and _telegram_enabled("OWNER")
        ),
        "chat_ids_count": len(TELEGRAM_CHAT_IDS),
        "group_owner": "PYTHON",
        "group_chat_ids_count": len(TELEGRAM_CHAT_IDS),
        "pine_chat_ids_count": len(TELEGRAM_PINE_CHAT_IDS),
        "pine_effective_chat_ids_count": len(pine_targets),
        "owner_fallback_configured": bool(TELEGRAM_OWNER_CHAT_IDS),
        "owner_chat_ids_count": len(owner_targets),
        "pine_destination": (
            "PINE_ROOM"
            if TELEGRAM_PINE_CHAT_IDS and pine_targets
            else "OWNER"
            if TELEGRAM_OWNER_CHAT_IDS and pine_targets
            else "DISABLED"
        ),
        "pine_group_fallback": False,
        "pine_notification_only": bool(
            SIGNAL_SOURCE == "PYTHON" and PINE_NOTIFICATION_ONLY
        ),
        "pine_monitor_enabled": bool(
            not TELEGRAM_SIGNAL_ONLY_MODE
            and TELEGRAM_PINE_NOTIFICATIONS_ENABLED
            and TELEGRAM_PINE_MONITOR_ENABLED
        ),
        "signal_only_mode": TELEGRAM_SIGNAL_ONLY_MODE,
        "trend_update_enabled": bool(
            not TELEGRAM_SIGNAL_ONLY_MODE
            and TELEGRAM_NOTIFY_TREND_UPDATE
        ),
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


@app.get("/execution/hourly-stats")
def execution_hourly_stats(key: str = "", symbol: str = PUBLIC_SYMBOL_DEFAULT):
    """Diagnostic-only win-rate-by-UTC-hour snapshot, built from real closed
    trades (see execution_lifecycle.py's _record_close). Ported from clean
    v5's trade_manager.py HourlyStats -- the seed of an AI-learning
    feedback loop. Never gates entry; nothing in engine_v4 reads this.
    """
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")
    public_symbol = symbol.replace("/", "")
    return {
        "status": "ok",
        "symbol": public_symbol,
        "hourly_stats": execution_lifecycle.hourly_stats_summary(public_symbol),
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
        "newday": newday_diagnostic(symbol.replace("/", "")),
    }


@app.get("/newday/map")
def newday_map(key: str = "", symbol: str = SYMBOL_DEFAULT):
    """Diagnostic-only view of the latest market-close NewdayMarketMap.

    This never gates entry (see PROJECT_CONTRACT.md / ALPHA_FUSION_CONTRACT.md
    red lines). It exists so the map that scripts/daily_market_scan.py already
    builds is actually reachable at runtime instead of sitting unused on disk.
    Returns available=false rather than an error when no map has been
    generated yet -- callers must treat that as "no newday context", not a
    failure.
    """
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    public_symbol = symbol.replace("/", "")
    diag = newday_diagnostic(public_symbol)
    return {
        "status": "ok",
        "symbol": public_symbol,
        "newday": diag,
    }


@app.get("/context/fundamental")
def context_fundamental(key: str = "", direction: str = ""):
    """Diagnostic-only DXY / Fear&Greed / COT / news-calendar snapshot.

    Ported from clean v5's context_engine.py + plugin_* modules -- v12-core
    had no fundamental layer at all before this. Never gates entry: pass
    `direction` (BUY/SELL) to also see the non-blocking combined adjustment
    that could later feed a soft risk_adjustment, same role as
    /newday/map's bias hint.
    """
    if not verify_license(key):
        raise HTTPException(status_code=403, detail="INVALID_LICENSE")

    direction = direction.upper().strip()
    if direction in {"BUY", "SELL"}:
        return {"status": "ok", "direction": direction, "fundamental": fundamental_bias_for_direction(direction)}
    return {"status": "ok", "fundamental": fundamental_diagnostic()}


@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    notification_only = SIGNAL_SOURCE == "PYTHON" and PINE_NOTIFICATION_ONLY
    if SIGNAL_SOURCE not in {"PINE", "HYBRID"} and not notification_only:
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
    # must pass the same market/risk gate; if it fails, keep the close
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
        # In Python production mode a transient bridge validates Pine without
        # persisting any command. Pine can notify its isolated destination but
        # can never race the dedicated Python EA command queue.
        relay = PineSignalBridge(None) if notification_only else pine_signal_bridge
        command = relay.ingest(effective_payload)
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

    if notification_only:
        public_payload = build_pine_api_payload(command)
        telegram_notified = maybe_broadcast_signal(public_payload)
        print(
            "AlphaBuffalo Pine notification accepted | "
            f"direction={command.get('direction')} signal_id={command.get('signal_id')} "
            f"execution_queued=False telegram_notified={telegram_notified}",
            flush=True,
        )
        return {
            "status": "accepted",
            "source": "PINE",
            "notification_only": True,
            "execution_queued": False,
            "command": {
                "action": "HOLD",
                "reason": "PINE_NOTIFICATION_ONLY",
                "signal_id": command.get("signal_id"),
                "direction": command.get("direction"),
            },
            "telegram_notified": telegram_notified,
        }

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
