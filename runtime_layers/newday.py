"""Read-only access to the NewdayMarketMap built by scripts/daily_market_scan.py.

This module never runs the scan itself and never blocks entry. It only loads
whatever the most recent market-close map on disk says, so `alpha_buffalo_signal.py`
and engine_v4 can expose it as diagnostic/context. Per PROJECT_CONTRACT.md and
ALPHA_FUSION_CONTRACT.md, newday bias must never become a V4 entry gate -- it is
context only, same as harmonic_bias_gate.py's post-BOS target role.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.models.newday_market_map import NewdayMarketMap

_MAP_DIR_ENV = "ALPHA_MARKET_MAP_DIR"
_DEFAULT_MAP_DIR = "data/market_maps"
_LOOKBACK_DAYS = 3  # tolerate a missed/late scan without going fully blind


def _map_dir() -> Path:
    raw = os.getenv(_MAP_DIR_ENV, _DEFAULT_MAP_DIR)
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _candidate_paths(public_symbol: str, as_of: Optional[datetime] = None) -> list[Path]:
    """Most-recent-first list of market map paths to try.

    daily_market_scan.py writes one file per UTC date it scanned (normally
    "yesterday" relative to when it ran, i.e. the just-closed trading day).
    We look back a few days so a missed cron run doesn't make the whole
    feature silently vanish -- the caller still gets the last known bias
    with its own map_date so it can decide the data is stale.
    """
    base = as_of or datetime.now(timezone.utc)
    out = []
    for offset in range(0, _LOOKBACK_DAYS + 1):
        d = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        out.append(_map_dir() / f"{public_symbol}_{d}.json")
    return out


def load_latest_newday_map(
    public_symbol: str, as_of: Optional[datetime] = None
) -> Optional[NewdayMarketMap]:
    """Return the most recent NewdayMarketMap available on disk, or None.

    Returns None (never raises) when no map has been generated yet, or the
    file on disk fails to parse -- callers must treat this as "no newday
    context available" and continue exactly as if the feature did not exist.
    """
    for path in _candidate_paths(public_symbol, as_of=as_of):
        try:
            if not path.exists():
                continue
            return NewdayMarketMap.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def newday_diagnostic(public_symbol: str, as_of: Optional[datetime] = None) -> dict:
    """Compact, JSON-safe diagnostic view for the /newday/map endpoint and
    for attaching to signal payloads. Never raises; always returns a dict.
    """
    market_map = load_latest_newday_map(public_symbol, as_of=as_of)
    if market_map is None:
        return {
            "available": False,
            "reason": "NO_MARKET_MAP_FOUND",
            "map_dir": str(_map_dir()),
        }

    is_stale = False
    try:
        map_date = datetime.strptime(market_map.map_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        now = as_of or datetime.now(timezone.utc)
        is_stale = (now - map_date) > timedelta(days=1, hours=6)
    except Exception:
        is_stale = False

    return {
        "available": True,
        "stale": is_stale,
        "symbol": market_map.symbol,
        "map_date": market_map.map_date,
        "generated_at": market_map.generated_at,
        "daily_bias": market_map.daily_bias,
        "asian_high": market_map.asian_high,
        "asian_low": market_map.asian_low,
        "previous_day_high": market_map.previous_day_high,
        "previous_day_low": market_map.previous_day_low,
        "projected_high": market_map.projected_high,
        "projected_low": market_map.projected_low,
        "lot0": market_map.lot0,
        "harmonic_direction": market_map.harmonic_context.direction,
        "harmonic_pattern": market_map.harmonic_context.pattern
        or market_map.harmonic_context.selected_pattern,
        "harmonic_d_point": market_map.harmonic_context.d_point,
        "harmonic_prz_low": market_map.harmonic_context.prz_low,
        "harmonic_prz_high": market_map.harmonic_context.prz_high,
        "narrative": market_map.narrative,
    }


def newday_bias_for_direction(public_symbol: str, direction: str) -> dict:
    """Non-blocking alignment note for a candidate BUY/SELL, for logging and
    for the soft risk_adjustment nudge in engine_v4/session_gate.py.

    This MUST NOT be used as an entry gate. It only ever returns an
    `aligned` flag plus the diagnostic; callers may use it to scale
    risk_adjustment slightly, never to veto a location-first setup.
    """
    diag = newday_diagnostic(public_symbol)
    if not diag.get("available") or diag.get("stale"):
        return {"aligned": None, "reason": diag.get("reason", "STALE_OR_MISSING"), **diag}

    direction = str(direction or "").upper()
    bias = str(diag.get("daily_bias", "")).upper()
    aligned = None
    if direction in {"BUY", "SELL"} and bias in {"BULLISH", "BEARISH"}:
        aligned = (direction == "BUY" and bias == "BULLISH") or (
            direction == "SELL" and bias == "BEARISH"
        )
    return {"aligned": aligned, **diag}
