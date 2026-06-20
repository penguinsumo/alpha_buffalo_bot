"""
session_clock.py — Alpha Buffalo v5.4 (Bangkok Time Standard)
Session Boundaries (Final):
  ASIA    05:00 – 14:00
  LONDON  14:00 – 19:00
  NY      19:00 – 02:15 (next day)
  CLOSED  02:15 – 05:00
"""
import pytz
from datetime import datetime, time

BKK = pytz.timezone('Asia/Bangkok')

class Session:
    ASIA = 'ASIA'
    LONDON = 'LONDON'
    NY = 'NY'
    CLOSED = 'CLOSED'

# Boundaries in Bangkok time
ASIA_START    = time(5, 0)
ASIA_END      = time(14, 0)
LONDON_START  = time(14, 0)
LONDON_END    = time(19, 0)
NY_START      = time(19, 0)
NY_END        = time(2, 15)   # next day

def get_current_session(now_bkk: datetime) -> str:
    t = now_bkk.time()
    # CLOSED: 02:15 - 05:00
    if time(2, 15) <= t < time(5, 0):
        return Session.CLOSED
    # ASIA: 05:00 - 14:00
    if ASIA_START <= t < ASIA_END:
        return Session.ASIA
    # LONDON: 14:00 - 19:00
    if LONDON_START <= t < LONDON_END:
        return Session.LONDON
    # NY: 19:00 - 02:15 (next day)
    if t >= NY_START or t < NY_END:
        return Session.NY
    # Fallback (should not happen)
    return Session.CLOSED

def get_market_session_info():
    now_bkk = datetime.now(BKK)
    session = get_current_session(now_bkk)
    return {
        'session': session,
        'bkk_time': now_bkk.strftime('%H:%M (BKK)'),
        'display_msg': f"Session: {session} | {now_bkk.strftime('%H:%M BKK')}"
    }

class H4SessionTracker:
    """ตรวจจับช่วงเวลา H4 ปัจจุบัน (อิงตาม BKK time)"""
    @staticmethod
    def get_h4_boundary():
        now_bkk = datetime.now(BKK)
        current_hour = now_bkk.hour
        h4_start = (current_hour // 4) * 4
        h4_end = h4_start + 4
        if h4_end >= 24:
            h4_end = 0
        is_approaching = (h4_end - current_hour) == 1 or (current_hour == 23 and h4_end == 0)
        return {
            'current_hour_bkk': current_hour,
            'h4_start_bkk': h4_start,
            'h4_end_bkk': h4_end,
            'is_boundary_approaching': is_approaching
        }
