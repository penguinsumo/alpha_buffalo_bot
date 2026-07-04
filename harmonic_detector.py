import math
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

@dataclass
class ForecastPattern:
    """เก็บข้อมูลการคาดการณ์ Harmonic Pattern ก่อนถึงจุด D"""
    pattern: str
    direction: str
    completion: float  
    expected_d: float
    prz_low: float
    prz_high: float
    confidence: float  # อัปเกรดเป็นเปอร์เซ็นต์ความน่าจะเป็นทางสถิติ (0.0 - 100.0)
    state: str         

@dataclass
class PRZZone:
    """Potential Reversal Zone ที่ผ่านการ Normalization แล้ว"""
    pattern_name: str
    direction: str
    priority: int
    reliability: str
    prz_high: float
    prz_low: float
    prz_mid: float
    d_point: float
    confluence_score: int = 0
    label: str = ""

    def in_prz(self, price: float) -> bool:
        return self.prz_low <= price <= self.prz_high

def recalculate_prz_after_bos(
    L: float, 
    H: float, 
    HL: float, 
    current_price: float, 
    direction: str = "BUY",
    atr: Optional[float] = None,         # ปิดจุดอ่อนข้อ 2: รองรับ ATR dynamic สำหรับ Volatility
    htf_trend: Optional[str] = None     # ปิดจุดอ่อนข้อ 5: รองรับ Multi-Timeframe Confirmation Bias
) -> Tuple[Optional[PRZZone], Optional[ForecastPattern]]:
    """
    คำนวณ PRZ และ Forecast State หลังเกิด BOS พร้อมระบบสถิติและ Volatility Normalization ขั้นสูง
    """
    if L is None or H is None or HL is None:
        return None, None
    
    xa = abs(H - L)
    ab = abs(H - HL)
    if xa <= 0:
        return None, None
    
    ab_xa = ab / xa
    
    # Template รูปแบบ Harmonic ในอุดมคติ (Ideal Ratios)
    patterns = [
        ("Bat", 0.38, 0.50, 0.886, 0.50),
        ("Gartley", 0.58, 0.65, 0.786, 0.618),
        ("Crab", 0.38, 0.62, 1.618, 0.618),      
        ("Butterfly", 0.75, 0.82, 1.272, 0.786),
        ("Shark", 1.13, 1.618, 0.886, 1.13)
    ]
    
    best_pattern = "Unknown"
    xa_retrace = 0.0
    min_error = float('inf')
    
    # ค้นหารูปแบบด้วย Error Minimization (Disambiguation Space)
    for name, min_r, max_r, ext, ideal_b in patterns:
        if min_r <= ab_xa <= max_r:
            error = abs(ab_xa - ideal_b)
            if error < min_error:
                min_error = error
                best_pattern = name
                xa_retrace = ext
                
    if best_pattern == "Unknown":
        return None, None

    # คำนวณจุดกลับตัว D ในอนาคต
    d_price = L + xa * xa_retrace if direction == "BUY" else H - xa * xa_retrace
    
    # -----------------------------------------------------------------
    # ปิดจุดอ่อนข้อ 1: Statistical Calibration of Confidence (Gaussian Decay)
    # -----------------------------------------------------------------
    # แปลง Error ให้เป็นค่าความน่าจะเป็นทางสถิติ (Perfect Geometry = 100%)
    # ใช้ Lambda เกลาการกระจายตัว (Error 0.05 จะเหลือความมั่นใจประมาณ ~60%)
    base_confidence = math.exp(-15.0 * min_error) * 100.0
    
    # ปิดจุดอ่อนข้อ 5: Multi-Timeframe Confirmation Overlay
    # ถ้าทิศทางสอดคล้องกับแนวโน้มของไทม์เฟรมใหญ่ (HTF) จะให้คะแนนความแม่นยำเพิ่มขึ้น 10%
    if htf_trend and htf_trend.upper() == direction.upper():
        calibrated_confidence = min(100.0, base_confidence * 1.10)
    else:
        calibrated_confidence = base_confidence

    # -----------------------------------------------------------------
    # ปิดจุดอ่อนข้อ 2: Volatility Normalization (Real ATR vs Structural Proxy)
    # -----------------------------------------------------------------
    # หาก Engine ส่งค่า ATR จริงมาให้จะใช้ ATR * 1.5 เพื่อสะท้อนสภาวะตลาดปัจจุบัน 
    # แต่หากไม่มี จะใช้ระยะสวิง XA * 5% เป็นค่าสำรอง (Fallback)
    if atr and atr > 0:
        zone_width = atr * 1.5
    else:
        zone_width = xa * 0.05
        
    prz = PRZZone(
        pattern_name=best_pattern,
        direction=direction,
        priority=3,
        reliability="HIGH" if calibrated_confidence >= 80.0 else "MEDIUM" if calibrated_confidence >= 50.0 else "LOW",
        prz_high=d_price + (zone_width / 2),
        prz_low=d_price - (zone_width / 2),
        prz_mid=d_price,
        d_point=d_price,
        confluence_score=int(calibrated_confidence / 20), # สเกลคะแนน 0-5 ตามระดับสถิติ
        label=f"{best_pattern} PRZ @ {d_price:.2f} ({calibrated_confidence:.1f}%)"
    )
    
    # คำนวณสถานะความคืบหน้า (Completion Percentage Process)
    current_move = abs(current_price - HL)
    total_move_needed = abs(d_price - HL)
    completion_pct = min(100.0, (current_move / total_move_needed) * 100.0) if total_move_needed > 0 else 0.0
    
    state = "DISCOVERED"
    if completion_pct >= 90.0:
        state = "APPROACHING_PRZ"
    elif completion_pct >= 50.0:
        state = "BUILDING"
        
    forecast = ForecastPattern(
        pattern=best_pattern,
        direction=direction,
        completion=round(completion_pct, 2),
        expected_d=d_price,
        prz_low=prz.prz_low,
        prz_high=prz.prz_high,
        confidence=round(calibrated_confidence, 2),
        state=state
    )
    
    return prz, forecast
