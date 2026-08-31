"""HEBDO AT META - backtest B v2, isolation ticker/date et exécution conservatrice."""
import pandas as pd
import numpy as np
from typing import Dict


def _detect_B_one(df_daily: pd.DataFrame) -> pd.DataFrame:
    df=df_daily.copy(); required={'close','volume','high','low'}; missing=required-set(df.columns)
    if missing: raise ValueError(f"BLOCK_DATA_B_DETECT: missing {sorted(missing)}")
    for c in required: df[c]=pd.to_numeric(df[c],errors='coerce')
    if not np.isfinite(df[list(required)].to_numpy(dtype=float)).all(): raise ValueError('BLOCK_DATA_B_DETECT: non-finite OHLCV')
    if 'date' in df.columns: dates=pd.to_datetime(df['date'],errors='coerce',utc=True)
    elif isinstance(df.index,pd.DatetimeIndex): dates=pd.to_datetime(df.index,errors='coerce',utc=True)
    else: raise ValueError('BLOCK_DATA_B_DETECT: explicit date or DatetimeIndex required')
    if pd.isna(dates).any() or pd.Index(dates).duplicated().any(): raise ValueError('BLOCK_DATA_B_DETECT: invalid/duplicate dates')
    df=df.assign(_b_date=dates).sort_values('_b_date').drop(columns=['_b_date'])
    df['volume_avg20']=df['volume'].rolling(20,min_periods=20).mean(); df['volume_std20']=df['volume'].rolling(20,min_periods=20).std()
    df['sma20']=df['close'].rolling(20,min_periods=20).mean(); df['sma200']=df['close'].rolling(200,min_periods=200).mean()
    prev=df['close'].shift(1); tr=pd.concat([(df['high']-df['low']),(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    df['atr_14']=tr.rolling(14,min_periods=14).mean(); df['atr_14_pct']=df['atr_14']/df['close'].replace(0,np.nan)
    df['vol_z']=(df['volume']-df['volume_avg20'])/df['volume_std20'].replace(0,np.nan); df['ret_1d']=df['close'].pct_change()
    df['B1_vol']=(df['vol_z']>3.0)&(df['ret_1d']<-0.015)&(df['close']<df['sma20'])
    df['B2_daily']=df['B1_vol'].shift(1).fillna(False).astype(bool); df['B_signal']=df['B1_vol']|df['B2_daily']
    df['B_signal_type']=np.where(df['B1_vol'],'B1_VOL',np.where(df['B2_daily'],'B2_DAILY_J+1','NONE'))
    return df


def detect_B_v2(df_daily:pd.DataFrame)->pd.DataFrame:
    if df_daily.empty: return df_daily.copy()
    work=df_daily.copy(); work['_b_order']=np.arange(len(work))
    if 'ticker' not in work.columns: return _detect_B_one(work).sort_values('_b_order').drop(columns=['_b_order'])
    if work['ticker'].isna().any() or work['ticker'].astype(str).str.strip().eq('').any(): raise ValueError('BLOCK_DATA_B_DETECT: invalid ticker')
    parts=[_detect_B_one(g) for _,g in work.groupby('ticker',sort=False)]
    return pd.concat(parts).sort_values('_b_order').drop(columns=['_b_order'])


def _valid_ohlc_row(row: pd.Series):
    vals=pd.to_numeric(row[['open','high','low','close']],errors='coerce')
    if vals.isna().any() or not np.isfinite(vals.to_numpy(dtype=float)).all(): return None,'BLOCK_DATA_OHLC_NONFINITE'
    o,h,l,c=map(float,[vals['open'],vals['high'],vals['low'],vals['close']])
    if min(o,h,l,c)<=0: return None,'BLOCK_DATA_OHLC_NONPOSITIVE'
    if l>h or o<l or o>h or c<l or c>h: return None,'BLOCK_DATA_OHLC_INCONSISTENT'
    return (o,h,l,c),None


def compute_true_26w_pnl(entry_price:float,hist_126d:pd.DataFrame,stop_pct:float=0.09,expected_days:int=126)->Dict:
    """Valide les barres séquentiellement: une donnée postérieure à la sortie ne peut invalider le trade."""
    block={'pnl':None,'hit_stop':None,'day_stop':None,'mae':None,'mfe':None,'exit_price':None}
    entry=pd.to_numeric(pd.Series([entry_price]),errors='coerce').iloc[0]
    if hist_126d is None or len(hist_126d)==0 or pd.isna(entry) or not np.isfinite(float(entry)) or entry<=0: return {**block,'block_reason':'BLOCK_DATA'}
    required={'open','high','low','close'}; missing=required-set(hist_126d.columns)
    if missing: return {**block,'block_reason':f"BLOCK_DATA_OHLC_MISSING_{'_'.join(sorted(missing))}"}
    if not (0<stop_pct<1) or expected_days<1: return {**block,'block_reason':'BLOCK_DATA_BACKTEST_PARAMETERS'}
    entry=float(entry); stop_level=entry*(1-stop_pct); lows_seen=[]; highs_seen=[]
    horizon=min(len(hist_126d),expected_days)
    for pos in range(horizon):
        vals,err=_valid_ohlc_row(hist_126d.iloc[pos])
        if err: return {**block,'block_reason':err}
        open_px,high_px,low_px,close_px=vals
        lows_seen.append(low_px); highs_seen.append(high_px)
        if low_px<=stop_level:
            exit_px=open_px if open_px<stop_level else stop_level
            return {'pnl':float(exit_px/entry-1),'hit_stop':True,'day_stop':pos+1,'mae':float(min(lows_seen)/entry-1),'mfe':float(max(highs_seen)/entry-1),'exit_price':float(exit_px),'block_reason':None}
    if len(hist_126d)<expected_days: return {**block,'block_reason':f'BLOCK_DATA_INCOMPLETE_HORIZON_{len(hist_126d)}d'}
    # Toutes les barres jusqu'à l'horizon ont déjà été validées dans la boucle.
    exit_px=float(pd.to_numeric(hist_126d.iloc[expected_days-1]['close'],errors='coerce'))
    return {'pnl':float(exit_px/entry-1),'hit_stop':False,'day_stop':expected_days,'mae':float(min(lows_seen)/entry-1),'mfe':float(max(highs_seen)/entry-1),'exit_price':exit_px,'block_reason':None}


def _signal_date(idx,sig):
    raw=sig.get('date',idx)
    try:
        ts=pd.to_datetime(raw,errors='coerce',utc=True); return None if pd.isna(ts) else ts
    except Exception: return None


def _future_path(df_prices:pd.DataFrame,ticker:str,signal_date,forward:int)->pd.DataFrame:
    if signal_date is None: return pd.DataFrame()
    prices=df_prices.copy()
    if 'ticker' in prices.columns: prices=prices[prices['ticker'].astype(str)==str(ticker)]
    elif ticker: return pd.DataFrame()
    if 'date' in prices.columns: dates=pd.to_datetime(prices['date'],errors='coerce',utc=True)
    elif isinstance(prices.index,pd.DatetimeIndex): dates=pd.to_datetime(prices.index,errors='coerce',utc=True)
    else: return pd.DataFrame()
    if pd.isna(dates).any(): return pd.DataFrame()
    prices=prices.assign(_bt_date=dates).sort_values('_bt_date')
    if prices['_bt_date'].duplicated().any(): return pd.DataFrame()
    return prices[prices['_bt_date']>signal_date].drop(columns=['_bt_date']).iloc[:forward]


def run_backtest_B_v2(df_signals:pd.DataFrame,df_prices:pd.DataFrame,stop_pct=0.09,forward=126)->pd.DataFrame:
    if 'B_signal' not in df_signals.columns: raise ValueError('BLOCK_DATA_BACKTEST: B_signal missing')
    if forward<1: raise ValueError('BLOCK_DATA_BACKTEST: forward must be >= 1')
    results=[]
    for idx,sig in df_signals[df_signals['B_signal'].fillna(False).astype(bool)].iterrows():
        entry=sig.get('close'); ticker=str(sig.get('ticker','')).strip(); signal_date=_signal_date(idx,sig); hist=_future_path(df_prices,ticker,signal_date,forward)
        res=compute_true_26w_pnl(entry,hist,stop_pct,expected_days=forward)
        if hist.empty and res.get('block_reason')=='BLOCK_DATA': res['block_reason']='BLOCK_DATA_PRICE_PATH'
        res.update({'date':signal_date,'ticker':ticker,'entry':entry,'type':sig.get('B_signal_type','')}); results.append(res)
    return pd.DataFrame(results)
