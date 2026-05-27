"""
score_manager.py — Alpha Buffalo v5.2 (Sprint Clean)
================================================
Single source of truth สำหรับการคำนวณ score ทั้งหมด

แก้ปัญหา double-counting จาก:
  - VSA นับ 2 ครั้ง (kivanc + vsa_gate)
  - GPS + Sweep + PDH/PDL นับ 3 ครั้ง (ทุกตัวมาจาก micro_engine)
  - FVG zone ซ้อน harmonic PRZ

Architecture: Score Buckets
  ─────────────────────────────────────────────
  BUCKET A — Trend Structure       max = +6
  BUCKET B — Entry Zone Quality    max = +5
  BUCKET C — Trigger Confirmation  max = +5
  BUCKET D — VSA                   max = +2
  BUCKET E — Context               max = ±4 (soft cap)
  ─────────────────────────────────────────────
  MAX THEORETICAL SCORE = 22
  V4_SESSION threshold  >= 4
  V5_SNIPER  threshold  >= 8  (ต้องมี B + C ด้วย)
"""

from dataclasses import dataclass, field
from typing import Optional


# ════════════════════════════════════════════════════════
# THRESHOLDS
# ════════════════════════════════════════════════════════

THRESHOLD_V4 = 4
THRESHOLD_V5 = 8


# ════════════════════════════════════════════════════════
# SCORE RESULT
# ════════════════════════════════════════════════════════

@dataclass
class ScoreResult:
    # Bucket scores
    bucket_a: int = 0   # Trend Structure
    bucket_b: int = 0   # Entry Zone Quality
    bucket_c: int = 0   # Trigger Confirmation
    bucket_d: int = 0   # VSA
    bucket_e: int = 0   # Context (can be negative)

    # Breakdown log
    breakdown: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.bucket_a + self.bucket_b + self.bucket_c + self.bucket_d + self.bucket_e

    @property
    def signal_type(self) -> str:
        if self.total >= THRESHOLD_V5 and self.bucket_b > 0 and self.bucket_c > 0:
            return "V5_SNIPER"
        elif self.total >= THRESHOLD_V4:
            return "V4_SESSION"
        else:
            return "NO_SIGNAL"

    @property
    def is_v5(self) -> bool:
        return self.signal_type == "V5_SNIPER"

    def summary(self) -> str:
        lines = [
            f"📊 Score Summary",
            f"   A (Trend)    : {self.bucket_a:+d}",
            f"   B (Zone)     : {self.bucket_b:+d}",
            f"   C (Trigger)  : {self.bucket_c:+d}",
            f"   D (VSA)      : {self.bucket_d:+d}",
            f"   E (Context)  : {self.bucket_e:+d}",
            f"   ─────────────────",
            f"   TOTAL        : {self.total}",
            f"   TYPE         : {self.signal_type}",
        ]
        if self.breakdown:
            lines.append("   Breakdown:")
            for k, v in self.breakdown.items():
                lines.append(f"     {k}: {v:+d}")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════
# SCORE MANAGER
# ════════════════════════════════════════════════════════

