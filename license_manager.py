"""
license_manager.py — Alpha Buffalo v5.2 (Sprint License)
=========================================================
License Key System + Quota + Supabase

Plans:
  TRIAL  : 7 วัน, 3 signals/day, ฟรี
  BASIC  : 30 วัน, 10 signals/day, ฿990
  PRO    : 30 วัน, unlimited, ฿2,490
  IB     : 365 วัน, unlimited, ฟรี (VTMarkets sub)
  MASTER : ไม่มีวันหมด (Admin)

Supabase SQL (รันครั้งเดียว):
  create table licenses (
      key          text primary key,
      plan         text,
      customer     text,
      created_at   text,
      expires_at   text,
      active       boolean default true,
      signals_used integer default 0,
      quota_date   text default ''
  );

Telegram Admin Commands:
  /newlicense PRO ชื่อลูกค้า
  /newtrial ชื่อลูกค้า
  /revoke AB-PRO-XXXXXXXX
  /extend AB-PRO-XXXXXXXX 30
  /licenses
  /quota AB-PRO-XXXXXXXX

Changes from v5.1:
  [NEW] chk() ต่อ Supabase แทน env var อย่างเดียว
  [NEW] IB plan สำหรับ VTMarkets sub
  [NEW] extend_license() ต่ออายุ
  [NEW] list_licenses() ดูทั้งหมด
  [NEW] handle_admin_command() parser
  [NEW] format HTML สำหรับ Telegram
"""

import os
import uuid
from datetime import datetime, timezone, timedelta, date as _date
from dataclasses import dataclass
from typing import Optional

BKK = timezone(timedelta(hours=7))

PLANS = {
    "TRIAL":  {"days": 7,     "signals_day": 3,   "price_thb": 0,    "label": "Trial 7 วัน"},
    "BASIC":  {"days": 30,    "signals_day": 10,  "price_thb": 990,  "label": "Basic 30 วัน"},
    "PRO":    {"days": 30,    "signals_day": 999, "price_thb": 2490, "label": "Pro 30 วัน"},
    "IB":     {"days": 365,   "signals_day": 999, "price_thb": 0,    "label": "IB Sub VTMarkets"},
    "MASTER": {"days": 36500, "signals_day": 999, "price_thb": 0,    "label": "Master Admin"},
}

MASTER_LICENSES = set(
    k.strip() for k in os.getenv("VALID_LICENSES", "DEMO123").split(",") if k.strip()
)


@dataclass
class License:
    key:          str
    plan:         str
    customer:     str
    created_at:   str
    expires_at:   str
    active:       bool
    signals_used: int = 0
    quota_date:   str = ""


_cache: dict = {}
_quota: dict = {}


def _sb():
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        k   = os.getenv("SUPABASE_KEY", "")
        if url and k:
            return create_client(url, k)
    except Exception:
        pass
    return None


def _load(key: str) -> Optional[License]:
    if key in _cache:
        return _cache[key]
    sb = _sb()
    if not sb:
        return None
    try:
        res = sb.table("licenses").select("*").eq("key", key).execute()
        if not res.data:
            return None
        r = res.data[0]
        lic = License(
            key=r["key"], plan=r["plan"], customer=r.get("customer",""),
            created_at=r.get("created_at",""), expires_at=r.get("expires_at",""),
            active=r.get("active",True), signals_used=r.get("signals_used",0),
            quota_date=r.get("quota_date",""),
        )
        _cache[key] = lic
        return lic
    except Exception:
        return None


def _save(lic: License) -> bool:
    _cache[lic.key] = lic
    sb = _sb()
    if not sb:
        return False
    try:
        sb.table("licenses").upsert({
            "key": lic.key, "plan": lic.plan, "customer": lic.customer,
            "created_at": lic.created_at, "expires_at": lic.expires_at,
            "active": lic.active, "signals_used": lic.signals_used,
            "quota_date": lic.quota_date,
        }).execute()
        return True
    except Exception as e:
        print(f"License save error: {e}")
        return False


