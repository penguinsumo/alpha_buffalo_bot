#!/usr/bin/env python3
"""
Regression tests for the Harmonic Pattern Forecast ("big picture
awareness") feature (10 ก.ย. 2026, owner's request).

Owner (after being shown a manual scan_harmonic() dump of every pattern
detected on live XAUUSD H1/H4, one of which price happened to be sitting
in): "จะทำยังงงัยให้ระบบเรา awareness เองได้ว่าเรามีถาะใหญ่เปนแบบที่คุณหามา
เพื่อที่จะคาดการณ์ทิศทางใน h4 แท่งต่อ แล้วกลายเปนภาพ new day โดยมี harmonic
เปนตัวชี้นำ" -- wants the SYSTEM ITSELF to routinely see this picture,
not just on manual request. Scope confirmed via clarifying questions:

  1. Show BOTH the active trend-aligned pattern (if price is inside one)
     + the next trend-aligned target ahead, AND rank every pattern
     scan_harmonic() found with a confidence % ("ใช้ pattern ที่เรามี
     ประเมินว่า % น่าจะเป็นตัวไหนมากที่สุด").
  2. Detail stays OWNER-ONLY ("เพิ่มให้เฉพาะ owners ดูก่อน ไม่ปล่อยห้อง
     ทั่วไป") -- format_harmonic_forecast_message(), sent only to
     ADMIN_ID, never broadcast to NOTIFY_IDS/the general room.
  3. SHOULD affect trend_monitor's `action` field ("ให้มีผลต่อ action
     ด้วย") -- escalates WATCH_SETUP/WAIT_AND_SEE to PATTERN_ACTIVE, but
     NEVER touches compute_signal()/execution (those stay governed
     entirely by score_manager's own thresholds -- this is display/
     labeling only).

confidence % is an explicitly-labeled RULE-BASED HEURISTIC (pattern tier +
timeframe weight + cascade-direction agreement + Auto Fibo confluence),
NOT a backtested win-rate or ML probability -- the owner separately noted
the project has a real "AI learning" track planned for that; this is
meant as an honest, explainable ranking aid in the meantime.

Run: python3 scripts/test_harmonic_forecast.py
Exits non-zero on any failure.
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.pop("ALPHA_TREND_HARMONIC_FORECAST_ENABLED", None)
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import pandas as pd

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


import signal_engine as se
from signal_engine import score_harmonic_confidence, build_harmonic_forecast
from auto_fibo_entry import AutoFiboEstimate, DIRECTION_UP, DIRECTION_DOWN


# ═══════════════════════════════════════════════════════════
# 1. score_harmonic_confidence()
# ═══════════════════════════════════════════════════════════

prz_p1_h4 = {"priority": 1, "tf": "H4", "direction": "BUY", "prz_low": 95.0, "prz_high": 105.0}
prz_p3_h1 = {"priority": 3, "tf": "H1", "direction": "SELL", "prz_low": 95.0, "prz_high": 105.0}

check("score: priority 1 + H4 + cascade match + no auto_fibo -> 40+25+25+0 = 90",
      score_harmonic_confidence(prz_p1_h4, "BUY", None) == 90.0)
check("score: priority 3 + H1 + no cascade match + no auto_fibo -> 10+15+0+0 = 25 (floor)",
      score_harmonic_confidence(prz_p3_h1, "BUY", None) == 25.0)

af_buy_overlap = AutoFiboEstimate(
    direction=DIRECTION_UP, swing_high=200.0, swing_low=100.0,
    entry_near=150.0, entry_deep=140.0, ext_target=250.0,
    zone_lo=98.0, zone_hi=110.0,
)
check("score: full house -- priority 1 + H4 + cascade match + Auto Fibo BUY-zone "
      "overlap (98-110 overlaps 95-105) -> 40+25+25+10 = 100 (max)",
      score_harmonic_confidence(prz_p1_h4, "BUY", af_buy_overlap) == 100.0)

af_wrong_dir = AutoFiboEstimate(
    direction=DIRECTION_DOWN, swing_high=200.0, swing_low=100.0,
    entry_near=150.0, entry_deep=140.0, ext_target=50.0,
    zone_lo=98.0, zone_hi=110.0,
)
check("score: Auto Fibo zone overlaps but implies SELL (DIRECTION_DOWN) while "
      "this PRZ is BUY -- confluence bonus withheld -> stays 90, not 100",
      score_harmonic_confidence(prz_p1_h4, "BUY", af_wrong_dir) == 90.0)

af_no_overlap = AutoFiboEstimate(
    direction=DIRECTION_UP, swing_high=500.0, swing_low=400.0,
    entry_near=450.0, entry_deep=440.0, ext_target=600.0,
    zone_lo=430.0, zone_hi=460.0,
)
check("score: Auto Fibo direction matches but zone (430-460) does NOT overlap "
      "the PRZ (95-105) -- no confluence bonus -> stays 90",
      score_harmonic_confidence(prz_p1_h4, "BUY", af_no_overlap) == 90.0)

check("score: missing priority/tf fields fall back to the least-favorable "
      "case (tier 10, H1 weight 15), never raises",
      score_harmonic_confidence({"direction": "BUY"}, "BUY", None) == 10 + 15 + 25)
check("score: totally empty dict never raises",
      score_harmonic_confidence({}, "BUY", None) == 10 + 15 + 0)


# ═══════════════════════════════════════════════════════════
# 2. build_harmonic_forecast()
# ═══════════════════════════════════════════════════════════

check("build_harmonic_forecast: empty prz_list -> everything None/[]/False",
      build_harmonic_forecast([], "BUY", 100.0, None) ==
      {"active": None, "next_target": None, "ranked": [], "tf_conflict": False})
check("build_harmonic_forecast: None prz_list -> never raises, same as []",
      build_harmonic_forecast(None, "BUY", 100.0, None) ==
      {"active": None, "next_target": None, "ranked": [], "tf_conflict": False})

# Fixture: price=100. Four candidates --
#  A: priority 2, H1, BUY, in-zone [95-105]   (earlier in list, lower tier)
#  B: priority 1, H4, BUY, in-zone [95-105]   (later in list, higher tier/confidence)
#  C: priority 1, H1, BUY, NOT in-zone [140-150] (trend-aligned target ahead)
#  D: priority 1, H4, SELL, in-zone [95-105]  (counter-trend, same zone)
prz_A = {"name": "A_Bat",     "tf": "H1", "direction": "BUY",  "priority": 2, "prz_low": 95.0,  "prz_high": 105.0, "prz_mid": 100.0}
prz_B = {"name": "B_Gartley", "tf": "H4", "direction": "BUY",  "priority": 1, "prz_low": 95.0,  "prz_high": 105.0, "prz_mid": 100.0}
prz_C = {"name": "C_Gartley", "tf": "H1", "direction": "BUY",  "priority": 1, "prz_low": 140.0, "prz_high": 150.0, "prz_mid": 145.0}
prz_D = {"name": "D_Cypher",  "tf": "H4", "direction": "SELL", "priority": 1, "prz_low": 95.0,  "prz_high": 105.0, "prz_mid": 100.0}

fc = build_harmonic_forecast([prz_A, prz_B, prz_C, prz_D], "BUY", 100.0, None)

check("build_harmonic_forecast: 'active' mirrors compute_signal()'s own "
      "prz_match loop -- the FIRST in-zone, direction-matching entry in "
      "the ORIGINAL list order (A), NOT the highest-confidence one (B), "
      "even though B scores higher",
      fc["active"] is not None and fc["active"]["name"] == "A_Bat")
check("build_harmonic_forecast: 'next_target' is the nearest trend-aligned "
      "PRZ price hasn't reached yet (C, the only BUY zone not containing "
      "price)",
      fc["next_target"] is not None and fc["next_target"]["name"] == "C_Gartley")
check("build_harmonic_forecast: 'next_target' distance is |100-145| = 45",
      fc["next_target"]["distance"] == 45.0)
check("build_harmonic_forecast: 'ranked' contains all 4 candidates",
      len(fc["ranked"]) == 4)
check("build_harmonic_forecast: 'ranked' sorted by confidence descending -- "
      "B (H4, prio1, matches) outranks A (H1, prio2, matches) outranks D "
      "(H4, prio1, but counter-trend, no cascade bonus)",
      fc["ranked"][0]["name"] == "B_Gartley"
      and fc["ranked"][0]["confidence"] >= fc["ranked"][1]["confidence"])
b_entry = next(e for e in fc["ranked"] if e["name"] == "B_Gartley")
d_entry = next(e for e in fc["ranked"] if e["name"] == "D_Cypher")
check("build_harmonic_forecast: is_active is True ONLY on the literal "
      "active match (A), never on B even though B is also in-zone+BUY",
      next(e for e in fc["ranked"] if e["name"] == "A_Bat")["is_active"] is True
      and b_entry["is_active"] is False)
check("build_harmonic_forecast: counter-trend entry (D, SELL while cascade "
      "is BUY) is present in 'ranked' but excluded from both active and "
      "next_target, and flagged matches_cascade=False",
      d_entry["matches_cascade"] is False
      and fc["active"]["name"] != "D_Cypher"
      and fc["next_target"]["name"] != "D_Cypher")

# No trend-aligned candidates at all -> active/next_target both None, but
# ranked still lists everything (so a caller can see counter-trend context).
fc_none = build_harmonic_forecast([prz_D], "BUY", 100.0, None)
check("build_harmonic_forecast: only a counter-trend candidate exists -> "
      "active=None, next_target=None, but it's still in 'ranked'",
      fc_none["active"] is None and fc_none["next_target"] is None
      and len(fc_none["ranked"]) == 1)

check("build_harmonic_forecast: ranked entries carry 'priority' straight "
      "through from the raw prz dict (needed for harmonic_pattern_log "
      "bucketing later)",
      next(e for e in fc["ranked"] if e["name"] == "A_Bat")["priority"] == 2)
check("build_harmonic_forecast: ranked entries carry 'auto_fibo_confluence' "
      "(False here since no auto_fibo was passed)",
      all(e["auto_fibo_confluence"] is False for e in fc["ranked"]))
fc_confluence = build_harmonic_forecast([prz_B], "BUY", 100.0, af_buy_overlap)
check("build_harmonic_forecast: 'auto_fibo_confluence' is True on a ranked "
      "entry when score_harmonic_confidence() would also award the "
      "confluence bonus for it (same _has_auto_fibo_confluence() helper)",
      fc_confluence["ranked"][0]["auto_fibo_confluence"] is True)


# ═══════════════════════════════════════════════════════════
# 2b. build_harmonic_forecast() -- tf_conflict
# ═══════════════════════════════════════════════════════════

# A (H1, BUY) is the only H1 candidate and the only match overall in the
# earlier fixture -- no H4 candidate at all there once D is excluded... but
# the full fc fixture ([A,B,C,D]) DOES have both H4 entries (B=BUY, D=SELL)
# and both H1 entries (A=BUY, C=BUY) -- best-by-confidence-per-TF: H4 top is
# B (BUY, higher confidence than D since D loses the cascade bonus), H1 top
# is whichever of A/C ranks higher (both BUY) -- so no conflict expected.
check("build_harmonic_forecast: H4-top (B, BUY) and H1-top (A or C, BUY) "
      "agree -> tf_conflict False",
      fc["tf_conflict"] is False)

# Force an actual conflict: H4's only/best candidate is SELL, H1's only/
# best candidate is BUY.
prz_h4_sell_only = {"name": "H4_Sell", "tf": "H4", "direction": "SELL", "priority": 1,
                     "prz_low": 200.0, "prz_high": 210.0, "prz_mid": 205.0}
prz_h1_buy_only  = {"name": "H1_Buy",  "tf": "H1", "direction": "BUY",  "priority": 1,
                     "prz_low": 95.0,  "prz_high": 105.0, "prz_mid": 100.0}
fc_conflict = build_harmonic_forecast([prz_h4_sell_only, prz_h1_buy_only], "BUY", 100.0, None)
check("build_harmonic_forecast: H4's best pattern (SELL) disagrees with "
      "H1's best pattern (BUY) -> tf_conflict True",
      fc_conflict["tf_conflict"] is True)

fc_h4_only = build_harmonic_forecast([prz_h4_sell_only], "BUY", 100.0, None)
check("build_harmonic_forecast: only H4 has a candidate (no H1 data at "
      "all) -> tf_conflict False, nothing to compare against",
      fc_h4_only["tf_conflict"] is False)


# ═══════════════════════════════════════════════════════════
# 3. trend_monitor.py wiring
# ═══════════════════════════════════════════════════════════

import trend_monitor as tm

check("trend_monitor: ALPHA_TREND_HARMONIC_FORECAST_ENABLED defaults to "
      "True in a fresh process with no env override",
      tm.HARMONIC_FORECAST_ENABLED is True)
check("trend_monitor: default is driven by the 'true' default string",
      'ALPHA_TREND_HARMONIC_FORECAST_ENABLED", "true"' in inspect.getsource(tm))

import numpy as np
n = 100
flat = [2000.0] * n
def make_flat_ohlcv():
    return pd.DataFrame({
        "open": flat, "high": [p + 1 for p in flat], "low": [p - 1 for p in flat],
        "close": flat, "volume": [100] * n,
    })
df_4h_stub = make_flat_ohlcv()
df_1h_stub = make_flat_ohlcv()
df_15m_stub = make_flat_ohlcv()

# Monkeypatch scan_harmonic/compute_cascade at the trend_monitor module
# level (same pattern test_extra_trend_digest.py already uses for
# alpha_buffalo_signal.py) so analyze_trend()'s PATTERN_ACTIVE escalation
# is tested deterministically, independent of real pivot/structure math.
_orig_scan_harmonic = tm.scan_harmonic
_orig_compute_cascade = tm.compute_cascade

active_prz = {"name": "Test_Gartley", "tf": "H1", "direction": "BUY", "priority": 1,
              "prz_low": 1995.0, "prz_high": 2005.0, "prz_mid": 2000.0}
tm.scan_harmonic = lambda df_1h, df_4h: [active_prz]
tm.compute_cascade = lambda df_4h, df_1h, df_15m: {"direction": "BUY"}

tr_active = tm.analyze_trend(df_4h_stub, df_1h_stub, df_15m_stub, symbol="XAUUSD")
check("analyze_trend(): with a trend-aligned in-zone pattern (monkeypatched "
      "scan_harmonic/compute_cascade), harmonic_forecast['active'] is populated",
      tr_active.harmonic_forecast is not None and tr_active.harmonic_forecast.get("active") is not None)
check("analyze_trend(): action escalates to PATTERN_ACTIVE",
      tr_active.action == "PATTERN_ACTIVE")

msg_general = tm.format_trend_message(tr_active)
check("format_trend_message(): PATTERN_ACTIVE shows the generic 'Strong "
      "Setup Forming' wording -- no pattern name/PRZ leaked to the "
      "general-room message",
      "Strong" in msg_general and "Setup Forming" in msg_general
      and "Test_Gartley" not in msg_general)

msg_admin = tm.format_harmonic_forecast_message(tr_active)
check("format_harmonic_forecast_message(): owner-only message DOES name "
      "the active pattern, its PRZ and a confidence %",
      "Test_Gartley" in msg_admin and "ACTIVE NOW" in msg_admin and "Confidence" in msg_admin)
check("format_harmonic_forecast_message(): no TF CONFLICT warning when "
      "tf_conflict is False (single H1 pattern here, nothing to conflict "
      "with)",
      "TF CONFLICT" not in msg_admin)

import types
dummy_ranked_entry = {"name": "Dummy", "tf": "H4", "direction": "BUY", "priority": 1,
                       "prz_low": 1990.0, "prz_high": 2000.0, "prz_mid": 1995.0,
                       "confidence": 65.0, "distance": 5.0, "in_zone": False,
                       "matches_cascade": True, "is_active": False, "auto_fibo_confluence": False}
tr_conflict = types.SimpleNamespace(
    symbol="XAUUSD", price=2000.0,
    harmonic_forecast={"active": None, "next_target": None, "ranked": [dummy_ranked_entry], "tf_conflict": True},
)
msg_conflict = tm.format_harmonic_forecast_message(tr_conflict)
check("format_harmonic_forecast_message(): tf_conflict True -> TF CONFLICT "
      "warning line shown",
      "TF CONFLICT" in msg_conflict)

# Cascade NEUTRAL -> compute_signal() itself would bail out (direction ==
# NEUTRAL: return None) so there is nothing meaningful to forecast against
# either -- harmonic_forecast must stay None, and PATTERN_ACTIVE must
# never fire from this feature in that case.
tm.compute_cascade = lambda df_4h, df_1h, df_15m: {"direction": "NEUTRAL"}
tr_neutral = tm.analyze_trend(df_4h_stub, df_1h_stub, df_15m_stub, symbol="XAUUSD")
check("analyze_trend(): cascade NEUTRAL -> harmonic_forecast stays None "
      "(mirrors compute_signal() bailing out on NEUTRAL too)",
      tr_neutral.harmonic_forecast is None)
check("analyze_trend(): cascade NEUTRAL -> action never escalated to "
      "PATTERN_ACTIVE by this feature",
      tr_neutral.action != "PATTERN_ACTIVE")

tm.scan_harmonic = _orig_scan_harmonic
tm.compute_cascade = _orig_compute_cascade


# Kill switch: disabling the flag must silence computation, action
# escalation, AND the owner-only message content, even with real
# trend-aligned data available.
os.environ["ALPHA_TREND_HARMONIC_FORECAST_ENABLED"] = "false"
import importlib
tm_off = importlib.reload(tm)
check("trend_monitor: ALPHA_TREND_HARMONIC_FORECAST_ENABLED=false disables the flag",
      tm_off.HARMONIC_FORECAST_ENABLED is False)

tm_off.scan_harmonic = lambda df_1h, df_4h: [active_prz]
tm_off.compute_cascade = lambda df_4h, df_1h, df_15m: {"direction": "BUY"}
tr_off = tm_off.analyze_trend(df_4h_stub, df_1h_stub, df_15m_stub, symbol="XAUUSD")
check("analyze_trend(): kill switch off -> harmonic_forecast stays None "
      "even with a real trend-aligned pattern available",
      tr_off.harmonic_forecast is None)
check("analyze_trend(): kill switch off -> action never escalates to PATTERN_ACTIVE",
      tr_off.action != "PATTERN_ACTIVE")
msg_off_admin = tm_off.format_harmonic_forecast_message(tr_off)
check("format_harmonic_forecast_message(): kill switch off -> falls back "
      "to the 'no data available' message, not a stale active pattern",
      "No trend-aligned harmonic pattern data" in msg_off_admin)

os.environ.pop("ALPHA_TREND_HARMONIC_FORECAST_ENABLED", None)
importlib.reload(tm)  # restore true default for anything importing tm later


# ═══════════════════════════════════════════════════════════
# 4. Source guards
# ═══════════════════════════════════════════════════════════

src_analyze = inspect.getsource(__import__("trend_monitor").analyze_trend)
check("analyze_trend(): harmonic_forecast computation wrapped in "
      "try/except, same as auto_fibo/zone_rvol",
      "harmonic_forecast = None" in src_analyze and "except Exception" in src_analyze)
check("analyze_trend(): skips build_harmonic_forecast() entirely when "
      "cascade is NEUTRAL (mirrors compute_signal()'s own bailout)",
      'cascade_direction != "NEUTRAL"' in src_analyze)
check("analyze_trend(): scan_harmonic(df_1h, df_4h) is computed once, "
      "shared between Zone Strength and the Harmonic Forecast (no double "
      "H1/H4 scan per cycle)",
      src_analyze.count("scan_harmonic(df_1h, df_4h)") == 1)

src_format = inspect.getsource(__import__("trend_monitor").format_trend_message)
check("format_trend_message(): PATTERN_ACTIVE branch present and never "
      "reads tr.harmonic_forecast directly (generic wording only -- a "
      "comment may still mention the feature by name, that's fine)",
      'tr.action == "PATTERN_ACTIVE"' in src_format
      and "tr.harmonic_forecast" not in src_format)

import alpha_buffalo_signal as runtime
src_runtime = inspect.getsource(runtime)
check("alpha_buffalo_signal.py: owner-only forecast send site uses "
      "chat_id=ADMIN_ID (never a bare broadcast to NOTIFY_IDS)",
      "format_harmonic_forecast_message(trend), chat_id=ADMIN_ID" in src_runtime)
check("alpha_buffalo_signal.py: trend_loop() logs every new active "
      "harmonic pattern via harmonic_pattern_log.log_new_active_pattern() "
      "(Phase 2 of the harmonic-accuracy strategy)",
      "log_new_active_pattern(SYMBOL, trend.harmonic_forecast)" in src_runtime)
check("alpha_buffalo_signal.py: trend_loop() also checks pending harmonic "
      "outcomes, throttled via harmonic_outcome_check_allowed()",
      "harmonic_outcome_check_allowed()" in src_runtime
      and "check_pending_harmonic_outcomes(get_ohlcv)" in src_runtime)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Harmonic Pattern Forecast regression checks passed.")
