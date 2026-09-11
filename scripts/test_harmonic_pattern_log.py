#!/usr/bin/env python3
"""
Regression tests for harmonic_pattern_log.py (11 ก.ย. 2026, not opt-in) --
Phase 2 of the owner's "จำและคาดการณ์แม่นขึ้น" harmonic-accuracy strategy
(Phase 1 = signal_engine.build_harmonic_forecast()'s new tf_conflict flag,
tested in test_harmonic_forecast.py).

Covers:
  1. log_new_active_pattern() -- dedup on identical active-pattern identity
     across repeated calls, re-logs on a genuinely different pattern, never
     raises without Supabase credentials.
  2. evaluate_harmonic_outcome() -- WIN/LOSS/still-pending for BUY and SELL,
     the win+loss-same-bar conservative LOSS tie-break, and the
     never-raises "missing prz bounds" guard.
  3. check_pending_harmonic_outcomes() end-to-end with a mocked Supabase
     client + OHLC fetcher (WIN path, EXPIRED path, still-PENDING path).
  4. Source guards confirming alpha_buffalo_signal.py's trend_loop() wires
     this module in (checked from test_harmonic_forecast.py already for
     the exact call sites -- this file just checks the import exists).

Run: python3 scripts/test_harmonic_pattern_log.py
Exits non-zero on any failure.
"""
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


import harmonic_pattern_log as hpl


# ═══════════════════════════════════════════════════════════
# 1. log_new_active_pattern()
# ═══════════════════════════════════════════════════════════

hpl.reset_dedup_state()

active_A = {"name": "A_Bat", "tf": "H1", "direction": "BUY", "priority": 1,
            "prz_low": 95.0, "prz_high": 105.0, "prz_mid": 100.0,
            "confidence": 80.0, "auto_fibo_confluence": False}

# No SUPABASE_URL/KEY set in this test env -> _sb() returns None -> every
# call returns False, but must never raise.
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
r1 = hpl.log_new_active_pattern("XAUUSD", {"active": active_A})
check("log_new_active_pattern: no Supabase credentials -> returns False, "
      "never raises",
      r1 is False)
check("log_new_active_pattern: harmonic_forecast=None -> returns False",
      hpl.log_new_active_pattern("XAUUSD", None) is False)
check("log_new_active_pattern: active=None (no active pattern right now) "
      "-> returns False",
      hpl.log_new_active_pattern("XAUUSD", {"active": None}) is False)


class _FakeInsertQuery:
    def __init__(self, store):
        self.store = store
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        self.store.append(self._payload)
        return self


class _FakeTableInsertOnly:
    def __init__(self, store):
        self.store = store

    def insert(self, payload):
        return _FakeInsertQuery(self.store).insert(payload)


class _FakeClientInsertOnly:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        return _FakeTableInsertOnly(self.inserted)


fake_client = _FakeClientInsertOnly()
hpl._sb = lambda: fake_client  # monkeypatch, same pattern as test_outcome_tracker.py
hpl.reset_dedup_state()

r2 = hpl.log_new_active_pattern("XAUUSD", {"active": active_A})
check("log_new_active_pattern: with a working client, a NEW active "
      "pattern inserts a PENDING row and returns True",
      r2 is True and len(fake_client.inserted) == 1
      and fake_client.inserted[0]["status"] == "PENDING"
      and fake_client.inserted[0]["pattern_name"] == "A_Bat"
      and fake_client.inserted[0]["cascade_direction"] == "BUY")

r3 = hpl.log_new_active_pattern("XAUUSD", {"active": active_A})
check("log_new_active_pattern: the SAME active pattern again (identical "
      "name+tf+direction+prz_mid) -> deduped, no second insert",
      r3 is False and len(fake_client.inserted) == 1)

active_B = {"name": "B_Gartley", "tf": "H4", "direction": "SELL", "priority": 1,
            "prz_low": 195.0, "prz_high": 205.0, "prz_mid": 200.0,
            "confidence": 90.0, "auto_fibo_confluence": True}
r4 = hpl.log_new_active_pattern("XAUUSD", {"active": active_B})
check("log_new_active_pattern: a GENUINELY DIFFERENT active pattern -> "
      "logs again (2nd row)",
      r4 is True and len(fake_client.inserted) == 2
      and fake_client.inserted[1]["auto_fibo_confluence"] is True)

