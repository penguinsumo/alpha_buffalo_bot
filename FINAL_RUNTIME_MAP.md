# Alpha Buffalo Final Runtime Map

This map defines which files are currently active in production and which files are research-only.

## Production Entry Point

`alpha_buffalo_signal.py`

Responsibilities:

- FastAPI app
- market data fetch/cache
- v12 scanner/composer adapter
- engine_v4 runtime bridge
- EA payload mapping
- public Telegram formatting
- background cloud scan loop

## Active V4 Runtime

`engine_v4/indicators.py`

- Bollinger Bands
- ATR
- HA / pinbar proxies
- Pine PRZ support/resistance
- VSA pressure proxy
- V4 buy/sell setup flags
- lower-zone sell block
- upper-zone buy block

`engine_v4/router.py`

- scans recent bars
- calls buy/sell engines
- ranks location-first candidates
- enriches V4 vs V5 journey metadata

`engine_v4/buy_engine.py`

- BUY candidate creation
- lower-zone V4 setup
- local SL
- BB mid / BB upper target path
- RR computation

`engine_v4/sell_engine.py`

- SELL candidate creation
- lower-zone SELL veto
- upper-zone V4 setup
- continuation-only trend filter
- local SL
- BB lower / signal TP target path
- RR computation

`engine_v4/final_gate.py`

- session / timing gate
- BUY session constraints
- SELL permissive gate unless risk state blocks it

`engine_v4/session_gate.py`

- legacy gate result model
- kept because active engines import `GateResult`

## Active Higher-Level Context

`scenario_scanner.py`

- market phase/context
- harmonic/newday market map context
- log line explaining scanner state

`signal_composer.py`

- compatibility composer for v12 payload
- should remain adapter-level, not primary V4 entry logic

`decision_engine.py`

- v12 decision context
- can produce fallback WAIT context
- must not replace engine_v4 as V4 entry source

## Customer Output

`alpha_buffalo_signal.py`

- `format_telegram_signal`
- `format_telegram_trend_update`

These functions are public-facing. Keep internals hidden.

## Research / Mine Later

`trade_manager.py`

- valuable V4 scalp / V5 runner lifecycle
- port after regression tests

`v10_modules/layer4_risk_gate.py`

- ATR/chop/daily loss/consecutive loss concepts
- port concepts, not file

`v10_modules/layer5_position_sizer.py`

- risk-based sizing
- drawdown-based leverage reduction
- port concepts, not file

`v10_modules/layer8_performance.py`

- equity curve / max DD / walk-forward score
- good candidate for telemetry

`research_engine_v112_v4_pro.py`

- useful idea: vectorized backtest + Monte Carlo
- not production
- needs secrets removed before reuse

## Older Branch / Zip Sources

Remote branches available through local git:

- `main`
- `feature/cleanup-signal-v5`
- `feature/engine-v4`
- `sell-micro-v4-2`
- `v12-core`

Local beta source:

- `/Users/mac/AlphaBuffalo_v5.3_Beta`

Zip sources:

- `alpha_buffalo_bot-feature-engine-v4.zip`
- `alpha_buffalo_bot-sell-micro-v4-2.zip`
- `alpha_buffalo_bot-main.zip`
- `alpha_buffalo_bot-12-core*.zip`

Use them as references only.

## Baseline Evidence

`BASELINE_TESTS.md`

- records the old Risk Gate `811 -> 770 -> 41` baseline
- separates it from `trade_evidence.json`
- documents why the old live-data scripts must be frozen into fixtures before CI
