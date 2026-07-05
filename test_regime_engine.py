import pytest
from regime_engine import classify_regime, MarketRegime, RegimeState

class TestClassifyRegime:
    @pytest.mark.parametrize("adx, slope", [(26, 0.02), (30, -0.03), (25.1, 0.0101)])
    def test_trending_clear(self, adx, slope):
        result = classify_regime(atr_ratio=1.0, adx=adx, ema_slope=slope)
        assert result.regime == MarketRegime.TRENDING
        assert result.confidence == 0.8
        assert 0.0 < result.trend_strength <= 1.0

    def test_ranging_low_adx(self):
        result = classify_regime(atr_ratio=1.0, adx=19.9, ema_slope=0.02)
        assert result.regime == MarketRegime.RANGING

    def test_high_volatility_atr_high(self):
        result = classify_regime(atr_ratio=1.6, adx=22.0, ema_slope=0.0)
        assert result.regime == MarketRegime.HIGH_VOLATILITY
        assert result.volatility_score == pytest.approx(1.0)

    def test_low_volatility_atr_low(self):
        result = classify_regime(atr_ratio=0.3, adx=None, ema_slope=0.0)
        assert result.regime == MarketRegime.LOW_VOLATILITY
        assert result.volatility_score == pytest.approx(0.3)

    def test_adx_none_unknown(self):
        result = classify_regime(atr_ratio=1.0, adx=None, ema_slope=0.0)
        assert result.regime == MarketRegime.UNKNOWN
        assert result.confidence == 0.4

    def test_clamped_values(self):
        result = classify_regime(atr_ratio=2.5, adx=30.0, ema_slope=0.1)
        assert 0.0 <= result.trend_strength <= 1.0
        assert result.volatility_score == 1.0