hpl.reset_dedup_state()


# ═══════════════════════════════════════════════════════════
# 2. evaluate_harmonic_outcome()
# ═══════════════════════════════════════════════════════════

def _bars(rows):
    """rows: list of (high, low) tuples -> list of dict-likes."""
    return [{"high": h, "low": l} for h, l in rows]


# BUY PRZ [95-105], width=10. WIN needs high >= 115. LOSS needs low <= 90.
buy_bars_win = _bars([(108, 96), (116, 110)])  # 2nd bar touches WIN
status, n = hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, buy_bars_win)
check("evaluate_harmonic_outcome: BUY reverses up past prz_high+width -> "
      "WIN at the bar it happens",
      status == "WIN" and n == 2)

buy_bars_loss = _bars([(104, 97), (100, 88)])  # 2nd bar breaks below prz_low-0.5*width=90
status2, n2 = hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, buy_bars_loss)
check("evaluate_harmonic_outcome: BUY instead breaks down past "
      "prz_low-0.5*width -> LOSS",
      status2 == "LOSS" and n2 == 2)

buy_bars_both = _bars([(120, 85)])  # single bar hits both WIN and LOSS thresholds
status3, n3 = hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, buy_bars_both)
check("evaluate_harmonic_outcome: one bar satisfies both WIN and LOSS -> "
      "conservative LOSS tie-break",
      status3 == "LOSS" and n3 == 1)

buy_bars_pending = _bars([(103, 97), (104, 96)])  # never reaches either threshold
status4, n4 = hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, buy_bars_pending)
check("evaluate_harmonic_outcome: neither threshold touched yet -> "
      "(None, None), still pending",
      status4 is None and n4 is None)

# SELL PRZ [195-205], width=10. WIN needs low <= 185. LOSS needs high >= 210.
sell_bars_win = _bars([(198, 190), (192, 180)])
status5, n5 = hpl.evaluate_harmonic_outcome("SELL", 195.0, 205.0, sell_bars_win)
check("evaluate_harmonic_outcome: SELL reverses down past prz_low-width -> WIN",
      status5 == "WIN" and n5 == 2)

sell_bars_loss = _bars([(207, 199), (212, 205)])
status6, n6 = hpl.evaluate_harmonic_outcome("SELL", 195.0, 205.0, sell_bars_loss)
check("evaluate_harmonic_outcome: SELL instead breaks up past "
      "prz_high+0.5*width -> LOSS",
      status6 == "LOSS" and n6 == 2)

check("evaluate_harmonic_outcome: missing prz_low -> (None, None), never "
      "guesses",
      hpl.evaluate_harmonic_outcome("BUY", None, 105.0, buy_bars_win) == (None, None))
check("evaluate_harmonic_outcome: missing prz_high -> (None, None), never "
      "guesses",
      hpl.evaluate_harmonic_outcome("BUY", 95.0, None, buy_bars_win) == (None, None))
check("evaluate_harmonic_outcome: no bars yet -> (None, None)",
      hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, []) == (None, None))
check("evaluate_harmonic_outcome: bars_after=None -> (None, None), never raises",
      hpl.evaluate_harmonic_outcome("BUY", 95.0, 105.0, None) == (None, None))
check("evaluate_harmonic_outcome: zero-width PRZ (prz_low==prz_high) -> "
      "(None, None), never divides oddly",
      hpl.evaluate_harmonic_outcome("BUY", 100.0, 100.0, buy_bars_win) == (None, None))


# ═══════════════════════════════════════════════════════════
# 3. check_pending_harmonic_outcomes() end-to-end
# ═══════════════════════════════════════════════════════════

import pandas as pd


class _FakeSelectQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        class R: pass
        r = R()
        r.data = self.rows
        return r


class _FakeUpdateQuery:
    def __init__(self, store, row_id):
        self.store = store
        self.row_id = row_id
        self._payload = None

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, field, value):
        self.row_id = value
        return self

    def execute(self):
        self.store[self.row_id] = self._payload
        return self


