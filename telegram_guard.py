"""Repository-wide hard gate for every outbound Telegram network call."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import requests

from session_clock import SessionClock


def telegram_market_is_open(
    *,
    payload_session: str = "",
    now: datetime | None = None,
) -> bool:
    """Fail closed when runtime or payload says the XAU market is closed."""
    try:
        current_session = str(SessionClock().get(now).session or "CLOSED").upper()
    except Exception:
        return False
    return current_session != "CLOSED" and str(payload_session or "").upper() != "CLOSED"


def guarded_telegram_post(
    url: str,
    *,
    json: dict[str, Any],
    timeout: float,
    payload_session: str = "",
    now: datetime | None = None,
    post: Callable[..., Any] | None = None,
):
    """Make a Telegram POST only after the centralized market-hours gate."""
    if not telegram_market_is_open(payload_session=payload_session, now=now):
        return None
    sender = post or requests.post
    return sender(url, json=json, timeout=timeout)
