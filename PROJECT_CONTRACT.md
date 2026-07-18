# Alpha Buffalo Project Contract

This file is the source-of-truth contract for finishing Alpha Buffalo.
Do not override it with older branch behavior unless a regression test proves the change is intentional.

## Production Base

- Runtime repo: `penguinsumo/alpha_buffalo_bot`
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

### Session Kivanc / Deep Sweep Entry

- Asia uses the Kivanc `0.618-0.786` reaction window.
- London/NY use the deeper V12 `0.720-0.886` reaction window.
- A sweep through `0.886` to the `1.00` boundary is setup-only, never an immediate entry.
- The sweep wick becomes a VSA pressure/absorption wall.
- BUY requires a later break of the wall candle high and reclaim inside `0.886-0.720`.
- SELL mirrors BUY: later break of the wall candle low and reclaim inside `0.720-0.886`.
- A normal zone pinbar is also setup-only; entry requires a later break of its high/low.
- SL is outside the preserved sweep/pinbar wall, not inside the wick.

### Position Lifecycle

Python owns the position state and EA executes commands only.

1. EA opens only `action=OPEN` and `execution_state=READY`.
2. EA confirms the actual fill to `POST /execution/fill`.
3. At TP1 Python emits one idempotent `PARTIAL_CLOSE_MOVE_BE` command.
4. After the EA ACK, TP1 is durable, remaining SL is at entry, and HA5 trailing is armed.
5. BUY closes the remainder after two completed red HA5 bars; SELL uses two completed green HA5 bars.
6. Hard SL, TP2 final, and max-bars timeout remain active. Missing M5 data means HOLD, never a guessed HA exit.
7. EA ACKs every management command through `POST /execution/ack`; a command is retried with the same ID until ACK.

Runtime endpoints:

- `GET /execution/state`
- `GET /execution/command`
- `POST /execution/fill`
- `POST /execution/ack`

The runtime permits one active position per symbol and blocks a second open while it is managing that position.
The exact EA request/ACK payloads and persistent-state requirement are defined
in `EA_EXECUTION_CONTRACT.md`.

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

### Telegram Market-Closed Gate

- Every automated Telegram path must fail closed before making a network call.
- XAU weekend closure follows New York time with DST: Friday 17:00 ET through
  Sunday 18:00 ET.
- Intraday `session=CLOSED` also blocks all signal and trend messages.
- Full-day exchange/broker holidays are configured as Bangkok dates with
  `ALPHA_MARKET_CLOSED_DATES=YYYY-MM-DD,YYYY-MM-DD`.
- `ALPHA_FORCE_MARKET_CLOSED=true` is the emergency global kill switch.

## Integration Contract

Older branches and zip files contain useful ideas. They must be mined through tests, not copied directly.

Before porting any old module:

1. Write or extend `scripts/alpha_regression_suite.py`.
2. Prove current v12 behavior still passes.
3. Port the minimum concept.
4. Run py_compile and regression suite.
5. Deploy only after production payload shape is unchanged or intentionally versioned.

## Baseline Evidence

Known baseline evidence is recorded in `BASELINE_TESTS.md`.

- The `811 -> 770 -> 41` Risk Gate result is a historical live-data baseline.
- It must not be treated as deterministic CI until the candle fixture is frozen.
- `trade_evidence.json` is a separate performance evidence snapshot, not the
  same test as the 41-trade Risk Gate proof.

## Fusion Contract

`ALPHA_FUSION_CONTRACT.md` defines how older branches should be mined into
v12-core.

- Port proof harness, risk accounting, and evidence fields from older baselines.
- Do not port trend-first PRZ entry gates.
- Any future merge must preserve PRZ/location-first V4 entry behavior.
