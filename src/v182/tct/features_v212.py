from __future__ import annotations
import math
import numpy as np
import pandas as pd

def _f(value):
    try:
        x=float(value); return x if math.isfinite(x) else None
    except Exception: return None

def technical_features(frame:pd.DataFrame)->dict[str,object]:
    """Pure point-in-time features from OHLCV ending at the signal close."""
    if frame is None or frame.empty:return {}
    cols={str(c).lower():c for c in frame.columns}; needed={k:cols.get(k) for k in ('open','high','low','close','volume')}
    if needed['close'] is None or needed['high'] is None or needed['low'] is None:return {}
    h=pd.DataFrame(index=frame.index)
    for k,c in needed.items():h[k]=pd.to_numeric(frame[c],errors='coerce') if c is not None else np.nan
    h=h.dropna(subset=['close','high','low'])
    if len(h)<60:return {}
    c=h.close; ma20=c.rolling(20).mean(); ma50=c.rolling(50).mean(); ma200=c.rolling(200).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean(); rsi=100-100/(1+gain/loss.replace(0,np.nan))
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean(); macd=ema12-ema26; signal=macd.ewm(span=9,adjust=False).mean(); hist=macd-signal; hist_delta3=hist.diff(3)
    prev=c.shift(1); tr=pd.concat([(h.high-h.low).abs(),(h.high-prev).abs(),(h.low-prev).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); atr_pct=atr/c.replace(0,np.nan)*100; atr_exp=atr/atr.rolling(20).mean().replace(0,np.nan)
    std20=c.rolling(20).std(ddof=0); bb_width=4*std20/ma20.replace(0,np.nan)*100; bb_change=(bb_width/bb_width.shift(5)-1)*100; bb_rank=bb_width.rolling(60).rank(pct=True)*100
    vol=h.volume; v20=vol.rolling(20).mean(); rvol=vol/v20.replace(0,np.nan); vtrend=vol.rolling(5).mean()/v20.replace(0,np.nan)
    prior20=c.shift(1).rolling(20).max(); breakout=(c/prior20.replace(0,np.nan)-1)*100; low20=h.low.shift(1).rolling(20).min(); high20=h.high.shift(1).rolling(20).max(); range_pos=(c-low20)/(high20-low20).replace(0,np.nan)*100
    ll=h.low.rolling(14).min(); hh=h.high.rolling(14).max(); sk=(c-ll)/(hh-ll).replace(0,np.nan)*100; sd=sk.rolling(3).mean(); stoch=bool(len(sk)>=2 and pd.notna(sk.iloc[-2]) and pd.notna(sd.iloc[-2]) and pd.notna(sk.iloc[-1]) and pd.notna(sd.iloc[-1]) and sk.iloc[-2]<=sd.iloc[-2] and sk.iloc[-1]>sd.iloc[-1])
    gap=((h.open/prev)-1)*100 if h.open.notna().any() else pd.Series(np.nan,index=h.index)
    now=float(c.iloc[-1]); m20=_f(ma20.iloc[-1]); m50=_f(ma50.iloc[-1]); m200=_f(ma200.iloc[-1]) if len(c)>=200 else None; align=None if None in (m20,m50,m200) else bool(now>m20>m50>m200)
    return {'ma20_v212':m20,'ma50_v212':m50,'ma200_v212':m200,'trend_alignment_flag_v212':align,'rsi14_v212':_f(rsi.iloc[-1]),'macd_hist_v212':_f(hist.iloc[-1]),'macd_hist_delta3_v212':_f(hist_delta3.iloc[-1]),'atr14_pct_v212':_f(atr_pct.iloc[-1]),'atr_expansion_ratio_v212':_f(atr_exp.iloc[-1]),'bb_width_pct_v212':_f(bb_width.iloc[-1]),'bb_width_change5_pct_v212':_f(bb_change.iloc[-1]),'bb_width_percentile60_v212':_f(bb_rank.iloc[-1]),'rvol20_v212':_f(rvol.iloc[-1]),'volume_trend_5_20_v212':_f(vtrend.iloc[-1]),'breakout_20d_flag_v212':bool(breakout.iloc[-1]>0) if pd.notna(breakout.iloc[-1]) else None,'breakout_distance_pct_v212':_f(breakout.iloc[-1]),'close_position_20d_range_pct_v212':_f(range_pos.iloc[-1]),'stoch_bull_cross_flag_v212':stoch,'gap_up_pct_v212':_f(gap.iloc[-1])}
