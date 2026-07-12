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
from datetime import datetime, timezone, timedelta, time
import os
from typing import Optional
from zoneinfo import ZoneInfo


BKK = timezone(timedelta(hours=7))
NEW_YORK = ZoneInfo("America/New_York")


def _configured_closed_dates() -> set[str]:
    """Full-day XAU closures in Bangkok date, supplied by deployment config."""
    raw = os.getenv("ALPHA_MARKET_CLOSED_DATES", "")
    return {value.strip() for value in raw.split(",") if value.strip()}


def _force_market_closed() -> bool:
    return os.getenv("ALPHA_FORCE_MARKET_CLOSED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def market_closed_reason(dt: Optional[datetime] = None) -> str:
    """Return the hard full-day closure reason, or an empty string when openable."""
    if dt is None:
        local = datetime.now(BKK)
    elif dt.tzinfo is None:
        local = dt.replace(tzinfo=BKK)
    else:
        local = dt.astimezone(BKK)

    if _force_market_closed():
        return "FORCED_CLOSED"
    # XAU weekend follows New York and DST: Friday 17:00 ET through Sunday
    # 18:00 ET.  A plain Bangkok weekday check would close several valid late-
    # Friday NY hours and open one hour early during US standard time.
    new_york = local.astimezone(NEW_YORK)
    ny_weekday = new_york.weekday()
    if (
        (ny_weekday == 4 and new_york.time() >= time(17, 0))
        or ny_weekday == 5
        or (ny_weekday == 6 and new_york.time() < time(18, 0))
    ):
        return "WEEKEND"
    if local.date().isoformat() in _configured_closed_dates():
        return "CONFIGURED_HOLIDAY"
    return ""


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

        schedule = self._schedule(dt.month)

        session = "CLOSED"
        liquidity = "NONE"

        # Hard closure uses the real New York weekend/DST boundary and explicit
        # deployment holidays before resolving the seasonal Bangkok sessions.
        if market_closed_reason(dt):
            return SessionState(
                session="CLOSED",
                liquidity="NONE",
                bkk_hour=dt.hour,
                utc_hour=utc_dt.hour,
                timestamp=dt.isoformat(),
            )

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
    def _in_overlap(bkk_minutes: int, schedule: dict) -> bool:
        start = schedule["overlap_start"]
        end = schedule["overlap_end"]

        if end >= 24 * 60:
            return bkk_minutes >= start

        return start <= bkk_minutes < end


class SessionClockBacktest(SessionClock):
    def get_many(self, dates: list[datetime]):
        return [self.get(dt) for dt in dates]
