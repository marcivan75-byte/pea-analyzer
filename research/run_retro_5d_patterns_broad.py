from __future__ import annotations

import math
import numpy as np
import pandas as pd

from research import retro_5d_patterns as r
from research import run_retro_5d_patterns as exact
from v182.hebdo.meta_price_history import load_2010_2026

BASE_ADD = r.add_features
ORIG_MATCH = exact._orig_matched

EXTRA_FEATURES = [
    # Momentum / acceleration
    'ret2_pct','ret15_pct','ret40_pct','ret60_pct','mom_acc_5_20','mom_acc_10_20',
    # Trend / moving averages
    'close_sma10_pct','close_sma100_pct','close_sma200_pct','sma10_slope5_pct','sma20_slope10_pct','sma100_slope10_pct','sma200_slope20_pct',
    'above_sma10','above_sma100','above_sma200','ma20_gt_50','ma50_gt_200',
    # Oscillators / trajectories
    'rsi7','rsi21','rsi7_d3','rsi7_d5','rsi21_d3','rsi21_d5','stoch28_k','stoch28_d','williams14','williams28',
    # MACD alternate / crosses
    'macd_fast_hist','macd_fast_hist_d3','macd_fast_hist_d5','macd_cross_std','macd_cross_fast',
    # Volatility / compression
    'atr7_pct','atr20_pct','vol10_pct','vol60_pct','bb_width50_pct','bb_z20','bb_z50','vol_compress_10_60',
    # Volume / liquidity
    'rvol5','rvol10','rvol60','volume_ratio20_60','obv_slope5_norm','obv_slope20_norm',
    # Price structure / breakout
    'dist_high120_pct','dist_high252_pct','dist_low20_pct','dist_low60_pct','dist_low120_pct','dist_low252_pct',
    'pos_range20','pos_range60','pos_range120','pos_range252','breakout60_flag','breakout120_flag','breakout252_flag',
    # Candle / gaps / reversal
    'gap_pct','close_location_pct','up_days5','up_days10','reversal3','reversal5',
    # Mean reversion / risk-adjusted momentum
    'zclose10','zclose20','zclose50','ret20_over_vol20','ret60_over_vol60',
]

r.FEATURES = list(dict.fromkeys(r.FEATURES + EXTRA_FEATURES))


def _ema_rsi(close: pd.Series, n: int, ticker: pd.Series) -> pd.Series:
    prev = close.groupby(ticker).shift(1)
    delta = close - prev
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.groupby(ticker).transform(lambda s: s.ewm(alpha=1/n, adjust=False, min_periods=n).mean())
    al = loss.groupby(ticker).transform(lambda s: s.ewm(alpha=1/n, adjust=False, min_periods=n).mean())
    return 100.0 - 100.0 / (1.0 + ag / al.replace(0, np.nan))


