"""
sweep_reentry.py — [OPT-IN via ALPHA_SIGNAL_SWEEP_REENTRY_ENABLED]

Round-2 re-entry after a Sweep Wick Entry (see signal_engine.py's
resolve_sweep_wick_entry() / ALPHA_SIGNAL_SWEEP_WICK_ENTRY).

Owner-observed XAUUSD behavior (2026-09-07): after the sweep-wick entry
fires, price often bounces back toward the M15 Bollinger Band middle
line, forms a Lower High (for SELL) / Higher Low (for BUY) on the way,
then reverses back in the original direction. That reversal is a second,
independently tradeable entry -- and once it's confirmed, it also proves
Trade 1's original thesis right, so Trade 1's SL should move to
breakeven (its own entry price) instead of sitting at its original wide
sweep-buffer SL.

This module tracks that pending pattern ACROSS POLLING CYCLES (the same
architecture as scenario_scanner.ScenarioScanner's active_zones list --
a module-level singleton holding state signal_engine.compute_signal()
itself can't hold, since compute_signal() only ever sees one snapshot
per call).

Confirmation rule (per owner, 2026-09-07): a bar must CLOSE beyond the
low/high of the bar that formed the Lower High/Higher Low -- not just
touch a level -- so this never fires on a naked wick/band-touch alone.

Round-2 SL rule (per owner, 2026-09-07): whichever of (the Lower High/
Higher Low itself + a small buffer) or (the original swing's 61.8% level)
is SAFER/further from price -- "กันเหนียวทั้งสองทาง" (hedge both ways).

Round-2 TP: carries Trade 1's own final TP target forward unchanged --
this is the same directional thesis, not a new target.
"""
import os
from dataclasses import dataclass
from typing import Optional

PIVOT_N        = int(os.getenv("ALPHA_SWEEP_REENTRY_PIVOT_N", "2"))
MAX_WATCH_BARS = int(os.getenv("ALPHA_SWEEP_REENTRY_EXPIRY_BARS", "24"))
SL_BUFFER      = 0.30


def sweep_reentry_enabled() -> bool:
    return os.getenv("ALPHA_SIGNAL_SWEEP_REENTRY_ENABLED", "false").lower() in {
        "1", "true", "yes", "on",
    }


def fib_618_reference(direction: str, swing_high: float, swing_low: float) -> Optional[float]:
    """
    The 61.8% retracement price of the original swing this setup was
    based on -- SELL measures down from swing_high, BUY up from
    swing_low, matching the convention already established in
    signal_engine.resolve_fibo_golden_number(). Returns None when the
    swing wasn't available (e.g. harmonic-PRZ-based setups, or a
    degenerate/inverted swing) -- callers fall back to structure-only SL.
    """
    if not swing_high or not swing_low:
        return None
    rng = swing_high - swing_low
    if rng <= 0:
        return None
    if direction == "SELL":
        return round(swing_high - rng * 0.618, 2)
    return round(swing_low + rng * 0.618, 2)


def resolve_reentry_sl(direction: str, pivot_break_level: float,
                        fib_ref_price: Optional[float], buffer: float = SL_BUFFER) -> float:
    """
    [per owner] Take whichever of (Lower High/Higher Low structure + buffer)
    or (the swing's 61.8% level) is the SAFER/wider one -- hedges both
    ways instead of picking a single reference. Falls back to
    structure-only when no fib reference is available.
    """
    structure_sl = (pivot_break_level + buffer) if direction == "SELL" else (pivot_break_level - buffer)
    if fib_ref_price is None:
        return round(structure_sl, 2)
    if direction == "SELL":
        return round(max(structure_sl, fib_ref_price), 2)
    return round(min(structure_sl, fib_ref_price), 2)


