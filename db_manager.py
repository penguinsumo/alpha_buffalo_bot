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
        # ทดสอบการเชื่อมต่อ
        if not self.supabase: return "DB Not Initialized"
        try:
            # สมมติว่ามีตารางชื่อ states
            data = self.supabase.table("states").select("*").execute()
            return "Connection OK"
        except Exception as e:
            return f"Error: {e}"

db = DBManager()
