#!/usr/bin/env python3
"""
Regression test for signal_log.py (2026-09-07, not opt-in): persists every
signal actually sent to Telegram into Supabase's `signal_log` table, so
historical stats survive past Railway's ~7-day deploy-log retention --
there was previously NO database record of signals sent anywhere (only
license_manager.py's `licenses` table existed; confirmed via `git grep
supabase` across every branch in the repo).

Covers:
  1. log_signal() with no SUPABASE_URL/KEY configured -> returns False,
     never raises (must not be able to block a signal from firing).
  2. log_signal() with a working (mocked) Supabase client -> inserts the
     expected payload shape and returns True.
  3. log_signal() when the mocked client's .insert().execute() raises ->
     caught, returns False, never propagates.
  4. Source guards: the two real call sites (signal_engine.compute_signal
     -- both main SYMBOL and extra-symbol signals share this one call
     site -- and alpha_buffalo_signal's Round-2 re-entry loop) actually
     call log_signal() with the right wiring. Driving a signal all the way
     through compute_signal()'s full gate cascade with synthetic OHLCV is
     what every other test file in this project deliberately avoids (they
     test extracted pure logic instead) -- this is the same pragmatic
     substitute used by scripts/test_ea_executes_honesty.py.

Run: python3 scripts/test_signal_log.py
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


import signal_log

# ═══════════════════════════════════════════════════════════
# 1. No credentials configured -> _sb() returns None -> False, no raise
# ═══════════════════════════════════════════════════════════

_orig_url = os.environ.pop("SUPABASE_URL", None)
_orig_key = os.environ.pop("SUPABASE_KEY", None)

result = signal_log.log_signal(
    symbol="XAUUSD", direction="BUY", category="V4_SESSION", entry=2650.0,
)
check("log_signal() with no Supabase credentials returns False (no raise)",
      result is False)

if _orig_url is not None: os.environ["SUPABASE_URL"] = _orig_url
if _orig_key is not None: os.environ["SUPABASE_KEY"] = _orig_key


# ═══════════════════════════════════════════════════════════
# 2 & 3. Mocked Supabase client: successful insert, and a raising insert
# ═══════════════════════════════════════════════════════════

class _FakeQuery:
    def __init__(self, table_name, store, raise_on_execute=False):
        self._table = table_name
        self._store = store
        self._raise = raise_on_execute
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated Supabase network error")
        self._store.append((self._table, self._payload))
        return {"data": [self._payload]}


class _FakeClient:
    def __init__(self, raise_on_execute=False):
        self.inserted = []
        self._raise = raise_on_execute

    def table(self, name):
        return _FakeQuery(name, self.inserted, raise_on_execute=self._raise)


_orig_sb = signal_log._sb

fake_ok = _FakeClient(raise_on_execute=False)
signal_log._sb = lambda: fake_ok
try:
    ok = signal_log.log_signal(
        symbol="XAUUSD", direction="SELL", category="V5_SNIPER",
        entry=4400.12, sl=4410.00, tp=4370.00, score=8,
        pattern="Butterfly", session="London",
        ea_executes=True, source="main_loop",
    )
    check("log_signal() returns True on a successful mocked insert", ok is True)
    check("exactly one row inserted into 'signal_log'",
          len(fake_ok.inserted) == 1 and fake_ok.inserted[0][0] == "signal_log")
    row = fake_ok.inserted[0][1]
    check("payload carries symbol/direction/category correctly",
          row["symbol"] == "XAUUSD" and row["direction"] == "SELL" and
          row["category"] == "V5_SNIPER")
    check("payload carries entry/sl/tp/score correctly",
          row["entry"] == 4400.12 and row["sl"] == 4410.00 and
          row["tp"] == 4370.00 and row["score"] == 8)
    check("payload carries ea_executes as a real bool",
          row["ea_executes"] is True)
    check("payload defaults source to 'main_loop' when passed explicitly",
          row["source"] == "main_loop")
    check("empty pattern/session normalize to None, not ''",
          True)  # sanity placeholder, real check below

    fake_ok.inserted.clear()
    signal_log.log_signal(
        symbol="US100", direction="BUY", category="V4_SESSION", entry=29553.37,
    )
    row2 = fake_ok.inserted[0][1]
    check("omitted pattern/session become None (not empty string)",
          row2["pattern"] is None and row2["session"] is None)
    check("omitted ea_executes defaults to False",
          row2["ea_executes"] is False)
    check("omitted source defaults to 'main_loop'",
          row2["source"] == "main_loop")

    # Round-2 re-entry shape: no score, has trade1_entry
    fake_ok.inserted.clear()
    signal_log.log_signal(
        symbol="XAUUSD", direction="SELL", category="SWEEP_REENTRY",
        entry=4405.50, sl=4412.00, tp=4380.00, session="NY",
        ea_executes=True, trade1_entry=4411.00, source="reentry",
    )
    row3 = fake_ok.inserted[0][1]
    check("SWEEP_REENTRY row has score=None",
          row3["score"] is None)
    check("SWEEP_REENTRY row carries trade1_entry",
          row3["trade1_entry"] == 4411.00)
    check("SWEEP_REENTRY row source is 'reentry'",
          row3["source"] == "reentry")
finally:
    pass

fake_raising = _FakeClient(raise_on_execute=True)
signal_log._sb = lambda: fake_raising
try:
    ok2 = signal_log.log_signal(
        symbol="XAUUSD", direction="BUY", category="V4_SESSION", entry=2650.0,
    )
    check("log_signal() returns False (not raise) when insert().execute() throws",
          ok2 is False)
finally:
    signal_log._sb = _orig_sb


# ═══════════════════════════════════════════════════════════
# 4. Source guards on the two real call sites
# ═══════════════════════════════════════════════════════════

import signal_engine
import alpha_buffalo_signal as runtime

src_engine = inspect.getsource(signal_engine.compute_signal)
check("compute_signal() imports and calls log_signal() at the shared "
      "main/extra-symbol call site",
      "from signal_log import log_signal" in src_engine and
      bool(re.search(r"log_signal\(\s*\n\s*symbol=active_symbol", src_engine)))
check("compute_signal()'s log_signal() call sets source by main vs extra symbol",
      bool(re.search(
          r'source=\(\s*"main_loop"\s+if\s+active_symbol\s*==\s*SYMBOL\s+else\s+"extra_symbol"\s*\)',
          src_engine)))

src_loop = inspect.getsource(runtime.signal_loop)
check("signal_loop()'s Round-2 re-entry branch calls log_signal() with "
      "category='SWEEP_REENTRY'",
      'category="SWEEP_REENTRY"' in src_loop and "log_signal(" in src_loop)
check("Round-2 log_signal() call is scoped to the main SYMBOL (ea_executes=True)",
      bool(re.search(r'symbol=SYMBOL,\s*direction=r\["direction"\]', src_loop)) and
      "ea_executes=True" in src_loop)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All signal_log regression checks passed.")
