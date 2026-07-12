# Alpha Buffalo EA Execution Contract

The Python service owns strategy, position state, risk gates, TP/BE decisions,
and HA5 exits. The EA is execution-only.

## Entry Flow

1. Poll `GET /signal/latest?key=...&symbol=XAU/USD`.
2. Open only when `ea.action=OPEN` and `ea.execution_state=READY`.
3. Use `ea.direction`, `ea.entry`, `ea.sl`, `ea.tp1`, and `ea.tp_final`.
4. After the broker confirms the fill, call `POST /execution/fill`:

```json
{
  "key": "LICENSE_KEY",
  "symbol": "XAUUSD",
  "signal_id": "VALUE_FROM_SIGNAL",
  "ticket": "BROKER_TICKET",
  "fill_price": 4100.25,
  "filled_at": "2026-07-12T01:00:00Z"
}
```

The service rejects fills that do not match the latest `READY` plan and
rejects a second active position for the same symbol.

## Management Flow

Poll `GET /execution/command?key=...&symbol=XAU/USD`. Supported actions are:

- `HOLD`: do nothing.
- `PARTIAL_CLOSE_MOVE_BE`: close `close_pct`, then set SL to `new_sl`.
- `CLOSE_ALL`: close all `close_pct` remaining size.

Every non-HOLD command contains `command_id`. The same ID is returned until
the EA ACKs it, so polling cannot cause duplicate execution.

After broker execution, call `POST /execution/ack`:

```json
{
  "key": "LICENSE_KEY",
  "symbol": "XAUUSD",
  "command_id": "VALUE_FROM_COMMAND",
  "success": true,
  "remaining_pct": 50,
  "r_multiple": 1.25,
  "acknowledged_at": "2026-07-12T01:30:00Z"
}
```

`r_multiple` is required when ACKing `CLOSE_ALL` so daily-loss and
consecutive-loss gates remain correct. A failed execution must ACK with
`success=false`; Python retains the same pending command for retry.

## Exit Priority

1. Hard SL.
2. TP2 final.
3. TP1 partial close and move remainder to BE.
4. Max-bars timeout.
5. Two completed opposite HA5 bars after the TP1/BE ACK.

BUY uses two red HA5 bars; SELL uses two green HA5 bars. The potentially live
last M5 candle is never used as HA confirmation. If M5 is unavailable, Python
returns HOLD and keeps SL/TP/timeout protection.

## Runtime State

Use `GET /execution/state?key=...&symbol=XAUUSD` for diagnostics. Set
`ALPHA_EXECUTION_STATE_FILE` to a path on a persistent Railway volume in
production. The default `/tmp/alpha_buffalo_execution_state.json` survives a
process restart only when that filesystem survives; it is not a substitute for
a configured persistent volume.

This repository contains no `.mq4` or `.mq5` source, so the MetaTrader project
must implement these HTTP calls and allow the service URL in MetaTrader's
WebRequest settings before live execution is enabled.
