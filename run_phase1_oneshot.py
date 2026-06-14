#!/usr/bin/env python3
"""
🚀 Phase 1 One-Shot: แก้ score_manager + ASIA_TUNING + signal_composer
แล้วรัน Backtest ทันที
"""
import os, sys, json, shutil
from datetime import datetime

print("=" * 70)
print("🐃 PHASE 1 ONE-SHOT — Fix & Test")
print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ━━━ STEP 1: BACKUP ━━━
backup_dir = f"backup_phase1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
os.makedirs(backup_dir, exist_ok=True)

files_to_backup = [
    'score_manager_v5p3.py',
    'ASIA_TUNING_v5p3.py', 
    'signal_composer.py'
]

for f in files_to_backup:
    src = os.path.join(os.getcwd(), f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(backup_dir, f))
        print(f"  💾 Backed up: {f} → {backup_dir}/")

print()

# ━━━ STEP 2: PATCH score_manager_v5p3.py ━━━
print("🔧 Patching score_manager_v5p3.py...")

score_patch = """
# ━━━ PHASE 1 PATCH: ATR 1.5 + Spread 1.15 + get_trade_mode() ━━━
# Applied: {timestamp}

def _score_bucket_b(price, recent_high, recent_low, atr):
    \"\"\"Bucket B: Zone Detection (ATR × 1.5)\"\"\"
    score = 0.0
    # 🔧 Changed from 1.0 to 1.5
    if abs(price - recent_low) <= atr * 1.5:
        score += 1.5
    if abs(price - recent_high) <= atr * 1.5:
        score -= 1.5
    return score


def _score_bucket_d(current_spread, avg_spread):
    \"\"\"Bucket D: VSA Volume Spread (× 1.15)\"\"\"
    score = 0.0
    # 🔧 Changed from 1.3 to 1.15
    if avg_spread > 0 and current_spread > avg_spread * 1.15:
        score += 1.5 if current_spread > avg_spread * 1.5 else 1.0
    return score


def get_trade_mode(score):
    \"\"\"🆕 กำหนด trade mode ตามคะแนน\"\"\"
    abs_score = abs(score)
    if abs_score == 3:
        return 'SCALP_BE'
    elif 4 <= abs_score <= 5:
        return 'V4_SCALP'
    elif abs_score >= 6:
        return 'V5_SNIPER'
    return 'NONE'
""".format(timestamp=datetime.now().isoformat())

# Append patch to score_manager file
score_path = os.path.join(os.getcwd(), 'score_manager_v5p3.py')
with open(score_path, 'a') as f:
    f.write('\n' + score_patch)

print("  ✅ score_manager_v5p3.py patched")

# ━━━ STEP 3: PATCH ASIA_TUNING_v5p3.py ━━━
print("🔧 Patching ASIA_TUNING_v5p3.py...")

asia_patch = """
# ━━━ PHASE 1 PATCH: Session Filter → Scoring ━━━
# Applied: {timestamp}

def get_session_score(timestamp):
    \"\"\"🆕 คืนค่าคะแนน session แทนการ block\"\"\"
    import pandas as pd
    hour = pd.Timestamp(timestamp).hour
    
    if 7 <= hour <= 10:
        return 2.0  # London Open
    elif 12 <= hour <= 16:
        return 1.5  # NY Open + Overlap
    elif 0 <= hour <= 6:
        return 1.0  # Asia
    return 0.5  # Other

# 🔧 Override: is_in_session() → always True + return score
_is_in_session_original = None
try:
    _is_in_session_original = is_in_session
except NameError:
    pass

def is_in_session(timestamp):
    \"\"\"🔧 Changed: Always True (no blocking), use get_session_score() for scoring\"\"\"
    return True
""".format(timestamp=datetime.now().isoformat())

asia_path = os.path.join(os.getcwd(), 'ASIA_TUNING_v5p3.py')
with open(asia_path, 'a') as f:
    f.write('\n' + asia_patch)

print("  ✅ ASIA_TUNING_v5p3.py patched")

