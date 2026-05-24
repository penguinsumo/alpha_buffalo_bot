"""
alpha_buffalo_signal.py — Alpha Buffalo v5 (Cloud-Driven)
"""
import os
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from signal_engine import compute_signal, signal_to_dict

# 1. จัดการ Webhook ที่ Railway เรียกใช้ (แก้ 502 Error)
class AlphaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alpha Buffalo V5 Online")

    def do_POST(self):
        self.send_response(200)
        self.end_headers()

# 2. ฟังก์ชันหลักสำหรับรัน Server และประมวลผล
def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), AlphaHandler)
    print(f"Starting server on port {port}...")
    server.serve_forever()

# 3. Main Logic
if __name__ == "__main__":
    import threading
    
    # รัน Server ใน Background thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # ใส่ส่วน Logic เดิมของคุณที่นี่ (เช่น signal_loop)
    print("System Ready")
    while True:
        # ใส่ signal_loop ของคุณที่ทำงานวนลูปที่นี่
        import time
        time.sleep(60)
