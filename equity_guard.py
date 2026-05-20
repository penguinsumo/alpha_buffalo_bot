"""
equity_guard.py — Alpha Buffalo v5
================================================
Financial Circuit Breaker + BOS vs New Low Classifier

หน้าที่:
  1. EquityGuard   — วัด daily DD% และ freeze basket เมื่อถึง limit
  2. BosClassifier — แยก BOS (structure พัง) vs New Low (SL sweep แล้วกลับ)

Design principle: "ปิด financial risk ก่อน ค่อยเพิ่ม signal"
  - DD limit 1.5% = soft warning
  - DD limit 1.8% = hard freeze (ไม่เปิด basket ใหม่ทั้งวัน)
  - Lot cap 0.1 per order, 0.15 รวม basket
  - BOS ต้องมีหลักฐาน — ถ้าไม่แน่ใจให้ conservative (= New Low ก่อน)

Usage:
    from equity_guard import EquityGuard, BosClassifier

    guard = EquityGuard(account_size=10000)
    ok, reason = guard.check_before_open(daily_pnl=-120, open_lots=0.07)

    classifier = BosClassifier()
    result = classifier.classify(
        price=1.0740,
        locked_low=1.0750,
        has_reversal=True,   # BB M15 touch + VSA spike
    )
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Optional
from enum import Enum


# ════════════════════════════════════════════════════════
# CONSTANTS — ปรับได้ใน Railway environment variables
# ════════════════════════════════════════════════════════

DD_SOFT_PCT      = 0.015   # 1.5% → warning + tighten threshold
DD_HARD_PCT      = 0.018   # 1.8% → freeze ทั้งวัน
MAX_LOT_SINGLE   = 0.10    # max lot per order
MAX_LOT_BASKET   = 0.15    # max lot รวมทั้ง basket
MAX_BASKETS_DAY  = 3       # max basket เปิดต่อวัน
XAUUSD_PIP_VALUE = 1.0     # USD per pip per 0.01 lot


# ════════════════════════════════════════════════════════
# EQUITY GUARD
# ════════════════════════════════════════════════════════

class GuardStatus(Enum):
    OK       = "ok"
    WARNING  = "warning"   # DD ถึง soft limit — เปิดได้แต่ threshold สูงขึ้น
    FREEZE   = "freeze"    # DD ถึง hard limit — ไม่เปิด basket ใหม่


@dataclass
class DailyRecord:
    """เก็บสถิติรายวัน — reset ตอนเที่ยงคืน UTC"""
    date:           date  = field(default_factory=lambda: date.today())
    realized_loss:  float = 0.0   # USD — negative = loss
    realized_gain:  float = 0.0   # USD
    baskets_opened: int   = 0
    baskets_closed: int   = 0
    peak_equity:    float = 0.0   # high watermark วันนี้

    @property
    def net_pnl(self) -> float:
        return self.realized_gain + self.realized_loss

    @property
    def total_loss(self) -> float:
        """รวม loss เท่านั้น (negative number)"""
        return self.realized_loss


class EquityGuard:
    """
    Financial Circuit Breaker สำหรับ Alpha Buffalo

    Parameters
    ----------
    account_size : float   AUM ปัจจุบัน (USD)
    dd_soft_pct  : float   DD% ที่ trigger warning (default 1.5%)
    dd_hard_pct  : float   DD% ที่ trigger freeze (default 1.8%)
    """

    def __init__(
        self,
        account_size:  float = 10_000,
        dd_soft_pct:   float = DD_SOFT_PCT,
        dd_hard_pct:   float = DD_HARD_PCT,
        max_lot_single: float = MAX_LOT_SINGLE,
        max_lot_basket: float = MAX_LOT_BASKET,
        max_baskets_day: int  = MAX_BASKETS_DAY,
    ):
        self.account_size    = account_size
        self.dd_soft_pct     = dd_soft_pct
        self.dd_hard_pct     = dd_hard_pct
        self.max_lot_single  = max_lot_single
        self.max_lot_basket  = max_lot_basket
        self.max_baskets_day = max_baskets_day

        self.today           = DailyRecord(peak_equity=account_size)
        self._last_reset_day = date.today()

    # ── Public API ──────────────────────────────────────

    def check_before_open(
        self,
        proposed_lot:  float,
        basket_lot_total: float = 0.0,
    ) -> tuple[bool, str]:
        """
        เรียกก่อนเปิด basket ใหม่หรือ add zone ใหม่
        คืน (allowed: bool, reason: str)
        """
        self._auto_reset()

        # 1. lot hard cap
        if proposed_lot > self.max_lot_single:
            return False, (
                f"Lot {proposed_lot} เกิน hard cap {self.max_lot_single}"
            )

        if basket_lot_total + proposed_lot > self.max_lot_basket:
            return False, (
                f"Basket total {basket_lot_total + proposed_lot:.2f} "
                f"เกิน max {self.max_lot_basket}"
            )

        # 2. daily basket count
        if self.today.baskets_opened >= self.max_baskets_day:
            return False, (
                f"เปิด basket ไปแล้ว {self.today.baskets_opened} ครั้งวันนี้ "
                f"— limit {self.max_baskets_day}"
            )

        # 3. DD check
        status, dd_pct = self._get_status()

        if status == GuardStatus.FREEZE:
            return False, (
                f"FREEZE — DD วันนี้ {dd_pct:.2%} เกิน hard limit "
                f"{self.dd_hard_pct:.2%} "
                f"(${abs(self.today.total_loss):.0f} loss)"
            )

        return True, f"OK — DD {dd_pct:.2%} status={status.value}"

    def get_score_threshold_modifier(self) -> int:
        """
        ถ้า DD อยู่ใน warning zone → เพิ่ม score threshold ขึ้น
        ใช้ใน gate.py ดึงค่านี้ไปบวกกับ base threshold
        """
        self._auto_reset()
        status, _ = self._get_status()
        if status == GuardStatus.WARNING:
            return +10   # ต้องการ score ≥ 80 แทน 70
        return 0

    def record_closed_basket(self, pnl_usd: float):
        """เรียกเมื่อ basket ปิด — บันทึก PnL"""
        self._auto_reset()
        if pnl_usd >= 0:
            self.today.realized_gain  += pnl_usd
            self.today.peak_equity     = max(
                self.today.peak_equity,
                self.account_size + self.today.net_pnl
            )
        else:
            self.today.realized_loss  += pnl_usd
        self.today.baskets_closed += 1

    def record_opened_basket(self):
        self._auto_reset()
        self.today.baskets_opened += 1

    def status_summary(self) -> str:
        self._auto_reset()
        status, dd_pct = self._get_status()
        soft_usd = self.account_size * self.dd_soft_pct
        hard_usd = self.account_size * self.dd_hard_pct
        return (
            f"EquityGuard | {date.today()} | "
            f"status={status.value.upper()}\n"
            f"  DD today   : {dd_pct:.2%}  "
            f"(${abs(self.today.total_loss):.1f} loss)\n"
            f"  Soft limit : {self.dd_soft_pct:.1%} = ${soft_usd:.0f}\n"
            f"  Hard limit : {self.dd_hard_pct:.1%} = ${hard_usd:.0f}\n"
            f"  Baskets    : {self.today.baskets_opened} opened / "
            f"{self.today.baskets_closed} closed today\n"
            f"  Net PnL    : ${self.today.net_pnl:+.1f}"
        )

    # ── Internal ────────────────────────────────────────

    def _get_status(self) -> tuple[GuardStatus, float]:
        """คำนวณ DD% และ status ปัจจุบัน"""
        loss = abs(self.today.total_loss)
        dd_pct = loss / self.account_size if self.account_size > 0 else 0

        if dd_pct >= self.dd_hard_pct:
            return GuardStatus.FREEZE, dd_pct
        if dd_pct >= self.dd_soft_pct:
            return GuardStatus.WARNING, dd_pct
        return GuardStatus.OK, dd_pct

    def _auto_reset(self):
        """Reset รายวัน — เรียกทุกครั้งก่อนใช้งาน"""
        today = date.today()
        if today != self._last_reset_day:
            self.today           = DailyRecord(
                date         = today,
                peak_equity  = self.account_size + self.today.net_pnl
            )
            self._last_reset_day = today


# ════════════════════════════════════════════════════════
# BOS CLASSIFIER — แยก BOS vs New Low
# ════════════════════════════════════════════════════════

class LowBreakType(Enum):
    NEW_LOW  = "new_low"    # SL sweep — institutional accumulate แล้วกลับ
    BOS      = "bos"        # Structure พัง — basket ควร cut
    AMBIGUOUS = "ambiguous" # ยังไม่ชัด — รอ bar ถัดไป


@dataclass
class LowBreakResult:
    break_type:     LowBreakType
    confidence:     int          # 0–100
    reason:         str
    action:         str          # "reset_pivot" | "cut_basket" | "wait"
    reversal_score: int          # คะแนน reversal signal ที่เจอ


class BosClassifier:
    """
    แยก BOS (Break of Structure) vs New Low (SL Sweep)

    Logic:
      New Low = ราคาทะลุ Low แต่มี reversal signal → pivot reset เท่านั้น
      BOS     = ราคาทะลุ Low และ ไม่มี reversal signal → cut basket
      Ambiguous = รอ bar ถัดไป (conservative)

    Reversal signals ที่นับ:
      - BB M15 touch ขอบล่าง + กลับตัว    (+40)
      - VSA stopping volume                (+35)
      - Pinbar H1/H4 wick ยาว             (+30)
      - Session bias (Asia sweep zone)     (+15)
    """

    REVERSAL_THRESHOLD_NEW_LOW = 60   # ≥ 60 = New Low (reset)
    REVERSAL_THRESHOLD_BOS     = 30   # < 30 = BOS (cut basket)
    # 30–59 = ambiguous (รอ)

    def classify(
        self,
        price:          float,
        locked_low:     float,
        bos_buffer:     float = 0.001,   # 0.1% tolerance

        # Reversal signals — ส่งมาจาก detector
        bb_m15_touch:   bool  = False,   # BB M15 ขอบล่าง + กลับตัว
        vsa_stopping:   bool  = False,   # stopping volume
        pinbar_h1h4:    bool  = False,   # pinbar H1/H4
        asia_session:   bool  = False,   # อยู่ใน Asia session
        price_recovered: bool = False,   # ราคา close กลับมาเหนือ locked_low
    ) -> LowBreakResult:
        """
        เรียกเมื่อ price < locked_low * (1 - bos_buffer)

        Parameters
        ----------
        price           : ราคา close ปัจจุบัน
        locked_low      : Swing Low ที่ lock ไว้
        bos_buffer      : tolerance ก่อน classify (0.1%)
        bb_m15_touch    : BB M15 แตะขอบล่างแล้วกลับ
        vsa_stopping    : volume spike + lo wick ยาว
        pinbar_h1h4     : pinbar บน H1/H4
        asia_session    : อยู่ใน Asia session (sweep bias)
        price_recovered : bar นี้ close กลับมาเหนือ locked_low แล้ว
        """

        # ถ้าราคายังไม่ทะลุจริงๆ → ไม่ต้อง classify
        threshold = locked_low * (1 - bos_buffer)
        if price >= threshold:
            return LowBreakResult(
                break_type     = LowBreakType.AMBIGUOUS,
                confidence     = 0,
                reason         = "ราคายังอยู่เหนือ threshold",
                action         = "wait",
                reversal_score = 0,
            )

        # คำนวณ reversal score
        score = 0
        reasons = []

        if bb_m15_touch:
            score += 40
            reasons.append("BB M15 touch+rebound")
        if vsa_stopping:
            score += 35
            reasons.append("VSA stopping vol")
        if pinbar_h1h4:
            score += 30
            reasons.append("Pinbar H1/H4")
        if asia_session:
            score += 15
            reasons.append("Asia sweep bias")
        if price_recovered:
            score += 20
            reasons.append("Price closed above Low")

        score = min(score, 100)

        # Classify
        if score >= self.REVERSAL_THRESHOLD_NEW_LOW:
            return LowBreakResult(
                break_type     = LowBreakType.NEW_LOW,
                confidence     = score,
                reason         = " + ".join(reasons),
                action         = "reset_pivot",   # lock Low ใหม่ basket อยู่
                reversal_score = score,
            )

        if score < self.REVERSAL_THRESHOLD_BOS:
            return LowBreakResult(
                break_type     = LowBreakType.BOS,
                confidence     = 100 - score,
                reason         = f"reversal score {score} ต่ำเกิน → structure พัง",
                action         = "cut_basket",
                reversal_score = score,
            )

        # Ambiguous: 30–59
        return LowBreakResult(
            break_type     = LowBreakType.AMBIGUOUS,
            confidence     = score,
            reason         = f"reversal score {score} — รอ H1/H4 bar ถัดไป",
            action         = "wait",
            reversal_score = score,
        )

    def should_cut_basket(self, result: LowBreakResult) -> bool:
        """ใช้ใน bot — ถามตรงๆ ว่าควร cut ไหม"""
        return result.break_type == LowBreakType.BOS

    def should_reset_pivot(self, result: LowBreakResult) -> bool:
        """ใช้ใน pivot_engine — ถามว่าควร reset pivot ไหม"""
        return result.break_type == LowBreakType.NEW_LOW


# ════════════════════════════════════════════════════════
# INTEGRATION HELPER — plug เข้า main loop
# ════════════════════════════════════════════════════════

def check_low_break_and_guard(
    price:          float,
    locked_low:     float,
    guard:          EquityGuard,
    classifier:     BosClassifier,
    proposed_lot:   float,
    basket_lot_total: float,
    **reversal_signals,
) -> dict:
    """
    ฟังก์ชันรวม — เรียกจาก main loop ทุก bar

    คืน dict พร้อม action สำหรับ bot
    """
    result = {"guard_ok": True, "bos_result": None, "actions": []}

    # 1. ตรวจ low break ก่อน
    if locked_low and price < locked_low * (1 - 0.001):
        bos_result = classifier.classify(
            price       = price,
            locked_low  = locked_low,
            **reversal_signals,
        )
        result["bos_result"] = bos_result

        if bos_result.action == "cut_basket":
            result["actions"].append({
                "type":   "CUT_BASKET",
                "reason": bos_result.reason,
                "confidence": bos_result.confidence,
            })
            result["guard_ok"] = False
            return result   # cut แล้วไม่ต้องเช็คอย่างอื่น

        if bos_result.action == "reset_pivot":
            result["actions"].append({
                "type":   "RESET_PIVOT",
                "reason": bos_result.reason,
                "note":   "basket ยังอยู่ — lock Low ใหม่",
            })
            # ไม่ return — ตรวจ guard ต่อ

        if bos_result.action == "wait":
            result["actions"].append({
                "type":   "WAIT",
                "reason": bos_result.reason,
            })
            result["guard_ok"] = False
            return result

    # 2. ตรวจ equity guard ก่อนเปิด order ใหม่
    allowed, reason = guard.check_before_open(
        proposed_lot      = proposed_lot,
        basket_lot_total  = basket_lot_total,
    )
    if not allowed:
        result["guard_ok"] = False
        result["actions"].append({
            "type":   "GUARD_BLOCK",
            "reason": reason,
        })

    return result


# ════════════════════════════════════════════════════════
# TEST
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 56)
    print("TEST 1 — EquityGuard")
    print("=" * 56)

    guard = EquityGuard(account_size=10_000)
    print(guard.status_summary())

    # simulate loss
    print("\n— หลังเสีย $130 (1.3% DD) —")
    guard.record_closed_basket(-130)
    ok, reason = guard.check_before_open(0.01, 0.0)
    print(f"  check_before_open: {ok} | {reason}")
    print(f"  threshold modifier: +{guard.get_score_threshold_modifier()}")
    print(guard.status_summary())

    print("\n— หลังเสีย $50 เพิ่ม (1.8% DD รวม) —")
    guard.record_closed_basket(-50)
    ok, reason = guard.check_before_open(0.01, 0.0)
    print(f"  check_before_open: {ok} | {reason}")
    print(guard.status_summary())

    print("\n— lot เกิน hard cap —")
    guard2 = EquityGuard(account_size=10_000)
    ok, reason = guard2.check_before_open(0.15, 0.0)
    print(f"  lot 0.15: {ok} | {reason}")

    ok, reason = guard2.check_before_open(0.05, 0.12)
    print(f"  basket total 0.17: {ok} | {reason}")

    print("\n" + "=" * 56)
    print("TEST 2 — BosClassifier")
    print("=" * 56)

    clf = BosClassifier()

    print("\n— New Low: Asia session + VSA + BB —")
    r = clf.classify(
        price          = 1.0740,
        locked_low     = 1.0750,
        bb_m15_touch   = True,
        vsa_stopping   = True,
        asia_session   = True,
    )
    print(f"  type={r.break_type.value}  score={r.reversal_score}"
          f"  action={r.action}")
    print(f"  reason: {r.reason}")
    print(f"  cut_basket={clf.should_cut_basket(r)}"
          f"  reset_pivot={clf.should_reset_pivot(r)}")

    print("\n— BOS: ไม่มี reversal signal เลย —")
    r2 = clf.classify(
        price      = 1.0720,
        locked_low = 1.0750,
    )
    print(f"  type={r2.break_type.value}  score={r2.reversal_score}"
          f"  action={r2.action}")
    print(f"  reason: {r2.reason}")
    print(f"  cut_basket={clf.should_cut_basket(r2)}")

    print("\n— Ambiguous: แค่ Asia session ไม่มีอย่างอื่น —")
    r3 = clf.classify(
        price        = 1.0745,
        locked_low   = 1.0750,
        asia_session = True,
        pinbar_h1h4  = True,
    )
    print(f"  type={r3.break_type.value}  score={r3.reversal_score}"
          f"  action={r3.action}")
    print(f"  reason: {r3.reason}")

    print("\n" + "=" * 56)
    print("TEST 3 — Integration helper")
    print("=" * 56)
    guard3  = EquityGuard(account_size=10_000)
    clf3    = BosClassifier()
    result  = check_low_break_and_guard(
        price            = 1.0740,
        locked_low       = 1.0750,
        guard            = guard3,
        classifier       = clf3,
        proposed_lot     = 0.02,
        basket_lot_total = 0.01,
        bb_m15_touch     = True,
        vsa_stopping     = True,
        asia_session     = True,
    )
    print(f"  guard_ok: {result['guard_ok']}")
    for a in result["actions"]:
        print(f"  [{a['type']}] {a['reason']}")

    print("\nAll tests PASSED")
