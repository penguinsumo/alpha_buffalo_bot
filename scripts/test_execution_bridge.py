#!/usr/bin/env python3
"""
Regression test for execution_bridge.py (2026-09-07, not opt-in): the state
machine behind the execution-only EA contract (/execution/command,
/execution/fill, /execution/ack). This module is the only place that
decides what the EA does -- direction/SL/TP come straight from a real
XAUUSD signal, and lot size is computed here from day_start_equity + a
risk-% input, never by the EA itself.

Covers:
  1. compute_lot() -- normal risk-% math, MIN/MAX clamping, bad-input
     fallback to MIN_LOT.
  2. queue_open_command() -- normal queue, refuses to stomp an already-
     tracked open position, refuses invalid direction/missing levels,
     uses partial[0] for tp1/close_pct when present, falls back to
     tp_final/50% when sig.partial is empty.
  3. get_command() -- HOLD with nothing pending, OPEN command carries a
     freshly computed "lot" field, non-OPEN actions pass through as-is.
  4. record_fill() -- matches by signal_id, rejects a mismatched/unknown
     one without raising.
  5. record_ack() -- success clears the pending command (and, for
     CLOSE_ALL, the tracked position too); failure leaves the SAME
     command_id pending for retry; MAX_COMMAND_RETRIES consecutive
     failures drops it (and drops position tracking only for a never-
     filled OPEN).
  6. check_tp1_and_queue_be() -- fires only once a filled position's
     price reaches tp1 in its favor, never twice, never before fill,
     never stomping an unrelated pending command.
  7. Source guards: signal_loop() wires in both queue_open_command() and
     check_tp1_and_queue_be(); the three /execution/* routes exist.

Run: python3 scripts/test_execution_bridge.py
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


import execution_bridge as eb


def reset():
    eb._pending_command = None
    eb._open_position = None


# ═══════════════════════════════════════════════════════════
# 1. compute_lot()
# ═══════════════════════════════════════════════════════════

expected = round((10000 * 0.02) / (5.0 * 100), 2)  # 2% of 10,000 risk, $5 SL distance
check("compute_lot(): 2% of 10,000 equity, $5 SL distance -> expected risk math",
      eb.compute_lot(10000, 5.0) == expected)
check("compute_lot(): larger equity -> larger lot (scales with capital)",
      eb.compute_lot(20000, 5.0) > eb.compute_lot(10000, 5.0))
check("compute_lot(): clamps below MIN_LOT",
      eb.compute_lot(1.0, 100.0) == eb.MIN_LOT)
check("compute_lot(): clamps above MAX_LOT",
      eb.compute_lot(10_000_000, 0.01) == eb.MAX_LOT)
check("compute_lot(): zero/negative equity -> MIN_LOT, never raises",
      eb.compute_lot(0, 5.0) == eb.MIN_LOT and eb.compute_lot(-100, 5.0) == eb.MIN_LOT)
check("compute_lot(): zero/negative SL distance -> MIN_LOT, never raises",
      eb.compute_lot(10000, 0) == eb.MIN_LOT and eb.compute_lot(10000, -1) == eb.MIN_LOT)
check("compute_lot(): None inputs -> MIN_LOT, never raises",
      eb.compute_lot(None, 5.0) == eb.MIN_LOT and eb.compute_lot(10000, None) == eb.MIN_LOT)


# ═══════════════════════════════════════════════════════════
# 2. queue_open_command()
# ═══════════════════════════════════════════════════════════

reset()
ok = eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                            be_price=4400.10,
                            partial=[{"pct": 50, "price": 4410.0, "reason": "Fibo"}],
                            reason="TREND_CONTINUATION")
check("queue_open_command(): returns True on a clean queue", ok is True)
check("queue_open_command(): pending command action=OPEN direction=BUY",
      eb._pending_command["action"] == "OPEN" and eb._pending_command["direction"] == "BUY")
check("queue_open_command(): tp1 taken from partial[0] price",
      eb._pending_command["tp1"] == 4410.0)
check("queue_open_command(): open_position tracks close_pct from partial[0]",
      eb._open_position["close_pct"] == 50)
check("queue_open_command(): open_position starts unfilled",
      eb._open_position["filled"] is False)

ok2 = eb.queue_open_command(direction="SELL", entry=4400.0, sl=4410.0, tp_final=4380.0,
                             be_price=4399.90, partial=[], reason="x")
check("queue_open_command(): refuses to stomp an already-tracked open position",
      ok2 is False and eb._pending_command["direction"] == "BUY")

reset()
ok3 = eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                             be_price=4400.10, partial=[], reason="no partial")
check("queue_open_command(): falls back to tp_final/50% when partial is empty",
      ok3 is True and eb._pending_command["tp1"] == 4420.0 and eb._open_position["close_pct"] == 50.0)

reset()
check("queue_open_command(): rejects invalid direction",
      eb.queue_open_command("LONG", 4400.0, 4390.0, 4420.0, 4400.1, []) is False
      and eb._pending_command is None)
check("queue_open_command(): rejects missing sl/tp/entry",
      eb.queue_open_command("BUY", None, 4390.0, 4420.0, 4400.1, []) is False
      and eb.queue_open_command("BUY", 4400.0, None, 4420.0, 4400.1, []) is False
      and eb.queue_open_command("BUY", 4400.0, 4390.0, None, 4400.1, []) is False)


# ═══════════════════════════════════════════════════════════
# 3. get_command()
# ═══════════════════════════════════════════════════════════

reset()
check("get_command(): HOLD when nothing pending",
      eb.get_command(day_start_equity=10000)["action"] == "HOLD")

eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
cmd = eb.get_command(day_start_equity=10000)
check("get_command(): OPEN command carries a freshly computed lot field",
      "lot" in cmd and cmd["lot"] == eb.compute_lot(10000, 10.0))  # |entry-sl| = 10.0
check("get_command(): OPEN command never leaks internal fail_count",
      "fail_count" not in cmd)

reset()
eb._pending_command = {"command_id": "abc123", "action": "CLOSE_ALL",
                        "reason": "manual", "symbol": "XAUUSD"}
cmd2 = eb.get_command(day_start_equity=10000)
check("get_command(): non-OPEN actions pass through without a lot field",
      cmd2["action"] == "CLOSE_ALL" and "lot" not in cmd2)


# ═══════════════════════════════════════════════════════════
# 4. record_fill()
# ═══════════════════════════════════════════════════════════

reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[])
sid = eb._open_position["signal_id"]
check("record_fill(): unknown signal_id rejected, never raises",
      eb.record_fill("not-the-real-id", "999", 4401.0) is False)
check("record_fill(): matching signal_id accepted",
      eb.record_fill(sid, "555", 4401.5) is True)
check("record_fill(): open_position now marked filled with ticket/price",
      eb._open_position["filled"] is True and eb._open_position["ticket"] == "555"
      and eb._open_position["fill_price"] == 4401.5)

check("record_fill(): with no tracked position at all, returns False not raise",
      (lambda: (reset(), eb.record_fill("anything", "1", 1.0))[1])() is False)


# ═══════════════════════════════════════════════════════════
# 5. record_ack()
# ═══════════════════════════════════════════════════════════

reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[])
cid = eb._pending_command["command_id"]
check("record_ack(): unknown command_id rejected",
      eb.record_ack("not-the-real-cid", True) is False)
check("record_ack(): OPEN success clears the pending command",
      eb.record_ack(cid, True, 100.0, 0.0) is True and eb._pending_command is None)
check("record_ack(): OPEN success keeps position tracking (still open on broker)",
      eb._open_position is not None)

# PARTIAL_CLOSE_MOVE_BE success -> be_done set, pending cleared
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
eb.record_fill(eb._open_position["signal_id"], "1", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # OPEN itself ACKed first, as in real flow
eb.check_tp1_and_queue_be(4410.0)
be_cid = eb._pending_command["command_id"]
check("record_ack(): PARTIAL_CLOSE_MOVE_BE success marks be_done, clears pending",
      eb.record_ack(be_cid, True, 50.0, 0.0) is True
      and eb._pending_command is None and eb._open_position["be_done"] is True)

# CLOSE_ALL success -> both pending and position cleared
reset()
eb.queue_close_all(reason="admin")
close_cid = eb._pending_command["command_id"]
check("record_ack(): CLOSE_ALL success clears both pending command and position",
      eb.record_ack(close_cid, True, 0.0, 0.0) is True
      and eb._pending_command is None and eb._open_position is None)

# Failure path: left pending for retry, fail_count increments
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[])
fail_cid = eb._pending_command["command_id"]
eb.record_ack(fail_cid, False, 100.0, 0.0)
check("record_ack(): failure leaves the SAME command_id pending for retry",
      eb._pending_command is not None and eb._pending_command["command_id"] == fail_cid)
check("record_ack(): failure increments fail_count", eb._pending_command["fail_count"] == 1)

# Drive it to the retry cap
for _ in range(eb.MAX_COMMAND_RETRIES - 1):
    eb.record_ack(fail_cid, False, 100.0, 0.0)
check(f"record_ack(): after {eb.MAX_COMMAND_RETRIES} failures, command is dropped",
      eb._pending_command is None)
check("record_ack(): a never-filled OPEN also drops position tracking after max retries",
      eb._open_position is None)

# Same retry-cap scenario but for PARTIAL_CLOSE_MOVE_BE -- position tracking
# must NOT be dropped (a real position exists on the broker either way).
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
eb.record_fill(eb._open_position["signal_id"], "1", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # OPEN itself ACKed first, as in real flow
eb.check_tp1_and_queue_be(4410.0)
be_fail_cid = eb._pending_command["command_id"]
for _ in range(eb.MAX_COMMAND_RETRIES):
    eb.record_ack(be_fail_cid, False, 100.0, 0.0)
check("record_ack(): BE command dropped after max retries, but position tracking KEPT "
      "(real position still exists on the broker, needs a human)",
      eb._pending_command is None and eb._open_position is not None)


# ═══════════════════════════════════════════════════════════
# 6. check_tp1_and_queue_be()
# ═══════════════════════════════════════════════════════════

reset()
check("check_tp1_and_queue_be(): no-op when nothing tracked open",
      eb.check_tp1_and_queue_be(4500.0) is False)

eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
check("check_tp1_and_queue_be(): no-op while still unfilled (OPEN not ACKed/filled yet)",
      eb.check_tp1_and_queue_be(4415.0) is False)

eb.record_fill(eb._open_position["signal_id"], "1", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # OPEN itself ACKed first, as in real flow
check("check_tp1_and_queue_be(): no-op below TP1 for a BUY",
      eb.check_tp1_and_queue_be(4405.0) is False)
check("check_tp1_and_queue_be(): fires exactly at/above TP1 for a BUY",
      eb.check_tp1_and_queue_be(4410.0) is True
      and eb._pending_command["action"] == "PARTIAL_CLOSE_MOVE_BE"
      and eb._pending_command["new_sl"] == 4400.10)
check("check_tp1_and_queue_be(): does not fire twice for the same position",
      eb.check_tp1_and_queue_be(4412.0) is False)

# SELL direction
reset()
eb.queue_open_command(direction="SELL", entry=4400.0, sl=4410.0, tp_final=4380.0,
                       be_price=4399.90, partial=[{"pct": 50, "price": 4390.0, "reason": "x"}])
eb.record_fill(eb._open_position["signal_id"], "2", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # OPEN itself ACKed first, as in real flow
check("check_tp1_and_queue_be(): no-op above TP1 for a SELL",
      eb.check_tp1_and_queue_be(4395.0) is False)
check("check_tp1_and_queue_be(): fires at/below TP1 for a SELL",
      eb.check_tp1_and_queue_be(4390.0) is True and eb._pending_command["new_sl"] == 4399.90)

# Doesn't stomp an unrelated pending command (e.g. a CLOSE_ALL already queued)
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
eb.record_fill(eb._open_position["signal_id"], "3", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # clear the OPEN command
eb.queue_close_all(reason="admin override")
before = dict(eb._pending_command)
check("check_tp1_and_queue_be(): never overwrites an unrelated already-pending command",
      eb.check_tp1_and_queue_be(4410.0) is False and eb._pending_command == before)


# ═══════════════════════════════════════════════════════════
# 7. expire_stale_command() -- the wall-clock backstop for a dead EA
# ═══════════════════════════════════════════════════════════

reset()
check("expire_stale_command(): no-op when nothing pending",
      eb.expire_stale_command() is False)

# Fresh command (just queued) -- not stale yet
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[])
check("expire_stale_command(): a freshly queued command is not expired",
      eb.expire_stale_command() is False and eb._pending_command is not None)

# Force it stale by backdating queued_at past the threshold
eb._pending_command["queued_at"] = eb._pending_command["queued_at"] - eb.COMMAND_STALE_AFTER_SEC - 1
check("expire_stale_command(): stale OPEN (never filled) drops BOTH pending command "
      "and position tracking -- nothing real was ever opened on the broker",
      eb.expire_stale_command() is True and eb._pending_command is None
      and eb._open_position is None)

# Stale command, but the position WAS confirmed filled -> keep position tracking
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[{"pct": 50, "price": 4410.0, "reason": "x"}])
eb.record_fill(eb._open_position["signal_id"], "1", 4400.5)
eb.record_ack(eb._pending_command["command_id"], True)  # OPEN acked, position now live
eb.check_tp1_and_queue_be(4410.0)  # queues a PARTIAL_CLOSE_MOVE_BE
eb._pending_command["queued_at"] = eb._pending_command["queued_at"] - eb.COMMAND_STALE_AFTER_SEC - 1
check("expire_stale_command(): stale BE command drops the pending command but KEEPS "
      "position tracking -- a real filled position must not be forgotten",
      eb.expire_stale_command() is True and eb._pending_command is None
      and eb._open_position is not None)

# Once dropped, a fresh signal can queue again (this is the actual bug this fixes:
# 7 ก.ย. 2026 production incident -- two real signals were skipped for 3+ hours
# because a dead EA never ACKed the one stuck command)
reset()
eb.queue_open_command(direction="BUY", entry=4400.0, sl=4390.0, tp_final=4420.0,
                       be_price=4400.10, partial=[])
eb._pending_command["queued_at"] = eb._pending_command["queued_at"] - eb.COMMAND_STALE_AFTER_SEC - 1
eb.expire_stale_command()
check("expire_stale_command(): after expiry, a brand-new signal can queue again",
      eb.queue_open_command(direction="SELL", entry=4400.0, sl=4410.0, tp_final=4380.0,
                             be_price=4399.90, partial=[]) is True)


# ═══════════════════════════════════════════════════════════
# 8. Source guards
# ═══════════════════════════════════════════════════════════

import alpha_buffalo_signal as runtime

src = inspect.getsource(runtime.signal_loop)
check("signal_loop() calls check_tp1_and_queue_be() on every price tick",
      "check_tp1_and_queue_be(price)" in src)
check("signal_loop() calls expire_stale_command() every pass (the dead-EA backstop)",
      "expire_stale_command()" in src)
check("signal_loop() calls queue_open_command() right after the main XAUUSD Telegram alert",
      "queue_open_command(" in src)

app_src = inspect.getsource(runtime)
check("alpha_buffalo_signal.py exposes GET /execution/command",
      '@app.get("/execution/command")' in app_src)
check("alpha_buffalo_signal.py exposes POST /execution/fill",
      '@app.post("/execution/fill")' in app_src)
check("alpha_buffalo_signal.py exposes POST /execution/ack",
      '@app.post("/execution/ack")' in app_src)

cmd_src = inspect.getsource(runtime.handle_cmd)
check("/closeea admin command wired to execution_bridge.queue_close_all()",
      "queue_close_all(" in cmd_src and 'ADMIN_ID' in cmd_src)
check("/testopen admin command wired to execution_bridge.queue_open_command()",
      '"/testopen"' in cmd_src and "queue_open_command(" in cmd_src
      and 'ADMIN_ID' in cmd_src)


# ═══════════════════════════════════════════════════════════
# 9. /testopen manual smoke-test logic (arithmetic + queuing, no network)
# ═══════════════════════════════════════════════════════════
# Mirrors the SL/TP/be_price formulas inside the /testopen handler so the
# arithmetic and its handoff into queue_open_command() are covered without
# needing a live TwelveData call or a real Telegram send.

def _build_testopen_command(direction, price):
    if direction == "BUY":
        sl, tp1, tp_final, be_price = (round(price-2.0,2), round(price+1.0,2),
                                        round(price+3.0,2), round(price+0.10,2))
    else:
        sl, tp1, tp_final, be_price = (round(price+2.0,2), round(price-1.0,2),
                                        round(price-3.0,2), round(price-0.10,2))
    partial = [{"pct":50,"price":tp1,"reason":"test_tp1"},
               {"pct":50,"price":tp_final,"reason":"test_tp_final"}]
    return eb.queue_open_command(direction=direction, entry=price, sl=sl,
                                  tp_final=tp_final, be_price=be_price, partial=partial,
                                  reason="admin /testopen manual smoke test")

reset()
check("/testopen BUY at 4400.0 queues correctly",
      _build_testopen_command("BUY", 4400.0) is True)
check("/testopen BUY: SL is 2.0 below entry",
      eb._pending_command["sl"] == 4398.0)
check("/testopen BUY: TP1 is 1.0 above entry",
      eb._pending_command["tp1"] == 4401.0)
check("/testopen BUY: TP final is 3.0 above entry",
      eb._pending_command["tp_final"] == 4403.0)

reset()
check("/testopen SELL at 4400.0 queues correctly",
      _build_testopen_command("SELL", 4400.0) is True)
check("/testopen SELL: SL is 2.0 above entry",
      eb._pending_command["sl"] == 4402.0)
check("/testopen SELL: TP1 is 1.0 below entry",
      eb._pending_command["tp1"] == 4399.0)
check("/testopen SELL: TP final is 3.0 below entry",
      eb._pending_command["tp_final"] == 4397.0)

# ── Skips cleanly (same as a real signal) if a position is already open ──
reset()
_build_testopen_command("BUY", 4400.0)
check("/testopen: second call skipped while a position is still tracked open",
      _build_testopen_command("BUY", 4405.0) is False)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All execution_bridge regression checks passed.")
