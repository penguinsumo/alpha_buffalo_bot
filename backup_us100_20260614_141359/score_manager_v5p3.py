from dataclasses import dataclass
THRESHOLD_V4 = 4
THRESHOLD_V5 = 8

class DXYRegime:
    NEUTRAL = 0
    BULLISH = 1
    BEARISH = -1
class ScoreResult:
    bucket_a=0;bucket_b=0;bucket_c=0;bucket_d=0;bucket_e=0
    @property
    def total(self): return self.bucket_a+self.bucket_b+self.bucket_c+self.bucket_d+self.bucket_e
    @property
    def is_tradable(self): return self.total >= THRESHOLD_V4
class ScoreManager:
    def calculate(self,**kw):
        r=ScoreResult()
        r.bucket_a=3
        r.bucket_b=2 if kw.get('kivanc_score',0)>0 else 0
        r.bucket_c=2 if kw.get('bos_detected',False) else 0
        r.bucket_d=2 if kw.get('vsa_ok',False) else 0
        return r
score_manager=ScoreManager()

# ━━━ PHASE 1 PATCH: ATR 1.5 + Spread 1.15 + get_trade_mode() ━━━
# Applied: 2026-06-14T09:42:34.974677

def _score_bucket_b(price, recent_high, recent_low, atr):
    """Bucket B: Zone Detection (ATR × 1.5)"""
    score = 0.0
    # 🔧 Changed from 1.0 to 1.5
    if abs(price - recent_low) <= atr * 1.5:
        score += 1.5
    if abs(price - recent_high) <= atr * 1.5:
        score -= 1.5
    return score


def _score_bucket_d(current_spread, avg_spread):
    """Bucket D: VSA Volume Spread (× 1.15)"""
    score = 0.0
    # 🔧 Changed from 1.3 to 1.15
    if avg_spread > 0 and current_spread > avg_spread * 1.15:
        score += 1.5 if current_spread > avg_spread * 1.5 else 1.0
    return score


def get_trade_mode(score):
    """🆕 กำหนด trade mode ตามคะแนน"""
    abs_score = abs(score)
    if abs_score == 3:
        return 'SCALP_BE'
    elif 4 <= abs_score <= 5:
        return 'V4_SCALP'
    elif abs_score >= 6:
        return 'V5_SNIPER'
    return 'NONE'
