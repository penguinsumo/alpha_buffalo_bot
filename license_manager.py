"""
license_manager.py — Alpha Buffalo v5
License Key System

Features:
- UUID unique ต่อลูกค้า
- Expiry date
- Plan tiers (TRIAL/BASIC/PRO)
- Supabase storage
- API validation endpoint
"""

import os
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

BKK = timezone(timedelta(hours=7))

# ── Plans ─────────────────────────────────────────────────
PLANS = {
    "TRIAL": {
        "days":        7,
        "signals_day": 3,
        "symbols":     ["XAUUSD"],
        "price_usd":   0,
    },
    "BASIC": {
        "days":        30,
        "signals_day": 10,
        "symbols":     ["XAUUSD"],
        "price_usd":   29,
    },
    "PRO": {
        "days":        30,
        "signals_day": 999,
        "symbols":     ["XAUUSD", "EURUSD", "BTCUSD", "NAS100"],
        "price_usd":   79,
    },
}

# Master licenses (env var) — ไม่มีวันหมด
MASTER_LICENSES = set(os.getenv("VALID_LICENSES", "DEMO123").split(","))


@dataclass
class License:
    key:         str
    plan:        str
    customer:    str
    created_at:  str
    expires_at:  str
    active:      bool
    signals_used:int = 0


# ── In-memory store (ใช้ Supabase ใน production) ──────────
_licenses: dict = {}


def generate_key(plan: str = "BASIC", customer: str = "") -> License:
    """สร้าง License Key ใหม่"""
    key = "AB-" + plan[:3].upper() + "-" + str(uuid.uuid4())[:8].upper()

    now     = datetime.now(BKK)
    days    = PLANS.get(plan, PLANS["BASIC"])["days"]
    expires = now + timedelta(days=days)

    lic = License(
        key        = key,
        plan       = plan,
        customer   = customer,
        created_at = now.strftime("%Y-%m-%d"),
        expires_at = expires.strftime("%Y-%m-%d"),
        active     = True,
    )
    _licenses[key] = lic
    return lic


def validate_license(key: str) -> dict:
    """
    ตรวจสอบ License Key
    คืน {"valid": bool, "plan": str, "reason": str}
    """
    # Master licenses ไม่มีวันหมด
    if key in MASTER_LICENSES:
        return {"valid":True, "plan":"MASTER", "reason":"OK",
                "signals_day":999, "symbols":["ALL"]}

    # เช็คใน store
    lic = _licenses.get(key)
    if not lic:
        # ลอง load จาก Supabase
        lic = _load_from_supabase(key)

    if not lic:
        return {"valid":False, "plan":"", "reason":"License not found"}

    if not lic.active:
        return {"valid":False, "plan":lic.plan, "reason":"License inactive"}

    # เช็ค expiry
    try:
        exp = datetime.strptime(lic.expires_at, "%Y-%m-%d").replace(tzinfo=BKK)
        if datetime.now(BKK) > exp:
            return {"valid":False, "plan":lic.plan,
                    "reason":"License expired: " + lic.expires_at}
    except Exception:
        pass

    plan_info = PLANS.get(lic.plan, PLANS["BASIC"])
    return {
        "valid":       True,
        "plan":        lic.plan,
        "reason":      "OK",
        "customer":    lic.customer,
        "expires_at":  lic.expires_at,
        "signals_day": plan_info["signals_day"],
        "symbols":     plan_info["symbols"],
    }


def _load_from_supabase(key: str) -> Optional[License]:
    """โหลด license จาก Supabase"""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        k   = os.getenv("SUPABASE_KEY", "")
        if not url or not k: return None

        sb  = create_client(url, k)
        res = sb.table("licenses").select("*").eq("key", key).execute()
        if not res.data: return None

        row = res.data[0]
        lic = License(
            key         = row["key"],
            plan        = row["plan"],
            customer    = row.get("customer",""),
            created_at  = row.get("created_at",""),
            expires_at  = row.get("expires_at",""),
            active      = row.get("active", True),
            signals_used= row.get("signals_used", 0),
        )
        _licenses[key] = lic
        return lic
    except Exception:
        return None


def save_to_supabase(lic: License) -> bool:
    """บันทึก license ไปยัง Supabase"""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        k   = os.getenv("SUPABASE_KEY", "")
        if not url or not k: return False

        sb = create_client(url, k)
        sb.table("licenses").upsert({
            "key":          lic.key,
            "plan":         lic.plan,
            "customer":     lic.customer,
            "created_at":   lic.created_at,
            "expires_at":   lic.expires_at,
            "active":       lic.active,
            "signals_used": lic.signals_used,
        }).execute()
        return True
    except Exception as e:
        print("License save error: " + str(e))
        return False


def create_trial(customer: str = "") -> License:
    """สร้าง Trial license"""
    lic = generate_key("TRIAL", customer)
    save_to_supabase(lic)
    return lic


def create_license(plan: str, customer: str = "") -> License:
    """สร้าง License ตาม plan"""
    lic = generate_key(plan, customer)
    save_to_supabase(lic)
    return lic


def revoke_license(key: str) -> bool:
    """ยกเลิก license"""
    lic = _licenses.get(key)
    if lic:
        lic.active = False
        save_to_supabase(lic)
        return True
    return False


def format_license_info(lic: License) -> str:
    """Format สำหรับส่ง Telegram"""
    plan_info = PLANS.get(lic.plan, {})
    return (
        "🔑 License Info\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Key     : " + lic.key + "\n"
        "Plan    : " + lic.plan + "\n"
        "Customer: " + (lic.customer or "N/A") + "\n"
        "Expires : " + lic.expires_at + "\n"
        "Active  : " + ("✅" if lic.active else "❌") + "\n"
        "Signals : " + str(plan_info.get("signals_day","N/A")) + "/day\n"
        "Symbols : " + ", ".join(plan_info.get("symbols",[])) + "\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⚠️ Not financial advice. Trade at your own risk."
    )
