#!/usr/bin/env python3
"""Static contract check for the isolated Railway Python EA lane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "alpha_buffalo_signal.py").read_text(encoding="utf-8")
EA = (ROOT / "mt5" / "AlphaBuffalo_RailwayPythonEA_v100.mq5").read_text(
    encoding="utf-8"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def main() -> None:
    require(
        '@app.get("/execution/python/command")' in SERVICE,
        "FastAPI exposes the dedicated Python command route",
    )
    require(
        '"reason": "USE_DEDICATED_PYTHON_ENDPOINT"' in SERVICE,
        "legacy command lane cannot race the Python EA",
    )
    require(
        '"/execution/python/command?key="' in EA,
        "EA polls the dedicated production route",
    )
    require(
        'input string ClientId           = "RAILWAY_PYTHON_V1";' in EA,
        "EA declares an isolated client identity",
    )
    require(
        "input int    Magic              = 20260719;" in EA,
        "EA uses a distinct MT5 magic number",
    )
    require(EA.count("{") == EA.count("}"), "EA MQL blocks are balanced")
    require(
        'ApiUrl             = "https://alphabuffalobot-production.up.railway.app"'
        in EA,
        "EA points to the live alpha_buffalo_bot service",
    )
    require(
        'candidates = TELEGRAM_PINE_CHAT_IDS or TELEGRAM_OWNER_CHAT_IDS' in SERVICE,
        "Pine falls back to owner when no Pine room is configured",
    )
    require(
        'return list(TELEGRAM_CHAT_IDS)' in SERVICE,
        "Python grouping destinations remain isolated",
    )
    require(
        '"group_owner": "PYTHON"' in SERVICE,
        "Telegram status declares Python grouping ownership",
    )
    require(
        '"pine_group_fallback": False' in SERVICE,
        "Pine cannot fall back to the grouping room",
    )
    require(
        'relay = PineSignalBridge(None) if notification_only else pine_signal_bridge'
        in SERVICE,
        "Pine notification-only validation cannot persist an EA command",
    )
    require(
        '"execution_queued": False' in SERVICE,
        "Pine notification-only response declares that execution is disabled",
    )
    require(
        'return [chat_id for chat_id in candidates if chat_id not in group_ids]'
        in SERVICE,
        "misconfigured Pine destinations cannot overlap grouping IDs",
    )
    print("Summary: 14/14 Python EA and Telegram ownership checks passed")


if __name__ == "__main__":
    main()
