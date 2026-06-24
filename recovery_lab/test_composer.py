import pandas as pd
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")

# ── Permission Table ──
SESSION_HOURS = {
    'ASIA':   {'BUY': [1],           'SELL': [3, 5]},
    'LONDON': {'BUY': [],            'SELL': [8, 9, 12]},
    'NY':     {'BUY': [13, 15, 16, 17], 'SELL': [13, 14, 15, 16, 17, 18]}
}
SESSION_V4_THRESHOLD = {'ASIA': 3, 'LONDON': 4, 'NY': 4}

def get_session(hour): 
    if 1 <= hour < 8: return "ASIA"
    elif 8 <= hour < 13: return "LONDON"
    elif 13 <= hour < 19: return "NY"
    return "CLOSED"

@dataclass
class ComposedSignal:
    direction: str; signal_type: str; entry_price: float
    sl_price: float; tp1_price: float; tp2_price: float
    lot_multiplier: float; basket_layer: int; confluence_score: int
    sources: list = None; label: str = ""; timestamp: str = ""

class SignalComposer:
    def compose(self, df_4h, df_1h, df_15m, blueprint=None):
        price = float(df_15m["close"].iloc[-1])
        ts = df_15m.index[-1]; session = get_session(ts.hour)
        
        direction = None
        if df_15m["EMA20"].iloc[-1] > df_15m["EMA50"].iloc[-1]:
            if df_15m["Low"].iloc[-1] <= df_15m["BB_Lower"].iloc[-1]:
                if ts.hour in SESSION_HOURS.get(session,{}).get('BUY',[]): direction = "BUY"
        elif df_15m["EMA20"].iloc[-1] < df_15m["EMA50"].iloc[-1]:
            if df_15m["High"].iloc[-1] >= df_15m["BB_Upper"].iloc[-1]:
                if ts.hour in SESSION_HOURS.get(session,{}).get('SELL',[]): direction = "SELL"
        if direction is None: return None

        from score_manager_v5p3 import ScoreManager
        mgr = ScoreManager(); kivanc_score = 1
        score = mgr.calculate(kivanc_score=kivanc_score, bos_detected=False, vsa_ok=False).total
        if score < SESSION_V4_THRESHOLD.get(session, 4): return None

        sl = price - df_15m["ATR14"].iloc[-1]*1.5 if direction == "BUY" else price + df_15m["ATR14"].iloc[-1]*1.5
        tp = df_15m["BB_Upper"].iloc[-1] if direction == "BUY" else df_15m["BB_Lower"].iloc[-1]
        return ComposedSignal(direction=direction, signal_type="V4_SCALP", entry_price=price,
                sl_price=sl, tp1_price=tp, tp2_price=tp, lot_multiplier=1.0, basket_layer=1,
                confluence_score=score, sources=["TestEngine"], timestamp=datetime.now(BKK).strftime("%H:%M:%S"))

composer = SignalComposer()
def compose_signal(df_4h, df_1h, df_15m, blueprint=None): return composer.compose(df_4h, df_1h, df_15m)
