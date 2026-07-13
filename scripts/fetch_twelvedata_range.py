#!/usr/bin/env python3
"""Fetch a long Twelve Data range in safe chunks without logging the API key."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import requests


KEY_NAMES = (
    "TWELVEDATA_API_KEY",
    "TWELVE_API_KEY",
    "TWELVE_DATA_API_KEY",
    "TWELVEDATA_KEY",
)


def api_key() -> str:
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError("Twelve Data key is not present in the process environment")


def chunks(start: pd.Timestamp, end: pd.Timestamp, days: int):
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + pd.Timedelta(days=days), end)
        yield cursor, chunk_end
        cursor = chunk_end


def fetch_chunk(key: str, start: pd.Timestamp, end: pd.Timestamp, retries: int = 3) -> pd.DataFrame:
    params = {
        "symbol": "XAU/USD",
        "interval": "15min",
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
        "outputsize": 5000,
        "timezone": "UTC",
        "order": "ASC",
        "apikey": key,
    }
    for attempt in range(1, retries + 1):
        try:
            response = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=45)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Network/JSON error for {start.date()}..{end.date()}: {exc}") from None
            time.sleep(min(5 * attempt, 15))
            continue
        values = payload.get("values") if isinstance(payload, dict) else None
        if values:
            frame = pd.DataFrame(values)
            break
        message = str(payload.get("message") or payload.get("status") or f"HTTP {response.status_code}")
        rate_limited = response.status_code == 429 or "credits" in message.lower() or "limit" in message.lower()
        if rate_limited and attempt < retries:
            time.sleep(min(15 * attempt, 30))
            continue
        raise RuntimeError(f"Twelve Data returned no candles for {start.date()}..{end.date()}: {message}")
    else:
        raise RuntimeError("Unreachable retry state")

    required = {"datetime", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Response missing columns: {sorted(missing)}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "volume" in frame:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    else:
        frame["volume"] = 0.0
    return frame.dropna(subset=["datetime", "open", "high", "low", "close"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk-days", type=int, default=31)
    args = parser.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    if start >= end:
        raise ValueError("start must be earlier than end")
    key = api_key()
    frames = []
    ranges = list(chunks(start, end, args.chunk_days))
    for number, (chunk_start, chunk_end) in enumerate(ranges, start=1):
        frame = fetch_chunk(key, chunk_start, chunk_end)
        frames.append(frame)
        print(
            f"CHUNK_OK {number}/{len(ranges)} rows={len(frame)} "
            f"start={frame['datetime'].min().isoformat()} end={frame['datetime'].max().isoformat()}",
            flush=True,
        )

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("datetime", keep="last")
        .sort_values("datetime")
        .set_index("datetime")
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined[["open", "high", "low", "close", "volume"]].to_csv(args.output, index_label="datetime")
    print(
        f"FETCH_OK rows={len(combined)} start={combined.index.min().isoformat()} "
        f"end={combined.index.max().isoformat()} output={args.output.resolve()}",
        flush=True,
    )


if __name__ == "__main__":
    main()
