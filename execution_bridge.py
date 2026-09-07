"""
execution_bridge.py — Alpha Buffalo v5 (7 ก.ย. 2026)
========================================================
Bridges the XAUUSD signal pipeline (the main SYMBOL path in
alpha_buffalo_signal.py's signal_loop()) to a purely execution-only MT5 EA:

    EA polls  GET  /execution/command  -> {"command": {...}} or HOLD
    EA posts  POST /execution/fill     -> reports ticket + fill price after OPEN
    EA posts  POST /execution/ack      -> reports success/failure of any command

Owner's explicit design (7 ก.ย. 2026): the EA must never decide anything --
direction / SL / TP / lot size all come from here. This module is the only
place that decides WHAT to tell the EA to do; the EA's only job is to
execute it and report back what actually happened. This is a different,
simpler contract than the older /signal/latest + /signal/confirm endpoints
(which a Railway HTTP-log audit showed no EA has ever actually called) --
this module and its two endpoints are the real, currently-used bridge.

Single account, single symbol (XAUUSD) -- matches the rest of this
project's architecture (one production EA, one MT5 account, one open
position at a time -- AllowMultiple=false on the EA side). State is
in-memory only: if the process restarts mid-trade, no pending command
survives, but nothing is force-closed either -- a real open position on
the broker is untouched, and the EA's own FindMagicPositionTicket()
recovery path is the safety net for reconciling with the account after a
restart, not this module.

Fail-safe by the same standard as signal_log.py / outcome_tracker.py:
nothing in here may ever raise back into signal_loop() or the FastAPI
handlers in alpha_buffalo_signal.py -- every public function catches its
own exceptions and returns a safe default.
"""
import os
import time
import uuid

# ── Position sizing ──────────────────────────────────────────────
# Risk % of the CURRENT TRADING DAY's starting equity -- the same
# Bangkok-day convention the EA already keeps locally for its daily-profit
# lock (DayStartEquity() in the EA). Owner's explicit choice (7 ก.ย. 2026):
# lot size is fixed for the whole trading day and only recalculated once a
# new day starts, so a win/loss mid-day never changes the size of the next
# trade that same day. The EA sends day_start_equity on every poll; this
# module never asks MT5 for it directly.
RISK_PCT_PER_TRADE   = float(os.getenv("ALPHA_EXEC_RISK_PCT", "2.0"))
XAUUSD_CONTRACT_SIZE = float(os.getenv("ALPHA_XAUUSD_CONTRACT_SIZE", "100"))  # oz per lot
MIN_LOT              = float(os.getenv("ALPHA_EXEC_MIN_LOT", "0.01"))
MAX_LOT              = float(os.getenv("ALPHA_EXEC_MAX_LOT", "5.0"))  # hard safety ceiling
MAX_COMMAND_RETRIES  = int(os.getenv("ALPHA_EXEC_MAX_RETRIES", "20"))

# Wall-clock backstop (2026-09-07): MAX_COMMAND_RETRIES only counts
# consecutive ACKed failures -- if the EA stops polling entirely (MT5
# closed, computer asleep, network dropped, WebRequest blocked) no ACK
# ever arrives, so a command can otherwise sit "pending" forever and
# silently block every future signal (queue_open_command() refuses to
# queue a new OPEN while _open_position is still tracked). This observed
# in production 7 ก.ย. 2026: the EA polled successfully for ~1 minute,
# then stopped -- two real XAUUSD signals over the next 3+ hours were
# skipped because the first command never got resolved either way.
COMMAND_STALE_AFTER_SEC = int(os.getenv("ALPHA_EXEC_STALE_AFTER_SEC", "900"))  # 15 min


def compute_lot(day_start_equity: float, sl_distance: float) -> float:
    """Risk-% position sizing: the lot such that a full SL hit loses
    RISK_PCT_PER_TRADE% of the trading day's starting equity. Never
    raises and never guesses upward on bad input -- falls back to
    MIN_LOT if equity or SL distance is missing/invalid."""
    try:
        if day_start_equity is None or sl_distance is None:
            return MIN_LOT
        day_start_equity = float(day_start_equity)
        sl_distance = float(sl_distance)
        if day_start_equity <= 0 or sl_distance <= 0:
            return MIN_LOT
        risk_usd = day_start_equity * (RISK_PCT_PER_TRADE / 100.0)
        lot = risk_usd / (sl_distance * XAUUSD_CONTRACT_SIZE)
        lot = round(lot, 2)
        return max(MIN_LOT, min(MAX_LOT, lot))
    except Exception:
        return MIN_LOT


# ── State (single account / single symbol) ──────────────────────────
_pending_command = None   # dict or None -- the one command the EA hasn't ACKed yet
_open_position   = None   # dict or None -- what we believe is live on the broker


