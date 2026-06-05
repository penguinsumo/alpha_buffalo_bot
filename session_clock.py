import pytz
from datetime import datetime

def get_market_session_info():
    """
    Returns current trading session based on UTC, 
    along with formatted time strings for both UTC and Thai Time.
    """
    utc_zone = pytz.timezone('UTC')
    thai_zone = pytz.timezone('Asia/Bangkok')
    
    now_utc = datetime.now(utc_zone)
    now_thai = now_utc.astimezone(thai_zone)
    
    hour_utc = now_utc.hour
    
    # Session routing based on UTC
    if 8 <= hour_utc < 16:
        if 13 <= hour_utc < 16:
            current_session = 'LONDON_NY_OVERLAP'
        else:
            current_session = 'LONDON'
    elif 13 <= hour_utc < 22:
        current_session = 'NY'
    else:
        current_session = 'ASIA'
        
    time_str_utc = now_utc.strftime('%H:%M UTC')
    time_str_thai = now_thai.strftime('%H:%M (Thai Time)')
    
    return {
        'session': current_session,
        'utc_time': time_str_utc,
        'thai_time': time_str_thai,
        'display_msg': f"Session: {current_session} | {time_str_utc} | {time_str_thai}"
    }
