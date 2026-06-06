"""
score_manager_v5p3.py — Alpha Buffalo v5.3 (Refined)
==================================================
Improved scoring system with refined bucket logic

Refinements from v5.2:
  ✓ H4 boundary filter: Remove H1 spike noise at session changes
  ✓ H1 spike separation: Split from sweep with confluence bonus
  ✓ V5_SNIPER strict: Require A + B + D + C (true sniper DNA)
  ✓ COT vs DXY: COT only counts when diverging from DXY regime

Architecture: Score Buckets (Unchanged)
  ─────────────────────────────────────────────
  BUCKET A — Trend Structure       max = +6
  BUCKET B — Entry Zone Quality    max = +5
  BUCKET C — Trigger Confirmation  max = +5  (now: Sweep + H1 Spike separate)
  BUCKET D — VSA                   max = +2
  BUCKET E — Context               max = ±4  (now: DXY primary, COT divergence)
  ─────────────────────────────────────────────
  MAX THEORETICAL SCORE = 22
  V4_SESSION threshold  >= 4
  V5_SNIPER  threshold  >= 8  (STRICT: must have A ≥ 3, B ≥ 3, D ≥ 2, C ≥ 2)
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ════════════════════════════════════════════════════════
# ENUMS & CONSTANTS
# ════════════════════════════════════════════════════════

class DXYRegime(Enum):
    """DXY market regime classification"""
    STRONG_UP = "strong_up"       # DXY bullish → USD strength
    STRONG_DOWN = "strong_down"   # DXY bearish → USD weakness
    NEUTRAL = "neutral"           # DXY ranging


THRESHOLD_V4 = 4
THRESHOLD_V5 = 8

# V5_SNIPER strict requirements
V5_MIN_BUCKET_A = 3   # Trend must be solid (cascade or reversal)
V5_MIN_BUCKET_B = 3   # Zone quality must be strong (harmonic/kivanc)
V5_MIN_BUCKET_C = 2   # Trigger must be present
V5_MIN_BUCKET_D = 2   # VSA wall required (true sniper DNA)


# ════════════════════════════════════════════════════════
# SCORE RESULT
# ════════════════════════════════════════════════════════

@dataclass
class ScoreResult:
    # Bucket scores
    bucket_a: int = 0   # Trend Structure
    bucket_b: int = 0   # Entry Zone Quality
    bucket_c: int = 0   # Trigger Confirmation (Sweep + H1 Spike separate)
    bucket_d: int = 0   # VSA
    bucket_e: int = 0   # Context (can be negative)

    # Breakdown log
    breakdown: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.bucket_a + self.bucket_b + self.bucket_c + self.bucket_d + self.bucket_e

    @property
    def signal_type(self) -> str:
        """
        V5_SNIPER: Strict bullish sniper setup
          - Trend is solid (A ≥ 3)
          - Entry zone is strong (B ≥ 3: harmonic or kivanc)
          - VSA buy wall present (D ≥ 2)
          - Trigger confirmed (C ≥ 2: sweep or spike)
          - Total ≥ 8
        
        V4_SESSION: Looser session reversal setup
          - Total ≥ 4 (may lack strict requirements)
        
        NO_SIGNAL: Below threshold or blocked
        """
        if (self.total >= THRESHOLD_V5 and
            self.bucket_a >= V5_MIN_BUCKET_A and
            self.bucket_b >= V5_MIN_BUCKET_B and
            self.bucket_c >= V5_MIN_BUCKET_C and
            self.bucket_d >= V5_MIN_BUCKET_D):
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
            f"📊 Score Summary (v5.3)",
            f"   A (Trend)    : {self.bucket_a:+d}  [min ≥ {V5_MIN_BUCKET_A} for V5]",
            f"   B (Zone)     : {self.bucket_b:+d}  [min ≥ {V5_MIN_BUCKET_B} for V5]",
            f"   C (Trigger)  : {self.bucket_c:+d}  [min ≥ {V5_MIN_BUCKET_C} for V5]",
            f"   D (VSA)      : {self.bucket_d:+d}  [min ≥ {V5_MIN_BUCKET_D} for V5]",
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
# SCORE MANAGER V5.3
# ════════════════════════════════════════════════════════

class ScoreManager:
    """
    v5.3 Refined scoring with:
    - H4 boundary filter for H1 spike
    - H1 spike + sweep separation + confluence bonus
    - V5_SNIPER strict DNA check (A+B+D+C)
    - COT vs DXY divergence logic

    Usage:
        sm = ScoreManager()
        result = sm.calculate(
            # Bucket A
            cascade_direction="UP",
            cascade_h4_only=False,
            reversal_stage=0,
            # Bucket B
            harmonic_in_prz=True,
            harmonic_priority="primary",
            kivanc_in_golden=False,
            kivanc_score=0,
            fvg_verdict="NONE",
            # Bucket C
            bos_detected=True,
            mss_detected=False,
            sweep_valid=True,
            sweep_is_pdh_pdl=True,
            h1_spike=True,
            h1_spike_volume=True,
            h1_spike_at_h4_boundary=False,  # ← NEW: filter session noise
            at_bonus=0,
            # Bucket D
            vsa_ok=True,
            # Bucket E
            news_block=False,
            fg_score=0,
            dxy_score=-2,
            dxy_regime=DXYRegime.STRONG_DOWN,  # ← NEW: for COT context
            cot_score=1,
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
    # NEW: Sweep และ H1 Spike แยก + confluence bonus

    def _score_bucket_c(
        self,
        bos_detected:           bool,
        mss_detected:           bool,
        sweep_valid:            bool,    # sweep + closed_back = True
        sweep_is_pdh_pdl:       bool,    # True = PDH/PDL sweep (สำคัญกว่า session)
        h1_spike:               bool,
        h1_spike_volume:        bool,    # True = volume confirmed
        h1_spike_at_h4_boundary: bool,   # NEW: True = เกิดที่ session boundary → filter
        at_bonus:               int,     # 0 หรือ 1 จาก alphatrend_gate
        breakdown:              dict,
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
        sweep_pts = 0
        if sweep_valid:
            sweep_pts = 3 if sweep_is_pdh_pdl else 2
            score += sweep_pts
            breakdown["Sweep (GPS/PDH/HL)"] = sweep_pts

        # H1 Spike (แยกออก + filter H4 boundary noise)
        # ถ้า spike เกิดที่ H4 boundary → ไม่นับ (session mechanics noise)
        spike_pts = 0
        if h1_spike and not h1_spike_at_h4_boundary:
            spike_pts = 4 if h1_spike_volume else 2
            score += spike_pts
            breakdown["H1 Spike"] = spike_pts
        elif h1_spike and h1_spike_at_h4_boundary:
            breakdown["H1 Spike (filtered: H4 boundary)"] = 0

        # NEW: Confluence bonus
        # ถ้า sweep + spike เกิดร่วมกันและไม่อยู่ boundary
        confluence_bonus = 0
        if sweep_pts > 0 and spike_pts > 0 and not h1_spike_at_h4_boundary:
            confluence_bonus = 1
            score += confluence_bonus
            breakdown["Sweep+Spike Confluence"] = confluence_bonus

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
    # NEW: COT as COT-vs-DXY divergence (not independent)

    def _score_bucket_e(
        self,
        news_block:   bool,              # True = block signal ทันที
        fg_score:     int,               # -2 to +2
        dxy_score:    int,               # -2 to +2
        dxy_regime:   DXYRegime,         # NEW: market regime context
        cot_score:    int,               # -2 to +2 (raw COT)
        breakdown:    dict,
    ) -> int:
        if news_block:
            breakdown["News BLOCK"] = -99   # signal หยุด
            return -99   # sentinel value → caller ต้อง block

        score = 0

        # DXY: Primary driver
        if dxy_score:
            score += dxy_score
            breakdown["DXY"] = dxy_score

        # Fear & Greed: Independent
        if fg_score:
            score += fg_score
            breakdown["Fear&Greed"] = fg_score

        # COT: Only count if diverging from DXY regime (NEW LOGIC)
        # Rationale:
        #   - If DXY strong_up (bullish USD) but COT bullish → bearish divergence
        #   - If DXY strong_down but COT bearish → alignment (redundant)
        #   - Only divergence or neutral regime counts
        cot_adjusted = 0
        if cot_score != 0:
            if dxy_regime == DXYRegime.STRONG_UP and cot_score > 0:
                # Bullish COT vs strong USD = divergence → ignore (lagged signal)
                cot_adjusted = 0
            elif dxy_regime == DXYRegime.STRONG_DOWN and cot_score < 0:
                # Bearish COT vs weak USD = alignment → ignore (redundant)
                cot_adjusted = 0
            else:
                # Divergence or neutral regime = valid signal
                cot_adjusted = cot_score

            if cot_adjusted:
                score += cot_adjusted
                breakdown["COT (vs DXY)"] = cot_adjusted
            elif cot_score != 0:
                breakdown["COT (filtered: regime aligned)"] = 0

        return max(-4, min(4, score))   # soft cap ±4

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
        h1_spike_at_h4_boundary: bool = False,  # NEW
        at_bonus:          int  = 0,
        # Bucket D inputs
        vsa_ok:            bool = False,
        # Bucket E inputs
        news_block:        bool = False,
        fg_score:          int  = 0,
        dxy_score:         int  = 0,
        dxy_regime:        DXYRegime = DXYRegime.NEUTRAL,  # NEW
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
            h1_spike_at_h4_boundary,  # NEW
            at_bonus, breakdown
        )
        result.bucket_d = self._score_bucket_d(vsa_ok, breakdown)
        result.bucket_e = self._score_bucket_e(
            news_block, fg_score, dxy_score, dxy_regime, cot_score, breakdown  # NEW: dxy_regime
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
    """Entry point สำหรับ signal_engine (v5.3)"""
    return score_manager.calculate(**kwargs)
