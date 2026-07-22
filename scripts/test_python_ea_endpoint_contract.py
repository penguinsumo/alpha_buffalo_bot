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
    print("Summary: 7/7 dedicated Python EA contract checks passed")


if __name__ == "__main__":
    main()
