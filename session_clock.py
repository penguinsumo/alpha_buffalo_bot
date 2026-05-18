"""
session_clock.py - Alpha Buffalo v5
Session-Based Pivot Clock
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

class Session(Enum):
    ASIA   = "asia"
    LONDON = "london"
    NY     = "ny"
    DEAD   = "dead"

SESSION_ORDER = [Session.ASIA, Session.LONDON, Session.NY]
_PRIORITY = [Session.NY, Session.LONDON, Session.ASIA]
_SESSION_HOURS = {Session.ASIA:(1,9), Session.LONDON:(7,16), Session.NY:(12,21)}
_SCORE_MODIFIER = {
    (Session.ASIA,"BUY"):+10, (Session.ASIA,"SELL"):-10,
    (Session.LONDON,"BUY"):0, (Session.LONDON,"SELL"):0,
    (Session.NY,"BUY"):0,     (Session.NY,"SELL"):0,
    (Session.DEAD,"BUY"):0,   (Session.DEAD,"SELL"):0,
}
_THRESHOLD    = {Session.ASIA:70, Session.LONDON:70, Session.NY:80, Session.DEAD:999}
_LOOKBACK_M15 = {Session.ASIA:32, Session.LONDON:32, Session.NY:32, Session.DEAD:0}

@dataclass
class SessionInfo:
    session: Session
    utc_hour: int
    score_threshold: int
    score_modifier: dict
    lookback_m15: int
    is_tradeable: bool
    label: str
    def modifier_for(self, direction): return self.score_modifier.get(direction.upper(), 0)
    def effective_threshold(self, direction): return self.score_threshold - self.modifier_for(direction)

class SessionClock:
    def __init__(self, dst_offset_hours=0): self.dst_offset = dst_offset_hours
    def get(self, utc_now=None):
        if utc_now is None: utc_now = datetime.now(timezone.utc)
        h = (utc_now.hour + self.dst_offset) % 24
        s = self._classify(h)
        return self._build_info(s, h)
    def get_session(self, utc_now=None): return self.get(utc_now).session
    def is_transition(self, prev, current):
        prev    = Session(prev)    if isinstance(prev, str)    else prev
        current = Session(current) if isinstance(current, str) else current
        if prev == current: return False
        if prev not in SESSION_ORDER or current not in SESSION_ORDER: return False
        return SESSION_ORDER.index(current) > SESSION_ORDER.index(prev)
    def should_trade(self, utc_now=None): return self.get(utc_now).is_tradeable
    def _classify(self, h):
        for s in _PRIORITY:
            start, end = _SESSION_HOURS[s]
            if start <= h < end: return s
        return Session.DEAD
    def _build_info(self, session, h):
        modifier = {"BUY": _SCORE_MODIFIER.get((session,"BUY"),0), "SELL": _SCORE_MODIFIER.get((session,"SELL"),0)}
        labels   = {Session.ASIA:"Asia 01:00-09:00 UTC", Session.LONDON:"London 07:00-16:00 UTC", Session.NY:"New York 12:00-21:00 UTC", Session.DEAD:"Dead zone 21:00-01:00 UTC"}
        return SessionInfo(session=session, utc_hour=h, score_threshold=_THRESHOLD[session], score_modifier=modifier, lookback_m15=_LOOKBACK_M15[session], is_tradeable=session!=Session.DEAD, label=labels[session])