class ScoreManager:
    """
    รับ raw signals จากทุก plugin แล้วคำนวณ score แบบ bucket
    เรียกครั้งเดียวต่อ poll cycle

    Usage:
        sm = ScoreManager()
        result = sm.calculate(
            cascade        = cascade_result,
            harmonic       = harmonic_result,
            kivanc         = kivanc_signal,
            fvg            = fvg_result,
            bos            = bos_detected,
            mss            = mss_detected,
            sweep          = micro_signals,
            h1_spike       = spike_result,
            vsa            = vsa_result,
            context        = context_result,
            reversal_stage = 0,
            at_bonus       = 0,
        )
    """

    # ── BUCKET A: Trend Structure (max +6) ────────────────

    def _score_bucket_a(
        self,
        cascade_direction: str,      # "UP" | "DOWN" | "NEUTRAL"
        cascade_h4_only:   bool,     # True = H4 alone (+3), False = H4+H1 (+6)
        reversal_stage:    int,      # 0-3 จาก Reversal Zone logic
        breakdown:         dict,
    ) -> int:
        score = 0

        if cascade_direction != "NEUTRAL":
            if cascade_h4_only:
                score += 3
                breakdown["H4 Cascade"] = 3
            else:
                score += 6
                breakdown["H4+H1 Cascade"] = 6
        elif reversal_stage > 0:
            # Reversal override แทน cascade=NEUTRAL
            rev_score = min(reversal_stage + 1, 4)   # stage1=+2, stage2=+3, stage3=+4
            score += rev_score
            breakdown[f"Reversal Stage{reversal_stage}"] = rev_score

        return min(score, 6)

    # ── BUCKET B: Entry Zone Quality (max +5) ─────────────
    # Priority: Harmonic PRZ > Kivanc Golden > FVG
    # นับแค่อันที่ดีที่สุด ไม่บวกซ้ำ

    def _score_bucket_b(
        self,
        harmonic_in_prz:   bool,    # ราคาอยู่ใน Harmonic PRZ
        harmonic_priority: str,     # "primary" | "secondary"
        kivanc_in_golden:  bool,    # Kivanc OB อยู่ใน Golden Zone
        kivanc_score:      int,     # confluence_score จาก kivanc (0-5)
        fvg_verdict:       str,     # "HUNT" | "MSS" | "WAIT" | "NONE"
        breakdown:         dict,
    ) -> int:

        # Priority 1: Harmonic PRZ (strongest)
        if harmonic_in_prz:
            pts = 5 if harmonic_priority == "primary" else 3
            breakdown["Harmonic PRZ"] = pts
            return pts

        # Priority 2: Kivanc Golden Zone
        if kivanc_in_golden and kivanc_score >= 3:
            pts = min(kivanc_score, 4)   # max +4 จาก kivanc
            breakdown["Kivanc Golden Zone"] = pts
            return pts

        # Priority 3: FVG zone (ต่ำสุด)
        if fvg_verdict in ("HUNT", "MSS"):
            pts = 2
            breakdown["FVG Zone"] = pts
            return pts

        return 0

    # ── BUCKET C: Trigger Confirmation (max +5) ───────────
    # รวม BOS + Sweep + H1 Spike
    # Sweep รวม PDH/PDL + Session HL + GPS ไว้เป็นก้อนเดียว

    def _score_bucket_c(
        self,
        bos_detected:      bool,
        mss_detected:      bool,
        sweep_valid:       bool,    # sweep + closed_back = True
        sweep_is_pdh_pdl:  bool,    # True = PDH/PDL sweep (สำคัญกว่า session)
        h1_spike:          bool,
        h1_spike_volume:   bool,    # True = volume confirmed
        at_bonus:          int,     # 0 หรือ 1 จาก alphatrend_gate
        breakdown:         dict,
    ) -> int:
        score = 0

        # BOS / MSS
        if bos_detected:
            score += 2
            breakdown["BOS"] = 2
        elif mss_detected:
            score += 1
            breakdown["MSS"] = 1

        # Sweep (รวม GPS + PDH/PDL + Session HL ในก้อนเดียว)
        if sweep_valid:
            pts = 3 if sweep_is_pdh_pdl else 2
            score += pts
            breakdown["Sweep (incl. GPS/PDH)"] = pts

        # H1 Spike
        if h1_spike:
            pts = 4 if h1_spike_volume else 2
            score += pts
            breakdown["H1 Spike"] = pts

        # AT Bonus (จาก alphatrend_gate)
        if at_bonus > 0:
            score += at_bonus
            breakdown["AT Cascade Bonus"] = at_bonus

        return min(score, 5)

    # ── BUCKET D: VSA (max +2) ────────────────────────────
    # vsa_gate.py เท่านั้น — kivanc VSA ไม่นับซ้ำ

    def _score_bucket_d(
        self,
        vsa_ok:    bool,   # จาก vsa_gate.py
        breakdown: dict,
    ) -> int:
        if vsa_ok:
            breakdown["VSA Gate"] = 2
            return 2
        return 0

    # ── BUCKET E: Context (soft cap ±4) ───────────────────

    def _score_bucket_e(
        self,
        news_block:  bool,   # True = block signal ทันที
        fg_score:    int,    # -2 to +2
        dxy_score:   int,    # -2 to +2
        cot_score:   int,    # -2 to +2
        breakdown:   dict,
    ) -> int:
        if news_block:
            breakdown["News BLOCK"] = -99   # signal หยุด
            return -99   # sentinel value → caller ต้อง block

        score = 0
        if fg_score:
            score += fg_score
            breakdown["Fear&Greed"] = fg_score
        if dxy_score:
            score += dxy_score
            breakdown["DXY"] = dxy_score
        if cot_score:
            score += cot_score
            breakdown["COT"] = cot_score

        return max(-4, min(4, score))   # soft cap

    # ── PUBLIC: CALCULATE ─────────────────────────────────

    def calculate(
        self,
        # Bucket A inputs
        cascade_direction: str  = "NEUTRAL",
        cascade_h4_only:   bool = True,
        reversal_stage:    int  = 0,
        # Bucket B inputs
        harmonic_in_prz:   bool = False,
        harmonic_priority: str  = "secondary",
        kivanc_in_golden:  bool = False,
        kivanc_score:      int  = 0,
        fvg_verdict:       str  = "NONE",
        # Bucket C inputs
        bos_detected:      bool = False,
        mss_detected:      bool = False,
        sweep_valid:       bool = False,
        sweep_is_pdh_pdl:  bool = False,
        h1_spike:          bool = False,
        h1_spike_volume:   bool = False,
        at_bonus:          int  = 0,
        # Bucket D inputs
        vsa_ok:            bool = False,
        # Bucket E inputs
        news_block:        bool = False,
        fg_score:          int  = 0,
        dxy_score:         int  = 0,
        cot_score:         int  = 0,
    ) -> ScoreResult:

        result    = ScoreResult()
        breakdown = {}

        result.bucket_a = self._score_bucket_a(
            cascade_direction, cascade_h4_only, reversal_stage, breakdown
        )
        result.bucket_b = self._score_bucket_b(
            harmonic_in_prz, harmonic_priority,
            kivanc_in_golden, kivanc_score,
            fvg_verdict, breakdown
        )
        result.bucket_c = self._score_bucket_c(
            bos_detected, mss_detected,
            sweep_valid, sweep_is_pdh_pdl,
            h1_spike, h1_spike_volume,
            at_bonus, breakdown
        )
        result.bucket_d = self._score_bucket_d(vsa_ok, breakdown)
        result.bucket_e = self._score_bucket_e(
            news_block, fg_score, dxy_score, cot_score, breakdown
        )

        result.breakdown = breakdown

        # ถ้า news_block → force NO_SIGNAL ด้วย total ต่ำ
        if result.bucket_e == -99:
            result.bucket_e = -99
            # total จะต่ำมาก → signal_type = NO_SIGNAL อัตโนมัติ

        return result


# ════════════════════════════════════════════════════════
# SINGLETON
# ════════════════════════════════════════════════════════

score_manager = ScoreManager()


def calculate_score(**kwargs) -> ScoreResult:
    """Entry point สำหรับ signal_engine"""
    return score_manager.calculate(**kwargs)
