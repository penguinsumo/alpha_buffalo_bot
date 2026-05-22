import os, logging
from supabase import create_client

class DBManager:
    def __init__(self):
        try:
            self.supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
        except: self.supabase = None
    
    def load_all_state(self):
        if not self.supabase: return None
        return {"status": "ok"}

db = DBManager()
