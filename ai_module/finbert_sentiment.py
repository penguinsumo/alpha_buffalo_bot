import numpy as np

class FinbertSentiment:
    """Mock Sentiment Engine – ใช้ราคาปิดจำลอง sentiment"""
    def __init__(self):
        print("[Mock FinBERT] Ready – using price-based sentiment")

    def get_score(self, symbol, current_price, prev_price):
        if prev_price == 0:
            return 0.0
        change = (current_price - prev_price) / prev_price
        return np.clip(change * 10, -1.0, 1.0)
