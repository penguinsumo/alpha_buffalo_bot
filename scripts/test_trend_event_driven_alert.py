#!/usr/bin/env python3
"""
Regression tests for the event-driven Trend Update alert + dedicated
trend-only polling loop (11 ก.ย. 2026, owner's request).

Owner, after being shown the system's own harmonic/Auto-Fibo picture
alongside a manually-drawn chart: "ทำอย่างไรให้ระบบสามารถปรับมุงมองในการ
update เทรนด์ให้ทัน" (how can the system adjust its trend-update viewpoint
to keep up). Narrowed via clarifying questions to exactly three things:

  1. "ทำให้ state (Impulse/Pullback/Sideways) ไวขึ้นต่อราคาจริง" -- make
     state track real price faster. Root cause: analyze_trend() itself
     was never slow, it was only ever run as often as signal_loop()
     polled (POLL_INTERVAL, default 30min) -- a cadence sized for the
     live trading engine, not trend awareness. Fix, per the owner's own
     choice ("แยก loop เฉพาะ trend ออกมา"): a DEDICATED trend_loop() in
     alpha_buffalo_signal.py on its own faster cadence
     (ALPHA_TREND_POLL_INTERVAL_SEC, default 300s/5min), fully decoupled
     from POLL_INTERVAL/compute_signal()'s own cadence and API footprint.
  2. "อัปเดตทันทีเมื่อ Harmonic/Auto Fibo เปลี่ยน" -- alert immediately
     when the active harmonic pattern or the Auto Fibo big-picture swing
     changes.
  3. "ส่ง Trend Update ถี่ขึ้น (ไม่รอ session เปลี่ยน)" -- send more often
     than just on session change. Per the owner's own choice ("Event-
     driven เท่านั้น"), NOT a periodic timer -- should_send_trend_alert()
     now fires on session change, bias/action change, any per-TF state
     change, harmonic active-pattern change, OR a new Auto Fibo swing --
     whichever comes first, still zero duplicate/no-op sends otherwise.

This file covers: should_send_trend_alert()'s new (TrendResult-based)
trigger logic, trend_loop()'s existence/wiring, and that signal_loop()
no longer computes/sends Trend Updates itself (moved out entirely, kept
only a cheap get_session() call for the session label other messages
still need).

Run: python3 scripts/test_trend_event_driven_alert.py
Exits non-zero on any failure.
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.pop("ALPHA_TREND_POLL_INTERVAL_SEC", None)
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "1")

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


import trend_monitor as tm
from trend_monitor import TFTrend, TrendResult
from auto_fibo_entry import AutoFiboEstimate, DIRECTION_UP, DIRECTION_DOWN


def make_tf(state="Sideways ➡️"):
    return TFTrend(tf="M15", state=state, emoji="➡️", pressure="", ema20=100.0, ema50=100.0, price=100.0)


def make_tr(session="London", bias="BUY", action="WATCH_SETUP",
            m15_state="Impulse Δ+", h1_state="Impulse Δ+", h4_state="Impulse Δ+",
            harmonic_active=None, auto_fibo=None):
    fc = None
    if harmonic_active is not None:
        fc = {"active": harmonic_active, "next_target": None, "ranked": [harmonic_active]}
    return TrendResult(
        symbol="XAUUSD", session=session, price=100.0,
        m15=make_tf(m15_state), h1=make_tf(h1_state), h4=make_tf(h4_state),
        bias=bias, action=action, timestamp="stub",
        auto_fibo=auto_fibo, harmonic_forecast=fc,
    )


active_pattern_a = {"name": "Bearish_Cypher", "tf": "H1", "direction": "SELL", "prz_mid": 4373.41}
active_pattern_b = {"name": "Bearish_Gartley", "tf": "H4", "direction": "SELL", "prz_mid": 4637.17}

af_up = AutoFiboEstimate(direction=DIRECTION_UP, swing_high=200.0, swing_low=100.0,
                          entry_near=150.0, entry_deep=140.0, ext_target=250.0,
                          zone_lo=145.0, zone_hi=155.0)
af_up_new_high = AutoFiboEstimate(direction=DIRECTION_UP, swing_high=210.0, swing_low=100.0,
                                   entry_near=150.0, entry_deep=140.0, ext_target=250.0,
                                   zone_lo=145.0, zone_hi=155.0)
af_up_tiny_noise = AutoFiboEstimate(direction=DIRECTION_UP, swing_high=200.004, swing_low=100.0,
                                     entry_near=150.0, entry_deep=140.0, ext_target=250.0,
                                     zone_lo=145.0, zone_hi=155.0)
af_down = AutoFiboEstimate(direction=DIRECTION_DOWN, swing_high=200.0, swing_low=100.0,
                            entry_near=150.0, entry_deep=140.0, ext_target=50.0,
                            zone_lo=145.0, zone_hi=155.0)


# ═══════════════════════════════════════════════════════════
# 1. should_send_trend_alert() -- fresh module state each check via reload
# ═══════════════════════════════════════════════════════════
import importlib


def fresh_tm():
    return importlib.reload(tm)


t = fresh_tm()
tr1 = make_tr()
check("first call ever -> always fires (fresh state, nothing sent yet)",
      t.should_send_trend_alert(tr1) is True)
check("immediate identical repeat -> does NOT fire (nothing changed since last SENT)",
      t.should_send_trend_alert(make_tr()) is False)

t = fresh_tm()
t.should_send_trend_alert(tr1)
check("session change alone -> fires", t.should_send_trend_alert(make_tr(session="NY")) is True)

t = fresh_tm()
t.should_send_trend_alert(tr1)
check("bias change alone -> fires", t.should_send_trend_alert(make_tr(bias="SELL")) is True)

t = fresh_tm()
t.should_send_trend_alert(tr1)
check("action change alone -> fires (e.g. WATCH_SETUP -> PATTERN_ACTIVE)",
      t.should_send_trend_alert(make_tr(action="PATTERN_ACTIVE")) is True)

t = fresh_tm()
t.should_send_trend_alert(tr1)
check("M15 per-TF state change alone -> fires",
      t.should_send_trend_alert(make_tr(m15_state="Sideways ➡️")) is True)
t = fresh_tm()
t.should_send_trend_alert(tr1)
check("H1 per-TF state change alone -> fires",
      t.should_send_trend_alert(make_tr(h1_state="Sideways ➡️")) is True)
t = fresh_tm()
t.should_send_trend_alert(tr1)
check("H4 per-TF state change alone -> fires",
      t.should_send_trend_alert(make_tr(h4_state="Sideways ➡️")) is True)

t = fresh_tm()
t.should_send_trend_alert(make_tr(harmonic_active=None))
check("harmonic active pattern appearing (None -> a real match) -> fires",
      t.should_send_trend_alert(make_tr(harmonic_active=active_pattern_a)) is True)

t = fresh_tm()
t.should_send_trend_alert(make_tr(harmonic_active=active_pattern_a))
check("harmonic active pattern changing to a DIFFERENT pattern -> fires",
      t.should_send_trend_alert(make_tr(harmonic_active=active_pattern_b)) is True)
t = fresh_tm()
t.should_send_trend_alert(make_tr(harmonic_active=active_pattern_a))
check("harmonic active pattern clearing (something -> None) -> fires",
      t.should_send_trend_alert(make_tr(harmonic_active=None)) is True)
t = fresh_tm()
t.should_send_trend_alert(make_tr(harmonic_active=active_pattern_a))
check("harmonic active pattern UNCHANGED (identical dict again) -> does NOT fire",
      t.should_send_trend_alert(make_tr(harmonic_active=dict(active_pattern_a))) is False)

t = fresh_tm()
t.should_send_trend_alert(make_tr(auto_fibo=af_up))
check("Auto Fibo direction flip (UP -> DOWN) -> fires",
      t.should_send_trend_alert(make_tr(auto_fibo=af_down)) is True)
t = fresh_tm()
t.should_send_trend_alert(make_tr(auto_fibo=af_up))
check("Auto Fibo swing_high shifting meaningfully (200 -> 210) -> fires",
      t.should_send_trend_alert(make_tr(auto_fibo=af_up_new_high)) is True)
t = fresh_tm()
t.should_send_trend_alert(make_tr(auto_fibo=af_up))
check("Auto Fibo sub-cent float noise (200.0 -> 200.004, rounds the same) "
      "-> does NOT fire (rounded key, not raw float equality)",
      t.should_send_trend_alert(make_tr(auto_fibo=af_up_tiny_noise)) is False)
t = fresh_tm()
t.should_send_trend_alert(make_tr(auto_fibo=None))
check("Auto Fibo unavailable both times (None -> None) -> does NOT falsely fire",
      t.should_send_trend_alert(make_tr(auto_fibo=None)) is False)

t = fresh_tm()
check("never raises on a TrendResult with auto_fibo=None and harmonic_forecast=None",
      isinstance(t.should_send_trend_alert(make_tr(auto_fibo=None, harmonic_active=None)), bool))

importlib.reload(tm)  # restore clean module state for anything importing tm later


# ═══════════════════════════════════════════════════════════
# 2. TREND_POLL_INTERVAL / ALPHA_TREND_POLL_INTERVAL_SEC
# ═══════════════════════════════════════════════════════════
import alpha_buffalo_signal as runtime

check("alpha_buffalo_signal: TREND_POLL_INTERVAL defaults to 300s with no env override",
      runtime.TREND_POLL_INTERVAL == 300)

os.environ["ALPHA_TREND_POLL_INTERVAL_SEC"] = "120"
runtime_reloaded = importlib.reload(runtime)
check("ALPHA_TREND_POLL_INTERVAL_SEC=120 overrides the default",
      runtime_reloaded.TREND_POLL_INTERVAL == 120)
os.environ.pop("ALPHA_TREND_POLL_INTERVAL_SEC", None)
importlib.reload(runtime)


# ═══════════════════════════════════════════════════════════
# 3. Source guards: trend computation/sending moved OUT of signal_loop(),
#    into a dedicated trend_loop() using the new should_send_trend_alert(tr)
# ═══════════════════════════════════════════════════════════
src_signal_loop = inspect.getsource(runtime.signal_loop)
check("signal_loop(): no longer calls analyze_trend() itself",
      "analyze_trend(df_4h, df_1h, df_15m, SYMBOL)" not in src_signal_loop)
check("signal_loop(): no longer calls should_send_trend_alert()/sends the "
      "Trend Update message -- that's trend_loop()'s job now",
      "should_send_trend_alert(" not in src_signal_loop
      and "format_trend_message(trend)" not in src_signal_loop)
check("signal_loop(): still gets a cheap session label via get_session() "
      "for format_signal_message()/log_signal() below",
      "session = get_session(" in src_signal_loop)
check("signal_loop(): format_signal_message()/log_signal() now use the "
      "local `session` var, not a removed `trend.session`",
      "session=trend.session" not in src_signal_loop
      and "session=session" in src_signal_loop)

check("trend_loop() exists as its own top-level function",
      hasattr(runtime, "trend_loop") and callable(runtime.trend_loop))
src_trend_loop = inspect.getsource(runtime.trend_loop)
check("trend_loop(): calls analyze_trend(df_4h, df_1h, df_15m, SYMBOL)",
      "analyze_trend(df_4h, df_1h, df_15m, SYMBOL)" in src_trend_loop)
check("trend_loop(): gates sends through should_send_trend_alert(trend) "
      "-- the new TrendResult-based signature, not the old trend.session one",
      "should_send_trend_alert(trend)" in src_trend_loop)
check("trend_loop(): sleeps on TREND_POLL_INTERVAL, not the main POLL_INTERVAL",
      "time.sleep(TREND_POLL_INTERVAL)" in src_trend_loop
      and "time.sleep(POLL_INTERVAL)" not in src_trend_loop)
check("trend_loop(): still sends the owner-only Harmonic Forecast message "
      "via chat_id=ADMIN_ID, same as before the move",
      "format_harmonic_forecast_message(trend), chat_id=ADMIN_ID" in src_trend_loop)

src_module = inspect.getsource(runtime)
check("startup: trend_loop is started as its own daemon thread alongside "
      "the existing command_loop/signal_loop/_signal_loop_watchdog",
      "threading.Thread(target=trend_loop, daemon=True).start()" in src_module)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All event-driven Trend Update alert regression checks passed.")
