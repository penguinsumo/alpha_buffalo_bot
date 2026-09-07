#!/usr/bin/env python3
"""
Regression test for a confirmed bug (2026-09-07, not opt-in): the Stage 3
"SIGNAL FIRING" alert (early_warning.alert_signal_ready(), called from
inside signal_engine.compute_signal()) hardcoded "🤖 EA executing..." for
EVERY symbol unconditionally -- including the opt-in extra-symbol scan
(BTC/US100/JPN225), which is never wired to auto-execution. A live US100
signal showed this exact contradiction: this message claimed "EA
executing..." while the very next message for the SAME signal
(format_signal_message, called with ea_executes=False) correctly said
"Signal only — not wired to auto-execution yet".

Fix: alert_signal_ready() takes a new ea_executes flag (default True,
preserving old wording for the one caller that always meant it -- the
main traded SYMBOL) and compute_signal() passes
ea_executes=(active_symbol == SYMBOL) -- active_symbol only differs from
the main SYMBOL when this call came from the extra-symbol scan
(symbol_label set), none of which are EA-executed.

Covers:
  1. alert_signal_ready() message content for ea_executes True/False
     (the actual bug -- wrong wording).
  2. A source-guard on compute_signal()'s call site: because driving a
     signal all the way through compute_signal()'s full score/BB/Auto-
     Fibo/scenario gate cascade with synthetic OHLCV is what every other
     test file in this project deliberately avoids (they test the
     extracted pure helpers instead), this checks the exact wiring
     expression is still present at the call site -- a regression guard
     against the fix being silently reverted or bypassed, not a
     substitute for check #1.

Run: python3 scripts/test_ea_executes_honesty.py
Exits non-zero on any failure.
"""
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ═══════════════════════════════════════════════════════════
# 1. alert_signal_ready() message content
# ═══════════════════════════════════════════════════════════

import early_warning

sent_messages = []


def fake_send_telegram(msg, chat_id=None):
    sent_messages.append(msg)


original_send = early_warning.send_telegram
early_warning.send_telegram = fake_send_telegram
try:
    sent_messages.clear()
    early_warning.alert_signal_ready(
        "XAUUSD", "BUY", "V4_SESSION", score=6,
        entry=2650.0, sl=2645.0, tp=2660.0, session="NY",
        ea_executes=True,
    )
    check("ea_executes=True -> message says 'EA executing...'",
          len(sent_messages) == 1 and "EA executing..." in sent_messages[0])
    check("ea_executes=True -> message does NOT say 'not wired'",
          "not wired" not in sent_messages[0])

    # Reset the per-symbol cooldown so the next call isn't swallowed by
    # _can_alert()'s STAGE_READY throttle (same symbol, back to back).
    early_warning._warn_states.pop("US100", None)
    sent_messages.clear()
    early_warning.alert_signal_ready(
        "US100", "BUY", "V4_SESSION", score=6,
        entry=29553.37, sl=29522.74, tp=29645.24, session="NY",
        ea_executes=False,
    )
    check("ea_executes=False -> message says 'Signal only — not wired to auto-execution yet'",
          len(sent_messages) == 1 and
          "Signal only — not wired to auto-execution yet" in sent_messages[0])
    check("ea_executes=False -> message does NOT claim 'EA executing...'",
          "EA executing..." not in sent_messages[0])

    # Default preserves the original always-executing wording -- the one
    # caller (the main traded SYMBOL) that never passed this kwarg before
    # this fix must see byte-identical behavior.
    early_warning._warn_states.pop("XAUUSD", None)
    sent_messages.clear()
    early_warning.alert_signal_ready(
        "XAUUSD", "SELL", "V5_SNIPER", score=8,
        entry=2650.0, sl=2655.0, tp=2635.0, session="London",
    )
    check("default (no ea_executes passed) -> still says 'EA executing...' (old behavior preserved)",
          len(sent_messages) == 1 and "EA executing..." in sent_messages[0])
finally:
    early_warning.send_telegram = original_send


# ═══════════════════════════════════════════════════════════
# 2. Source guard: compute_signal()'s call site passes the fix through
# ═══════════════════════════════════════════════════════════

import signal_engine

src = inspect.getsource(signal_engine.compute_signal)
check("compute_signal() call site passes ea_executes=(active_symbol == SYMBOL)",
      bool(re.search(r"ea_executes\s*=\s*\(\s*active_symbol\s*==\s*SYMBOL\s*\)", src)))


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All EA-executes-honesty regression checks passed.")