def broad_add_features(df: pd.DataFrame) -> pd.DataFrame:
    x = BASE_ADD(df)
    x = x.sort_values(['ticker','date']).reset_index(drop=True)
    t = x['ticker']
    g = x.groupby('ticker', group_keys=False)
    c = pd.to_numeric(x['close'], errors='coerce')
    h = pd.to_numeric(x['high'], errors='coerce')
    l = pd.to_numeric(x['low'], errors='coerce')
    o = pd.to_numeric(x['open'], errors='coerce')
    v = pd.to_numeric(x['volume'], errors='coerce')
    prev = g['close'].shift(1)
    dret = c / prev - 1.0

    for n in [2,15,40,60]:
        x[f'ret{n}_pct'] = (c / g['close'].shift(n) - 1.0) * 100.0
    x['mom_acc_5_20'] = x['ret5_pct'] - x['ret20_pct']/4.0
    x['mom_acc_10_20'] = x['ret10_pct'] - x['ret20_pct']/2.0

    mas = {}
    for n in [10,20,50,100,200]:
        mas[n] = g['close'].transform(lambda s, n=n: s.rolling(n, min_periods=n).mean())
    x['close_sma10_pct'] = (c/mas[10]-1)*100
    x['close_sma100_pct'] = (c/mas[100]-1)*100
    x['close_sma200_pct'] = (c/mas[200]-1)*100
    x['sma10_slope5_pct'] = (mas[10] / mas[10].groupby(t).shift(5)-1)*100
    x['sma20_slope10_pct'] = (mas[20] / mas[20].groupby(t).shift(10)-1)*100
    x['sma100_slope10_pct'] = (mas[100] / mas[100].groupby(t).shift(10)-1)*100
    x['sma200_slope20_pct'] = (mas[200] / mas[200].groupby(t).shift(20)-1)*100
    x['above_sma10'] = (c > mas[10]).astype(float)
    x['above_sma100'] = (c > mas[100]).astype(float)
    x['above_sma200'] = (c > mas[200]).astype(float)
    x['ma20_gt_50'] = (mas[20] > mas[50]).astype(float)
    x['ma50_gt_200'] = (mas[50] > mas[200]).astype(float)

    x['rsi7'] = _ema_rsi(c, 7, t)
    x['rsi21'] = _ema_rsi(c, 21, t)
    for n in [7,21]:
        for lag in [3,5]:
            x[f'rsi{n}_d{lag}'] = x[f'rsi{n}'] - x.groupby('ticker')[f'rsi{n}'].shift(lag)

    low14 = g['low'].transform(lambda s: s.rolling(14, min_periods=14).min())
    high14 = g['high'].transform(lambda s: s.rolling(14, min_periods=14).max())
    low28 = g['low'].transform(lambda s: s.rolling(28, min_periods=28).min())
    high28 = g['high'].transform(lambda s: s.rolling(28, min_periods=28).max())
    x['williams14'] = -100*(high14-c)/(high14-low14).replace(0,np.nan)
    x['stoch28_k'] = 100*(c-low28)/(high28-low28).replace(0,np.nan)
    x['stoch28_d'] = x.groupby('ticker')['stoch28_k'].transform(lambda s: s.rolling(3,min_periods=3).mean())
    x['williams28'] = -100*(high28-c)/(high28-low28).replace(0,np.nan)

    ema6 = g['close'].transform(lambda s: s.ewm(span=6,adjust=False,min_periods=6).mean())
    ema13 = g['close'].transform(lambda s: s.ewm(span=13,adjust=False,min_periods=13).mean())
    mf = ema6-ema13
    msf = mf.groupby(t).transform(lambda s: s.ewm(span=5,adjust=False,min_periods=5).mean())
    x['macd_fast_hist'] = mf-msf
    x['macd_fast_hist_d3'] = x['macd_fast_hist']-x.groupby('ticker')['macd_fast_hist'].shift(3)
    x['macd_fast_hist_d5'] = x['macd_fast_hist']-x.groupby('ticker')['macd_fast_hist'].shift(5)
    x['macd_cross_std'] = ((x['macd_hist']>0)&(x.groupby('ticker')['macd_hist'].shift(1)<=0)).astype(float)
    x['macd_cross_fast'] = ((x['macd_fast_hist']>0)&(x.groupby('ticker')['macd_fast_hist'].shift(1)<=0)).astype(float)

    tr = pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    for n in [7,20]:
        atr = tr.groupby(t).transform(lambda s,n=n: s.rolling(n,min_periods=n).mean())
        x[f'atr{n}_pct'] = 100*atr/c
    for n in [10,60]:
        x[f'vol{n}_pct'] = dret.groupby(t).transform(lambda s,n=n: s.rolling(n,min_periods=n).std(ddof=0))*math.sqrt(252)*100
    sd20 = g['close'].transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))
    sd50 = g['close'].transform(lambda s:s.rolling(50,min_periods=50).std(ddof=0))
    x['bb_width50_pct'] = 100*4*sd50/mas[50]
    x['bb_z20'] = (c-mas[20])/sd20.replace(0,np.nan)
    x['bb_z50'] = (c-mas[50])/sd50.replace(0,np.nan)
    x['vol_compress_10_60'] = x['vol10_pct']/x['vol60_pct'].replace(0,np.nan)

    for n in [5,10,60]:
        vm = g['volume'].transform(lambda s,n=n:s.rolling(n,min_periods=n).mean())
        x[f'rvol{n}'] = v/vm.replace(0,np.nan)
    v20 = g['volume'].transform(lambda s:s.rolling(20,min_periods=20).mean())
    v60 = g['volume'].transform(lambda s:s.rolling(60,min_periods=60).mean())
    x['volume_ratio20_60'] = v20/v60.replace(0,np.nan)
    obv_step = np.sign(c-prev).fillna(0)*v
    obv = obv_step.groupby(t).cumsum()
    x['obv_slope5_norm'] = (obv-obv.groupby(t).shift(5))/v20.replace(0,np.nan)
    x['obv_slope20_norm'] = (obv-obv.groupby(t).shift(20))/v20.replace(0,np.nan)

    for n in [20,60,120,252]:
        hp = g['high'].transform(lambda s,n=n:s.shift(1).rolling(n,min_periods=n).max())
        lp = g['low'].transform(lambda s,n=n:s.shift(1).rolling(n,min_periods=n).min())
        if n in [120,252]: x[f'dist_high{n}_pct']=(c/hp-1)*100
        x[f'dist_low{n}_pct']=(c/lp-1)*100
        x[f'pos_range{n}']=100*(c-lp)/(hp-lp).replace(0,np.nan)
        if n in [60,120,252]: x[f'breakout{n}_flag']=(c>=hp).astype(float)

    x['gap_pct'] = (o/prev-1)*100
    x['close_location_pct'] = 100*(c-l)/(h-l).replace(0,np.nan)
    x['up_days5'] = (dret>0).groupby(t).transform(lambda s:s.rolling(5,min_periods=5).sum())
    x['up_days10'] = (dret>0).groupby(t).transform(lambda s:s.rolling(10,min_periods=10).sum())
    x['reversal3'] = x['ret1_pct']-x['ret3_pct']/3
    x['reversal5'] = x['ret1_pct']-x['ret5_pct']/5
    for n in [10,20,50]:
        sd = g['close'].transform(lambda s,n=n:s.rolling(n,min_periods=n).std(ddof=0))
        x[f'zclose{n}']=(c-mas[n])/sd.replace(0,np.nan)
    x['ret20_over_vol20'] = x['ret20_pct']/x['vol20_pct'].replace(0,np.nan)
    x['ret60_over_vol60'] = x['ret60_pct']/x['vol60_pct'].replace(0,np.nan)
    return x


def broad_retained(df: pd.DataFrame) -> pd.DataFrame:
    exact._FULL = df
    valid_future = np.isfinite(df['close_t5']) & (df['close_t5']>0)
    clean_start = (df['close']>=1.0) & (df['volume']>=5000)
    no_extreme = df['ret_fwd5_pct']<=50.0
    no_round = ~exact.exact_round_ratio_suspect(df['future_ratio'])
    out = df.loc[valid_future & clean_start & no_extreme & no_round].copy()
    out['winner_5d'] = out['ret_fwd5_pct']>20.0
    return out


def broad_matched(univ: pd.DataFrame, episodes: pd.DataFrame, n_controls: int=3) -> pd.DataFrame:
    ok = np.isfinite(episodes[r.MATCH_FEATURES].to_numpy(float)).all(axis=1)
    return ORIG_MATCH(univ, episodes.loc[ok].copy(), n_controls=n_controls)

r.add_features = broad_add_features
r.retained_universe = broad_retained
r.winner_episodes = exact.exact_winner_episodes
r.matched_controls = broad_matched

if __name__ == '__main__':
    df = load_2010_2026(
        'inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet',
        'inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json',
        'data/cache/actions',
    )
    r.main(df, 'outputs/retro_5d_patterns_broad')
