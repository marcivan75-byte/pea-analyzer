"""Research-only V10: focused optimisation bank for Block E overheating.

WIP=1. Frozen: entry models, standing protective stop 9%, early false-positive
block, and D-01 reversal anchor. Only Block E criteria/thresholds/weights are
varied. The bank is deterministic and hypothesis-driven (no random search).

The objective is not to maximise one in-sample metric. Candidates are ranked on
risk integrity and robustness across ALL/12/18/24/36M, then RR/PF/net mean and
winner preservation. RR > 4 remains a target, never a claim unless robustly met.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import itertools, json, math, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES,summarize
from .at_weekly_exit_opt_v7_protective_stop import fixed_stop_fill
from .at_weekly_exit_opt_v8_profit_reversal_blocks import add_indicators,last_daily_change_by_week,evidence,fp_confirm,robust_score,trade

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V10_OVERHEAT_WEIGHT_BANK.json'
STOP_PCT=9.0; FP_WEEKS=2; FP_FLOOR=-1.5
D_CFG={'family':['daily_drop','bb_reentry','adx_decay','rsi_rev','stoch_rev','psar_break','sma20_break','sma50_break'],'activation_pct':5.0,'score':1,'daily_drop_pct':4.0}

# V9 winner is an anchor only; V10 may retain or reject each E criterion.
V9_ANCHOR_PROFILE={'rsi':80.,'stoch':80.,'bb_ext':1.00,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.}

CRITERIA=['rsi','stoch','high52','bb','sma20','sma50','adx','accel','persistence']
HYPOTHESES={
 'H1_CORE_OSC':['rsi','stoch','persistence'],
 'H2_BREAKOUT':['high52','bb','accel'],
 'H3_EXTENSION':['bb','sma20','sma50','high52'],
 'H4_TREND_EXHAUST':['adx','rsi','stoch','sma20'],
 'H5_CORE_PLUS_52':['rsi','stoch','high52','persistence'],
 'H6_OSC_EXTENSION':['rsi','stoch','bb','sma20','persistence'],
 'H7_FULL_NO_SMA50':['rsi','stoch','high52','bb','sma20','adx','accel','persistence'],
 'H8_FULL':['rsi','stoch','high52','bb','sma20','sma50','adx','accel','persistence'],
}

# Interpretable weight hypotheses. Missing criteria are zeroed by the family mask.
WEIGHT_SCHEMES={
 'W_EQUAL':{'rsi':1,'stoch':1,'high52':1,'bb':1,'sma20':1,'sma50':1,'adx':1,'accel':1,'persistence':1},
 'W_OSC_2X':{'rsi':2,'stoch':2,'high52':1,'bb':1,'sma20':1,'sma50':1,'adx':1,'accel':1,'persistence':1},
 'W_OSC_3X':{'rsi':3,'stoch':3,'high52':1,'bb':1,'sma20':1,'sma50':1,'adx':1,'accel':1,'persistence':2},
 'W_52_2X':{'rsi':2,'stoch':2,'high52':2,'bb':1,'sma20':1,'sma50':1,'adx':1,'accel':1,'persistence':1},
 'W_EXTENSION_2X':{'rsi':2,'stoch':2,'high52':2,'bb':2,'sma20':2,'sma50':1,'adx':1,'accel':1,'persistence':1},
 'W_BREAKOUT_2X':{'rsi':1,'stoch':1,'high52':3,'bb':2,'sma20':1,'sma50':1,'adx':1,'accel':2,'persistence':1},
 'W_TREND_2X':{'rsi':2,'stoch':2,'high52':1,'bb':1,'sma20':2,'sma50':1,'adx':2,'accel':1,'persistence':1},
 'W_V9_LOCAL':{'rsi':2,'stoch':2,'high52':1,'bb':1,'sma20':1,'sma50':0,'adx':1,'accel':0,'persistence':0},
}

# Focused threshold profiles around the V9 optimum plus stricter/looser alternatives.
PROFILES=[
 {'name':'T78','rsi':78.,'stoch':78.,'bb_ext':1.00,'sma20_dist':8.,'sma50_dist':12.,'adx':35.,'accel4':10.},
 {'name':'T80','rsi':80.,'stoch':80.,'bb_ext':1.00,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
 {'name':'T82','rsi':82.,'stoch':82.,'bb_ext':1.01,'sma20_dist':10.,'sma50_dist':15.,'adx':40.,'accel4':12.},
 {'name':'T85','rsi':85.,'stoch':85.,'bb_ext':1.02,'sma20_dist':12.,'sma50_dist':18.,'adx':42.,'accel4':15.},
 {'name':'MIX_R80_S85','rsi':80.,'stoch':85.,'bb_ext':1.00,'sma20_dist':10.,'sma50_dist':15.,'adx':40.,'accel4':12.},
 {'name':'MIX_R85_S80','rsi':85.,'stoch':80.,'bb_ext':1.00,'sma20_dist':10.,'sma50_dist':15.,'adx':40.,'accel4':12.},
]
SCORE_RATIOS=[0.60,0.70,0.75,0.80]
BEHAVIOURS=['E_CONFIRM_D','D_OR_E_EXTREME']
COST_SENSITIVITY_PCT=[0.0,0.2,0.5]


def add_e_indicators(b):
 x=b.copy()
 x['high52_prev']=x.high.shift(1).rolling(52,min_periods=26).max()
 x['sma50_e']=x.close.rolling(50,min_periods=50).mean()
 x['ret4']=x.close.pct_change(4)*100
 return x


def overheat(i,a,p):
 c=a['close']
 rsi_hot=bool(np.isfinite(a['rsi'][i]) and a['rsi'][i]>=p['rsi'])
 sto_hot=bool(np.isfinite(a['k'][i]) and a['k'][i]>=p['stoch'])
 rsi_prev=bool(i>=1 and np.isfinite(a['rsi'][i-1]) and a['rsi'][i-1]>=p['rsi'])
 sto_prev=bool(i>=1 and np.isfinite(a['k'][i-1]) and a['k'][i-1]>=p['stoch'])
 return {
  'rsi':rsi_hot,
  'stoch':sto_hot,
  'high52':bool(np.isfinite(a['h52'][i]) and c[i]>a['h52'][i]),
  'bb':bool(np.isfinite(a['bb'][i]) and c[i]>=a['bb'][i]*p['bb_ext']),
  'sma20':bool(np.isfinite(a['sma20'][i]) and a['sma20'][i]>0 and (c[i]/a['sma20'][i]-1)*100>=p['sma20_dist']),
  'sma50':bool(np.isfinite(a['sma50'][i]) and a['sma50'][i]>0 and (c[i]/a['sma50'][i]-1)*100>=p['sma50_dist']),
  'adx':bool(np.isfinite(a['adx'][i]) and a['adx'][i]>=p['adx'] and np.isfinite(a['pdi'][i]) and np.isfinite(a['mdi'][i]) and a['pdi'][i]>a['mdi'][i]),
  'accel':bool(np.isfinite(a['ret4'][i]) and a['ret4'][i]>=p['accel4']),
  'persistence':bool((rsi_hot and rsi_prev) or (sto_hot and sto_prev)),
 }


def masked_weights(family,scheme): return {k:(scheme[k] if k in family else 0) for k in CRITERIA}
def score(ev,w): return sum(w[k] for k,v in ev.items() if v)
def max_score(w): return sum(w.values())


def simulate(sym,b,a,sig,entry_label,cfg,model,control=False):
 o,l,c=a['open'],a['low'],a['close']; pos=-1; price=0.; peak=0.; out=[]
 for i in range(1,len(c)):
  if pos>=0 and i>=pos:
   sp=price*(1-STOP_PCT/100); fill=fixed_stop_fill(o[i],l[i],sp)
   if fill is not None:
    xp,kind=fill; out.append(trade(sym,entry_label,model,b,pos,price,i,xp,[f'PROTECTIVE_STOP_{STOP_PCT:g}_{kind}'],stop_price=sp,peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
   if np.isfinite(c[i]): peak=max(peak,(c[i]/price-1)*100)
   if i<len(c)-1:
    ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
    if held<=FP_WEEKS and ret<=FP_FLOOR and c[i]<c[i-1] and fp_confirm(i,a): reasons.append('EARLY_FALSE_POSITIVE')
    d_hits=[]
    if peak>=D_CFG['activation_pct']:
     dev=evidence(i,b,a,D_CFG['daily_drop_pct']); d_hits=[k for k in D_CFG['family'] if dev[k]]
    d_signal=len(d_hits)>=D_CFG['score']
    if control:
     if d_signal: reasons.append('PROFIT_REVERSAL_'+'+'.join(d_hits))
    elif peak>=D_CFG['activation_pct']:
     ee=overheat(i,a,cfg['profile']); sc=score(ee,cfg['weights']); need=cfg['score_ratio']*max_score(cfg['weights']); e_signal=(max_score(cfg['weights'])>0 and sc+1e-12>=need)
     if cfg['behaviour']=='E_CONFIRM_D' and e_signal and d_signal:
      reasons.append('OVERHEAT_E_CONFIRM_D_'+'+'.join(k for k,v in ee.items() if v and cfg['weights'][k]>0))
     elif cfg['behaviour']=='D_OR_E_EXTREME' and (d_signal or e_signal):
      if d_signal: reasons.append('PROFIT_REVERSAL_'+'+'.join(d_hits))
      if e_signal: reasons.append('OVERHEAT_E_'+'+'.join(k for k,v in ee.items() if v and cfg['weights'][k]>0))
    if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
     out.append(trade(sym,entry_label,model,b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i],peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
  if pos<0 and i<len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
   pos=i+1; price=float(o[i+1]); peak=0.
 if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(trade(sym,entry_label,model,b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1],peak_ret=peak))
 return out


def e_metrics(df):
 if df.empty:return {}
 e=df[df.exit_reasons.str.contains('OVERHEAT_E',na=False)].copy()
 if e.empty:return {'e_exits':0,'e_avg_realised_return':None,'e_avg_peak_return':None,'e_avg_giveback':None,'e_median_giveback':None}
 e['giveback']=pd.to_numeric(e.peak_return_pct,errors='coerce')-pd.to_numeric(e.return_pct,errors='coerce')
 return {'e_exits':int(len(e)),'e_avg_realised_return':round(float(e.return_pct.mean()),3),'e_avg_peak_return':round(float(e.peak_return_pct.mean()),3),'e_avg_giveback':round(float(e.giveback.mean()),3),'e_median_giveback':round(float(e.giveback.median()),3)}


def cost_metrics(df):
 r=pd.to_numeric(df.return_pct,errors='coerce').dropna() if not df.empty else pd.Series(dtype=float)
 out={}
 for cost in COST_SENSITIVITY_PCT:
  rr=r-cost
  w=rr[rr>0]; loss=rr[rr<0]
  pf=float(w.sum()/(-loss.sum())) if len(loss) and -loss.sum()>0 else None
  out[f'cost_{cost:g}pct']={'mean_return_pct':round(float(rr.mean()),3) if len(rr) else None,'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,'win_rate_pct':round(float((rr>0).mean()*100),2) if len(rr) else None}
 return out


def criterion_trigger_share(df):
 e=df[df.exit_reasons.str.contains('OVERHEAT_E',na=False)] if not df.empty else df
 return {k:int(e.exit_reasons.str.contains(k,regex=False).sum()) if not e.empty else 0 for k in CRITERIA}


def run():
 st=time.perf_counter(); bars={}; arr={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=add_e_indicators(add_indicators(build_bars(w))); daily=last_daily_change_by_week(hist,b.index)
   bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
   arr[sym]={'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'mdi':b.minus_di.to_numpy(float),'daily':daily,'h52':b.high52_prev.to_numpy(float),'ret4':b.ret4.to_numpy(float)}
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last)

 def eval_model(mid,cfg=None,control=False):
  combined=[]; by={}
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],cfg,mid,control))
   df=pd.DataFrame(tr); by[e['label']]=summarize(df,end); combined.extend(tr)
  df=pd.DataFrame(combined); sm=summarize(df,end); rb=robust_score(sm)
  return {'exit_model':mid,'config':cfg,'by_entry':by,'combined':sm,'robust':rb,'e_metrics':e_metrics(df),'cost_sensitivity':cost_metrics(df),'criterion_trigger_counts':criterion_trigger_share(df),'tail_counts':{f'lt_{n}pct':int((df.return_pct<=-n).sum()) for n in [10,15,20]},'worst_10':df.sort_values('return_pct').head(10).to_dict('records') if not df.empty else []}

 control=eval_model('V10_D01_CONTROL',control=True)
 configs=[]
 # Stage 1: single-criterion screening (proves/rejects marginal usefulness under D confirmation).
 for crit in CRITERIA:
  for p in PROFILES:
   w={k:(1 if k==crit else 0) for k in CRITERIA}
   configs.append({'stage':'S1_SINGLE','hypothesis':'SINGLE_'+crit,'profile':p,'weights':w,'weight_scheme':'single','behaviour':'E_CONFIRM_D','score_ratio':1.0})
 # Stage 2: structured multi-criterion hypotheses and explicit weighting schemes.
 for hname,fam in HYPOTHESES.items():
  for p,wname,ratio,beh in itertools.product(PROFILES,WEIGHT_SCHEMES,SCORE_RATIOS,BEHAVIOURS):
   w=masked_weights(fam,WEIGHT_SCHEMES[wname])
   if max_score(w)<=0: continue
   configs.append({'stage':'S2_WEIGHT_BANK','hypothesis':hname,'profile':p,'weights':w,'weight_scheme':wname,'behaviour':beh,'score_ratio':ratio})
 # Deduplicate equivalent masked configs.
 seen=set(); unique=[]
 for cfg in configs:
  key=(cfg['profile']['name'],tuple(cfg['weights'][k] for k in CRITERIA),cfg['behaviour'],cfg['score_ratio'])
  if key in seen: continue
  seen.add(key); unique.append(cfg)
 configs=unique

 models=[]
 for n,cfg in enumerate(configs,1): models.append(eval_model(f'V10_E_{n:04d}',cfg))
 valid=[z for z in models if z['robust'] and z['combined']['ALL']['trades']>=100 and all(z['by_entry'][e['label']]['ALL']['trades']>=50 for e in ENTRIES)]
 for z in valid:
  z['delta_vs_D01']={k:round(z['combined']['ALL'][k]-control['combined']['ALL'][k],3) for k in ['mean_return_pct','profit_factor','reward_risk','p10_return_pct','avg_win_pct','avg_loss_pct','max_loss_pct'] if z['combined']['ALL'].get(k) is not None and control['combined']['ALL'].get(k) is not None}
  # Robust target is deliberately strict: RR>=4 in every eligible window, while PF/mean do not collapse.
  z['robust_rr4']=bool(z['robust']['min_rr']>=4.0)
  z['passes_profit_guard']=bool(z['robust']['min_pf']>=1.30 and z['robust']['min_mean']>=1.50)
  z['passes_tail_guard']=bool(z['combined']['ALL']['max_loss_pct']>=-10.0 and z['robust']['min_p10']>=-9.5)
  z['passes_all_guards']=bool(z['passes_profit_guard'] and z['passes_tail_guard'])

 # Risk/robustness first. RR4 is rewarded only after the locked tail guards survive.
 valid.sort(key=lambda z:(z['passes_all_guards'],z['combined']['ALL']['max_loss_pct'],z['robust']['min_p10'],z['robust_rr4'],z['robust']['min_rr'],z['robust']['min_pf'],z['robust']['min_mean'],z['combined']['ALL']['mean_return_pct']),reverse=True)

 singles=[z for z in valid if z['config']['stage']=='S1_SINGLE']
 singles.sort(key=lambda z:(z['passes_all_guards'],z['robust']['min_rr'],z['robust']['min_pf'],z['robust']['min_mean']),reverse=True)
 best=valid[0] if valid else None
 rr4=[z for z in valid if z.get('robust_rr4') and z.get('passes_all_guards')]
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V10_OVERHEAT_WEIGHT_BANK','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},
  'locked':{'entries_fixed':True,'protective_stop_pct':STOP_PCT,'false_positive_block_fixed':True,'D01_anchor_fixed':D_CFG,'endpoint_mark_is_execution':False},
  'method':{'deterministic':True,'random_search':False,'stages':['single-criterion screening','structured hypothesis/weight bank'],'criteria':CRITERIA,'hypotheses':HYPOTHESES,'profiles':[p['name'] for p in PROFILES],'weight_schemes':list(WEIGHT_SCHEMES),'score_ratios':SCORE_RATIOS,'behaviours':BEHAVIOURS,'sample_guard_per_entry':50,'cost_sensitivity_pct':COST_SENSITIVITY_PCT},
  'D01_control':control,'models_tested':len(models),'eligible_models':len(valid),'best_risk_robust':best,'robust_rr4_guarded_count':len(rr4),'top30':valid[:30],'single_criterion_ranking':singles[:20],
  'lookahead_controls':{'completed_week_signals_only':True,'strategic_execution':'next_week_open','high52_uses_prior_window_only':True,'fixed_stop_known_before_bar':True,'endpoint_mark_is_execution':False},
  'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','COST_SENSITIVITY_IS_SCENARIO_NOT_BROKER_FEE_MODEL','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
