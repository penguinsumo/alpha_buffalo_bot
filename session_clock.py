"""
session_clock.py — Alpha Buffalo V12 (READ ONLY LAYER)

ROLE:
    - Provide market session state ONLY
    - No decision logic
    - No threshold logic
    - No strategy logic

OUTPUT:
    - session: ASIA | LONDON | NY | CLOSED
    - liquidity: NORMAL | OVERLAP | NONE
    - time: BKK / UTC hour
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, time
import os
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo


# =========================================================
# TIMEBASE (BKK STANDARD)
# =========================================================

BKK = timezone(timedelta(hours=7))
NEW_YORK = ZoneInfo("America/New_York")


# =========================================================
# SESSION CONSTANTS (BKK TIME)
# =========================================================

ASIA_START   = time(5, 0)
ASIA_END     = time(14, 0)

LONDON_START = time(14, 0)
LONDON_END   = time(19, 0)

NY_START     = time(19, 0)
NY_END       = time(2, 15)

CLOSED_START = time(2, 15)
CLOSED_END   = time(5, 0)


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


# =========================================================
# DATA MODEL (READ ONLY OUTPUT)
# =========================================================

@dataclass(frozen=True)
class SessionState:
    session: str
    liquidity: str
    bkk_hour: int
    utc_hour: int
    timestamp: str


# =========================================================
# SESSION CLOCK (READ ONLY)
# =========================================================

class SessionClock:
    """
    Read-only market session provider.
    No trading logic allowed.
    """

    def get(self, dt: Optional[datetime] = None) -> SessionState:

        if dt is None:
            dt = datetime.now(BKK)

        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=BKK)

        else:
            dt = dt.astimezone(BKK)

        bkk_time = dt.time()
        bkk_hour = dt.hour

        utc_dt = dt.astimezone(timezone.utc)
        utc_hour = utc_dt.hour

        session = "CLOSED"
        liquidity = "NONE"

        # Full-day closure is resolved before intraday sessions.  Previously a
        # Saturday/Sunday at 05:00 BKK was incorrectly classified as ASIA.
        if market_closed_reason(dt):
            return SessionState(
                session="CLOSED",
                liquidity="NONE",
                bkk_hour=bkk_hour,
                utc_hour=utc_hour,
                timestamp=dt.isoformat(),
            )

        # =========================
        # SESSION RESOLUTION
        # =========================

        if ASIA_START <= bkk_time < ASIA_END:
            session = "ASIA"
            liquidity = "NORMAL"

        elif LONDON_START <= bkk_time < LONDON_END:
            session = "LONDON"
            liquidity = "NORMAL"

        elif bkk_time >= LONDON_START or bkk_time < time(2, 15):
            session = "NY"

            # liquidity refinement only (no decision logic)
            if 19 <= bkk_hour <= 23:
                liquidity = "OVERLAP"
            else:
                liquidity = "NORMAL"

        elif CLOSED_START <= bkk_time < CLOSED_END:
            session = "CLOSED"
            liquidity = "NONE"

        return SessionState(
            session=session,
            liquidity=liquidity,
            bkk_hour=bkk_hour,
            utc_hour=utc_hour,
            timestamp=dt.isoformat()
        )


# =========================================================
# BACKTEST SUPPORT ONLY
# =========================================================

class SessionClockBacktest(SessionClock):

    def get_many(self, dates: list[datetime]):
        return [self.get(dt) for dt in dates]
