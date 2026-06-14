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