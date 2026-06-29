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
from typing import Optional, Dict, Any


# =========================================================
# TIMEBASE (BKK STANDARD)
# =========================================================

BKK = timezone(timedelta(hours=7))


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

# =========================================================
# BACKWARD COMPATIBILITY (for signal_composer.py)
# =========================================================
class H4SessionTracker:
    """Placeholder for legacy support."""
    pass

def get_current_session():
    """Return current session string for legacy code."""
    clock = SessionClock()
    state = clock.get()
    return state.session
