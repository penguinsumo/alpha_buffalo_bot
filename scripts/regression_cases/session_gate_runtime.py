"""SessionGate BUY off-hours policy regressions.

Protects two things at once:
1. Default behavior (ALPHA_BUY_SOFT_SESSION_GATE unset/false) is byte-for-byte
   identical to the historical hard block -- nothing changes for production
   until someone opts in deliberately.
2. When opted in, an off-hours BUY is allowed with a reduced risk_adjustment
   instead of vetoed -- and every other gate (market closed, daily DD,
   consecutive loss) still blocks exactly as before regardless of the flag.
"""
from __future__ import annotations

import importlib
import os

from scripts.regression_cases.common import *

import engine_v4.session_gate as session_gate_module


ASIA_SESSION = SessionState(
    session="ASIA",
    liquidity="NORMAL",
    bkk_hour=10,
    utc_hour=3,
    timestamp="2026-07-28T03:00:00+00:00",
)

NY_EARLY_SESSION = SessionState(
    session="NY",
    liquidity="NORMAL",
    bkk_hour=20,
    utc_hour=13,
    timestamp="2026-07-28T13:00:00+00:00",
)


def _reload_session_gate(env_value: str | None):
    if env_value is None:
        os.environ.pop("ALPHA_BUY_SOFT_SESSION_GATE", None)
    else:
        os.environ["ALPHA_BUY_SOFT_SESSION_GATE"] = env_value
    return importlib.reload(session_gate_module)


def test_buy_offhours_default_is_still_a_hard_block():
    mod = _reload_session_gate(None)
    gate = mod.SessionGate(SessionClock())

    asia_result = gate.evaluate(ASIA_SESSION, "BUY")
    ny_early_result = gate.evaluate(NY_EARLY_SESSION, "BUY")

    assert_true(not asia_result.allowed, "default gate must still block BUY outside NY")
    assert_true(not ny_early_result.allowed, "default gate must still block BUY before 15 UTC")
    assert_equal(asia_result.risk_adjustment, 1.0, "blocked result should not carry a risk hint")


def test_buy_offhours_soft_gate_allows_with_reduced_risk():
    mod = _reload_session_gate("true")
    try:
        gate = mod.SessionGate(SessionClock())

        asia_result = gate.evaluate(ASIA_SESSION, "BUY")
        ny_early_result = gate.evaluate(NY_EARLY_SESSION, "BUY")

        assert_true(asia_result.allowed, "soft gate must allow off-hours BUY through")
        assert_true(ny_early_result.allowed, "soft gate must allow pre-15UTC NY BUY through")
        assert_true(
            0.0 < asia_result.risk_adjustment < 1.0,
            "off-hours BUY must carry a reduced (graduated) risk_adjustment",
        )
        assert_equal(
            asia_result.risk_adjustment,
            ny_early_result.risk_adjustment,
            "off-hours risk multiplier should be consistent regardless of which off-hours case",
        )
    finally:
        _reload_session_gate(None)


def test_buy_offhours_soft_gate_still_respects_risk_gates():
    mod = _reload_session_gate("true")
    try:
        gate = mod.SessionGate(SessionClock())

        dd_blocked = gate.evaluate(ASIA_SESSION, "BUY", daily_dd_ok=False)
        consec_blocked = gate.evaluate(ASIA_SESSION, "BUY", consec_loss_ok=False)
        closed = gate.evaluate(
            SessionState(session="CLOSED", liquidity="NONE", bkk_hour=4, utc_hour=21, timestamp="x"),
            "BUY",
        )

        assert_true(not dd_blocked.allowed, "soft gate must not bypass daily DD stop")
        assert_true(not consec_blocked.allowed, "soft gate must not bypass consecutive-loss stop")
        assert_true(not closed.allowed, "soft gate must not bypass market-closed gate")
    finally:
        _reload_session_gate(None)


def test_sell_is_never_touched_by_the_buy_offhours_policy():
    for env_value in (None, "true"):
        mod = _reload_session_gate(env_value)
        try:
            gate = mod.SessionGate(SessionClock())
            asia_sell = gate.evaluate(ASIA_SESSION, "SELL")
            assert_true(asia_sell.allowed, "SELL must never be gated by the BUY off-hours policy")
            assert_equal(asia_sell.risk_adjustment, 1.0, "SELL must not receive a BUY-only risk adjustment")
        finally:
            if env_value is not None:
                _reload_session_gate(None)
