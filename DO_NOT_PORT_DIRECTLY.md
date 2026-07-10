# Do Not Port Directly

This file lists useful but unsafe code that should not be copied into production without tests and adaptation.

## Sniper / Trap Prototype

Source:

- `/Users/mac/AlphaBuffalo_v5.3_Beta/sniper_gate.py`
- `/Users/mac/AlphaBuffalo_v5.3_Beta/payload_manager.py`
- `/Users/mac/AlphaBuffalo_v5.3_Beta/signal_engine_sni.py`
- `/Users/mac/AlphaBuffalo_v5.3_Beta/vsa_gate_sni.py`
- `/Users/mac/AlphaBuffalo_v5.3_Beta/AlphaBuffalo_CloudEA_sni.mq5`

Good ideas:

- trap payload
- TTL
- atomic payload write
- max slippage
- fail-silent if no VSA wall

Do not port directly because:

- `signal_engine_sni.py` contains placeholder validation (`is_valid = True`)
- payload function signatures do not match between modules
- execution schema is not aligned with current EA payload
- no regression tests exist for trap expiry, slippage, or stale payload clearing

Required before porting:

- define `TrapPayload` schema
- add offline tests
- add EA compatibility test
- prove stale trap clears safely

## v5.3 SignalComposer

Source:

- `/Users/mac/AlphaBuffalo_v5.3_Beta/signal_composer.py`
- `/Users/mac/alpha_buffalo_bot_clean/signal_composer.py`

Good ideas:

- confluence scoring
- Kivanc/Harmonic/Micro source labels
- basket layer concept
- TP1/TP2 calculation

Do not port directly because:

- it is a different architecture than v12-core
- basket/martingale language conflicts with current risk discipline
- direct copy can reintroduce trend-first or score-first behavior

Required before porting:

- extract only lifecycle or target calculation
- keep engine_v4 as entry source
- add regression tests proving location-first behavior survives

## Old Session Clock

Source:

- `/Users/mac/alpha_buffalo_bot_clean/session_clock.py`
- `/Users/mac/AlphaBuffalo_v5.3_Beta/session_clock.py`

Good ideas:

- session thresholds
- allowed zone metadata
- overlap scoring

Do not port directly because:

- current `session_clock.py` is already used by production runtime
- engine_v4 gates expect the current `SessionState` shape
- replacing it can break Railway runtime and FinalGate

Port only as an additive `SessionPolicy`, not as a replacement.

## Online Backtest Scripts

Examples:

- `backtest_new_session_logic.py`
- `backtest_visual_tp_all.py`
- `backtest_adaptive_hourly.py`
- branch/zip regression scripts that call TwelveData directly

Do not use as daily regression because:

- depend on network/API availability
- can leak or rely on secrets
- not reproducible

Convert useful checks into offline fixtures under `scripts/alpha_regression_suite.py`.

## Research Engine

Source:

- `research_engine_v112_v4_pro.py`

Good ideas:

- vectorized simulation
- Monte Carlo robustness
- regime clustering

Do not port directly because:

- contains placeholder API key text
- simulation assumptions differ from production EA payload

Port after making it file-based and deterministic.

