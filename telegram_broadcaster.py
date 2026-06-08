import asyncio
import aiohttp
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class TelegramBroadcaster:
    def __init__(self, token: str, default_chat_ids: List[str]):
        self.token = token
        self.default_chat_ids = default_chat_ids
        self.api_base = f"https://api.telegram.org/bot{token}"

    async def _send_single(self, session: aiohttp.ClientSession, chat_id: str, text: str) -> bool:
        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True}  # 1. แก้ไขพารามิเตอร์ที่ล้าสมัย
        }
        try:
            async with session.post(url, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    logger.info(f"Sent to {chat_id}")
                    return True
                else:
                    logger.error(f"Failed {chat_id}: {resp.status}")
                    return False
        except Exception as e:  # 2. เพิ่ม except เพื่อจัดการ Error ไม่ให้โปรแกรมพัง
            logger.error(f"Error sending to {chat_id}: {str(e)}")
            return False

    async def send_message(self, text: str, chat_ids: Optional[List[str]] = None) -> List[bool]:
        targets = chat_ids if chat_ids is not None else self.default_chat_ids
        if not targets:
            logger.warning("No chat IDs to send")
            return []

        async with aiohttp.ClientSession() as session:
            # 3. ใช้ asyncio.gather เพื่อส่งพร้อมกันทีเดียว (Concurrent)
            tasks = [self._send_single(session, chat_id, text) for chat_id in targets]
            results = await asyncio.gather(*tasks)
            
        return list(results)
