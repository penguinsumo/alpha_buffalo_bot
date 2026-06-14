
import yfinance as yf
import pandas as pd

def fetch_vix_data(start_date=None, end_date=None, interval='1h'):
    if start_date is None: start_date = pd.Timestamp.now() - pd.Timedelta(days=7)
    if end_date is None: end_date = pd.Timestamp.now()
    vix = yf.download('^VIX', start=start_date, end=end_date, interval=interval, progress=False)
    if isinstance(vix.columns, pd.MultiIndex): vix.columns = vix.columns.get_level_values(0)
    vix.index = pd.to_datetime(vix.index).tz_convert(None)
    vix = vix[~vix.index.duplicated(keep='first')].sort_index()
    return vix[['Close']].rename(columns={'Close': 'VIX'})

def get_current_vix():
    try:
        vix = yf.download('^VIX', period='1d', interval='5m', progress=False)
        if len(vix) > 0: return float(vix['Close'].iloc[-1])
    except: pass
    return None
