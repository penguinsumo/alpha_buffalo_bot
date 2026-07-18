# Alpha Buffalo Production Freeze

## Freeze Base

- Base branch: `v12-core`
- Base commit: `4cd39f9aba9fad3e7855b62bec7d2aaf802fb7f3`
- Production entry point: `alpha_buffalo_signal.py`
- Active EA: `AlphaBuffalo_CloudEA_ExecutionOnly_v304.mq5`

## Scope

This freeze removes legacy research code, duplicated CI, ad-hoc backtests,
superseded EA v303, unused providers/plugins, and generated macOS metadata.
It does not change trading rules, signal routing, Telegram behavior, API payloads,
or execution lifecycle behavior.

The open strategy experiment on `agent/baseline-default-policy` is intentionally
excluded. Future strategy work must start from a separate branch and pass the
supported checks listed in `FINAL_RUNTIME_MAP.md`.

All removed sources remain recoverable from Git history at the freeze base.
