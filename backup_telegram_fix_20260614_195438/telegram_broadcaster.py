from dotenv import load_dotenv
load_dotenv()

import logging
import aiohttp
from typing import Optional, List

logger = logging.getLogger("AlphaBuffalo")

class TelegramBroadcaster:
    def __init__(self, token=None, chat_ids=None):
        import os
        self.token = token or os.environ.get('TELEGRAM_TOKEN', '')
        self.chat_ids = chat_ids or os.environ.get('NOTIFY_IDS', '').split(',')
        self.chat_ids = [c.strip() for c in self.chat_ids if c.strip()]
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self.enabled = bool(self.token and self.chat_ids)
        if self.enabled:
            logger.info("TelegramBroadcaster initialized")
        else:
            logger.warning("TelegramBroadcaster missing config")

    async def send_message(self, text: str, chat_ids: Optional[List[str]] = None, parse_mode: str = "Markdown"):
        if not self.enabled:
            return False
        
        ids = chat_ids or self.chat_ids
        url = f"{self.base_url}/sendMessage"
        
        async with aiohttp.ClientSession() as session:
            for chat_id in ids:
                try:
                    async with session.post(url, json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": parse_mode
                    }) as resp:
                        if resp.status == 200:
                            return True
                        else:
                            logger.error(f"Telegram send failed: {resp.status}")
                except Exception as e:
                    logger.error(f"Telegram error: {e}")
        return False

# Initialize พร้อมรับ Argument เพื่อป้องกันการ Crash
telegram_broadcaster = TelegramBroadcaster()

async def broadcast_message(msg: str, parse_mode: str = "Markdown"):
    return await telegram_broadcaster.send_message(msg, parse_mode=parse_mode)


def format_signal_message(data):
    """Format Signal → Telegram Message"""
    try:
        risk = data.entry_price - data.sl_price
        reward = data.tp1_price - data.entry_price
        rr_ratio = reward / abs(risk) if risk != 0 else 0
    except:
        rr_ratio = 0
    
    asset = getattr(data, 'asset', 'XAUUSD')
    tf = getattr(data, 'timeframe', '1H')
    signal = getattr(data, 'signal_type', 'NONE')
    entry = getattr(data, 'entry_price', 0)
    sl = getattr(data, 'sl_price', 0)
    tp1 = getattr(data, 'tp1_price', 0)
    tp2 = getattr(data, 'tp2_price', 0)
    
    message = (
        f"🐃 **ALPHA BUFFALO — {signal}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Asset:** {asset} (TF: {tf})\n"
        f"🟢 **Entry:** {entry:.2f}\n"
        f"🛑 **Stop Loss:** {sl:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **TP Forecast**\n"
        f"✅ **TP1:** {tp1:.2f}\n"
        f"🚀 **TP2:** {tp2:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 **R:R Ratio:** {rr_ratio:.2f}\n"
        f"_Trade with risk management._\n"
        f"#XAUUSD #AlphaBuffalo #SniperSignal"
    )
    return message

def format_signal_full(signal: dict) -> str:
    """Format signal message with ALL fields — same as EA receives"""
    
    direction = signal.get("direction", "N/A")
    entry = signal.get("entry", 0)
    sl = signal.get("sl", 0)
    tp = signal.get("tp_final", signal.get("tp", 0))
    score = signal.get("score", 0)
    signal_type = signal.get("signal_type", "N/A")
    session = signal.get("session", "N/A")
    pattern = signal.get("pattern", "")
    harmonic = signal.get("harmonic", "")
    prz_low = signal.get("prz_low", 0)
    prz_high = signal.get("prz_high", 0)
    be_price = signal.get("be_price", 0)
    trail = signal.get("trail", 0)
    vsa_gate = signal.get("vsa_gate", signal.get("vsa_ok", "N/A"))
    
    partials = signal.get("partials", signal.get("partial", []))
    fallback_sl = signal.get("fallback_sl", 0)
    fallback_tp = signal.get("fallback_tp", 0)
    
    # Emoji
    dir_emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"
    score_emoji = "⭐" if score >= 6 else "✅" if score >= 4 else "⚠️"
    
    msg = f"""
🐃 ALPHA BUFFALO v5.4 — SIGNAL
━━━━━━━━━━━━━━━━━━━━━━━━━
{dir_emoji} {direction} | {signal_type} | Score: {score} {score_emoji}
📊 Session: {session}
"""
    
    if pattern or harmonic:
        msg += f"📐 Pattern: {pattern or harmonic}\n"
    
    msg += f"""
💰 Entry: ${entry:.2f}
🛑 SL:    ${sl:.2f}
🎯 TP:    ${tp:.2f}
"""
    
    if be_price > 0:
        msg += f"🛡️ BE @ ${be_price:.2f}\n"
    
    if prz_low > 0 and prz_high > 0:
        msg += f"📍 PRZ: ${prz_low:.0f} - ${prz_high:.0f}\n"
    
    if partials:
        msg += "📤 Partial Close:\n"
        for p in partials:
            pct = p.get("pct", 0)
            price = p.get("price", 0)
            reason = p.get("reason", "")
            if pct > 0 and price > 0:
                msg += f"   {pct}% @ ${price:.2f} ({reason})\n"
    
    if vsa_gate and vsa_gate != "N/A":
        msg += f"📊 VSA Gate: {vsa_gate}\n"
    
    if fallback_sl > 0:
        msg += f"⚠️ Fallback SL: ${fallback_sl:.2f} | TP: ${fallback_tp:.2f}\n"
    
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    return msg

# 🔧 PHASE 6C: Use full format for Telegram messages
# Replace old format_message() call with format_signal_full()
