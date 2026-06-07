import os
from datetime import datetime, date
from supabase import create_client, Client

class LicenseManager:
    def __init__(self):
        self.supabase: Client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
        self.cache = {}  # simple cache for current request

    def validate_license(self, license_key: str) -> bool:
        """ตรวจสอบ license key ว่ายัง active และไม่หมดอายุ"""
        if license_key in self.cache:
            return self.cache[license_key]
        try:
            resp = self.supabase.table("licenses").select("*").eq("key", license_key).execute()
            if not resp.data:
                return False
            lic = resp.data[0]
            if not lic.get("active", False):
                return False
            expires = lic.get("expires_at")
            if expires and datetime.fromisoformat(expires) < datetime.now():
                return False
            self.cache[license_key] = True
            return True
        except Exception as e:
            print(f"License check error: {e}")
            return False  # fail-closed

    def use_signal(self, license_key: str) -> bool:
        """เพิ่มจำนวนสัญญาณที่ใช้ (quota) ถ้าเกิน quota ให้ return False"""
        try:
            # ดึงข้อมูล license
            resp = self.supabase.table("licenses").select("*").eq("key", license_key).execute()
            if not resp.data:
                return False
            lic = resp.data[0]
            # ตรวจสอบ quota รายวัน (optional)
            today = date.today().isoformat()
            quota_date = lic.get("quota_date")
            if quota_date != today:
                # รีเซ็ต signals_used
                self.supabase.table("licenses").update({"signals_used": 0, "quota_date": today}).eq("key", license_key).execute()
                signals_used = 0
            else:
                signals_used = lic.get("signals_used", 0)
            # ตัวอย่าง: จำกัด 5 สัญญาณต่อวัน (adjustable)
            if signals_used >= 5:
                print(f"License {license_key} exceeded daily quota")
                return False
            # increment
            self.supabase.table("licenses").update({"signals_used": signals_used + 1}).eq("key", license_key).execute()
            return True
        except Exception as e:
            print(f"License quota error: {e}")
            return True  # fail-open (เพื่อไม่ให้กระทบ signal)

# singleton
_license_manager = None

def get_license_manager():
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager()
    return _license_manager
