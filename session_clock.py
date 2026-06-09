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

# ── H4 Boundary Tracker (Added for v5.3) ──────────────
class H4SessionTracker:
    @staticmethod
    def get_h4_boundary():
        """
        ตรวจจับช่วงเวลา H4 ปัจจุบัน (0, 4, 8, 12, 16, 20) อิงตาม UTC
        คืน dict พร้อมฟิลด์ is_boundary_approaching
        """
        import pytz
        from datetime import datetime
        
        utc_zone = pytz.timezone('UTC')
        now_utc = datetime.now(utc_zone)
        current_hour = now_utc.hour
        
        # หาจุดเริ่มต้นและสิ้นสุดของแท่ง H4
        h4_start = (current_hour // 4) * 4
        h4_end = 0 if h4_start == 20 else h4_start + 4
        
        # ใกล้จบแท่งไหม? (เหลือน้อยกว่า 1 ชั่วโมง)
        is_approaching = (h4_end - current_hour == 1) or (current_hour == 23 and h4_end == 0)
        
        return {
            'current_hour_utc': current_hour,
            'h4_start_utc': h4_start,
            'h4_end_utc': h4_end,
            'is_boundary_approaching': is_approaching
        }

# ── H4 Boundary Tracker (Added for v5.3) ──────────────
class H4SessionTracker:
    @staticmethod
    def get_h4_boundary():
        """
        ตรวจจับช่วงเวลา H4 ปัจจุบัน (0, 4, 8, 12, 16, 20) อิงตาม UTC
        คืน dict พร้อมฟิลด์ is_boundary_approaching
        """
        import pytz
        from datetime import datetime
        
        utc_zone = pytz.timezone('UTC')
        now_utc = datetime.now(utc_zone)
        current_hour = now_utc.hour
        
        # หาจุดเริ่มต้นและสิ้นสุดของแท่ง H4
        h4_start = (current_hour // 4) * 4
        h4_end = 0 if h4_start == 20 else h4_start + 4
        
        # ใกล้จบแท่งไหม? (เหลือน้อยกว่า 1 ชั่วโมง)
        is_approaching = (h4_end - current_hour == 1) or (current_hour == 23 and h4_end == 0)
        
        return {
            'current_hour_utc': current_hour,
            'h4_start_utc': h4_start,
            'h4_end_utc': h4_end,
            'is_boundary_approaching': is_approaching
        }
