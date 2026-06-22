import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from scenario_scanner import scanner
from signal_composer import compose_signal

def mock_ohlcv(start_price=2400, periods=200, trend="UP"):
    dates = [datetime.now(timezone.utc) - timedelta(minutes=15*i) for i in reversed(range(periods))]
    np.random.seed(42)
    if trend == "UP":
        trend_line = np.linspace(0, 10, periods) + np.random.randn(periods) * 0.5
    elif trend == "DOWN":
        trend_line = np.linspace(0, -10, periods) + np.random.randn(periods) * 0.5
    else:
        trend_line = np.random.randn(periods) * 1.0
    close = start_price + trend_line.cumsum()
    df = pd.DataFrame({
        'open': close - 0.2,
        'high': close + 1.5,
        'low': close - 1.5,
        'close': close,
        'volume': np.random.randint(100, 1000, periods)
    }, index=dates)
    df = df.sort_index()
    df_1h = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    df_4h = df.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    return df, df_1h, df_4h

print("="*60)
print("Alpha Buffalo v11.2 - Signal Test (Mock Data)")
print("="*60)

for trend_name in ["UP", "DOWN"]:
    print(f"\n>>> Testing with {trend_name}TREND data...")
    df_15m, df_1h, df_4h = mock_ohlcv(2400, 200, trend=trend_name)
    try:
        bp = scanner.scan(df_4h, df_1h, df_15m)
        print(f"✅ Blueprint: tunnel_valid={bp.tunnel_valid}, market_mode={bp.market_mode}")
        sw_L = f"{bp.swing_L:.2f}" if bp.swing_L else "None"
        sw_H = f"{bp.swing_H:.2f}" if bp.swing_H else "None"
        sw_HL = f"{bp.swing_HL:.2f}" if bp.swing_HL else "None"
        print(f"   Swing L={sw_L}, H={sw_H}, HL={sw_HL}")
        if bp.tunnel_valid:
            print(f"   Tunnel: lower={bp.tunnel_lower:.2f}, upper={bp.tunnel_upper:.2f}, mid={bp.tunnel_mid:.2f}")
        gzl = f"{bp.golden_zone_low:.2f}" if bp.golden_zone_low else "N/A"
        gzh = f"{bp.golden_zone_high:.2f}" if bp.golden_zone_high else "N/A"
        print(f"   Golden Zone: low={gzl}, high={gzh}")
        if bp.plan_a_entry:
            print(f"   Plan A: entry={bp.plan_a_entry:.2f}, tp={bp.plan_a_tp:.2f}, sl={bp.plan_a_sl:.2f}")
        if bp.plan_b_entry:
            print(f"   Plan B: entry={bp.plan_b_entry:.2f}, tp1={bp.plan_b_tp1:.2f}, tp2={bp.plan_b_tp2:.2f}")

        sig = compose_signal(df_4h, df_1h, df_15m, blueprint=bp)
        if sig:
            print(f"✅ Signal: {sig.direction} | Score:{sig.confluence_score} | Layer:{sig.basket_layer}")
            print(f"   Entry={sig.entry_price:.2f}, SL={sig.sl_price:.2f}, TP1={sig.tp1_price:.2f}, TP2={sig.tp2_price:.2f}")
        else:
            print("⚠️ No signal (normal for mock data)")
    except Exception as e:
        print(f"❌ Error: {e}")

print("\n" + "="*60)
print("Test completed.")
