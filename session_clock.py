"""
session_clock.py — Alpha Buffalo v5.4 (BKK Time UTC+7)
Session Constants: ASIA 05:00-14:00, LONDON 14:00-19:00, NY 19:00-03:15 BKK
"""
import pytz
from datetime import datetime

class SessionConstants:
    ASIA_START_HOUR = 5
    ASIA_END_HOUR = 14
    
    LONDON_START_HOUR = 14
    LONDON_END_HOUR = 19
    
    NY_SUMMER_START = 19
    NY_WINTER_START = 20
    
    NY_END_HOUR = 3
    NY_END_MINUTE = 15

def get_ny_start_hour(current_date: datetime) -> int:
    tz_us = pytz.timezone("US/Eastern")
    if current_date.tzinfo is None:
        current_date = pytz.timezone('Asia/Bangkok').localize(current_date)
    localized_time = current_date.astimezone(tz_us)
    if localized_time.dst().total_seconds() > 0:
        return SessionConstants.NY_SUMMER_START
    return SessionConstants.NY_WINTER_START

def get_market_session_info():
    bkk = pytz.timezone('Asia/Bangkok')
    now_bkk = datetime.now(bkk)
    hour = now_bkk.hour
    minute = now_bkk.minute
    
    # ASIA: 05:00-14:00 BKK
    if SessionConstants.ASIA_START_HOUR <= hour < SessionConstants.ASIA_END_HOUR:
        session = "ASIA"
    # LONDON: 14:00-19:00 BKK
    elif SessionConstants.LONDON_START_HOUR <= hour < SessionConstants.LONDON_END_HOUR:
        session = "LONDON"
    # NY: 19:00-03:15 BKK (summer) / 20:00-03:15 BKK (winter)
    elif (hour >= SessionConstants.NY_SUMMER_START) or (hour < SessionConstants.NY_END_HOUR) or \
         (hour == SessionConstants.NY_END_HOUR and minute < SessionConstants.NY_END_MINUTE):
        session = "NY"
    else:
        session = "CLOSED"
    
    return {
        'session': session,
        'bkk_time': now_bkk.strftime('%H:%M (BKK)'),
        'display_msg': f"Session: {session} | {now_bkk.strftime('%H:%M BKK')}"
    }
