import os, json, logging
from supabase import create_client, Client

class DBManager:
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL")
        self.key = os.environ.get("SUPABASE_KEY")
        self.supabase: Client = create_client(self.url, self.key)
    
    def load_all_state(self):
        try:
            pivot = self.supabase.table("pivot_state").select("*").eq("id", 1).execute().data
            basket = self.supabase.table("basket_positions").select("*").eq("status", "open").execute().data
            fvgs = self.supabase.table("fvg_zones").select("*").eq("is_active", True).execute().data
            return {"pivot": pivot, "basket": basket, "fvgs": fvgs}
        except Exception as e:
            logging.error(f"DB Load Error: {e}")
            return None

    def save_pivot_state(self, locked_high, locked_low, trend_dir):
        return self.supabase.table("pivot_state").upsert({"id": 1, "locked_high": locked_high, "locked_low": locked_low, "trend_dir": trend_dir}).execute()

# สร้าง instance ที่ import ได้
db = DBManager()
