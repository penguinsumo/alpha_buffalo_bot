"""
Confluence Engine — Weighted Intelligence Aggregator (ConfluenceContract)
"""
from typing import List
from core.contracts.confluence_contract import ConfluenceContract
from core.models.detector_output import DetectorOutput
from core.models.confluence import ConfluenceScore

class ConfluenceEngine(ConfluenceContract):
    def run(self, detectors: List[DetectorOutput]) -> ConfluenceScore:
        if not detectors:
            return ConfluenceScore(
                direction_bias="NEUTRAL",
                confluence_score=0.0,
                aligned_detectors=[],
                conflicting_detectors=[]
            )

        # คำนวณ Simple Average (จะถูกแทนที่ด้วย Weighted Formula ในภายหลัง)
        total_strength = sum(d.strength * d.confidence for d in detectors if d.is_valid)
        valid_count = sum(1 for d in detectors if d.is_valid)
        avg_score = total_strength / valid_count if valid_count > 0 else 0.0

        # หา Direction Bias จาก Detector ที่มี strength สูงสุด
        best_detector = max(detectors, key=lambda d: d.strength * d.confidence)
        direction_bias = best_detector.direction

        # แยก Aligned vs Conflicting
        aligned = [d for d in detectors if d.direction == direction_bias]
        conflicting = [d for d in detectors if d.direction != direction_bias]

        return ConfluenceScore(
            direction_bias=direction_bias,
            confluence_score=avg_score,
            aligned_detectors=aligned,
            conflicting_detectors=conflicting
        )
