import requests
import pandas as pd
import time
from pathlib import Path

class TwelveDataProvider:
    def __init__(self, api_key=None):
        self.api_key = api_key if api_key != "CSV" else None
        self.use_csv = (api_key == "CSV") or (not api_key)

    def _fetch_twelvedata_api(self, symbol, interval, outputsize=100):
        """ใช้โค้ดของคุณโดยตรง"""
        print(f"📥 กำลังเชื่อมต่อ Twelve Data API เพื่อดึงข้อมูล {interval}...")
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol.replace("/", ""),  # Twelve Data ใช้ XAUUSD ไม่ใช่ XAU/USD
            "interval": interval,
            "apikey": self.api_key,
            "outputsize": outputsize,
            "format": "JSON"
        }
        
        response = requests.get(url, params=params)
        if response.status_code != 200:
            print(f"❌ HTTP Error: {response.status_code}")
            return pd.DataFrame()
            
        data = response.json()
        if "status" in data and data["status"] == "error":
            print(f"❌ API Error: {data['message']}")
            return pd.DataFrame()
            
        if "values" not in data:
            print("❌ ไม่พบข้อมูล (No values returned)")
            return pd.DataFrame()
            
        df = pd.DataFrame(data["values"])
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
            
        df = df.iloc[::-1]  # จากใหม่ไปเก่า -> เก่าไปใหม่
        # เพิ่มคอลัมน์ Volume ถ้าไม่มี
        if 'volume' in df.columns:
            df.rename(columns={'volume': 'Volume'}, inplace=True)
        else:
            df['Volume'] = 0
        # เปลี่ยนชื่อคอลัมน์เป็น Capitalize (Open, High, Low, Close)
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
        print(f"✅ เชื่อมต่อสำเร็จ ดึงข้อมูล {interval} มาได้ {len(df)} แท่ง")
        return df

    def _load_csv_data(self, symbol, timeframe):
        """อ่านข้อมูลจาก CSV ในโฟลเดอร์ data/"""
        filename = f"data/{symbol}_{timeframe}.csv"
        path = Path(filename)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {filename}")
        df = pd.read_csv(path, parse_dates=['time'], index_col='time')
        # เปลี่ยนชื่อให้ตรง: open->Open, high->High ...
        df.columns = [col.capitalize() for col in df.columns]
        if 'Volume' not in df.columns:
            df['Volume'] = 0
        return df

    def get_historical(self, symbol="XAUUSD", timeframe="H1", start=None, end=None, outputsize=100):
        """
        ดึงข้อมูลย้อนหลัง – ถ้าใช้ API key จริงจะเรียก API,
        ถ้าเป็น CSV mode (หรือไม่มี key) จะอ่านจากไฟล์ CSV
        """
        if self.use_csv or not self.api_key:
            print(f"📂 ใช้ข้อมูลจาก CSV: {symbol}_{timeframe}.csv")
            return self._load_csv_data(symbol, timeframe)
        else:
            return self._fetch_twelvedata_api(symbol, timeframe, outputsize)