def _new_command_id() -> str:
    return uuid.uuid4().hex[:12]


def queue_open_command(direction, entry, sl, tp_final, be_price, partial, reason=""):
    """Call once, right after a real Stage-3 XAUUSD signal has actually
    been sent to Telegram (the only place ea_executes=True is guaranteed).
    Never overwrites a position already tracked as open -- mirrors the
    EA's own one-position-at-a-time design (AllowMultiple=false)."""
    global _pending_command, _open_position
    try:
        if _open_position is not None:
            print(f"⚠️ execution_bridge: OPEN skipped, position already tracked open "
                  f"(signal_id={_open_position.get('signal_id')})")
            return False
        if direction not in ("BUY", "SELL"):
            return False
        if sl is None or tp_final is None or entry is None:
            return False
        tp1 = partial[0]["price"] if partial else tp_final
        close_pct = partial[0]["pct"] if partial else 50.0
        signal_id = uuid.uuid4().hex[:8]
        _pending_command = {
            "command_id": _new_command_id(), "action": "OPEN",
            "reason": reason or "signal", "symbol": "XAUUSD",
            "signal_id": signal_id, "direction": direction,
            "entry": entry, "sl": sl, "tp1": tp1, "tp_final": tp_final,
            "queued_at": time.time(),
        }
        _open_position = {
            "signal_id": signal_id, "direction": direction, "entry": entry,
            "sl": sl, "tp_final": tp_final, "tp1": tp1, "close_pct": close_pct,
            "be_price": be_price, "filled": False, "ticket": None,
            "fill_price": None, "be_issued": False, "be_done": False,
        }
        print(f"📤 execution_bridge: queued OPEN {direction} entry={entry} sl={sl} "
              f"tp1={tp1} tp_final={tp_final} signal_id={signal_id}")
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge queue_open_command error: {e}")
        return False


def queue_close_all(reason="manual"):
    """Manual kill-switch -- e.g. the /closeea admin Telegram command.
    Overrides whatever command is currently pending: closing takes
    priority over an unacked OPEN or BE move."""
    global _pending_command
    try:
        _pending_command = {
            "command_id": _new_command_id(), "action": "CLOSE_ALL",
            "reason": reason, "symbol": "XAUUSD", "queued_at": time.time(),
        }
        print(f"📤 execution_bridge: queued CLOSE_ALL ({reason})")
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge queue_close_all error: {e}")
        return False


def check_tp1_and_queue_be(current_price: float) -> bool:
    """Call every signal_loop() pass (cheap -- in-memory only, no network):
    if a position is tracked as open, filled, and price has now reached
    TP1 in the trade's favor, and the move-to-breakeven command hasn't
    been issued yet, queue it. Only ever acts on the position THIS module
    is tracking -- never infers from price alone that a position exists."""
    global _pending_command
    try:
        pos = _open_position
        if pos is None or not pos.get("filled") or pos.get("be_issued"):
            return False
        if current_price is None:
            return False
        current_price = float(current_price)
        hit = (pos["direction"] == "BUY" and current_price >= pos["tp1"]) or \
              (pos["direction"] == "SELL" and current_price <= pos["tp1"])
        if not hit:
            return False
        if _pending_command is not None:
            # Don't stomp an unrelated pending command (e.g. a just-queued
            # CLOSE_ALL, or the OPEN itself not yet ACKed).
            return False
        _pending_command = {
            "command_id": _new_command_id(), "action": "PARTIAL_CLOSE_MOVE_BE",
            "reason": "TP1 hit", "symbol": "XAUUSD",
            "close_pct": pos["close_pct"], "new_sl": pos["be_price"],
            "queued_at": time.time(),
        }
        pos["be_issued"] = True
        print(f"📤 execution_bridge: TP1 hit @ {current_price} -> queued "
              f"PARTIAL_CLOSE_MOVE_BE (close_pct={pos['close_pct']}, new_sl={pos['be_price']})")
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge check_tp1_and_queue_be error: {e}")
        return False


def expire_stale_command() -> bool:
    """Call every signal_loop() pass (cheap -- in-memory only, no network).
    MAX_COMMAND_RETRIES only counts consecutive ACKed failures -- if the EA
    stops polling entirely (MT5 closed, computer asleep, network dropped,
    WebRequest blocked) no ACK ever arrives, so a command would otherwise
    sit "pending" forever, silently blocking every future signal
    (queue_open_command() refuses to queue a new OPEN while _open_position
    is still tracked). This is the wall-clock backstop: after
    COMMAND_STALE_AFTER_SEC with no ACK either way, drop the command.

    Position tracking is dropped too ONLY if nothing was ever confirmed
    filled on the broker -- nothing real exists to lose track of in that
    case. A position already confirmed filled stays tracked (so TP1/BE
    monitoring keeps working once the EA reconnects); the EA's own
    existing-position recovery (FindMagicPositionTicket) is the backstop
    against ever double-opening if backend and broker state disagree."""
    global _pending_command, _open_position
    try:
        cmd = _pending_command
        if cmd is None:
            return False
        age = time.time() - cmd.get("queued_at", time.time())
        if age < COMMAND_STALE_AFTER_SEC:
            return False
        print(f"🛑 execution_bridge: command_id={cmd.get('command_id')} "
              f"action={cmd.get('action')} stale for {int(age)}s with no ACK "
              f"-- EA likely disconnected, dropping so new signals aren't blocked")
        _pending_command = None
        if _open_position is not None and not _open_position.get("filled"):
            _open_position = None
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge expire_stale_command error: {e}")
        return False