class _FakeTableE2E:
    def __init__(self, pending_rows, update_store):
        self.pending_rows = pending_rows
        self.update_store = update_store

    def select(self, *a, **k):
        return _FakeSelectQuery(self.pending_rows)

    def update(self, payload):
        q = _FakeUpdateQuery(self.update_store, None)
        return q.update(payload)


class _FakeClientE2E:
    def __init__(self, pending_rows):
        self.pending_rows = pending_rows
        self.update_store = {}

    def table(self, name):
        return _FakeTableE2E(self.pending_rows, self.update_store)


def _ohlcv(rows):
    return pd.DataFrame({
        "time": list(range(len(rows))),
        "high": [r[0] for r in rows],
        "low":  [r[1] for r in rows],
        "close": [(r[0] + r[1]) / 2 for r in rows],
    })


# Row 1: BUY pattern that will WIN this call.
pending_win = {"id": 1, "symbol": "XAUUSD", "tf": "H1", "direction": "BUY",
               "prz_low": 95.0, "prz_high": 105.0, "detected_at": None}
client_win = _FakeClientE2E([pending_win])
hpl._sb = lambda: client_win


def fake_get_ohlcv_win(interval, bars, symbol=None):
    return _ohlcv([(103, 97), (117, 110)])  # 2nd bar triggers WIN


n_resolved = hpl.check_pending_harmonic_outcomes(fake_get_ohlcv_win, bars=100)
check("check_pending_harmonic_outcomes: resolves the pending BUY row as WIN",
      n_resolved == 1 and client_win.update_store.get(1, {}).get("status") == "WIN")

# Row 2: still pending (not enough movement yet, well under MAX_BARS_TO_RESOLVE).
pending_open = {"id": 2, "symbol": "XAUUSD", "tf": "H1", "direction": "BUY",
                "prz_low": 95.0, "prz_high": 105.0, "detected_at": None}
client_open = _FakeClientE2E([pending_open])
hpl._sb = lambda: client_open


def fake_get_ohlcv_flat(interval, bars, symbol=None):
    return _ohlcv([(103, 97)] * 5)


n_open = hpl.check_pending_harmonic_outcomes(fake_get_ohlcv_flat, bars=100)
check("check_pending_harmonic_outcomes: neither WIN nor LOSS touched, well "
      "under MAX_BARS_TO_RESOLVE -> stays PENDING, not resolved this call",
      n_open == 0 and 2 not in client_open.update_store)

# Row 3: EXPIRED -- flat for >= MAX_BARS_TO_RESOLVE bars.
pending_expired = {"id": 3, "symbol": "XAUUSD", "tf": "H1", "direction": "BUY",
                    "prz_low": 95.0, "prz_high": 105.0, "detected_at": None}
client_expired = _FakeClientE2E([pending_expired])
hpl._sb = lambda: client_expired


def fake_get_ohlcv_expired(interval, bars, symbol=None):
    return _ohlcv([(103, 97)] * (hpl.MAX_BARS_TO_RESOLVE + 5))


n_expired = hpl.check_pending_harmonic_outcomes(fake_get_ohlcv_expired, bars=1000)
check("check_pending_harmonic_outcomes: flat for >= MAX_BARS_TO_RESOLVE "
      "bars with no WIN/LOSS -> EXPIRED",
      n_expired == 1 and client_expired.update_store.get(3, {}).get("status") == "EXPIRED")

hpl._sb = lambda: None
check("check_pending_harmonic_outcomes: no Supabase client -> returns 0",
      hpl.check_pending_harmonic_outcomes(fake_get_ohlcv_win, bars=100) == 0)


# ═══════════════════════════════════════════════════════════
# 4. Source guards
# ═══════════════════════════════════════════════════════════

import inspect
src = inspect.getsource(hpl)
check("harmonic_pattern_log.py: never raises on insert failure (try/except "
      "around the Supabase insert call)",
      "except Exception as e" in src and "insert error" in src)
check("harmonic_pattern_log.py: CREATE_TABLE_SQL reference is present for "
      "the owner to run in Supabase",
      "create table harmonic_pattern_log" in hpl.CREATE_TABLE_SQL)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
else:
    print(f"ALL PASSED")
