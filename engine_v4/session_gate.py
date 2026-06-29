#!/usr/bin/env python3
"""
SessionGate — ใช้ SessionClock จริง + Time Gate (BUY >= 15 UTC)
+ Daily DD / Consecutive Loss Check
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from session_clock import SessionClock, SessionInfo

@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    risk_adjustment: float = 1.0  # ตัวคูณลด risk เพิ่มเติม

class SessionGate:
    def __init__(self, session_clock: SessionClock):
        self.clock = session_clock

    def evaluate(self, session_info: SessionInfo, direction: str,
                 utc_hour: int, daily_dd_ok: bool = True,
                 consec_loss_ok: bool = True) -> GateResult:
        """
        session_info: จาก SessionClock.get()
        direction: 'BUY' หรือ 'SELL'
        utc_hour: ชั่วโมง UTC ของแท่งปัจจุบัน
        daily_dd_ok: True ถ้ายังไม่เกิน Daily DD Limit (จาก Risk Manager)
        consec_loss_ok: True ถ้ายังไม่เกิน Max Consecutive Losses
        """
        if session_info.session == 'CLOSED':
            return GateResult(False, "Market closed")

        if not daily_dd_ok:
            return GateResult(False, "Daily DD limit reached")
        if not consec_loss_ok:
            return GateResult(False, "Max consecutive losses reached")

        # BUY time gate: UTC >= 15 เท่านั้น
        if direction == 'BUY':
            if session_info.session != 'NY':
                return GateResult(False, "BUY allowed only in NY session")
            if utc_hour < 15:
                return GateResult(False, f"BUY before 15 UTC (now {utc_hour})")

        # SELL: อนุญาตทุก session ยกเว้น CLOSED (ตรวจไปแล้วข้างต้น)
        return GateResult(True, "Gate passed")