def find_pivot_since(df, direction: str, since_idx: int, n: int = PIVOT_N):
    """
    Find the most recent confirmed local extreme since `since_idx` among
    CLOSED bars only (mirrors scenario_scanner._find_swings's df.iloc[:-1]
    convention -- the still-forming last bar is never used to confirm a
    pivot): a Lower High for SELL (a high greater than the `n` bars on
    each side of it), a Higher Low for BUY (a low lower than the `n` bars
    on each side).

    Returns (pivot_idx, pivot_price, break_level) for the LATEST such
    pivot found, or (None, None, None) if none is confirmed yet.
    break_level is the level a later bar must CLOSE beyond to confirm
    Round 2: the pivot bar's own LOW for SELL (BUY: its own HIGH).
    """
    safe = df.iloc[:-1]
    lo = max(since_idx, n)
    hi = len(safe) - n
    best = None
    for i in range(lo, hi):
        if direction == "SELL":
            target = safe["high"].iloc[i]
            cond = (all(target > safe["high"].iloc[i - j] for j in range(1, n + 1)) and
                    all(target > safe["high"].iloc[i + j] for j in range(1, n + 1)))
        else:
            target = safe["low"].iloc[i]
            cond = (all(target < safe["low"].iloc[i - j] for j in range(1, n + 1)) and
                    all(target < safe["low"].iloc[i + j] for j in range(1, n + 1)))
        if cond:
            best = i  # keep scanning -- we want the LATEST confirmed pivot
    if best is None:
        return None, None, None
    if direction == "SELL":
        pivot_price = float(safe["high"].iloc[best])
        break_level = float(safe["low"].iloc[best])
    else:
        pivot_price = float(safe["low"].iloc[best])
        break_level = float(safe["high"].iloc[best])
    return best, pivot_price, break_level


@dataclass
class PendingReentry:
    direction:      str
    trade1_entry:   float
    trade1_tp:      float
    fib_ref_price:  Optional[float]
    formed_bar:     int


class SweepReentryWatcher:
    """
    Module-level singleton (see `sweep_reentry_watcher` below) -- holds
    pending Round-2 watches across polling cycles, the same architecture
    as scenario_scanner.ScenarioScanner.active_zones.
    """

    def __init__(self):
        self.pending: list[PendingReentry] = []

    def register(self, direction: str, trade1_entry: float, trade1_tp: float,
                 fib_ref_price: Optional[float], formed_bar: int):
        self.pending.append(PendingReentry(
            direction=direction, trade1_entry=trade1_entry, trade1_tp=trade1_tp,
            fib_ref_price=fib_ref_price, formed_bar=formed_bar,
        ))

    def check(self, df_15m) -> list:
        """
        Call once per poll with the latest M15 OHLCV. Returns a list of
        dicts for any Round-2 re-entries that confirmed THIS pass:
        {direction, entry, sl, tp, trade1_entry} -- trade1_entry tells the
        caller which open Trade 1 to move to breakeven. Expired or
        invalidated watches are dropped silently (no signal fires).
        One-shot per watch: a fired or dropped watch is never re-added.
        """
        if df_15m is None or len(df_15m) < (2 * PIVOT_N + 3):
            return []
        current_bar = len(df_15m)
        last_close = float(df_15m["close"].iloc[-2])  # last CLOSED bar
        fired: list = []
        still_pending: list[PendingReentry] = []

        for w in self.pending:
            if current_bar - w.formed_bar > MAX_WATCH_BARS:
                continue  # expired -- drop, no signal

            # Invalidation: price already closed back past the original
            # sweep-wick entry in the WRONG direction -- the whole thesis
            # (and Trade 1 itself) is dead, no Round 2 to watch for.
            if w.direction == "SELL" and last_close > w.trade1_entry:
                continue
            if w.direction == "BUY" and last_close < w.trade1_entry:
                continue

            pivot_idx, _pivot_price, break_level = find_pivot_since(df_15m, w.direction, w.formed_bar)
            if pivot_idx is None:
                still_pending.append(w)
                continue

            confirmed = False
            safe = df_15m.iloc[:-1]
            for j in range(pivot_idx + 1, len(safe)):
                c = float(safe["close"].iloc[j])
                if (w.direction == "SELL" and c < break_level) or \
                   (w.direction == "BUY" and c > break_level):
                    confirmed = True
                    break

            if not confirmed:
                still_pending.append(w)
                continue

            sl = resolve_reentry_sl(w.direction, break_level, w.fib_ref_price)
            fired.append({
                "direction":    w.direction,
                "entry":        round(break_level, 2),
                "sl":           sl,
                "tp":           w.trade1_tp,
                "trade1_entry": w.trade1_entry,
            })
            # confirmed and reported -- one-shot, do not re-add

        self.pending = still_pending
        return fired


sweep_reentry_watcher = SweepReentryWatcher()
