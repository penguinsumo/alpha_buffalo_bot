# Alpha Buffalo Project Contract

This file is the source-of-truth contract for finishing Alpha Buffalo.
Do not override it with older branch behavior unless a regression test proves the change is intentional.

## Production Base

- Runtime repo: `/Users/mac/alpha_buffalo_bot`
- Branch: `v12-core`
- Procfile: `web: uvicorn alpha_buffalo_signal:app --host 0.0.0.0 --port ${PORT}`
- Production service: `alphabuffalobot-production.up.railway.app`

## Core Trading Contract

Alpha Buffalo is location-first, not trend-first.

### V4 BUY Entry

BUY V4 can exist when:

- PRZ Support / lower location is active
- BB lower edge or equivalent lower reaction is active
- HA or pinbar bullish confirmation is present
- VSA buy pressure wins over sell pressure
- levels are directionally valid

RR decides execution readiness. A low-RR candidate may be visible for diagnostics, but EA must stay `WAIT` when `rr_ok=false`.

### V4 SELL Entry

SELL V4 can exist when:

- PRZ Resistance / upper location is active
- BB upper edge or equivalent upper rejection is active
- HA or pinbar bearish confirmation is present
- VSA sell pressure wins over buy pressure
- levels are directionally valid

Trend context must not block a valid upper-zone V4 SELL. Trend can score, label, or manage the trade, but it must not become the first gate for V4 location entry.

### Hard Directional Blocks

- Lower BB / PRZ Support + bullish PA/VSA must not open fresh SELL.
- Upper BB / PRZ Resistance + bearish PA/VSA must not open fresh BUY.

### BOS / CHoCH

BOS or CHoCH is a promotion condition, not a V4 entry requirement.

- BOS/CHoCH passed: promote to V5 journey.
- BOS/CHoCH not passed: keep V4 scalp/range management.

### RR Gate

- `TRADE_MIN_RR` defaults to `1.5`.
- `rr_ok=false` means EA action must be `WAIT`.
- A candidate with `rr_ok=false` may still populate `engine_v4` so the runtime can explain why it is waiting.

## Customer Message Contract

Telegram customer-facing output must not expose engine internals.

Do not show:

- `engine_v4`
- `V4_SELL_PINE_PRZ_VSA`
- `SELL_CF_READY`
- `BUY_CF_READY`
- `V5_*`
- raw VSA/BOS diagnostic state
- confluence formula details

Customer output should show only:

- asset
- BUY/SELL visual marker
- public type such as `V4_SESSION`
- entry
- SL zone
- TP1 / TP2
- public watch label such as `WAIT SETUP 🔴 SELL`

## Integration Contract

Older branches and zip files contain useful ideas. They must be mined through tests, not copied directly.

Before porting any old module:

1. Write or extend `scripts/alpha_regression_suite.py`.
2. Prove current v12 behavior still passes.
3. Port the minimum concept.
4. Run py_compile and regression suite.
5. Deploy only after production payload shape is unchanged or intentionally versioned.

