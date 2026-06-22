#!/usr/bin/env python3
"""AI Screening Test — แยก Engine v11.2 vs NewV4 (ปรับ AI Threshold)"""
import random, statistics, math, pandas as pd, numpy as np
from itertools import product

# ── Mock AI Engines ──
class MockFinBERT:
    def analyze(self, price, vol, session):
        s = math.sin(price/100) + math.cos(vol/1000) + random.gauss(0,0.3)
        score = (s + 1.5) / 3.0
        score = max(0, min(1, score))
        return {'score': round(score,2), 'text': f"Mock sentiment {score:.2f}"}

class MockForecaster:
    def predict(self, price, atr, ema20, ema50):
        trend = 1 if ema20 > ema50 else -1
        noise = random.gauss(0, 0.2)
        raw = trend * (atr/price*100) + noise
        score = max(0, min(1, (raw + 1)/2))
        return {'score': round(score,2), 'text': f"Forecast {score:.2f}"}

# ── Session Generator ──
SESSIONS = ['ASIA','LONDON','NY']
def gen_signals(n=3):
    sigs = []
    for s in SESSIONS:
        for _ in range(n):
            d = random.choice(['BUY','SELL'])
            p = random.gauss(2000, 20)
            vol = random.gauss(5000, 2000)
            atr = random.gauss(15, 5)
            ema20 = p + random.gauss(0,2)
            ema50 = p + random.gauss(0,3)
            sigs.append({'session':s,'direction':d,'price':p,'volume':vol,
                         'atr':atr,'ema20':ema20,'ema50':ema50})
    return sigs

# ── AI Screening ──
def ai_screen(signals, engine_label, fbert, forecaster, threshold=0.5):
    results = []
    for sig in signals:
        ai_score = 0
        ai_used = False
        
        if fbert and forecaster:
            fs = forecaster.predict(sig['price'], sig['atr'], sig['ema20'], sig['ema50'])
            fb = fbert.analyze(sig['price'], sig['volume'], sig['session'])
            ai_score = round((fs['score'] + fb['score']) / 2, 2)
            
            if ai_score >= threshold:
                ai_used = True
                # AI-approved: tighter SL, further TP
                sl = round(sig['price'] - 0.02 if sig['direction']=='BUY' else sig['price'] + 0.02, 2)
                tp = round(sig['price'] + 0.05 if sig['direction']=='BUY' else sig['price'] - 0.05, 2)
            else:
                sl = round(sig['price'] - 0.005 if sig['direction']=='BUY' else sig['price'] + 0.005, 2)
                tp = round(sig['price'] + 0.015 if sig['direction']=='BUY' else sig['price'] - 0.015, 2)
        else:
            sl = round(sig['price'] - 0.005 if sig['direction']=='BUY' else sig['price'] + 0.005, 2)
            tp = round(sig['price'] + 0.015 if sig['direction']=='BUY' else sig['price'] - 0.015, 2)
        
        results.append({
            'engine': engine_label,
            'session': sig['session'],
            'direction': sig['direction'],
            'entry': round(sig['price'], 2),
            'ai_score': ai_score,
            'ai_used': ai_used,
            'sl': sl,
            'tp': tp
        })
    return results

# ── Run ──
if __name__ == '__main__':
    random.seed(42)
    signals = gen_signals(3)  # 9 signals
    
    fbert = MockFinBERT()
    forecaster = MockForecaster()
    
    # v11.2 — AI threshold 0.5 (เดิม)
    v112 = ai_screen(signals, 'v11.2', fbert, forecaster, threshold=0.5)
    # NewV4 — AI threshold 0.3 (ต่ำกว่า กล้าใช้ AI มากขึ้น)
    newv4 = ai_screen(signals, 'NewV4', fbert, forecaster, threshold=0.3)
    
    all_results = v112 + newv4
    
    # Print
    print("=== AI Screening Test (Adjusted Threshold) ===")
    for r in all_results:
        ai_status = f"AI Used: {str(r['ai_used']):5s} (Score: {r['ai_score']:.1f})"
        print(f"[{r['engine']:<5s}] {r['session']:<6s} {r['direction']:<4s} | Entry: {r['entry']:.2f} | {ai_status} | SL: {r['sl']:.2f} | TP: {r['tp']:.2f}")
