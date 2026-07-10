# Alpha Buffalo Baseline Tests

This file records the known baseline evidence that should be preserved while
finishing v12-core. Older branches contain useful test scripts, but most of
them depend on live market data and therefore must be treated carefully.

## Baseline A: Risk Gate 41

Source files:

- `origin/sell-micro-v4-2:verify_41_trades.py`
- `origin/sell-micro-v4-2:verify_41_reasons.py`
- `origin/feature/engine-v4:verify_41_trades.py`
- `origin/feature/engine-v4:verify_41_reasons.py`

Expected historical result from those scripts:

- Total generated trades before Risk Gate: `811`
- Trades executed after Risk Gate: `770`
- Trades skipped by Risk Gate: `41`

Risk settings used by the baseline:

- Initial balance: `10000`
- Risk per trade: `0.0075`
- Max contracts: `10`
- Daily drawdown stop: `0.03`
- Max consecutive losses: `5`

Important caveat:

The original scripts fetch `XAU/USD` 15 minute data from TwelveData for 90 days,
then keep the last 60 days:

```python
df = fetch_twelvedata("XAU/USD", "15min", 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
```

That means the `811 -> 770 -> 41` count is not a deterministic CI test unless
the source candles are frozen. If it is run against fresh data, the count can
move even when the engine code is correct.

Before promoting this baseline into CI:

1. Export the exact 15 minute XAU/USD candle fixture used for the baseline.
2. Store the fixture without API keys or provider-specific secrets.
3. Port the runner to read the fixture from disk.
4. Assert the generated/executed/skipped counts.
5. Keep live TwelveData fetches as a manual research command only.

## Baseline B: Trade Evidence Snapshot

Source file:

- `trade_evidence.json`

Current snapshot summary:

- Records: `502`
- Wins: `292`
- Losses: `8`
- Breakeven: `202`
- Win rate excluding breakeven: `97.33%`
- Win rate including breakeven: `58.17%`
- Net PnL: `42939.00`
- Gross profit: `43681.14`
- Gross loss: `742.14`
- Profit factor: `58.86`

Breakdown:

- Side: `SELL=459`, `BUY=43`
- Session: `NY=250`, `ASIA=160`, `LONDON=92`
- Entry mode: `V4_SELL_BASE=413`, `V5_SELL_CANDIDATE=46`, `BUY_BASE=43`
- Exit reason: `BE=202`, `V4_BB_LOWER=157`, `V5_SIGNAL_TP=96`, `TP=27`,
  `TRAIL=13`, `SL=6`, `TIME=1`

This snapshot is useful for checking that performance evidence still exists,
but it should not be confused with Baseline A. It is not the 41-trade Risk Gate
proof.

## Current CI Guardrail

The active offline guardrail is:

- `scripts/alpha_regression_suite.py`

It protects the final v12 behavior without network access:

- Upper-zone V4 SELL is not blocked by bullish trend context.
- Lower-zone support blocks fresh SELL.
- Upper-zone resistance blocks fresh BUY.
- Low RR candidate remains visible but EA waits.
- CHoCH/BOS promotes V5 journey.
- No CHoCH/BOS remains V4 scalp/range.
- Public Telegram output hides engine internals.

Use this suite for normal CI. Use Baseline A only after the candle fixture is
frozen.
