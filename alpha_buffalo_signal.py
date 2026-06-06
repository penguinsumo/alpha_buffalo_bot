#!/usr/bin/env python3
"""
Alpha Buffalo v5.2 - Signal Generator with VSA gate integration
สามารถเปิด/ปิด VSA ได้โดยใช้ environment variable USE_VSA (TRUE/FALSE)
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import os

from signal_composer import compose_signal
from vsa_gate import check_vsa_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_data(data_dir: str, asset: str, timeframe: str, bars: int = 500) -> Optional[pd.DataFrame]:
    """Load OHLCV data for a given asset and timeframe"""
    data_path = Path(data_dir)
    patterns = [
        data_path / f"{asset}_{timeframe}.csv",
        data_path / f"{asset}.csv",
        data_path / f"{asset.lower()}_{timeframe.lower()}.csv",
        data_path / f"{timeframe}_{asset}.csv",
    ]

    for fp in patterns:
        if fp.exists():
            df = pd.read_csv(fp)
            df.columns = [c.lower().strip() for c in df.columns]

            if 'time' in df.columns:
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
            elif 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)

            required = ['open', 'high', 'low', 'close']
            if all(c in df.columns for c in required):
                if 'volume' not in df.columns:
                    df['volume'] = 1000000
                return df.tail(bars)

    return None


def get_current_session():
    """Determine session based on current hour (simplified)"""
    hour = datetime.now().hour
    if 0 <= hour < 9:
        return "ASIA"
    elif 9 <= hour < 17:
        return "LONDON"
    else:
        return "NY"


def main():
    parser = argparse.ArgumentParser(description="Alpha Buffalo v5.2 with VSA gate")
    parser.add_argument("--asset", default="EURUSD", help="Asset to analyze")
    parser.add_argument("--all-assets", action="store_true", help="Scan all assets")
    parser.add_argument("--data-dir", default="./data", help="Data directory")
    parser.add_argument("--output", choices=["console", "json"], default="console")

    args = parser.parse_args()

    default_assets = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "JPN225", "US100"]
    if args.all_assets:
        assets = default_assets
    else:
        assets = [args.asset.upper()]

    # Environment variable to toggle VSA gate (default: TRUE)
    use_vsa = os.getenv("USE_VSA", "TRUE").upper() == "TRUE"
    if not use_vsa:
        print("⚠️ VSA gate is DISABLED via USE_VSA=FALSE")

    session = get_current_session()
    asia_mode = (session == "ASIA")

    print("\n" + "=" * 70)
    print(f"🐂 ALPHA BUFFALO v5.2 with VSA")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📍 Session: {session} | VSA: {'ON' if use_vsa else 'OFF'}")
    print("=" * 70)

    results = []

    for asset in assets:
        print(f"\n📊 {asset}")
        df_4h = load_data(args.data_dir, asset, "H4", 100)
        df_1h = load_data(args.data_dir, asset, "H1", 200)
        df_15m = load_data(args.data_dir, asset, "M15", 500)

        if df_4h is None or df_1h is None or df_15m is None:
            print("   ⚠️ Missing data for one or more timeframes")
            continue

        # Get signal from composer
        signal = compose_signal(df_4h, df_1h, df_15m)

        if signal:
            # Apply VSA gate if enabled
            if use_vsa:
                vsa = check_vsa_signal(
                    df=df_1h,
                    direction=signal.direction,
                    asia_mode=asia_mode,
                    spike_detected=False,
                    lookback=20
                )
                if not vsa["ok"]:
                    print(f"   ❌ VSA REJECT: {vsa['reason']}")
                    continue
                else:
                    print(f"   ✅ VSA OK: {vsa['reason']} (bonus={vsa['bonus']})")
                    # Optionally store VSA info in signal
                    signal.vsa_bonus = vsa["bonus"]
                    signal.vsa_multiplier = vsa["position_multiplier"]

            # Display signal
            emoji = "🟢" if signal.direction == "BUY" else "🔴"
            print(f"   {emoji} {signal.direction}")
            print(f"   Type    : {signal.signal_type}")
            print(f"   Entry   : {signal.entry_price:.5f}")
            print(f"   SL      : {signal.sl_price:.5f}")
            print(f"   TP1     : {signal.tp1_price:.5f}")
            print(f"   TP2     : {signal.tp2_price:.5f}")
            print(f"   Lot     : x{signal.lot_multiplier} (Layer {signal.basket_layer}/2)")
            print(f"   Score   : {signal.confluence_score}")
            print(f"   Sources : {', '.join(signal.sources[:3])}")

            results.append({
                "asset": asset,
                "direction": signal.direction,
                "type": signal.signal_type,
                "entry": signal.entry_price,
                "score": signal.confluence_score,
            })
        else:
            print("   ⚪ No signal")

    print("\n" + "=" * 70)

    if args.output == "json":
        print(json.dumps(results, indent=2))

    return results


if __name__ == "__main__":
    main()