def get_command(day_start_equity: float = 0.0) -> dict:
    """What GET /execution/command should return under the "command" key.
    For a pending OPEN, computes the lot fresh from day_start_equity (the
    EA sends its own account snapshot on every poll) -- the EA never
    computes or decides its own lot size."""
    try:
        cmd = _pending_command
        if cmd is None:
            return {"action": "HOLD", "reason": "no pending command"}
        out = {k: v for k, v in cmd.items() if k not in ("fail_count", "queued_at")}
        if cmd["action"] == "OPEN":
            sl_distance = abs(float(cmd["entry"]) - float(cmd["sl"]))
            out["lot"] = compute_lot(day_start_equity, sl_distance)
        return out
    except Exception as e:
        print(f"⚠️ execution_bridge get_command error: {e}")
        return {"action": "HOLD", "reason": "error"}


def record_fill(signal_id: str, ticket, fill_price) -> bool:
    """POST /execution/fill -- the EA reporting a real ticket + fill price
    right after it successfully opened the position."""
    global _open_position
    try:
        pos = _open_position
        if pos is None or pos.get("signal_id") != signal_id:
            print(f"⚠️ execution_bridge: fill for unknown/mismatched signal_id={signal_id}")
            return False
        pos["filled"] = True
        pos["ticket"] = ticket
        pos["fill_price"] = fill_price
        print(f"✅ execution_bridge: OPEN filled | signal_id={signal_id} "
              f"ticket={ticket} price={fill_price}")
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge record_fill error: {e}")
        return False


def record_ack(command_id: str, success: bool, remaining_pct: float = 100.0,
                r_multiple: float = 0.0) -> bool:
    """POST /execution/ack -- the EA reporting whether the command it was
    just given actually succeeded. On failure the command is LEFT pending
    (same command_id) so the EA's next poll naturally retries it -- this
    is what lets a transient issue (e.g. spread too wide) resolve itself.
    After MAX_COMMAND_RETRIES consecutive failures the command is dropped
    (an OPEN that never filled also drops its position tracking, since
    nothing was ever actually opened on the broker in that case; a
    PARTIAL_CLOSE_MOVE_BE or CLOSE_ALL that keeps failing leaves position
    tracking alone, since a real position still exists on the broker and
    needs a human to look at it)."""
    global _pending_command, _open_position
    try:
        cmd = _pending_command
        if cmd is None or cmd.get("command_id") != command_id:
            print(f"⚠️ execution_bridge: ACK for unknown/stale command_id={command_id}")
            return False
        action = cmd["action"]
        if not success:
            cmd["fail_count"] = cmd.get("fail_count", 0) + 1
            if cmd["fail_count"] >= MAX_COMMAND_RETRIES:
                print(f"🛑 execution_bridge: {action} failed {cmd['fail_count']}x in a row "
                      f"-- dropping command_id={command_id}, needs manual review")
                _pending_command = None
                if action == "OPEN":
                    _open_position = None
            else:
                print(f"⚠️ execution_bridge: EA reported FAILURE #{cmd['fail_count']} "
                      f"for {action} (command_id={command_id}) -- left pending, will retry")
            return True
        # success:
        if action == "OPEN":
            _pending_command = None
        elif action == "PARTIAL_CLOSE_MOVE_BE":
            if _open_position is not None:
                _open_position["be_done"] = True
            _pending_command = None
        elif action == "CLOSE_ALL":
            _pending_command = None
            _open_position = None
        else:
            _pending_command = None
        print(f"✅ execution_bridge: ACK success | action={action} command_id={command_id} "
              f"remaining_pct={remaining_pct} r_multiple={r_multiple}")
        return True
    except Exception as e:
        print(f"⚠️ execution_bridge record_ack error: {e}")
        return False


def status() -> dict:
    """Read-only snapshot for /health-style debugging (e.g. a future /status
    Telegram command) -- never raises."""
    try:
        return {"pending_command": _pending_command, "open_position": _open_position}
    except Exception:
        return {"pending_command": None, "open_position": None}
