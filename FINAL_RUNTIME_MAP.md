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
- `runtime_layers/newday.py` — reads the map `daily_market_scan.py` already
  builds and exposes it at runtime via `GET /newday/map`. This was
  previously dead code: nothing in the running service read the map, and
  no scheduler ran the scan. **Wiring is done; scheduling is not.**
  `scripts/daily_market_scan.py` still needs an external trigger (a
  Railway cron service, or equivalent) running once after each daily
  close, writing into the same `ALPHA_MARKET_MAP_DIR` volume the API
  process reads from. Until that is configured, `/newday/map` returns
  `available=false` and every consumer degrades to "no newday context"
  exactly as if the feature were absent.
- `fundamental/` — DXY, Fear & Greed, COT, and news-calendar context ported
  from clean v5's `context_engine.py`/`plugin_*.py`, exposed via
  `GET /context/fundamental`. v12-core had no fundamental layer before
  this. Every source fails closed to a neutral value on network error.
- `runtime_layers/hourly_stats.py` — adaptive win-rate-by-UTC-hour
  tracker ported from clean v5's `trade_manager.py` (`HourlyStats`),
  recorded automatically on every closed trade in
  `execution_lifecycle.py`, exposed via `GET /execution/hourly-stats`,
  persisted in the same state file as positions/risk.

All three additions above are diagnostic/context only -- nothing in
`engine_v4` reads them, so none can become a trend/EMA/BOS-style entry
gate (see the Red Lines section of `ALPHA_FUSION_CONTRACT.md`). The BUY
off-hours policy in `engine_v4/session_gate.py` is the one gate-adjacent
change: it now supports an opt-in soft mode
(`ALPHA_BUY_SOFT_SESSION_GATE=true`) that trades the historical hard block
for a reduced `risk_adjustment` (`ALPHA_BUY_OFFHOURS_RISK_MULTIPLIER`,
default `0.5`) instead of vetoing the setup outright. Default behavior
(flag unset) is unchanged from the historical hard block.

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
