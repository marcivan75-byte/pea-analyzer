"""Research-only V6: confirmed false-positive invalidation for weekly exits.
Fixed optimized entries. Completed-week signals, next-week-open execution only.
Goal: improve on V4 by requiring genuine momentum/trend confirmation before an early losing trade is invalidated.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES, arrays, summarize, _trade

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V6_CONFIRMED_FP.json'


def C(loss=4.5, fp_weeks=2, fp_floor=-2.0, confirm_mode='stoch_or_trend', confirm_n=1,
      trail=None, activation=10.0, rsi=75.0, stoch=75.0, trend='psar'):
    return {'loss_cut_pct':loss,'fp_weeks':fp_weeks,'fp_floor_pct':fp_floor,
            'confirm_mode':confirm_mode,'confirm_n':confirm_n,'trail_pct':trail,
            'trail_activation_pct':activation,'rsi_heat':rsi,'stoch_heat':stoch,'trend':trend}

# Concentrated around V4's robust region, but replaces the tautological score
# (weekly_reversal + below_entry) with independent confirmation evidence.
CONFIGS=[]
for loss in [4.0,4.5,5.0,5.5,6.0]:
    for weeks in [1,2,3]:
        for floor in [-1.0,-1.5,-2.0,-2.5,-3.0]:
            for mode in ['stoch_only','stoch_or_trend','momentum_or_trend','two_confirmations']:
                for rsi in [72.5,75.0,77.5]:
                    CONFIGS.append(C(loss,weeks,floor,mode,1,None,10.0,rsi,75.0,'psar'))
# Explicit 5% trailing reversal family, activated only after a winner has developed.
for loss in [4.5,5.0,5.5]:
    for weeks in [1,2,3]:
        for floor in [-1.5,-2.0,-2.5]:
            for mode in ['stoch_or_trend','momentum_or_trend']:
                for act in [8.0,10.0,12.0,15.0]:
                    CONFIGS.append(C(loss,weeks,floor,mode,1,5.0,act,75.0,75.0,'psar'))
_seen=set(); CONFIGS=[x for x in CONFIGS if not ((k:=tuple(sorted(x.items()))) in _seen or _seen.add(k))]


def confirm_components(i,c,rsi,k,d,psar,s20,s50):
    return {
        'stoch_crossdown': bool(np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]),
        'rsi_falling': bool(np.isfinite(rsi[i]) and np.isfinite(rsi[i-1]) and rsi[i]<rsi[i-1]),
        'rsi_weak': bool(np.isfinite(rsi[i]) and rsi[i]<50),
        'below_psar': bool(np.isfinite(psar[i]) and c[i]<psar[i]),
        'below_sma20': bool(np.isfinite(s20[i]) and c[i]<s20[i]),
        'below_sma50': bool(np.isfinite(s50[i]) and c[i]<s50[i]),
        'ma20_weakening': bool(i>=2 and np.isfinite(s20[i]) and np.isfinite(s20[i-1]) and s20[i]<s20[i-1]),
    }


def confirmed(mode, comps):
    trend = comps['below_psar'] or comps['below_sma20'] or comps['ma20_weakening']
    momentum = comps['stoch_crossdown'] or comps['rsi_falling'] or comps['rsi_weak']
    if mode=='stoch_only': return comps['stoch_crossdown']
    if mode=='stoch_or_trend': return comps['stoch_crossdown'] or trend
    if mode=='momentum_or_trend': return momentum or trend
    if mode=='two_confirmations': return sum(bool(v) for v in comps.values())>=2
    return False


def simulate(sym,b,a,sig,entry_label,cfg,cfg_id,diag):
    o,c,rsi,k,d,psar,s20,s50=(a[x] for x in ['open','close','rsi','k','d','psar','sma20','sma50'])
    pos_idx=-1; price=0.; peak=0.; peak_gain=0.; out=[]
    for i in range(1,len(c)-1):
        nxt=o[i+1]
        if pos_idx>=0 and i>=pos_idx:
            close=c[i]; prev=c[i-1]; peak=max(peak,close); ret=(close/price-1)*100
            peak_gain=max(peak_gain,(peak/price-1)*100); held=i-pos_idx+1; reasons=[]
            loss=cfg['loss_cut_pct']
            if ret<=-loss: reasons.append(f'LOSS_CLOSE_{loss:g}')
            if held<=cfg['fp_weeks'] and ret<=cfg['fp_floor_pct'] and np.isfinite(prev) and close<prev:
                comps=confirm_components(i,c,rsi,k,d,psar,s20,s50)
                if confirmed(cfg['confirm_mode'],comps):
                    reasons.append(f'CONFIRMED_FALSE_POSITIVE_{cfg["confirm_mode"]}')
                    diag['fp_triggers']+=1
                    for key,val in comps.items():
                        if val: diag['fp_components'][key]=diag['fp_components'].get(key,0)+1
            t=cfg['trail_pct']; act=cfg['trail_activation_pct']
            if t is not None and peak_gain>=act and (close/peak-1)*100<=-t and close<prev:
                reasons.append(f'TRAIL_{t:g}_ACT{act:g}_REV')
            if np.isfinite(rsi[i]) and rsi[i]>cfg['rsi_heat'] and close<prev:
                reasons.append(f'RSI_GT{cfg["rsi_heat"]:g}_REV')
            crossdn=np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]
            if np.isfinite(k[i]) and k[i]>cfg['stoch_heat'] and crossdn:
                reasons.append(f'STOCH_GT{cfg["stoch_heat"]:g}_CROSSDOWN')
            if cfg['trend']=='psar' and np.isfinite(psar[i]) and close<psar[i]: reasons.append('CLOSE_LT_PSAR')
            if reasons and np.isfinite(nxt) and nxt>0:
                out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,i+1,float(nxt),reasons)); pos_idx=-1; price=0.; peak=0.; peak_gain=0.; continue
        if pos_idx<0 and sig[i] and np.isfinite(nxt) and nxt>0:
            pos_idx=i+1; price=float(nxt); peak=price; peak_gain=0.
    if pos_idx>=0 and np.isfinite(c[-1]) and c[-1]>0:
        out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True))
    return out


def admissible(by,comb):
    for e in ENTRIES:
        z=by[e['label']]
        if z['ALL']['trades']<50 or z['12M']['trades']<30: return False
        if (z['12M']['profit_factor'] or 0)<1.05 or (z['12M']['mean_return_pct'] or -999)<=0: return False
        if z['ALL']['endpoint_share_pct']>15: return False
    return (comb['12M']['profit_factor'] or 0)>=1.10 and (comb['12M']['mean_return_pct'] or -999)>0


def vals(by,field,default=-999):
    out=[]
    for e in ENTRIES:
        for w in ['ALL','12M','18M','24M']:
            z=by[e['label']][w]
            if z['trades']>=15:
                v=z.get(field); out.append(default if v is None else v)
    return out


def best_family(results,pred):
    xs=[z for z in results if pred(z['config'])]
    return max(xs,key=lambda z:tuple(z['rank_key'])) if xs else None


def run():
    st=time.perf_counter(); OUT.parent.mkdir(parents=True,exist_ok=True); bars={}; arr={}; signals={}; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for sym,hist,err in _iter_consolidated(path):
            if sym is None or err or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            b=build_bars(w); bars[sym]=b; arr[sym]=arrays(b); first.append(b.index.min()); last.append(b.index.max())
            signals[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
    end=max(last); results=[]
    for ci,cfg in enumerate(CONFIGS,1):
        cid=f'V6_{ci:04d}'; alltr=[]; by={}; diag={'fp_triggers':0,'fp_components':{}}
        for ent in ENTRIES:
            tr=[]
            for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],signals[sym][ent['label']],ent['label'],cfg,cid,diag))
            by[ent['label']]=summarize(pd.DataFrame(tr),end); alltr.extend(tr)
        comb=summarize(pd.DataFrame(alltr),end); ok=admissible(by,comb)
        rr=min(vals(by,'reward_risk',0)); pf=min(vals(by,'profit_factor',0)); p10=min(vals(by,'p10_return_pct')); avgl=min(vals(by,'avg_loss_pct')); maxl=min(vals(by,'max_loss_pct')); mean=min(vals(by,'mean_return_pct'))
        rank=[1 if ok else 0,rr,pf,p10,avgl,maxl,mean,comb['ALL']['profit_factor'] or 0]
        results.append({'exit_model':cid,'config':cfg,'admissible':ok,'by_entry':by,'combined':comb,'robust':{'reward_risk':round(rr,3),'profit_factor':round(pf,3),'p10_return_pct':round(p10,3),'avg_loss_pct':round(avgl,3),'max_loss_pct':round(maxl,3),'mean_return_pct':round(mean,3)},'false_positive_diagnostics':diag,'rank_key':rank})
    results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
    payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V6_CONFIRMED_FP','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:40],'diagnostics':{'best_overall':results[0] if results else None,'best_5pct_trailing':best_family(results,lambda c:c['trail_pct']==5.0),'best_stoch_only':best_family(results,lambda c:c['confirm_mode']=='stoch_only'),'best_stoch_or_trend':best_family(results,lambda c:c['confirm_mode']=='stoch_or_trend'),'best_momentum_or_trend':best_family(results,lambda c:c['confirm_mode']=='momentum_or_trend'),'best_two_confirmations':best_family(results,lambda c:c['confirm_mode']=='two_confirmations')},'selection_rule':'Fixed entries; robust ranking across both entries and ALL/12M/18M/24M with sample, PF, positive-return and endpoint safeguards. Early invalidation requires a losing completed-week reversal plus independent momentum/trend confirmation.','requested_5pct_trailing_explicitly_tested':True,'lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','ENDPOINT_MARK_TO_MARKET_FOR_CENSORING_CONTROL','RESEARCH_ONLY']}
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
