import os
from supabase import create_client

class DBManager:
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL")
        self.key = os.environ.get("SUPABASE_KEY")
        self.supabase = None
        if self.url and self.key:
            try:
                self.supabase = create_client(self.url, self.key)
            except Exception as e:
                print(f"DEBUG: DB Init Error: {e}")

    def load_all_state(self):
        if not self.supabase: return "DB Not Initialized"
        try:
            # เปลี่ยนจาก 'states' เป็น 'bot_status' ตามที่ Error แนะนำครับ
            data = self.supabase.table("bot_status").select("*").execute()
            return "Connection OK (Data Retrieved)"
        except Exception as e:
            return f"Error: {e}"

db = DBManager()
