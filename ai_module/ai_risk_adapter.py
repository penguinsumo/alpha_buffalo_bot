import numpy as np

class AIRiskAdapter:
    def __init__(self, forecaster, sentiment_engine=None):
        self.forecaster = forecaster
        self.sentiment = sentiment_engine

    def get_adjusted_risk(self, entry_price, current_volatility, signal_direction,
                          session, price_window, last_sentiment_score=0.0):
        ai_pred = self.forecaster.predict(price_window, last_sentiment_score, session)
        dir_prob = ai_pred['direction_prob']
        ai_vol = ai_pred['pred_vol']
        ai_return = ai_pred['pred_return']
        use_ai = dir_prob > 0.65 and abs(ai_return) > 0.001
        if use_ai and signal_direction * ai_return > 0:
            sl_mult = 1.8
            tp_dist = abs(ai_return) * entry_price
        else:
            sl_mult = 2.0
            tp_dist = current_volatility * 2
        sl_price = entry_price - signal_direction * ai_vol * entry_price * sl_mult
        tp_price = entry_price + signal_direction * tp_dist
        be_trigger = entry_price + signal_direction * ai_vol * entry_price * 0.7
        ai_score = (dir_prob - 0.5) * 20 * signal_direction
        return {
            'sl_price': sl_price,
            'tp_price': tp_price,
            'be_trigger': be_trigger,
            'ai_score': np.clip(ai_score, -10, 10),
            'use_ai': use_ai
        }