def chk(key: str) -> bool:
    """ใช้แทน lambda เดิมใน alpha_buffalo_signal.py"""
    if not key:
        return False
    return validate_license(key)["valid"]


def validate_license(key: str) -> dict:
    key = key.strip()
    if key in MASTER_LICENSES:
        return {"valid": True, "plan": "MASTER", "reason": "OK",
                "customer": "Admin", "expires_at": "Never", "signals_day": 999}
    lic = _load(key)
    if not lic:
        return {"valid": False, "plan": "", "reason": "License not found"}
    if not lic.active:
        return {"valid": False, "plan": lic.plan, "reason": "License inactive"}
    try:
        exp = datetime.strptime(lic.expires_at, "%Y-%m-%d").replace(tzinfo=BKK)
        if datetime.now(BKK) > exp:
            return {"valid": False, "plan": lic.plan, "reason": f"Expired: {lic.expires_at}"}
    except Exception:
        pass
    plan_info = PLANS.get(lic.plan, PLANS["BASIC"])
    return {"valid": True, "plan": lic.plan, "reason": "OK",
            "customer": lic.customer, "expires_at": lic.expires_at,
            "signals_day": plan_info["signals_day"]}


def create_license(plan: str = "BASIC", customer: str = "") -> License:
    plan = plan.upper()
    if plan not in PLANS:
        plan = "BASIC"
    key  = "AB-" + plan[:3] + "-" + str(uuid.uuid4())[:8].upper()
    now  = datetime.now(BKK)
    exp  = now + timedelta(days=PLANS[plan]["days"])
    lic  = License(key=key, plan=plan, customer=customer,
                   created_at=now.strftime("%Y-%m-%d"),
                   expires_at=exp.strftime("%Y-%m-%d"), active=True)
    _save(lic)
    return lic


def create_trial(customer: str = "") -> License:
    return create_license("TRIAL", customer)


def revoke_license(key: str) -> bool:
    lic = _load(key)
    if not lic:
        return False
    lic.active = False
    return _save(lic)


def extend_license(key: str, days: int = 30) -> bool:
    lic = _load(key)
    if not lic:
        return False
    try:
        exp = datetime.strptime(lic.expires_at, "%Y-%m-%d").replace(tzinfo=BKK)
        lic.expires_at = (exp + timedelta(days=days)).strftime("%Y-%m-%d")
        return _save(lic)
    except Exception:
        return False


def list_licenses(active_only: bool = True) -> list:
    sb = _sb()
    if not sb:
        return list(_cache.values())
    try:
        q   = sb.table("licenses").select("*")
        if active_only:
            q = q.eq("active", True)
        res = q.execute()
        return [License(key=r["key"], plan=r["plan"], customer=r.get("customer",""),
                        created_at=r.get("created_at",""), expires_at=r.get("expires_at",""),
                        active=r.get("active",True), signals_used=r.get("signals_used",0))
                for r in (res.data or [])]
    except Exception:
        return []


def check_quota(key: str) -> dict:
    if key in MASTER_LICENSES:
        return {"allowed": True, "used": 0, "limit": 999, "remaining": 999}
    result = validate_license(key)
    if not result["valid"]:
        return {"allowed": False, "used": 0, "limit": 0, "remaining": 0}
    limit = result.get("signals_day", 10)
    today = str(_date.today())
    quota = _quota.get(key, {"date": "", "count": 0})
    if quota["date"] != today:
        quota = {"date": today, "count": 0}
        _quota[key] = quota
    used = quota["count"]
    return {"allowed": used < limit, "used": used,
            "limit": limit, "remaining": max(0, limit - used), "reset_at": "00:00 BKK"}


def consume_quota(key: str) -> bool:
    if key in MASTER_LICENSES:
        return True
    today = str(_date.today())
    quota = _quota.get(key, {"date": "", "count": 0})
    if quota["date"] != today:
        quota = {"date": today, "count": 0}
    quota["count"] += 1
    quota["date"]   = today
    _quota[key]     = quota
    sb = _sb()
    if sb:
        try:
            sb.table("licenses").update(
                {"signals_used": quota["count"], "quota_date": today}
            ).eq("key", key).execute()
        except Exception:
            pass
    return True


