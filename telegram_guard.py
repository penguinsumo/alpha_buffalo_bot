"""Repository-wide hard gate for every outbound Telegram network call."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import requests

from session_clock import SessionClock


TELEGRAM_DISCLAIMER = (
    'Disclaimer: For educational and system operations only. Content is provided '
    '"as-is" with no guarantee of accuracy or completeness. It does not constitute '
    'financial advice or a solicitation to buy/sell securities, digital assets, or '
    'financial products. Use at your own risk.'
)


def ensure_telegram_disclaimer(text: str) -> str:
    """Return one canonical footer for every outbound Telegram message."""
    body = str(text or "").rstrip()
    if TELEGRAM_DISCLAIMER in body:
        return body
    return f"{body}\n\n{TELEGRAM_DISCLAIMER}" if body else TELEGRAM_DISCLAIMER


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
    allow_closed_test: bool = False,
):
    """Post after the market gate, except an explicit authenticated TEST send."""
    if (
        not allow_closed_test
        and not telegram_market_is_open(payload_session=payload_session, now=now)
    ):
        return None
    sender = post or requests.post
    payload = dict(json)
    if "text" in payload:
        payload["text"] = ensure_telegram_disclaimer(payload["text"])
    return sender(url, json=payload, timeout=timeout)