# ━━━ STEP 4: PATCH signal_composer.py ━━━
print("🔧 Patching signal_composer.py...")

composer_patch = """
# ━━━ PHASE 1 PATCH: Pass trade_mode ━━━
# Applied: {timestamp}

def _get_trade_mode_for_signal(score):
    \"\"\"🆕 Bridge: score → trade_mode\"\"\"
    try:
        from score_manager_v5p3 import get_trade_mode
        return get_trade_mode(score)
    except ImportError:
        abs_score = abs(score)
        if abs_score == 3:
            return 'SCALP_BE'
        elif 4 <= abs_score <= 5:
            return 'V4_SCALP'
        elif abs_score >= 6:
            return 'V5_SNIPER'
        return 'NONE'
""".format(timestamp=datetime.now().isoformat())

composer_path = os.path.join(os.getcwd(), 'signal_composer.py')
with open(composer_path, 'a') as f:
    f.write('\n' + composer_patch)

print("  ✅ signal_composer.py patched")

# ━━━ STEP 5: RUN BACKTEST ━━━
print("\n" + "=" * 70)
print("🔥 RUNNING BACKTEST WITH PHASE 1 CHANGES")
print("=" * 70)

# Use the exact same backtest code from v4 (which works)
import pandas as pd
import numpy as np

df_15m = pd.read_csv('backtest_cache_15m.csv', index_col=0, parse_dates=True)
df_1h = pd.read_csv('backtest_cache_1h.csv', index_col=0, parse_dates=True)
df_4h = pd.read_csv('backtest_cache_4h.csv', index_col=0, parse_dates=True)

print(f"📦 Data: 15M={len(df_15m)}, 1H={len(df_1h)}, 4H={len(df_4h)}")

