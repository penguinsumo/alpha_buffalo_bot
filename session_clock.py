"""
session_clock.py — Alpha Buffalo V4+
================================================
Asia / London / NY Session Filter

Logic:
- Asia   → 02:00–09:00 UTC  (เปิดให้ entry ที่ 0.618 ถ้า score ผ่าน)
- London → 07:00–16:00 UTC  (Main session — full entry allowed)
- NY     → 12:00–21:00 UTC  (Main session — full entry allowed)
- Closed → ไม่มี session overlap — ไม่ entry

Overlap:
- London+NY → 12:00–16:00 UTC (สัญญาณแรงที่สุด)

Design principle:
- Asia  → Early entry เฉพาะ 0.618 + score ≥ 60
- London/NY → Full entry ทุก zone ที่ score ≥ 70
- Closed → return None ทันที ไม่ต้องคำนวณอะไร
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass


# ════════════════════════════════════════════════════════
# SESSION CONSTANTS (UTC)
# ════════════════════════════════════════════════════════

SESSIONS = {
    "asia":   {"open": 2,  "close": 9},
    "london": {"open": 7,  "close": 16},
    "ny":     {"open": 12, "close": 21},
}

# Score threshold แต่ละ session
SCORE_THRESHOLD = {
    "asia":          60,   # Early entry — ผ่อนปรน
    "london":        70,   # Main session
    "ny":            70,   # Main session
    "london_ny":     65,   # Overlap — แรงสุด ลด threshold
    "closed":        999,  # ไม่ entry
}

# Zone ที่อนุญาตแต่ละ session
ALLOWED_ZONES = {
    "asia":      [0.618, 0.786],   # เฉพาะ 0.618 + Inst Zone
    "london":    [0.618, 0.786],   # ทุก zone ที่มีความหมาย
    "ny":        [0.618, 0.786],
    "london_ny": [0.618, 0.786],
    "closed":    [],               # ไม่มี
}

BKK = timezone(timedelta(hours=7))


# ════════════════════════════════════════════════════════
# DATA CLASS
# ════════════════════════════════════════════════════════

@dataclass
class SessionInfo:
    name:            str        # "asia" | "london" | "ny" | "london_ny" | "closed"
    is_open:         bool
    score_threshold: int
    allowed_zones:   list
    utc_hour:        int
    bkk_hour:        int
    note:            str = ""

    def allows_zone(self, fibo_ratio: float, tolerance: float = 0.01) -> bool:
        """ตรวจว่า zone นี้ entry ได้ใน session นี้ไหม"""
        return any(
            abs(fibo_ratio - z) <= tolerance
            for z in self.allowed_zones
        )

    def score_ok(self, score: int) -> bool:
        """ตรวจว่า score ผ่าน threshold ของ session นี้ไหม"""
        return score >= self.score_threshold


# ════════════════════════════════════════════════════════
# SESSION CLOCK
# ════════════════════════════════════════════════════════

class SessionClock:
    """
    Alpha Buffalo Session Clock
    
    Usage:
        clock = SessionClock()
        info  = clock.current()
        
        if info.is_open:
            if info.allows_zone(state.fibo_ratio):
                if info.score_ok(state.confluence):
                    → entry ได้
    """

    def current(self, dt: datetime = None) -> SessionInfo:
        """
        คืน SessionInfo ของตอนนี้
        dt: ถ้าไม่ส่งมา ใช้เวลาปัจจุบัน (UTC)
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        hour     = dt.hour
        bkk_hour = (dt + timedelta(hours=7)).hour

        in_asia   = SESSIONS["asia"]["open"]   <= hour < SESSIONS["asia"]["close"]
        in_london = SESSIONS["london"]["open"] <= hour < SESSIONS["london"]["close"]
        in_ny     = SESSIONS["ny"]["open"]     <= hour < SESSIONS["ny"]["close"]

        # London + NY overlap
        if in_london and in_ny:
            return SessionInfo(
                name            = "london_ny",
                is_open         = True,
                score_threshold = SCORE_THRESHOLD["london_ny"],
                allowed_zones   = ALLOWED_ZONES["london_ny"],
                utc_hour        = hour,
                bkk_hour        = bkk_hour,
                note            = "London+NY Overlap — สัญญาณแรงสุด",
            )

        if in_london:
            return SessionInfo(
                name            = "london",
                is_open         = True,
                score_threshold = SCORE_THRESHOLD["london"],
                allowed_zones   = ALLOWED_ZONES["london"],
                utc_hour        = hour,
                bkk_hour        = bkk_hour,
                note            = "London Session",
            )

        if in_ny:
            return SessionInfo(
                name            = "ny",
                is_open         = True,
                score_threshold = SCORE_THRESHOLD["ny"],
                allowed_zones   = ALLOWED_ZONES["ny"],
                utc_hour        = hour,
                bkk_hour        = bkk_hour,
                note            = "New York Session",
            )

        if in_asia:
            return SessionInfo(
                name            = "asia",
                is_open         = True,
                score_threshold = SCORE_THRESHOLD["asia"],
                allowed_zones   = ALLOWED_ZONES["asia"],
                utc_hour        = hour,
                bkk_hour        = bkk_hour,
                note            = "Asia Session — Early entry 0.618 only",
            )

        return SessionInfo(
            name            = "closed",
            is_open         = False,
            score_threshold = SCORE_THRESHOLD["closed"],
            allowed_zones   = ALLOWED_ZONES["closed"],
            utc_hour        = hour,
            bkk_hour        = bkk_hour,
            note            = "ตลาดปิด — ไม่ entry",
        )

    def can_entry(
        self,
        fibo_ratio: float,
        confluence: int,
        in_inst_zone: bool = False,
    ) -> tuple[bool, str]:
        """
        Gate รวม: session + zone + score

        Returns:
            (True/False, reason string)
        """
        info = self.current()

        # ตลาดปิด
        if not info.is_open:
            return False, f"ตลาดปิด ({info.utc_hour}:00 UTC)"

        # Zone ไม่อนุญาตใน session นี้
        if not info.allows_zone(fibo_ratio):
            return False, (
                f"Zone {fibo_ratio:.3f} ไม่อนุญาตใน {info.name} session"
            )

        # Asia session: ต้องมี inst zone ด้วย ถ้าเป็น 0.786
        if info.name == "asia" and abs(fibo_ratio - 0.786) < 0.01:
            if not in_inst_zone:
                return False, "Asia + 0.786 ต้องการ Inst Zone confirm"

        # Score ไม่ผ่าน
        if not info.score_ok(confluence):
            return False, (
                f"Score {confluence} < threshold {info.score_threshold} "
                f"({info.name} session)"
            )

        return True, f"✅ Entry allowed | {info.name} | score {confluence}"

    def summary(self) -> str:
        info = self.current()
        lines = [
            f"=== Session Clock ===",
            f"  Session  : {info.name.upper()}",
            f"  UTC      : {info.utc_hour:02d}:xx",
            f"  BKK      : {info.bkk_hour:02d}:xx",
            f"  Open     : {info.is_open}",
            f"  Threshold: {info.score_threshold}",
            f"  Zones    : {info.allowed_zones}",
            f"  Note     : {info.note}",
        ]
        return "\n".join(lines)


