"""Research-only V4: selective false-positive invalidation for weekly exits.
Fixed optimized entries. Completed-week signals, next-week-open execution only.
Goal: identify early losers without cutting healthy winners indiscriminately.
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
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V4_FALSE_POSITIVE.json'

# V4 starts from the V2 philosophy but makes early invalidation selective.
# A position is not exited merely because it is slightly negative: it needs a cluster
# of completed-week deterioration signs.
def C(loss=4.5, fp_weeks=2, fp_score=2, fp_floor=-1.0, trail=None, activation=8.0,
      rsi=75.0, stoch=75.0, trend='psar'):
    return {'loss_cut_pct':loss,'fp_weeks':fp_weeks,'fp_score':fp_score,'fp_floor_pct':fp_floor,
            'trail_pct':trail,'trail_activation_pct':activation,'rsi_heat':rsi,
            'stoch_heat':stoch,'trend':trend}

CONFIGS=[]
for loss in [3.5,4.0,4.5,5.0,5.5,6.0]:
    for weeks in [1,2,3]:
        for score in [2,3,4]:
            for floor in [0.0,-1.0,-2.0,-3.0]:
                for rsi in [72.5,75.0,77.5]:
                    CONFIGS.append(C(loss,weeks,score,floor,None,8.0,rsi,75.0,'psar'))
# Winner-preserving 5% trailing family explicitly retained.
for loss in [4.0,4.5,5.0,5.5]:
    for weeks in [1,2,3]:
        for score in [2,3,4]:
            for floor in [-1.0,-2.0]:
                for act in [8.0,10.0,12.0,15.0]:
                    CONFIGS.append(C(loss,weeks,score,floor,5.0,act,75.0,75.0,'psar'))
_seen=set(); CONFIGS=[x for x in CONFIGS if not ((k:=tuple(sorted(x.items()))) in _seen or _seen.add(k))]


def fp_components(i,c,rsi,k,d,psar,s20,s50,price):
    prev=c[i-1]; ret=(c[i]/price-1)*100
    comps={}
    comps['weekly_reversal']=bool(np.isfinite(prev) and c[i]<prev)
    comps['below_entry']=bool(ret<0)
    comps['below_psar']=bool(np.isfinite(psar[i]) and c[i]<psar[i])
    comps['below_sma20']=bool(np.isfinite(s20[i]) and c[i]<s20[i])
    comps['ma20_weakening']=bool(i>=2 and np.isfinite(s20[i]) and np.isfinite(s20[i-1]) and s20[i]<s20[i-1])
    comps['stoch_crossdown']=bool(np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1])
    comps['rsi_weak']=bool(np.isfinite(rsi[i]) and rsi[i]<50)
    return comps


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
            if held<=cfg['fp_weeks'] and ret<=cfg['fp_floor_pct']:
                comps=fp_components(i,c,rsi,k,d,psar,s20,s50,price)
                score=sum(comps.values())
                if score>=cfg['fp_score']:
                    reasons.append(f'FALSE_POSITIVE_SCORE_{score}')
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
        cid=f'V4_{ci:04d}'; alltr=[]; by={}; diag={'fp_triggers':0,'fp_components':{}}
        for ent in ENTRIES:
            tr=[]
            for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],signals[sym][ent['label']],ent['label'],cfg,cid,diag))
            by[ent['label']]=summarize(pd.DataFrame(tr),end); alltr.extend(tr)
        comb=summarize(pd.DataFrame(alltr),end); ok=admissible(by,comb)
        rr=min(vals(by,'reward_risk',0)); pf=min(vals(by,'profit_factor',0)); p10=min(vals(by,'p10_return_pct')); avgl=min(vals(by,'avg_loss_pct')); maxl=min(vals(by,'max_loss_pct')); mean=min(vals(by,'mean_return_pct'))
        rank=[1 if ok else 0,rr,pf,p10,avgl,maxl,mean,comb['ALL']['profit_factor'] or 0]
        results.append({'exit_model':cid,'config':cfg,'admissible':ok,'by_entry':by,'combined':comb,'robust':{'reward_risk':round(rr,3),'profit_factor':round(pf,3),'p10_return_pct':round(p10,3),'avg_loss_pct':round(avgl,3),'max_loss_pct':round(maxl,3),'mean_return_pct':round(mean,3)},'false_positive_diagnostics':diag,'rank_key':rank})
    results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
    payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V4_FALSE_POSITIVE','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:30],'diagnostics':{'best_overall':results[0] if results else None,'best_5pct_trailing':best_family(results,lambda c:c['trail_pct']==5.0),'best_fp_score2':best_family(results,lambda c:c['fp_score']==2),'best_fp_score3':best_family(results,lambda c:c['fp_score']==3),'best_fp_score4':best_family(results,lambda c:c['fp_score']==4)},'selection_rule':'Fixed entries; robust ranking across both entries and ALL/12M/18M/24M with sample, PF, positive-return and endpoint safeguards. Early exits require a cluster of deterioration signs to target false positives selectively.','false_positive_components':['weekly_reversal','below_entry','below_psar','below_sma20','ma20_weakening','stoch_crossdown','rsi_weak'],'requested_5pct_trailing_explicitly_tested':True,'lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','ENDPOINT_MARK_TO_MARKET_FOR_CENSORING_CONTROL','RESEARCH_ONLY']}
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
