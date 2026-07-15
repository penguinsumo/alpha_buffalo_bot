# Alpha Buffalo Fusion Contract

This file captures how the proven baseline is blended into v12-core without
breaking the PRZ/Harmonic confirmation and execution contracts.

## Final Strategy Spine

Alpha Buffalo v12-core is baseline-default, with asymmetric evidence-based
entry rules.

The engine order is:

1. SELL uses the historical H1/EMA + bearish sweep/upper-BB baseline.
2. BUY requires its historical trend/sweep baseline and the current PRZ/PA/VSA setup together.
3. A confirmed harmonic D/PRZ may override direction only when the matching local setup is active.
4. Apply RR for execution readiness.
5. Promote to V5 only when BOS/CHoCH/structure confirms.
6. Take TP1, move the remainder to BE, then trail with two closed opposite HA5 bars.
7. Keep one canonical API/EA schema for both directions.

## Kivanc Wall/Reclaim State Machine

- Asia location: `0.618-0.786`.
- London/NY location: `0.720-0.886`.
- Premium overlap: `0.720-0.786`.
- A London/NY move to the `1.00` boundary may arm a deep sweep wall when PRZ,
  BB edge, pinbar/wick, and winning VSA pressure are present.
- The `1.00` candle cannot enter. A later candle must break the wall toward the
  trade and close back in `0.886-0.720` before the setup is executable.
- BUY/SELL are strict mirrors and share the same API schema.
- Without real volume, the evidence is labelled a VSA pressure/absorption
  proxy; it is not claimed as confirmed exchange volume.

## End-to-End Exit Contract

- TP1: partial close and move the remainder to BE.
- TP2: close all remaining size.
- HA5 trailing: enabled only after the TP1/BE command is ACKed.
- BUY trailing exit: two completed red HA5 bars.
- SELL trailing exit: two completed green HA5 bars.
- Hard SL and max-bars timeout are Python-owned fallback exits.
- Commands are idempotent and remain pending until the EA ACKs them.
- Risk permissions use closed-trade R accounting for daily loss and
  consecutive-loss gates; they do not choose trade direction.

## PRZ Rule That Must Survive Every Refactor

Support side (normal BUY confirmation):

- baseline H1/EMA/sweep permission
- PRZ Support + BB Lower/lower reaction
- HA/Pinbar bullish confirmation
- VSA_BUY > VSA_SELL
- RR >= `TRADE_MIN_RR`
- result: BUY V4 entry

Resistance side (harmonic SELL override confirmation):

- PRZ Resistance + BB Upper/upper rejection
- HA/Pinbar bearish confirmation
- VSA_SELL > VSA_BUY
- RR >= `TRADE_MIN_RR`
- result: SELL V4 entry

BOS/CHoCH does not create V4 entry. It promotes a V4 setup into V5 journey.
The normal SELL baseline can enter without a Pine PRZ flag; a counter-trend
SELL cannot.

## What The Baseline Branches Contribute

`sell-micro-v4-2` and `feature/engine-v4` contribute useful proof machinery:

- 15 minute signal replay
- session-aware gate calls
- simulated exits for BUY and SELL
- per-session risk state
- daily drawdown stop
- consecutive-loss stop
- the historical `811 -> 770 -> 41` Risk Gate proof

The correct blend restores the proven SELL entry filter, keeps BUY dual-
confirmed by the current PRZ setup, and rejects the old single-latest-bar
router and non-frozen live-data proof.

## What v12-core Already Owns

`engine_v4/indicators.py`

- Pine PRZ support/resistance ranges
- BB/PRZ confluence flags
- HA and pinbar confirmation
- VSA buy/sell pressure proxy
- V4 BUY/SELL setup flags
- lower-zone SELL veto
- upper-zone BUY veto
- CHoCH/BOS promotion flags

`engine_v4/buy_engine.py`

- baseline trend/sweep plus lower-zone BUY candidate
- 1.5 ATR baseline SL; local PRZ wall SL for harmonic override
- BB mid/upper cashflow path
- RR visibility

`engine_v4/sell_engine.py`

- baseline downtrend/EMA/sweep SELL candidate
- lower-zone SELL veto
- 1.5 ATR baseline SL; local PRZ wall SL for harmonic override
- Fib 0.72 / next PRZ target path
- RR visibility

`engine_v4/router.py`

- recent setup selection
- V4 vs V5 journey metadata
- candidate ranking

`alpha_buffalo_signal.py`

- production bridge
- EA payload mapping
- public Telegram formatting

## Best Pieces To Mine Next

### 1. Frozen Baseline Replay

Convert the old `verify_41_*` scripts into a deterministic replay that reads a
frozen candle fixture instead of live TwelveData. Then assert:

- generated trades before Risk Gate
- executed trades after Risk Gate
- skipped trades by daily DD / consecutive loss
- skipped trades by session

This should become CI only after the fixture is frozen.

### 2. Risk State Telemetry

Bring forward the useful accounting concepts:

- per-session equity
- daily equity start
- daily DD stop reason
- consecutive loss stop reason
- skipped trade count

Do not let this decide PRZ entry direction. It should decide execution
permission and explain why execution is blocked.

### 3. Evidence Fields

Keep evidence fields that help audit customer behavior:

- entry RR
- entry-to-SL points
- entry-to-TP points
- exit mode
- session quality gate
- wick/body ratios
- V5 quality score

Keep these private in API/logs. Do not expose them in customer Telegram output.

### 4. V5 Sniper Ideas

Older v5/sniper code can be mined for scoring and lifecycle ideas:

- Kivanc/VSA OB as context
- harmonic PRZ as higher-timeframe map
- micro sweep as local timing
- TP1/TP2 refinement

Do not port basket/martingale behavior into production without a separate risk
contract and regression suite.

## Red Lines

- Never allow plain PRZ contact to override the baseline direction.
- Never allow a forming/unconfirmed harmonic to open the D reversal.
- Never let BOS/CHoCH become a V4 entry requirement.
- Never use Telegram internals as proof of signal correctness.
- Never change the canonical BUY/SELL API schema by strategy profile.

## Test Contract

Every future AI session should run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpha_pycache python3 -m py_compile alpha_buffalo_signal.py engine_v4/*.py scripts/alpha_regression_suite.py
PYTHONPYCACHEPREFIX=/private/tmp/alpha_pycache python3 scripts/alpha_regression_suite.py
```

The regression suite must keep proving:

- baseline SELL passes only with downtrend/EMA/sweep evidence
- baseline BUY also requires the current PRZ setup
- a confirmed harmonic D is the only counter-trend override
- lower support blocks fresh SELL
- upper resistance blocks fresh BUY
- RR below minimum keeps EA waiting
- BOS/CHoCH promotes to V5
- no BOS/CHoCH stays V4 scalp/range
- public Telegram hides internal logic
- deep `1.00` sweep requires a later reclaim before entry
- Kivanc pinbar requires a later high/low break
- TP1/BE and HA5 exits mirror correctly for BUY and SELL
- active-position commands are idempotent and block duplicate opens
