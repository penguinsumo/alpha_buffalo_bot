
"""Simple License Manager (no supabase dependency)"""
import os
from datetime import datetime

class SimpleLicenseManager:
    def __init__(self):
        self.key = os.getenv("LICENSE_KEY", "DEMO123")
    
    def is_valid(self, key=None):
        if key is None:
            key = self.key
        return key in ["DEMO123", "ALPHA_BUFFALO_2026"]
    
    def check(self):
        return self.is_valid()


    def validate_key(self, key):
        return self.is_valid(key)

def get_license_manager():
    return SimpleLicenseManager()
