#!/usr/bin/env python3
"""
alpha_buffalo_signal_fixed.py — สำหรับ Railway deployment
ใช้ signal_composer version เดิม
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

# ใช้ version เดิม
from signal_composer import compose_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_data(data_dir: str, asset: str, timeframe: str, bars: int = 500) -> Optional[pd.DataFrame]:
    """Load OHLCV data"""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", help="Asset to analyze")
    parser.add_argument("--all-assets", action="store_true")
    parser.add_argument("--assets", help="Comma-separated list")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--output", choices=["console", "json"], default="console")
    
    args = parser.parse_args()
    
    # กำหนด assets
    if args.all_assets:
        assets = ["EURUSD", "JPN225", "US100"]
    elif args.assets:
        assets = [a.strip().upper() for a in args.assets.split(",")]
    elif args.asset:
        assets = [args.asset.upper()]
    else:
        assets = ["EURUSD"]
    
    results = []
    
    print("\n" + "="*70)
    print(f"🐂 ALPHA BUFFALO v5.2 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    for asset in assets:
        print(f"\n📊 Analyzing {asset}...")
        
        try:
            # Load data
            df_4h = load_data(args.data_dir, asset, "H4", 100)
            df_1h = load_data(args.data_dir, asset, "H1", 200)
            df_15m = load_data(args.data_dir, asset, "M15", 500)
            
            if df_4h is None or df_1h is None or df_15m is None:
                print(f"   ⚠️ Cannot load data for {asset}")
                continue
            
            # Generate signal
            signal = compose_signal(df_4h, df_1h, df_15m)
            
            if signal:
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
                    "signal_type": signal.signal_type,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.sl_price,
                    "tp1": signal.tp1_price,
                    "tp2": signal.tp2_price,
                    "score": signal.confluence_score,
                })
            else:
                print(f"   ⚪ No signal")
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print("\n" + "="*70)
    
    if args.output == "json" and results:
        print(json.dumps(results, indent=2))
    
    return results


if __name__ == "__main__":
    main()
