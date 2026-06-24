def create_signal(direction="HOLD", confidence=0.0, source="system"):
    return {
        "direction": direction,
        "confidence": float(confidence),
        "source": str(source)
    }
