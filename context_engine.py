"""
context_engine.py — Alpha Buffalo v5
รวม External Plugins เป็น Context Score
เสริม signal_engine.py

Flow:
signal_engine คำนวณ Technical Score
context_engine คำนวณ Fundamental Score
รวมกัน → Final Score → ส่งให้ EA
"""

import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from plugin_news       import check_news_filter
from plugin_fear_greed import get_fg_score_adj
from plugin_dxy        import get_dxy_score_adj
from plugin_cot        import get_cot_score_adj

BKK = timezone(timedelta(hours=7))

TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "")

# Thresholds
V4_FINAL_MIN = 4   # Technical + Context
V5_FINAL_MIN = 8


@dataclass
class ContextResult:
    """ผลลัพธ์จาก Context Engine"""
    safe:         bool    # ปลอดภัยในการเทรดไหม
    total_adj:    int     # รวม score adjustment
    final_score:  int     # Technical + Context
    signal_type:  str     # V4/V5/BLOCKED
    reasons:      list = field(default_factory=list)
    news:         dict = field(default_factory=dict)
    fear_greed:   dict = field(default_factory=dict)
    dxy:          dict = field(default_factory=dict)
    cot:          dict = field(default_factory=dict)
    summary:      str = ""


def compute_context(
    direction:       str,
    technical_score: int,
    news_buffer:     int = 30,
) -> ContextResult:
    """
    รับ Technical Score → เพิ่ม Context → คืน Final Score

    Args:
        direction: "BUY" or "SELL"
        technical_score: score จาก signal_engine
        news_buffer: นาที buffer รอบข่าว
    """
    reasons = []
    total_adj = 0

    # ── 1. News Filter ─────────────────────────────────────
    news = check_news_filter(news_buffer, news_buffer)
    total_adj += news["score_adj"]
    reasons.append(news["reason"])

    # ถ้า news บล็อก → หยุดทันที
    if not news["safe"]:
        return ContextResult(
            safe         = False,
            total_adj    = news["score_adj"],
            final_score  = 0,
            signal_type  = "BLOCKED",
            reasons      = reasons,
            news         = news,
            summary      = f"🚫 BLOCKED: {news['reason']}",
        )

    # ── 2. Fear & Greed ────────────────────────────────────
    fg = get_fg_score_adj(direction)
    total_adj += fg["score_adj"]
    reasons.append(fg["reason"])

    # ── 3. DXY ────────────────────────────────────────────
    dxy = get_dxy_score_adj(direction, TWELVE_API_KEY)
    total_adj += dxy["score_adj"]
    reasons.append(dxy["reason"])

    # ── 4. COT ────────────────────────────────────────────
    cot = get_cot_score_adj(direction)
    total_adj += cot["score_adj"]
    reasons.append(cot["reason"])

    # ── Final Score ────────────────────────────────────────
    final_score = technical_score + total_adj
    final_score = max(0, final_score)  # ไม่ติดลบ

    # ── Signal Type ────────────────────────────────────────
    if final_score >= V5_FINAL_MIN:
        signal_type = "V5_SNIPER"
    elif final_score >= V4_FINAL_MIN:
        signal_type = "V4_SESSION"
    else:
        signal_type = "WEAK"

    safe = final_score >= V4_FINAL_MIN

    # ── Summary ────────────────────────────────────────────
    adj_str = f"{total_adj:+d}" if total_adj != 0 else "0"
    summary = (
        f"{'✅' if safe else '⚠️'} Context: "
        f"Tech:{technical_score} + Ctx:{adj_str} = {final_score} "
        f"[{signal_type}]"
    )

    return ContextResult(
        safe        = safe,
        total_adj   = total_adj,
        final_score = final_score,
        signal_type = signal_type,
        reasons     = reasons,
        news        = news,
        fear_greed  = fg,
        dxy         = dxy,
        cot         = cot,
        summary     = summary,
    )


def format_context_log(ctx: ContextResult) -> str:
    """สร้าง log string สำหรับ Telegram/print"""
    lines = [ctx.summary]
    for r in ctx.reasons:
        lines.append(f"  {r}")
    return "\n".join(lines)


def get_context_status() -> dict:
    """เช็คสถานะ context ทั้งหมด (สำหรับ /context command)"""
    now = datetime.now(BKK).strftime("%H:%M:%S")
    news = check_news_filter()
    fg   = get_fg_score_adj("BUY")
    dxy  = get_dxy_score_adj("BUY", TWELVE_API_KEY)
    cot  = get_cot_score_adj("BUY")

    return {
        "timestamp":   now,
        "news_safe":   news["safe"],
        "news_reason": news["reason"],
        "fear_greed":  f"{fg['emoji']} {fg['value']} ({fg['label']})",
        "dxy_trend":   f"{dxy['emoji']} {dxy['trend']}",
        "cot_rank":    f"{cot['emoji']} {cot['pct_rank']}% ({cot['bias']})",
    }