def format_license_info(lic: License) -> str:
    p = PLANS.get(lic.plan, PLANS["BASIC"])
    days_left = ""
    try:
        exp = datetime.strptime(lic.expires_at, "%Y-%m-%d").replace(tzinfo=BKK)
        d   = (exp - datetime.now(BKK)).days
        days_left = f" ({max(0,d)} วันเหลือ)"
    except Exception:
        pass
    return (
        f"🔑 <b>License Info</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Key      : <code>{lic.key}</code>\n"
        f"Plan     : {lic.plan} — {p['label']}\n"
        f"Customer : {lic.customer or 'N/A'}\n"
        f"Expires  : {lic.expires_at}{days_left}\n"
        f"Active   : {'✅' if lic.active else '❌'}\n"
        f"Signals  : {p['signals_day']}/day\n"
        f"Price    : ฿{p['price_thb']:,}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Not financial advice."
    )


def format_quota_info(key: str) -> str:
    q  = check_quota(key)
    b  = int(q["used"] / max(q["limit"], 1) * 10)
    bar = "█" * b + "░" * (10 - b)
    return (
        f"📊 <b>Signal Quota</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Key      : {key[:14]}...\n"
        f"Used     : {q['used']}/{q['limit']}\n"
        f"[{bar}]\n"
        f"Remaining: {q['remaining']}\n"
        f"Status   : {'✅ OK' if q['allowed'] else '🚫 Exceeded'}\n"
        f"Reset    : {q.get('reset_at','00:00 BKK')}\n"
        f"━━━━━━━━━━━━━━━━━"
    )


def format_licenses_list(licenses: list) -> str:
    if not licenses:
        return "📋 ไม่มี active licenses"
    lines = ["📋 <b>Active Licenses</b>\n━━━━━━━━━━━━━━━━━"]
    for lic in licenses[:10]:
        try:
            exp = datetime.strptime(lic.expires_at, "%Y-%m-%d").replace(tzinfo=BKK)
            d   = max(0, (exp - datetime.now(BKK)).days)
            ds  = f"{d}d"
        except Exception:
            ds = "?"
        lines.append(f"• <code>{lic.key}</code> | {lic.plan} | {lic.customer or 'N/A'} | {ds}")
    if len(licenses) > 10:
        lines.append(f"...และอีก {len(licenses)-10}")
    return "\n".join(lines)


def handle_admin_command(text: str) -> str:
    """Parser สำหรับ Telegram admin commands"""
    parts = text.strip().split()
    cmd   = parts[0].lower() if parts else ""

    if cmd == "/newlicense":
        plan     = parts[1].upper() if len(parts) > 1 else "BASIC"
        customer = " ".join(parts[2:]) if len(parts) > 2 else ""
        return format_license_info(create_license(plan, customer))

    elif cmd == "/newtrial":
        customer = " ".join(parts[1:]) if len(parts) > 1 else ""
        return format_license_info(create_trial(customer))

    elif cmd == "/revoke":
        key = parts[1] if len(parts) > 1 else ""
        if not key:
            return "❌ ระบุ key: /revoke AB-PRO-XXXXXXXX"
        return f"{'✅ Revoked: '+key if revoke_license(key) else '❌ Not found: '+key}"

    elif cmd == "/extend":
        key  = parts[1] if len(parts) > 1 else ""
        days = int(parts[2]) if len(parts) > 2 else 30
        if not key:
            return "❌ ระบุ key: /extend AB-PRO-XXXXXXXX 30"
        return f"{'✅ Extended +'+str(days)+'d: '+key if extend_license(key,days) else '❌ Not found'}"

    elif cmd == "/licenses":
        return format_licenses_list(list_licenses(active_only=True))

    elif cmd == "/quota":
        key = parts[1] if len(parts) > 1 else "DEMO123"
        return format_quota_info(key)

    return "❓ ไม่รู้จักคำสั่ง"
