import pandas as pd
from buy_signal_engine import BuySignalEngine


class DummyGate:
    def __init__(self, allowed=True):
        self.allowed = allowed


class DummySession:
    def __init__(self):
        self.session = "TEST"


def make_valid_row():
    return pd.DataFrame([{
        "Trend_1H_Up": True,
        "Diff": 10,
        "Swing_H": 100,
        "close": 95,
        "low": 94,
        "Bull_Sweep": True,
        "BB_Lower": 93,
        "BB_Upper": 110,
        "ATR14": 2
    }])


def test_buy_signal_success():
    engine = BuySignalEngine()
    df = make_valid_row()

    result = engine.evaluate(
        df=df,
        idx=0,
        session_info=DummySession(),
        gate_result=DummyGate(True)
    )

    assert result is not None
    assert result["direction"] == "BUY"


def test_buy_gate_block():
    engine = BuySignalEngine()
    df = make_valid_row()

    result = engine.evaluate(
        df=df,
        idx=0,
        session_info=DummySession(),
        gate_result=DummyGate(False)
    )

    assert result is None


def test_buy_trend_block():
    engine = BuySignalEngine()
    df = make_valid_row()
    df.loc[0, "Trend_1H_Up"] = False

    result = engine.evaluate(
        df=df,
        idx=0,
        session_info=DummySession(),
        gate_result=DummyGate(True)
    )

    assert result is None
