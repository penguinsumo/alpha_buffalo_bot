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
