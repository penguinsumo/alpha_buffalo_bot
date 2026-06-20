import numpy as np
from config.ai_config import AI_ENABLED
from ai_module import MockForecaster, FinbertSentiment, AIRiskAdapter
import importlib

forecaster = None
sentiment_engine = None
ai_adapter = None

def init_ai():
    global forecaster, sentiment_engine, ai_adapter
    if AI_ENABLED:
        forecaster = MockForecaster()
        sentiment_engine = FinbertSentiment()
        ai_adapter = AIRiskAdapter(forecaster, sentiment_engine)
        print("[AI] Mock AI activated")
    else:
        print("[AI] AI disabled")

def patch_signal_composer():
    import signal_composer as sc
    original = sc.SignalComposer.compose
    def new_compose(self, *args, **kwargs):
        signal = original(self, *args, **kwargs)
        if signal is None or not AI_ENABLED:
            return signal
        try:
            symbol = getattr(signal, 'symbol', 'XAUUSD')
            tf = getattr(signal, 'timeframe', 'H1')
            if hasattr(self, 'data_provider'):
                window = self.data_provider.get_last_n_candles(symbol, tf, 30)
                if window is not None:
                    current = getattr(signal, 'entry_price', window[-1,3])
                    prev = window[-2,3] if len(window)>1 else window[-1,3]
                    sent = sentiment_engine.get_score(symbol, current, prev)
                    sess = self.session_manager.current_session() if hasattr(self, 'session_manager') else 'LONDON'
                    risk = ai_adapter.get_adjusted_risk(
                        entry_price=current,
                        current_volatility=getattr(signal,'atr',0.008),
                        signal_direction=getattr(signal,'direction',1),
                        session=sess,
                        price_window=window,
                        last_sentiment_score=sent
                    )
                    signal.ai_risk = risk
        except Exception as e:
            print(f"[AI] Patch error: {e}")
        return signal
    sc.SignalComposer.compose = new_compose
    print("[AI] signal_composer patched")

def patch_trade_manager():
    import trade_manager as tm
    # ลองเรียกใช้ wrapper ที่เราแทรกไว้ (มันจะทำงานเมื่อ module โหลด)
    # อาจต้อง reload
    importlib.reload(tm)
    print("[AI] trade_manager checked – if auto-wrapper exists, it's active.")
    # ไม่ต้องทำอะไรเพิ่ม; auto-wrapper จะ handle เองตอน runtime

def patch_score_manager():
    import score_manager as sm
    if hasattr(sm, 'ScoreManager'):
        original = sm.ScoreManager.calculate
        def new_calc(self, signal):
            score = original(self, signal)
            if AI_ENABLED and hasattr(signal, 'ai_risk'):
                score += np.clip(signal.ai_risk.get('ai_score',0), -10, 10)
            return score
        sm.ScoreManager.calculate = new_calc
        print("[AI] score_manager (class) patched")
    elif hasattr(sm, 'calculate'):
        original = sm.calculate
        def new_calc(signal):
            score = original(signal)
            if AI_ENABLED and hasattr(signal, 'ai_risk'):
                score += np.clip(signal.ai_risk.get('ai_score',0), -10, 10)
            return score
        sm.calculate = new_calc
        print("[AI] score_manager (function) patched")

def apply_all_patches():
    init_ai()
    if AI_ENABLED:
        patch_signal_composer()
        patch_trade_manager()
        patch_score_manager()
        print("[AI] All patches applied. AI is LIVE.")
    else:
        print("[AI] AI disabled.")
