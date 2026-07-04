import numpy as np
import pandas as pd
from typing import List, Dict


class ProbabilityCalibrator:
    """
    แปลง feature scores → probability ที่ calibrated จากผลจริง
    """

    def __init__(self):
        self.df = None
        self.prob_map = {}

    def load_data(self, trades: List[dict]):
        self.df = pd.DataFrame(trades)
        self.df["win"] = self.df["pnl"] > 0

    def calibrate_by_confidence_bucket(self):
        self.df["bucket"] = (self.df["confidence"] // 10) * 10
        grouped = self.df.groupby("bucket")
        for b, data in grouped:
            win_rate = data["win"].mean()
            n = len(data)
            smoothed = (win_rate * n + 0.5 * 10) / (n + 10)
            self.prob_map[int(b)] = smoothed * 100
        return self.prob_map

    def get_probability(self, confidence: float) -> float:
        bucket = int(confidence // 10) * 10
        return self.prob_map.get(bucket, 50.0)
