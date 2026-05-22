import json
import os

STATE_FILE = 'shared_state.json'

# Initialize JSON if not exists
if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, 'w') as f:
        json.dump({"locked_high": 0.0, "locked_low": 0.0, "harmonic_stage": "neutral"}, f)

def get_shared_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"locked_high": 0.0, "locked_low": 0.0, "harmonic_stage": "neutral"}

def get_flexible_score():
    """
    Plugin สำหรับ V4: คำนวณ Score จากระยะห่างของราคาล่าสุด กับ Pivot Points
    """
    state = get_shared_state()
    # ตรงนี้คือจุดที่คุณจะใส่ Logic V5 จริงๆ
    # ตัวอย่าง: คำนวณความใกล้เคียงของราคาปัจจุบัน (dummy) กับ locked_high/low
    # Placeholder: คืนค่าเป็น 85 เพื่อทดสอบ Integration
    return 85

def update_pivot_from_v4(high, low):
    # ฟังก์ชันนี้ใช้เมื่อ V4 ตรวจพบ Swing ใหม่
    state = get_shared_state()
    state.update({"locked_high": high, "locked_low": low})
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
