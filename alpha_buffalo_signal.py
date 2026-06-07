#!/usr/bin/env python3
"""
Alpha Buffalo v5.2 - Signal Generator with VSA gate + Event-driven spike detection
"""
import argparse, json, logging, os, pandas as pd, numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional
from signal_composer import compose_signal
from vsa_gate import check_vsa_signal
from micro_engine import detect_spike_15m
from harmonic_detector import run_harmonic, get_active_prz
from kivanc_vsaob import run_kivanc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_data(data_dir, asset, timeframe, bars=500):
    data_path = Path(data_dir)
    patterns = [data_path / f"{asset}_{timeframe}.csv", data_path / f"{asset}.csv"]
    for fp in patterns:
        if fp.exists():
            df = pd.read_csv(fp)
            df.columns = [c.lower().strip() for c in df.columns]
            if 'time' in df.columns: df.set_index('time', inplace=True)
            if all(c in df.columns for c in ['open','high','low','close']):
                if 'volume' not in df.columns: df['volume'] = 1000000
                return df.tail(bars)
    return None

def get_current_session():
    hour = datetime.now().hour
    return "ASIA" if hour < 9 else ("LONDON" if hour < 17 else "NY")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default="EURUSD")
    parser.add_argument("--all-assets", action="store_true")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--output", choices=["console","json"], default="console")
    args = parser.parse_args()
    assets = ["EURUSD","GBPUSD","USDJPY","XAUUSD","JPN225","US100"] if args.all_assets else [args.asset.upper()]
    use_vsa = os.getenv("USE_VSA", "TRUE").upper() == "TRUE"
    print("\n"+"="*70)
    print(f"🐂 ALPHA BUFFALO v5.2 with VSA (Event-driven spike)")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | VSA: {'ON' if use_vsa else 'OFF'}")
    print("="*70)
    results, last_session = [], None
    for asset in assets:
        print(f"\n📊 {asset}")
        df_4h = load_data(args.data_dir, asset, "H4", 100)
        df_1h = load_data(args.data_dir, asset, "H1", 200)
        df_15m = load_data(args.data_dir, asset, "M15", 500)
        if None in (df_4h, df_1h, df_15m):
            print("   ⚠️ Missing data")
            continue
        current_session = get_current_session()
        spike_detected = False
        if current_session != last_session:
            print(f"   🔄 Session changed: {last_session} -> {current_session} | Checking spike...")
            spike_detected, spike_type = detect_spike_15m(df_15m)
            if spike_detected:
                prz_list = run_harmonic(df_4h) + run_harmonic(df_1h)
                price = df_1h['close'].iloc[-1]
                active_prz = get_active_prz(prz_list, price, 0.002)
                kivanc = run_kivanc(df_1h)
                in_prz = len(active_prz) > 0
                in_ob = (kivanc is not None and ((kivanc.direction == "BUY" and price <= kivanc.zone_high) or (kivanc.direction == "SELL" and price >= kivanc.zone_low)))
                if not (in_prz or in_ob):
                    print(f"   ⚠️ Spike ignored (no PRZ/OB)")
                    spike_detected = False
            last_session = current_session
        signal = compose_signal(df_4h, df_1h, df_15m)
        if signal:
            if use_vsa:
                vsa = check_vsa_signal(df=df_1h, direction=signal.direction, asia_mode=(current_session=="ASIA"), spike_detected=spike_detected, lookback=20)
                if not vsa["ok"]:
                    print(f"   ❌ VSA REJECT: {vsa['reason']}")
                    continue
                else:
                    print(f"   ✅ VSA OK: {vsa['reason']} (bonus={vsa['bonus']})")
            emoji = "🟢" if signal.direction == "BUY" else "🔴"
            print(f"   {emoji} {signal.direction} | {signal.signal_type} | Score: {signal.confluence_score}")
            print(f"   Entry: {signal.entry_price:.5f}  SL: {signal.sl_price:.5f}  TP1: {signal.tp1_price:.5f}  TP2: {signal.tp2_price:.5f}")
            results.append({"asset":asset, "direction":signal.direction, "entry":signal.entry_price})
        else:
            print("   ⚪ No signal")
    if args.output == "json": print(json.dumps(results, indent=2))
    return results

if __name__ == "__main__": main()
