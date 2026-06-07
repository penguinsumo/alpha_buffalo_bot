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

    async def send_message(self, text: str, chat_ids: Optional[List[str]] = None) -> List[bool]:
        targets = chat_ids if chat_ids is not None else self.default_chat_ids
        results = []
        async with aiohttp.ClientSession() as session:
            for chat_id in targets:
                url = f"{self.api_base}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                try:
                    async with session.post(url, json=payload, timeout=5) as resp:
                        if resp.status == 200:
                            logger.info(f"Sent to {chat_id}")
                            results.append(True)
                        else:
                            logger.error(f"Failed {chat_id}: {resp.status}")
                            results.append(False)
                except Exception as e:
                    logger.error(f"Error {chat_id}: {e}")
                    results.append(False)
        return results

    def send_message_sync(self, text: str, chat_ids: Optional[List[str]] = None) -> List[bool]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.send_message(text, chat_ids))
                return future.result()
        else:
            return asyncio.run(self.send_message(text, chat_ids))