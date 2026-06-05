import pandas as pd
from kivanc_vsaob import calculate_kivanc_zone
from session_clock import get_market_session_info

def validate_trade_signal(current_price, fibo_levels, direction, has_score_or_pattern, session):
    if not has_score_or_pattern:
        return False

    if direction == 'BUY':
        if session == 'ASIA':
            return fibo_levels['1.000'] <= current_price <= fibo_levels['0.618']
        elif session in ['LONDON', 'NY', 'LONDON_NY_OVERLAP']:
            return fibo_levels['1.000'] <= current_price <= fibo_levels['0.786']
            
    elif direction == 'SELL':
        return fibo_levels['0.786'] <= current_price <= fibo_levels['1.000']

    return False

def generate_signal(df_m15, cascade_trend='BUY', has_score_or_pattern=True):
    current_price = df_m15['close'].iloc[-1]
    
    kivanc_data = calculate_kivanc_zone(df_m15, cascade_trend)
    if not kivanc_data['is_valid']:
        return {
            "signal": "NONE", 
            "zone_valid": False,
            "session_display": "INVALID_SWING"
        }
        
    session_info = get_market_session_info()
    current_session = session_info['session']
    
    is_valid_entry = validate_trade_signal(
        current_price, 
        kivanc_data['fibo_levels'], 
        cascade_trend, 
        has_score_or_pattern,
        current_session
    )
    
    signal_type = cascade_trend if is_valid_entry else "NONE"
    
    return {
        "signal": signal_type,
        "session": current_session,
        "session_display": session_info['display_msg'],
        "visual_sl": round(kivanc_data['visual_sl'], 3),
        "zone_valid": is_valid_entry,
        "current_price": current_price
    }
