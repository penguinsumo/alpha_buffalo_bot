"""
binance_feed.py — Alpha Buffalo v5
Opt-in real-volume OHLCV source for BTC via Binance's public REST API.

Root cause this exists for: TwelveData's BTC/USD feed (the source
alpha_buffalo_signal.get_ohlcv() uses everywhere else in this bot) has NO
volume column at all. trend_monitor.calc_tf_trend() already has a real
Volume Analysis / Pressure block (vol_spike -> Selling/Buying Pressure,
already shown in the Telegram Trend Update) -- it is just a silent no-op
for BTC today because vol_avg stays 0 with no volume data.

Binance's public klines endpoint needs no API key/signup and returns real
traded volume per bar, in the exact same OHLCV shape get_ohlcv() already
returns (UTC-indexed DataFrame, open/high/low/close as float). Swapping
BTC's source to this module is therefore a drop-in data-source change --
it turns on an analysis path that already exists, it does not add a new
one. See ALPHA_BTC_BINANCE_VOLUME_ENABLED in alpha_buffalo_signal.py for
the opt-in wiring (default OFF; BTC keeps using TwelveData, with no
volume, exactly as before, unless this is explicitly turned on).
"""
import os
import requests
import pandas as pd

# Default to Binance's own dedicated public market-data mirror
# (data-api.binance.vision), NOT api.binance.com. Verified live from this
# sandbox: api.binance.com (and its api1/api2/api3/api-gcp mirrors) returned
# HTTP 451 "Service unavailable from a restricted location" -- Binance's
# compliance geo-block on the main trading API, which applies by the
# request's source IP/region and would hit any cloud host in a blocked
# region the same way, Railway included, with zero warning until this was
# actually tested live. data-api.binance.vision is Binance's own read-only,
# unauthenticated, no-geo-block mirror built for exactly this use case
# (public kline/market-data replication) -- confirmed live to return the
# identical klines schema. Override via ALPHA_BINANCE_API_BASE if needed.
BINANCE_API_BASE = os.getenv("ALPHA_BINANCE_API_BASE", "https://data-api.binance.vision")

# get_ohlcv() elsewhere in this project calls this with "15min" for
# 15-minute bars (TwelveData's own interval string); Binance's klines
# endpoint expects "15m". Map only what this bot actually requests --
# 4h/1h pass through unchanged.
_INTERVAL_MAP = {"15min": "15m"}

_SYMBOL_MAP = {
    "BTCUSD":  "BTCUSDT",
    "BTC/USD": "BTCUSDT",
    "BTCUSDT": "BTCUSDT",
}


def resolve_binance_symbol(label: str) -> str:
    """BTCUSD / BTC/USD / BTCUSDT (any casing/spacing) -> BTCUSDT.
    Unmapped input falls back to label.upper() with '/' stripped, so this
    stays usable if another Binance-listed symbol is ever wired in later.
    """
    key = (label or "").upper().replace(" ", "")
    return _SYMBOL_MAP.get(key, key.replace("/", ""))


def get_ohlcv_binance(interval="1h", bars=200, symbol="BTCUSD", timeout=15):
    """Fetch OHLCV (with REAL traded volume) from Binance's public klines
    endpoint. No API key required. Returns a UTC-indexed DataFrame with
    columns open/high/low/close/volume (all float) -- the same shape
    alpha_buffalo_signal.get_ohlcv() returns -- or None on any failure,
    mirroring get_ohlcv()'s own "never raise, return None" contract so
    callers don't need to know which source served a given symbol.
    """
    try:
        binance_symbol = resolve_binance_symbol(symbol)
        binance_interval = _INTERVAL_MAP.get(interval, interval)
        r = requests.get(
            f"{BINANCE_API_BASE}/api/v3/klines",
            params={
                "symbol": binance_symbol,
                "interval": binance_interval,
                "limit": bars,
            },
            timeout=timeout,
        )
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return None
        df = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "num_trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.sort_values("datetime").reset_index(drop=True)
        df.set_index("datetime", inplace=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df[["open", "high", "low", "close", "volume"]]
    except Exception:
        return None
