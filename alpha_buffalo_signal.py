from __future__ import annotations

import os
import time
from typing import Dict, Tuple

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request, Response

from decision_engine import DecisionEngine
from scenario_scanner import ScenarioScanner
from signal_composer import SignalComposer


app = FastAPI(title="Alpha Buffalo v12 API Adapter", version="12.0.0")

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


def fetch_multi_tf(symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        fetch_twelvedata(symbol, "4h"),
        fetch_twelvedata(symbol, "1h"),
        fetch_twelvedata(symbol, "15min"),
    )


def run_pipeline(symbol: str = SYMBOL_DEFAULT, public_symbol: str = PUBLIC_SYMBOL_DEFAULT) -> Dict:
    df_4h, df_1h, df_15m = fetch_multi_tf(symbol)

    scanner = ScenarioScanner()
    blueprint = scanner.scan(df_4h, df_1h, df_15m, symbol=public_symbol)

    engine = DecisionEngine()
    decision = engine.evaluate(blueprint)

    composer = SignalComposer()
    signal = composer.compose(
        blueprint=blueprint,
        decision=decision,
        symbol=public_symbol,
    )

    return {
        "status": "ok",
        "symbol": public_symbol,
        "signal": signal,
    }



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
    return run_pipeline(symbol=symbol, public_symbol=public_symbol)


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
