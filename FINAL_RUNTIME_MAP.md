# Alpha Buffalo Frozen Runtime Map

This document is the production source map for the frozen `v12-core` runtime.
Files not listed here are not deployment entry points.

## Deployment

- FastAPI entry point: `alpha_buffalo_signal.py`
- Railway command: `uvicorn alpha_buffalo_signal:app`
- Compatibility facade: `alpha_buffalo_signal.py` keeps every existing
  endpoint and import name stable while delegating pure runtime work to:
  - `runtime_layers/common.py` — normalization and confirmed-time helpers
  - `runtime_layers/evidence.py` — PRZ memory, M5 sniper, and HOLD diagnostics
  - `runtime_layers/harmonic.py` — Newday/harmonic guidance normalization
  - `runtime_layers/execution.py` — canonical EA and API signal contracts
- Signal contract: `signal_schema.py`
- Market/session guard: `session_clock.py`, `telegram_guard.py`
- Execution lifecycle: `execution_lifecycle.py`, `pine_signal_bridge.py`
- Legacy/Pine MT5 executor: `mt5/AlphaBuffalo_CloudEA_ExecutionOnly_v304.mq5`
- Isolated Python MT5 executor: `mt5/AlphaBuffalo_RailwayPythonEA_v100.mq5`
- Pine compatibility source: `tradingview/alpha_buff_gold_analyzer_v2_4.pine`

## Telegram Ownership

- Python production messages use `TELEGRAM_GROUP_CHAT_IDS` (legacy fallback:
  `NOTIFY_IDS`, `TELEGRAM_CHAT_IDS`, or `TELEGRAM_CHAT_ID`).
- Pine messages prefer the isolated `TELEGRAM_PINE_CHAT_IDS` destination.
- When no Pine room is configured, Pine falls back to
  `TELEGRAM_OWNER_CHAT_ID` (then `OWNER_CHAT_ID` or `ADMIN_ID`).
- Pine never falls back to the Python grouping room.
- With `ALPHA_SIGNAL_SOURCE=PYTHON`, `ALPHA_PINE_NOTIFICATION_ONLY=true`
  accepts Pine for its isolated Telegram destination without persisting an EA
  command or replacing the latest Python signal.
- `GET /telegram/status` reports the effective destination class without
  exposing tokens or chat IDs.

## Trading Runtime

- `engine_v4/router.py`
- `engine_v4/buy_engine.py`
- `engine_v4/sell_engine.py`
- `engine_v4/final_gate.py`
- `engine_v4/session_gate.py`
- `engine_v4/harmonic_bias_gate.py`
- `engine_v4/indicators.py`

The V4 engines share one signal schema. `SIGNAL` is a status; `BUY` and `SELL`
are directions. RR and directional price validation still decide EA readiness.

## Context and Newday

- `scenario_blueprint.py`
- `scenario_scanner.py`
- `decision_engine.py`
- `signal_composer.py`
- `harmonic_detector.py`
- `kivanc_vsaob.py`
- `scripts/daily_market_scan.py`
- `core/models/newday_market_map.py`

## Supported Checks

- `scripts/alpha_regression_suite.py`
- `scripts/regression_cases/engine_core.py`
- `scripts/regression_cases/lifecycle.py`
- `scripts/regression_cases/telegram.py`
- `scripts/regression_cases/prz_runtime.py`
- `scripts/test_python_execution_roundtrip.py`
- `scripts/test_pine_webhook_roundtrip.py`
- `scripts/test_pine_ea_bridge.py`
- `scripts/test_harmonic_webhook_gate.py`
- `scripts/test_harmonic_projection_bias.py`
- `scripts/test_harmonic_newday_route.py`
- `scripts/check_pine_v2_4.py`

Historical research, superseded EAs, experimental engines, and ad-hoc backtests
were removed from the production tree. They remain recoverable from Git history.