# ════════════════════════════════════════════════════════
# QUICK TEST
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    from datetime import timezone

    clock = SessionClock()

    # ทดสอบทุก session
    test_hours = [
        (3,  "Asia"),
        (8,  "London"),
        (13, "London+NY Overlap"),
        (18, "NY"),
        (22, "Closed"),
    ]

    print("=" * 50)
    print("Session Clock Test")
    print("=" * 50)

    for hour, label in test_hours:
        dt   = datetime(2026, 5, 19, hour, 0, tzinfo=timezone.utc)
        info = clock.current(dt)
        print(f"\n[{label}] UTC {hour:02d}:00")
        print(f"  Session  : {info.name}")
        print(f"  Is Open  : {info.is_open}")
        print(f"  Threshold: {info.score_threshold}")
        print(f"  Zones    : {info.allowed_zones}")

    print("\n" + "=" * 50)
    print("can_entry() Gate Test")
    print("=" * 50)

    # ทดสอบ can_entry
    test_cases = [
        # (hour, fibo, score, in_inst, label)
        (3,  0.618, 65, False, "Asia + 0.618 + score 65"),
        (3,  0.618, 55, False, "Asia + 0.618 + score 55 (too low)"),
        (3,  0.382, 75, False, "Asia + 0.382 (zone not allowed)"),
        (3,  0.786, 80, True,  "Asia + 0.786 + inst zone"),
        (3,  0.786, 80, False, "Asia + 0.786 no inst zone"),
        (8,  0.618, 70, False, "London + 0.618 + score 70"),
        (13, 0.786, 85, True,  "London+NY + 0.786 + inst"),
        (22, 0.618, 90, False, "Closed — ไม่ entry"),
    ]

    for hour, fibo, score, inst, label in test_cases:
        dt = datetime(2026, 5, 19, hour, 0, tzinfo=timezone.utc)
        # override current time สำหรับ test
        info = clock.current(dt)
        # simulate can_entry ด้วย dt
        temp_clock = SessionClock()
        temp_clock._test_dt = dt

        # manual check
        ok = info.is_open
        ok = ok and info.allows_zone(fibo)
        if info.name == "asia" and abs(fibo - 0.786) < 0.01:
            ok = ok and inst
        ok = ok and info.score_ok(score)

        result = "✅" if ok else "❌"
        print(f"  {result} {label}")

    print("\nAll tests done.")
