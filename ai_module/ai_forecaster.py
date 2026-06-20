import numpy as np

class MockForecaster:
    def __init__(self):
        print("[Mock Forecaster] Ready – using simple heuristics")

    def predict(self, price_window, sentiment_score, session="LONDON"):
        # price_window: (30, N_FEATURES) - idx 3 = 'Close'
        last_close = price_window[-1, 3]
        prev_close = price_window[-5, 3]
        trend = (last_close - prev_close) / prev_close if prev_close != 0 else 0
        direction_prob = np.clip(0.5 + trend * 8, 0.05, 0.95)
        direction_prob += sentiment_score * 0.05
        direction_prob = np.clip(direction_prob, 0.05, 0.95)
        return {
            'direction_prob': float(direction_prob),
            'pred_vol': 0.008,
            'pred_return': float(trend * 0.5)
        }
