#!/usr/bin/env python3
"""
Regression tests for the new /testalert admin Telegram command (9 ก.ย. 2026):
a manual smoke test for the entry-consistency fix (see
scripts/test_alert_entry_consistency.py) -- owner asked to actually SEE the
"SESSION SIGNAL FIRING" alert show a correct/consistent Entry+SL pair in
Telegram, not just trust the code-level fix.

/testalert deliberately offsets a simulated Entry away from the live price
the same way ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL clamps a real Entry into its
scored Fib/PRZ zone -- exactly the scenario that exposed the original bug
(entry_price vs raw price mismatch). It calls early_warning.
alert_signal_ready() DIRECTLY -- never touches compute_signal(),
execution_bridge, or signal_log -- so it queues no order and pollutes no
historical stats. Unrelated to /testopen, which tests real order execution.

Covers:
  1. Non-admin is rejected, no alert fired.
  2. Admin BUY: exactly one SESSION SIGNAL FIRING alert fires, with Entry
     offset ABOVE the live price and SL correctly BELOW that Entry (the
     exact invariant the entry-consistency fix restores).
  3. Admin SELL: mirror -- Entry offset BELOW live price, SL ABOVE it.
  4. The admin's confirmation reply says no order was queued.
  5. Source guards: no execution_bridge/queue_open_command/log_signal call
     anywhere in the /testalert block -- message-format test only, never a
     real trading action.

Run: python3 scripts/test_testalert_command.py
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


import pandas as pd

import alpha_buffalo_signal as runtime
import early_warning

ADMIN_ID = "999"
runtime.ADMIN_ID = ADMIN_ID


def fake_get_ohlcv(interval, bars, symbol=None):
    return pd.DataFrame({"close": [4400.0]})


runtime.get_ohlcv = fake_get_ohlcv

captured_alerts = []
captured_replies = []

original_ew_send = early_warning.send_telegram
original_rt_send = runtime.send_telegram


def fake_ew_send(msg, chat_id=None):
    captured_alerts.append(msg)


def fake_rt_send(msg, chat_id=None):
    captured_replies.append((msg, chat_id))


early_warning.send_telegram = fake_ew_send
runtime.send_telegram = fake_rt_send

try:
    # ═══════════════════════════════════════════════════════════
    # 1. Non-admin rejected
    # ═══════════════════════════════════════════════════════════
    captured_alerts.clear(); captured_replies.clear()
    runtime.handle_cmd("/testalert", "111")
    check("non-admin: rejected with Unauthorized, no alert fired",
          captured_replies == [("Unauthorized", "111")] and captured_alerts == [])

    # ═══════════════════════════════════════════════════════════
    # 2. Admin BUY -- Entry offset above live price, SL correctly below Entry
    # ═══════════════════════════════════════════════════════════
    early_warning._warn_states.pop("XAUUSD", None)
    captured_alerts.clear(); captured_replies.clear()
    runtime.handle_cmd("/testalert", ADMIN_ID)
    check("admin BUY: exactly one SESSION SIGNAL FIRING alert fired",
          len(captured_alerts) == 1)
    if captured_alerts:
        msg = captured_alerts[0]
        check("admin BUY: alert shows Entry offset ABOVE the live price (4400 -> 4405.00)",
              "Entry  : 4,405.00" in msg)
        check("admin BUY: SL sits BELOW the displayed Entry (4403.00 < 4405.00)",
              "SL     : 4403.00" in msg)
        check("admin BUY: TP sits above Entry", "TP     : 4,411.00" in msg)
        check("admin BUY: alert clearly marked as a test (TEST_ALERT pattern)",
              "TEST_ALERT" in msg)
        check("admin BUY: does not falsely claim EA execution",
              "not wired to auto-execution" in msg)
    check("admin BUY: gets exactly one confirmation reply, says no order queued",
          len(captured_replies) == 1 and "No order was queued" in captured_replies[0][0]
          and captured_replies[0][1] == ADMIN_ID)

    # ═══════════════════════════════════════════════════════════
    # 3. Admin SELL -- mirror
    # ═══════════════════════════════════════════════════════════
    early_warning._warn_states.pop("XAUUSD", None)
    captured_alerts.clear(); captured_replies.clear()
    runtime.handle_cmd("/testalert sell", ADMIN_ID)
    check("admin SELL: exactly one SESSION SIGNAL FIRING alert fired",
          len(captured_alerts) == 1)
    if captured_alerts:
        msg = captured_alerts[0]
        check("admin SELL: alert shows Entry offset BELOW the live price (4400 -> 4395.00)",
              "Entry  : 4,395.00" in msg)
        check("admin SELL: SL sits ABOVE the displayed Entry (4397.00 > 4395.00)",
              "SL     : 4397.00" in msg)
        check("admin SELL: TP sits below Entry", "TP     : 4,389.00" in msg)
finally:
    early_warning.send_telegram = original_ew_send
    runtime.send_telegram = original_rt_send


# ═══════════════════════════════════════════════════════════
# 4. Source guards -- /testalert never touches execution/logging
# ═══════════════════════════════════════════════════════════

src = inspect.getsource(runtime.handle_cmd)
m = re.search(r'elif t == "/testalert".*?(?=\n    elif )', src, re.S)
check("source guard: /testalert block found in handle_cmd()", m is not None)
if m:
    block = m.group(0)
    check("/testalert block gates on ADMIN_ID", "ADMIN_ID" in block)
    check("/testalert block calls alert_signal_ready() directly",
          "alert_signal_ready(" in block)
    check("/testalert block NEVER calls queue_open_command() -- no real order",
          "queue_open_command(" not in block)
    check("/testalert block NEVER imports/calls execution_bridge -- message test only",
          "import execution_bridge" not in block and "execution_bridge." not in block
          and "execution_bridge import" not in block)
    check("/testalert block NEVER calls log_signal() -- no stats pollution",
          "log_signal(" not in block)

check("/help text documents /testalert",
      '"/testalert' in inspect.getsource(runtime.handle_cmd))


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All /testalert command regression checks passed.")
