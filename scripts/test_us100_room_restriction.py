#!/usr/bin/env python3
"""
Regression tests for restricting US100 broadcasts to ADMIN_ID only (owner's
explicit request, 9 ก.ย. 2026): US100 still uses the unverified
QQQ x US100_QQQ_SCALE stopgap for its price data (a real TwelveData-
confirmed NDX/NQ ticker never got confirmed), so the owner asked to stop
broadcasting US100 signals into shared/other rooms until that's resolved
properly -- every other extra symbol (BTCUSD, JPN225) keeps broadcasting to
every configured room exactly as before, and US100 itself still gets fully
scanned/alerted, just only into ADMIN_ID (the bot owner's own room).

Covers:
  1. _extra_notify_targets() -- the new routing helper: US100 restricted to
     [ADMIN_ID] by default, every other symbol unaffected (full
     EXTRA_NOTIFY_IDS), kill switch (ALPHA_EXTRA_SYMBOL_US100_RESTRICT_TO_
     ADMIN=false) restores broadcasting US100 everywhere too.
  2. Source guards on run_extra_symbol_pass(): both the Trend Update and
     the BUY/SELL Signal send loops route through _extra_notify_targets(
     symbol) instead of the raw EXTRA_NOTIFY_IDS list. Source guards on the
     combined multi-symbol digest block in signal_loop(): ADMIN_ID always
     gets the full digest, every other room gets a digest with US100 left
     out. Driving signal_loop() itself is deliberately avoided (it's an
     infinite loop making live network calls) -- same reasoning as every
     other test file in this project for code that isn't practical to
     drive end-to-end (see test_ea_executes_honesty.py).

Run: python3 scripts/test_us100_room_restriction.py
Exits non-zero on any failure.
"""
import inspect
import os
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


import alpha_buffalo_signal as runtime

# ═══════════════════════════════════════════════════════════
# 1. _extra_notify_targets()
# ═══════════════════════════════════════════════════════════

runtime.ADMIN_ID = "111"
runtime.EXTRA_NOTIFY_IDS = ["111", "-100222", "-100333"]

runtime.US100_RESTRICT_TO_ADMIN = True
check("default (restriction on): US100 targets ONLY ADMIN_ID",
      runtime._extra_notify_targets("US100") == ["111"])
check("default (restriction on): BTCUSD is unaffected -- full room list",
      runtime._extra_notify_targets("BTCUSD") == ["111", "-100222", "-100333"])
check("default (restriction on): JPN225 is unaffected -- full room list",
      runtime._extra_notify_targets("JPN225") == ["111", "-100222", "-100333"])

runtime.US100_RESTRICT_TO_ADMIN = False
check("kill switch off: US100 also gets the full room list again",
      runtime._extra_notify_targets("US100") == ["111", "-100222", "-100333"])
runtime.US100_RESTRICT_TO_ADMIN = True  # restore for the rest of this file


# ═══════════════════════════════════════════════════════════
# 2. Source guards
# ═══════════════════════════════════════════════════════════

src_pass = inspect.getsource(runtime.run_extra_symbol_pass)
check("run_extra_symbol_pass(): Trend Update loop routes through "
      "_extra_notify_targets(symbol)",
      "for cid in _extra_notify_targets(symbol):" in src_pass)
check("run_extra_symbol_pass(): no longer sends Trend Update/Signal "
      "straight to the raw EXTRA_NOTIFY_IDS list",
      "for cid in EXTRA_NOTIFY_IDS:" not in src_pass)
# Two separate send loops (Trend Update + BUY/SELL Signal) both fixed.
check("run_extra_symbol_pass(): BOTH send loops (trend + signal) were fixed",
      src_pass.count("for cid in _extra_notify_targets(symbol):") == 2)

src_loop = inspect.getsource(runtime.signal_loop)
check("signal_loop() digest: ADMIN_ID always gets the full multi-symbol digest",
      'send_telegram(digest_msg, chat_id=ADMIN_ID)' in src_loop)
check("signal_loop() digest: other rooms get a digest with US100 excluded",
      "non_us100_trends" in src_loop and "other_room_ids" in src_loop)
check("signal_loop() digest: no longer blindly sends the full digest to "
      "every EXTRA_NOTIFY_IDS room",
      "for cid in EXTRA_NOTIFY_IDS:\n                        send_telegram(digest_msg, chat_id=cid)"
      not in src_loop)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All US100-room-restriction regression checks passed.")
