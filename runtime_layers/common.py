"""Shared, side-effect-free runtime normalization helpers."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_float(*values, default: float = 0.0) -> float:
    """Return the first non-zero numeric value from an ordered fallback list."""
    for value in values:
        parsed = _safe_float(value, default=0.0)
        if parsed != 0.0:
            return parsed
    return default


def _iso_timestamp(value) -> str:
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value or "")
    except Exception:
        return ""

def _engine_v4_scalar(value):
    """Convert pandas/numpy diagnostic values into JSON-safe primitives."""
    try:
        if hasattr(value, "item"):
            value = value.item()
    except Exception:
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return _iso_timestamp(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)

def _blueprint_float(blueprint, field: str) -> float:
    """Read a numeric blueprint field from either the dataclass or a dict."""
    if blueprint is None:
        return 0.0
    value = (
        blueprint.get(field, 0.0)
        if isinstance(blueprint, dict)
        else getattr(blueprint, field, 0.0)
    )
    return _safe_float(value)

def _blueprint_zone_overlap(
    df: pd.DataFrame,
    blueprint,
    low_field: str,
    high_field: str,
) -> pd.Series:
    """Return causal wick overlap against one already-confirmed blueprint zone."""
    zone_low = _blueprint_float(blueprint, low_field)
    zone_high = _blueprint_float(blueprint, high_field)
    if zone_low <= 0 or zone_high <= 0:
        return pd.Series(False, index=df.index, dtype=bool)
    if zone_low > zone_high:
        zone_low, zone_high = zone_high, zone_low
    return (
        (pd.to_numeric(df["low"], errors="coerce") <= zone_high)
        & (pd.to_numeric(df["high"], errors="coerce") >= zone_low)
    ).fillna(False)

def _v4_bool_series(df: pd.DataFrame, field: str) -> pd.Series:
    if field not in df:
        return pd.Series(False, index=df.index, dtype=bool)
    return df[field].fillna(False).astype(bool)

def _timed_ohlc_frame(df: pd.DataFrame | None) -> pd.DataFrame:
    """Normalize an OHLC frame to a sorted UTC index without inventing time."""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()

    out = df.copy()
    if "datetime" in out.columns:
        timestamps = pd.to_datetime(out["datetime"], errors="coerce", utc=True)
        out = out.loc[timestamps.notna()].copy()
        out.index = pd.DatetimeIndex(timestamps[timestamps.notna()])
    elif isinstance(out.index, pd.DatetimeIndex):
        if out.index.tz is None:
            out.index = out.index.tz_localize("UTC")
        else:
            out.index = out.index.tz_convert("UTC")
    else:
        return pd.DataFrame()

    for field in ("open", "high", "low", "close"):
        if field not in out:
            return pd.DataFrame()
        out[field] = pd.to_numeric(out[field], errors="coerce")
    return (
        out.dropna(subset=["open", "high", "low", "close"])
        .sort_index()
        .loc[lambda frame: ~frame.index.duplicated(keep="last")]
    )

def _positive_levels(*values) -> list[float]:
    levels = []
    for value in values:
        number = _safe_float(value)
        if number > 0 and not any(abs(number - existing) < 1e-6 for existing in levels):
            levels.append(number)
    return levels

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
