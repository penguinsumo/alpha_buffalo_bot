"""
session_clock.py — Alpha Buffalo V12 (READ ONLY LAYER)

ROLE:
    - Provide market session state ONLY
    - No decision logic
    - No threshold logic
    - No strategy logic

SOURCE OF TRUTH:
    - BKK/GMT+7 Forex market hours
    - Summer: Mar-Oct
    - Winter: Nov-Feb

OUTPUT:
    - session: ASIA | LONDON | NY | CLOSED
    - liquidity: NORMAL | OVERLAP | NONE
    - time: BKK / UTC hour
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional


BKK = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class SessionState:
    session: str
    liquidity: str
    bkk_hour: int
    utc_hour: int
    timestamp: str


class SessionClock:
    """
    Read-only market session provider.
    No trading decision logic allowed.
    """

    def get(self, dt: Optional[datetime] = None) -> SessionState:
        if dt is None:
            dt = datetime.now(BKK)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=BKK)
        else:
            dt = dt.astimezone(BKK)

        utc_dt = dt.astimezone(timezone.utc)
        bkk_minutes = dt.hour * 60 + dt.minute
        weekday = dt.weekday()  # Mon=0 ... Sun=6

        schedule = self._schedule(dt.month)

        session = "CLOSED"
        liquidity = "NONE"

        if not self._is_weekend_closed(weekday, bkk_minutes, schedule):
            if schedule["asia_start"] <= bkk_minutes < schedule["london_start"]:
                session = "ASIA"
                liquidity = "NORMAL"

            elif schedule["london_start"] <= bkk_minutes < schedule["ny_start"]:
                session = "LONDON"
                liquidity = "NORMAL"

            elif bkk_minutes >= schedule["ny_start"] or bkk_minutes < schedule["ny_end"]:
                session = "NY"
                liquidity = "OVERLAP" if self._in_overlap(bkk_minutes, schedule) else "NORMAL"

        return SessionState(
            session=session,
            liquidity=liquidity,
            bkk_hour=dt.hour,
            utc_hour=utc_dt.hour,
            timestamp=dt.isoformat(),
        )

    @staticmethod
    def _schedule(month: int) -> dict:
        # Summer: Mar-Oct / Winter: Nov-Feb
        if 3 <= month <= 10:
            return {
                "season": "SUMMER",
                "asia_start": 4 * 60,
                "london_start": 14 * 60,
                "ny_start": 19 * 60,
                "ny_end": 2 * 60,
                "overlap_start": 19 * 60,
                "overlap_end": 23 * 60,
            }

        return {
            "season": "WINTER",
            "asia_start": 5 * 60,
            "london_start": 15 * 60,
            "ny_start": 20 * 60,
            "ny_end": 3 * 60,
            "overlap_start": 20 * 60,
            "overlap_end": 24 * 60,
        }

    @staticmethod
    def _is_weekend_closed(weekday: int, bkk_minutes: int, schedule: dict) -> bool:
        # Saturday after NY close -> closed
        if weekday == 5 and bkk_minutes >= schedule["ny_end"]:
            return True

        # Sunday all day closed
        if weekday == 6:
            return True

        # Monday before Asia/Sydney open -> closed
        if weekday == 0 and bkk_minutes < schedule["asia_start"]:
            return True

        return False

    @staticmethod
    def _in_overlap(bkk_minutes: int, schedule: dict) -> bool:
        start = schedule["overlap_start"]
        end = schedule["overlap_end"]

        if end >= 24 * 60:
            return bkk_minutes >= start

        return start <= bkk_minutes < end


class SessionClockBacktest(SessionClock):
    def get_many(self, dates: list[datetime]):
        return [self.get(dt) for dt in dates]