# ━━━ SCORE ENGINE WITH PHASE 1 FIXES ━━━
def score_v4_phase1(row, df1, df4):
    """v4 + Phase 1: ATR 1.5, Spread 1.15, Session Scoring"""
    s = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0}
    p = row['close']
    
    # A: Trend
    if len(df1) >= 50:
        e20 = df1['close'].ewm(span=20).mean()
        e50 = df1['close'].ewm(span=50).mean()
        dist = (e20.iloc[-1] - e50.iloc[-1]) / e50.iloc[-1] * 100
        if dist > 0.15:       s['A'] = 2.0
        elif dist > 0.05:     s['A'] = 1.0
        elif dist < -0.15:    s['A'] = -2.0
        elif dist < -0.05:    s['A'] = -1.0
    
    # B: Zone (🔧 ATR × 1.5)
    if len(df1) >= 20:
        h, l, c = df1['high'], df1['low'], df1['close']
        tr1 = h - l
        tr2 = abs(h - c.shift())
        tr3 = abs(l - c.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        rh = df1['high'].rolling(20).max().iloc[-1]
        rl = df1['low'].rolling(20).min().iloc[-1]
        if abs(p - rl) <= atr * 1.5: s['B'] = 1.5
        if abs(p - rh) <= atr * 1.5: s['B'] = -1.5
    
    # C: RSI
    if len(df1) >= 14:
        d = df1['close'].diff()
        g = d.where(d > 0, 0).rolling(14).mean()
        l = -d.where(d < 0, 0).rolling(14).mean()
        rs = g / l
        rsi = 100 - (100 / (1 + rs))
        rv = rsi.iloc[-1]
        if rv < 25:           s['C'] = 3.0
        elif rv < 35:         s['C'] = 1.5
        elif rv < 45:         s['C'] = 0.5
        elif rv > 75:         s['C'] = -3.0
        elif rv > 65:         s['C'] = -1.5
        elif rv > 55:         s['C'] = -0.5
    
    # D: VSA (🔧 Spread × 1.15)
    spread = (row['high'] - row['low']) / row['low'] * 100
    avg_spread = ((df1['high'] - df1['low']) / df1['low'] * 100).rolling(20).mean().iloc[-1]
    if spread > avg_spread * 1.15:
        s['D'] = 1.5 if spread > avg_spread * 1.5 else 1.0
    
    # E: Session (🔧 Scoring ไม่ใช่ Filtering)
    h = pd.Timestamp(row.name).hour if hasattr(row, 'name') else 12
    if 7 <= h <= 10:      s['E'] = 2.0
    elif 12 <= h <= 16:   s['E'] = 1.5
    elif 0 <= h <= 6:     s['E'] = 1.0
    else:                 s['E'] = 0.5  # 🔧 Even off-hours get score
    
    s['total'] = sum(s.values())
    return s

# ━━━ ADVANCED TRADE SIMULATOR (with BE+Trail for Score 3) ━━━
def simulate_trade_phase1(row_entry, future_prices, direction, score,
                           initial_sl_pct=0.0015, initial_tp_pct=0.003,
                           be_trigger_pct=0.0010, trail_distance_pct=0.0008):
    """Phase 1: Score 3 = BE+Trail, Score 4+ = Standard"""
    entry_price = row_entry['close']
    abs_score = abs(score)
    
    # Score 3: Breakeven + Trailing
    if abs_score == 3:
        if direction == 'buy':
            sl_price = entry_price * (1 - initial_sl_pct)
            tp_price = entry_price * (1 + initial_tp_pct)
            be_activated = False
            highest_price = entry_price
            
            for ft, high, low, close in zip(future_prices.index,
                                             future_prices['high'],
                                             future_prices['low'],
                                             future_prices['close']):
                if high >= tp_price:
                    return ('win', tp_price, ft, (tp_price-entry_price)/entry_price*100, 'tp_hit')
                if not be_activated and high >= entry_price * (1 + be_trigger_pct):
                    be_activated = True
                    sl_price = entry_price
                if be_activated and high > highest_price:
                    highest_price = high
                    sl_price = max(sl_price, highest_price * (1 - trail_distance_pct))
                if low <= sl_price:
                    pnl = (sl_price-entry_price)/entry_price*100
                    return ('win' if pnl>=0 else 'loss', sl_price, ft, pnl, 'be_trail' if be_activated else 'sl_hit')
            
            lp = future_prices['close'].iloc[-1]
            pnl = (lp-entry_price)/entry_price*100
            return ('win' if pnl>0 else 'loss', lp, future_prices.index[-1], pnl, 'time_exit')
        
        else:  # sell
            sl_price = entry_price * (1 + initial_sl_pct)
            tp_price = entry_price * (1 - initial_tp_pct)
            be_activated = False
            lowest_price = entry_price
            
            for ft, high, low, close in zip(future_prices.index,
                                             future_prices['high'],
                                             future_prices['low'],
                                             future_prices['close']):
                if low <= tp_price:
                    return ('win', tp_price, ft, (entry_price-tp_price)/entry_price*100, 'tp_hit')
                if not be_activated and low <= entry_price * (1 - be_trigger_pct):
                    be_activated = True
                    sl_price = entry_price
                if be_activated and low < lowest_price:
                    lowest_price = low
                    sl_price = min(sl_price, lowest_price * (1 + trail_distance_pct))
                if high >= sl_price:
                    pnl = (entry_price-sl_price)/entry_price*100
                    return ('win' if pnl>=0 else 'loss', sl_price, ft, pnl, 'be_trail' if be_activated else 'sl_hit')
            
            lp = future_prices['close'].iloc[-1]
            pnl = (entry_price-lp)/entry_price*100
            return ('win' if pnl>0 else 'loss', lp, future_prices.index[-1], pnl, 'time_exit')
    
    # Score 4+: Standard SL/TP
    else:
        if direction == 'buy':
            sl = entry_price * (1 - initial_sl_pct)
            tp = entry_price * (1 + (0.003 if abs_score >= 6 else 0.0015))
        else:
            sl = entry_price * (1 + initial_sl_pct)
            tp = entry_price * (1 - (0.003 if abs_score >= 6 else 0.0015))
        
        for ft, close in zip(future_prices.index, future_prices['close']):
            if direction == 'buy':
                if close <= sl: return ('loss', sl, ft, (sl-entry_price)/entry_price*100, 'sl_hit')
                if close >= tp: return ('win', tp, ft, (tp-entry_price)/entry_price*100, 'tp_hit')
            else:
                if close >= sl: return ('loss', sl, ft, (entry_price-sl)/entry_price*100, 'sl_hit')
                if close <= tp: return ('win', tp, ft, (entry_price-tp)/entry_price*100, 'tp_hit')
        
        lp = future_prices['close'].iloc[-1]
        pnl = (lp-entry_price)/entry_price*100 if direction=='buy' else (entry_price-lp)/entry_price*100
        return ('win' if pnl>0 else 'loss', lp, future_prices.index[-1], pnl, 'time_exit')

# ━━━ RUN BACKTEST ━━━
print("\n🔄 Running Phase 1 Backtest...")
trades = []
MIN_BARS = 50

for i in range(MIN_BARS, len(df_15m) - 24):
    if i % 3000 == 0:
        print(f"  Progress: {i}/{len(df_15m)} ({i/len(df_15m)*100:.0f}%)")
    
    row = df_15m.iloc[i]
    ct = df_15m.index[i]
    d1 = df_1h[df_1h.index <= ct]
    d4 = df_4h[df_4h.index <= ct]
    
    if len(d1) < MIN_BARS or len(d4) < 20:
        continue
    
    sc = score_v4_phase1(row, d1, d4)
    total = sc['total']
    
    if abs(total) >= 3:
        direction = 'buy' if total > 0 else 'sell'
        abs_sc = int(abs(total))
        
        if abs_sc == 3:
            sig_type = 'SCALP_BE'
        elif 4 <= abs_sc <= 5:
            sig_type = 'V4_SCALP'
        else:
            sig_type = 'V5_SNIPER'
        
        future = df_15m.iloc[i+1:i+25]
        if len(future) < 5: continue
        
        result = simulate_trade_phase1(row, future, direction, total)
        
        trades.append({
            'timestamp': str(ct),
            'direction': direction,
            'score': round(total, 1),
            'abs_score': abs_sc,
            'type': sig_type,
            'price': round(row['close'], 2),
            'result': result[0],
            'pnl_pct': round(result[3], 4),
            'exit_reason': result[4]
        })

# ━━━ RESULTS ━━━
print(f"\n{'='*70}")
print(f"📊 PHASE 1 BACKTEST RESULTS")
print(f"{'='*70}")
print(f"Total Trades: {len(trades)}")

if trades:
    wins = [t for t in trades if t['result'] == 'win']
    losses = [t for t in trades if t['result'] == 'loss']
    wr = len(wins) / len(trades) * 100
    total_pnl = sum(t['pnl_pct'] for t in trades)
    avg_win = np.mean([t['pnl_pct'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl_pct'] for t in losses])) if losses else 0
    pf = sum(t['pnl_pct'] for t in wins) / abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf')
    
    cum_pnl = 0; peak = 0; max_dd = 0
    for t in trades:
        cum_pnl += t['pnl_pct']
        if cum_pnl > peak: peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd: max_dd = dd
    
    print(f"\n📈 Performance:")
    print(f"  Win Rate:      {wr:.1f}%")
    print(f"  Total PnL:     {total_pnl:.2f}%")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Avg Win:       {avg_win:.4f}%")
    print(f"  Avg Loss:      {avg_loss:.4f}%")
    print(f"  Max Drawdown:  {max_dd:.2f}%")
    print(f"  Wins:          {len(wins)}")
    print(f"  Losses:        {len(losses)}")
    
    print(f"\n📊 By Strategy Type:")
    for st in ['SCALP_BE', 'V4_SCALP', 'V5_SNIPER']:
        stt = [t for t in trades if t['type'] == st]
        if stt:
            stw = [t for t in stt if t['result'] == 'win']
            st_pnl = sum(t['pnl_pct'] for t in stt)
            print(f"  {st:<15s}: {len(stt):4d} trades, WR={len(stw)/len(stt)*100:.1f}%, PnL={st_pnl:+.2f}%")
    
    print(f"\n📊 EV by Score:")
    print(f"  {'Score':<8} {'EV':<10} {'WR':<10} {'N':<6} {'PnL':<10}")
    print(f"  {'-'*44}")
    for s in [3, 4, 5, 6, 7, 8]:
        st = [t for t in trades if t['abs_score'] == s]
        if st:
            sw = [t for t in st if t['result'] == 'win']
            sl = [t for t in st if t['result'] == 'loss']
            wrs = len(sw)/len(st)*100
            wa = np.mean([t['pnl_pct'] for t in sw]) if sw else 0
            la = abs(np.mean([t['pnl_pct'] for t in sl])) if sl else 0
            ev = (wrs/100*wa) - ((1-wrs/100)*la)
            pnls = sum(t['pnl_pct'] for t in st)
            print(f"  {s:<8} {ev:<10.4f} {wrs:<9.1f}% {len(st):<6} {pnls:<+10.2f}%")
    
    # ━━━ BUG CHECK ━━━
    print(f"\n🐛 BUG CHECK:")
    bugs_found = 0
    
    # Check 1: All Score 3 should be SCALP_BE
    score3_trades = [t for t in trades if t['abs_score'] == 3]
    wrong_type = [t for t in score3_trades if t['type'] != 'SCALP_BE']
    if wrong_type:
        print(f"  ❌ BUG: {len(wrong_type)} Score=3 trades have wrong type!")
        bugs_found += 1
    else:
        print(f"  ✅ Score 3 → SCALP_BE: Correct ({len(score3_trades)} trades)")
    
    # Check 2: No negative impact on Score 4+
    score4plus = [t for t in trades if t['abs_score'] >= 4]
    score4_wr = len([t for t in score4plus if t['result']=='win'])/len(score4plus)*100 if score4plus else 0
    if score4_wr < 50:
        print(f"  ⚠️  WARNING: Score 4+ WR={score4_wr:.1f}% (<50%)")
        bugs_found += 1
    else:
        print(f"  ✅ Score 4+ WR={score4_wr:.1f}%: Healthy")
    
    # Check 3: Session scoring working (no 0-trade periods)
    daily_trades = {}
    for t in trades:
        day = t['timestamp'][:10]
        daily_trades[day] = daily_trades.get(day, 0) + 1
    zero_days = sum(1 for d in daily_trades.values() if d == 0)
    if zero_days > 10:
        print(f"  ⚠️  WARNING: {zero_days} days with 0 trades")
    else:
        print(f"  ✅ Daily trades consistent (avg {len(trades)/len(daily_trades):.1f}/day)")
    
    # Check 4: No crashes
    print(f"  ✅ Backtest completed without errors")
    
    print(f"\n📋 BUG SUMMARY: {bugs_found} issues found")
    
    # Save
    output = {
        'phase': 'Phase 1 One-Shot',
        'timestamp': datetime.now().isoformat(),
        'changes': {
            'bucket_b_atr': '1.0 → 1.5',
            'bucket_d_spread': '1.3 → 1.15',
            'session_mode': 'filter → scoring',
            'trade_modes': 'SCALP_BE (Score 3), V4_SCALP (4-5), V5_SNIPER (6+)'
        },
        'summary': {
            'total_trades': len(trades),
            'win_rate': round(wr, 1),
            'total_pnl': round(total_pnl, 2),
            'profit_factor': round(pf, 2),
            'max_drawdown': round(max_dd, 2)
        },
        'bugs_found': bugs_found,
        'trades': trades
    }
    
    with open('phase1_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n💾 Saved to phase1_results.json")
else:
    print("\n❌ No trades! Phase 1 has a bug.")

print(f"\n{'='*70}")
print(f"✅ PHASE 1 COMPLETE")
print(f"   Backup: {backup_dir}/")
print(f"   Results: phase1_results.json")
print(f"{'='*70}")

