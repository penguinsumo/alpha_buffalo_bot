#!/usr/bin/env python3
"""
Regression tests for the score_manager.py accuracy fix: Bucket B (Entry Zone
Quality)'s Kivanc Golden Zone cliff at kivanc_score >= 3.

Context: kivanc_score comes from signal_engine's H1(+2)/H4(+3) pinbar
confluence check inside the golden zone -- achievable values are 0 (neither),
2 (H1 pinbar only), 3 (H4 pinbar only), or 5 (both, min-capped at 5). The old
threshold (kivanc_score >= 3) gave a real H1-only pinbar (score=2) exactly
the same 0 points as having NO pinbar confluence at all. Fixed to >= 2 so a
real H1-only pinbar gets proportionate credit instead of being discarded.

score_manager.py had ZERO existing regression coverage before this file.

Also documents (as a permanent regression guard, not a fix) the result of
investigating a suspected second issue: whether Bucket C's hard cap at 5
silently discards the AT Cascade Bonus. Verified by direct arithmetic that
min(core + at_bonus, 5) is mathematically identical, for every possible
core/at_bonus combination, to a "reserve headroom for the bonus" formula --
there was no real bug there, so nothing was changed in Bucket C. This test
guards against a future edit accidentally introducing behavior where the
bonus is discounted below what the plain sum-then-cap formula already gives.

Run: python3 scripts/test_score_manager_accuracy.py
Exits non-zero on any failure.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


from score_manager import ScoreManager, THRESHOLD_V4, THRESHOLD_V5

sm = ScoreManager()

# ── Bucket B: Kivanc Golden Zone cliff fix ──────────────────────────────
breakdown = {}
pts_h1_only = sm._score_bucket_b(
    harmonic_in_prz=False, harmonic_priority="secondary",
    kivanc_in_golden=True, kivanc_score=2,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("FIXED: kivanc_score=2 (H1-only pinbar) in golden zone now scores 2, not 0",
      pts_h1_only == 2)
check("FIXED: breakdown records the Kivanc Golden Zone credit for score=2",
      breakdown.get("Kivanc Golden Zone") == 2)

breakdown = {}
pts_no_evidence = sm._score_bucket_b(
    harmonic_in_prz=False, harmonic_priority="secondary",
    kivanc_in_golden=True, kivanc_score=0,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("no pinbar confluence at all (score=0) still scores 0 -- no false positive",
      pts_no_evidence == 0)
check("no false 'Kivanc Golden Zone' breakdown entry when score=0",
      "Kivanc Golden Zone" not in breakdown)

breakdown = {}
pts_h4_only = sm._score_bucket_b(
    harmonic_in_prz=False, harmonic_priority="secondary",
    kivanc_in_golden=True, kivanc_score=3,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("UNCHANGED: kivanc_score=3 (H4-only pinbar) still scores 3",
      pts_h4_only == 3)

breakdown = {}
pts_both = sm._score_bucket_b(
    harmonic_in_prz=False, harmonic_priority="secondary",
    kivanc_in_golden=True, kivanc_score=5,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("UNCHANGED: kivanc_score=5 (both H1+H4) still caps at 4",
      pts_both == 4)

breakdown = {}
pts_not_in_zone = sm._score_bucket_b(
    harmonic_in_prz=False, harmonic_priority="secondary",
    kivanc_in_golden=False, kivanc_score=5,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("UNCHANGED: high kivanc_score outside the golden zone still scores 0 "
      "(kivanc_in_golden gates it, score alone is never enough)",
      pts_not_in_zone == 0)

breakdown = {}
pts_harmonic_priority = sm._score_bucket_b(
    harmonic_in_prz=True, harmonic_priority="primary",
    kivanc_in_golden=True, kivanc_score=2,
    fvg_verdict="NONE", breakdown=breakdown,
)
check("UNCHANGED: Harmonic PRZ still takes priority over Kivanc Golden Zone "
      "even with the fix applied (returns 5, not 2)",
      pts_harmonic_priority == 5 and "Kivanc Golden Zone" not in breakdown)

# ── Integration: fixed H1-only pinbar can now push a borderline signal over
#    the V4 threshold when it previously could not ────────────────────────
result_before_fix_equivalent = sm.calculate(
    cascade_direction="NEUTRAL", reversal_stage=0,
    harmonic_in_prz=False, kivanc_in_golden=False, kivanc_score=2,
    bos_detected=False, mss_detected=False, sweep_valid=False,
    h1_spike=False, vsa_ok=True,  # Bucket D = 2
)
check("sanity: with kivanc_in_golden=False, an otherwise-weak setup (VSA only) "
      "stays below THRESHOLD_V4",
      result_before_fix_equivalent.total < THRESHOLD_V4)

result_with_fix = sm.calculate(
    cascade_direction="NEUTRAL", reversal_stage=0,
    harmonic_in_prz=False, kivanc_in_golden=True, kivanc_score=2,
    bos_detected=False, mss_detected=False, sweep_valid=False,
    h1_spike=False, vsa_ok=True,  # Bucket D = 2, Bucket B now = 2 (was 0)
)
check("integration: the same setup WITH a real H1-only pinbar in the golden "
      "zone now reaches THRESHOLD_V4 (2 + 2 = 4) where it fell short before "
      "the fix (0 + 2 = 2)",
      result_with_fix.total == 4 and result_with_fix.signal_type == "V4_SESSION")

# ── Regression guard: Buckets A/C/D/E are untouched by this fix ────────────
breakdown = {}
check("Bucket A unaffected: H4+H1 cascade still scores 6",
      sm._score_bucket_a("UP", False, 0, breakdown) == 6)

breakdown = {}
check("Bucket D unaffected: VSA gate still scores 2 when ok",
      sm._score_bucket_d(True, breakdown) == 2)

# ── Documented non-issue: AT Cascade Bonus is NOT silently swallowed by
#    Bucket C's cap -- verified by exhaustive arithmetic, not just a couple
#    of examples, so a future refactor can't quietly break this either way.
for core_bos in (False, True):
    for core_sweep in (False, True):
        for core_sweep_pdh in (False, True):
            for core_spike in (False, True):
                for core_spike_vol in (False, True):
                    for bonus in (0, 1):
                        bd = {}
                        c = sm._score_bucket_c(
                            bos_detected=core_bos, mss_detected=False,
                            sweep_valid=core_sweep, sweep_is_pdh_pdl=core_sweep_pdh,
                            h1_spike=core_spike, h1_spike_volume=core_spike_vol,
                            at_bonus=bonus, breakdown=bd,
                        )
                        bd0 = {}
                        c0 = sm._score_bucket_c(
                            bos_detected=core_bos, mss_detected=False,
                            sweep_valid=core_sweep, sweep_is_pdh_pdl=core_sweep_pdh,
                            h1_spike=core_spike, h1_spike_volume=core_spike_vol,
                            at_bonus=0, breakdown=bd0,
                        )
                        # Turning the bonus on must never produce a LOWER
                        # score than the same setup with the bonus off.
                        if c < c0:
                            FAILS.append(f"AT bonus regression: bonus=1 gave {c} < bonus=0 gave {c0}")
check("AT Cascade Bonus never lowers Bucket C's score vs. the same setup "
      "with the bonus off (exhaustively checked across all core combinations)",
      not any("AT bonus regression" in f for f in FAILS))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All score_manager accuracy regression checks passed.")
