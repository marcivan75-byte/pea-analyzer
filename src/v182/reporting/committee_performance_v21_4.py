from __future__ import annotations

from datetime import datetime, timezone, date
from pathlib import Path
import json
import logging
import math
import pandas as pd

from v182.reporting.committee_performance import _price_master, _read, _save, _style_xlsx

logger=logging.getLogger(__name__)


def _num(value):
    try:
        x=float(value); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None


def _model_version(root:Path)->str:
    summary=root/"outputs"/"committee_master"/"SUMMARY.json"
    if summary.exists():
        try:
            value=json.loads(summary.read_text(encoding="utf-8")).get("version")
            if value: return str(value)
        except (OSError,ValueError,json.JSONDecodeError) as exc:
            logger.debug("Unable to read Committee summary version from %s: %s: %s",summary,type(exc).__name__,exc)
    try:
        return str(json.loads((root/"config"/"COMMITTEE_MASTER_V21.json").read_text(encoding="utf-8")).get("version","UNKNOWN"))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        logger.warning("Unable to resolve model version from Committee config: %s: %s",type(exc).__name__,exc)
        return "UNKNOWN"


def _drawdown_multiplier(nav:pd.DataFrame,cfg:dict)->tuple[float,float]:
    if nav.empty or not cfg.get("drawdown_throttle",{}).get("enabled",False): return 1.0,0.0
    vals=pd.to_numeric(nav.get("nav_eur"),errors="coerce").dropna()
    if vals.empty: return 1.0,0.0
    peak=float(vals.max()); latest=float(vals.iloc[-1]); dd=max(0.0,(1.0-latest/peak)*100.0) if peak>0 else 0.0
    t=cfg["drawdown_throttle"]
    if dd>=float(t["level_3_drawdown_pct"]): return float(t["level_3_new_position_multiplier"]),dd
    if dd>=float(t["level_2_drawdown_pct"]): return float(t["level_2_new_position_multiplier"]),dd
    if dd>=float(t["level_1_drawdown_pct"]): return float(t["level_1_new_position_multiplier"]),dd
    return 1.0,dd


def _current_value(positions:pd.DataFrame,prices:dict[str,dict])->float:
    total=0.0
    if positions.empty: return total
    for _,r in positions.iterrows():
        if str(r.get("status"))!="OPEN": continue
        qty=_num(r.get("quantity")) or 0.0; p=prices.get(str(r.get("isin","") or ""),{}).get("price") or _num(r.get("entry_price")) or 0.0
        total+=qty*p
    return total


def _eligible_signal_rows(decisions:pd.DataFrame,cfg:dict)->pd.DataFrame:
    buy=set(cfg["buy_decisions"]); min_score=float(cfg["minimum_buy_score"]); min_cov=float(cfg.get("minimum_signal_coverage_pct",70.0))
    d=decisions[decisions["decision"].astype(str).isin(buy)].copy()
    d["_score"]=pd.to_numeric(d.get("score"),errors="coerce"); d["_coverage"]=pd.to_numeric(d.get("coverage_pct"),errors="coerce")
    d=d[(d["_score"]>=min_score)&(d["_coverage"]>=min_cov)&d["isin"].notna()]
    if d.empty: return d
    horizons=d.groupby("isin")["horizon"].apply(lambda s:"|".join(sorted(set(map(str,s))))).to_dict()
    best=d.sort_values(["_score","_coverage"],ascending=False).drop_duplicates("isin",keep="first").copy()
    best["contributing_horizons"]=best["isin"].astype(str).map(horizons)
    return best


def run(root:Path)->dict:
    cfg=json.loads((root/"config"/"COMMITTEE_VIRTUAL_MONEY_MANAGEMENT.json").read_text(encoding="utf-8"))
    decisions_path=root/"outputs"/"committee_master"/"COMMITTEE_DECISIONS.csv"
    if not decisions_path.exists(): return {"status":"BLOCKED_NO_COMMITTEE_DECISIONS"}
    decisions=pd.read_csv(decisions_path,sep=";",encoding="utf-8-sig",low_memory=False)
    actions=_read(root/"outputs"/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"); etfs=_read(root/"outputs"/"V18.2_PEA_ETF_MASTER_ENRICHED.csv"); prices=_price_master(actions,etfs)
    model=_model_version(root); book_id=f"BOOK::{model}"; today=datetime.now(timezone.utc).date().isoformat()
    state=root/"state"/"committee_performance_v21_4"; outdir=root/"outputs"/"performance"; state.mkdir(parents=True,exist_ok=True); outdir.mkdir(parents=True,exist_ok=True)
    signals=_read(state/"signals.csv"); positions=_read(state/"positions.csv"); tx=_read(state/"transactions.csv"); nav=_read(state/"nav.csv"); marks=_read(state/"daily_marks.csv")
    for df in (signals,positions,tx,nav,marks):
        if not df.empty and "book_id" not in df.columns: df["book_id"]="LEGACY_PRE_V21_4"
    fee=float(cfg["transaction_cost_per_side_pct"])/100.0; allowed={"ACTION","ETF"} if not cfg.get("gold_in_virtual_pea_book",False) else {"ACTION","ETF","GOLD"}
    current=decisions[decisions["asset_class"].astype(str).isin(allowed)].copy()
    if positions.empty: positions=pd.DataFrame(columns=["position_id","book_id","model_version","open_date","asset_class","primary_horizon","contributing_horizons","isin","name","sector","entry_price","quantity","entry_score","entry_coverage_pct","stop_pct","status"])
    if signals.empty: signals=pd.DataFrame(columns=["signal_id","book_id","model_version","signal_date","status","asset_class","primary_horizon","contributing_horizons","isin","name","sector","signal_score","signal_coverage_pct","filled_date","filled_price","closed_date","closed_price","realized_return_pct","close_reason"])

    current_by_isin={k:g for k,g in current.groupby(current["isin"].astype(str))}
    cash=float(cfg["initial_capital_eur"])
    book_nav=nav[nav.get("book_id",pd.Series(index=nav.index,dtype=str)).astype(str)==book_id] if not nav.empty else pd.DataFrame()
    if not book_nav.empty:
        cash=_num(book_nav.iloc[-1].get("cash_eur")) or cash
    for idx,pos in positions[(positions["book_id"].astype(str)==book_id)&(positions["status"].astype(str)=="OPEN")].iterrows():
        isin=str(pos.get("isin","") or ""); p=prices.get(isin,{}).get("price"); entry=_num(pos.get("entry_price")); qty=_num(pos.get("quantity")); stop=_num(pos.get("stop_pct"))
        if p is None or entry is None or qty is None: continue
        stop_hit=stop is not None and p<=entry*(1.0-stop/100.0); rows=current_by_isin.get(isin,pd.DataFrame())
        decisions_now=set(rows.get("decision",pd.Series(dtype=str)).astype(str)) if not rows.empty else set()
        data_only_failure=bool(decisions_now) and decisions_now.issubset({"BLOCK_DATA","FAILED","BLOCKED_INPUT","BLOCKED_CONFIG"})
        hold=bool(decisions_now.intersection(set(cfg.get("hold_decisions",[]))))
        decision_exit=bool(decisions_now) and not hold and not data_only_failure
        if stop_hit or decision_exit:
            proceeds=qty*p*(1.0-fee); cash+=proceeds; reason="STOP" if stop_hit else "NO_ACTIVE_HOLD_DECISION"
            positions.at[idx,"status"]="CLOSED"; positions.at[idx,"close_date"]=today; positions.at[idx,"close_price"]=p; positions.at[idx,"exit_reason"]=reason
            tx=pd.concat([tx,pd.DataFrame([{"book_id":book_id,"model_version":model,"date":today,"type":"SELL","position_id":pos.get("position_id"),"isin":isin,"price":p,"quantity":qty,"gross_eur":qty*p,"cost_eur":qty*p*fee,"reason":reason}])],ignore_index=True)
            mask=(signals.get("book_id",pd.Series(index=signals.index,dtype=str)).astype(str)==book_id)&(signals.get("isin",pd.Series(index=signals.index,dtype=str)).astype(str)==isin)&(signals.get("status",pd.Series(index=signals.index,dtype=str)).astype(str)=="OPEN")
            for sidx in signals[mask].index:
                ep=_num(signals.at[sidx,"filled_price"]); signals.at[sidx,"status"]="CLOSED"; signals.at[sidx,"closed_date"]=today; signals.at[sidx,"closed_price"]=p; signals.at[sidx,"close_reason"]=reason; signals.at[sidx,"realized_return_pct"]=(p/ep-1.0)*100.0 if ep else None

    open_pos=positions[(positions["book_id"].astype(str)==book_id)&(positions["status"].astype(str)=="OPEN")].copy(); market_value=_current_value(open_pos,prices); equity=max(0.0,cash+market_value)
    throttle,drawdown=_drawdown_multiplier(book_nav,cfg); daily_turnover=0.0
    open_isins=set(open_pos["isin"].astype(str)); pending=signals[(signals["book_id"].astype(str)==book_id)&(signals["status"].astype(str)=="PENDING_ENTRY")].copy()

    for sidx,s in pending.sort_values("signal_date").iterrows():
        if str(s.get("signal_date"))>=today: continue
        isin=str(s.get("isin","") or "")
        if isin in open_isins:
            signals.at[sidx,"status"]="CANCELLED_DUPLICATE_ISIN"; continue
        try: age=(date.fromisoformat(today)-date.fromisoformat(str(s.get("signal_date")))).days
        except ValueError: age=0
        if age>7: signals.at[sidx,"status"]="CANCELLED_STALE"; continue
        p=prices.get(isin,{}).get("price"); score=_num(s.get("signal_score")); coverage=_num(s.get("signal_coverage_pct")); horizon=str(s.get("primary_horizon") or "MT")
        if p is None or p<=0 or score is None or coverage is None or throttle<=0 or equity<=0: continue
        if len(open_isins)>=int(cfg.get("max_open_positions",20)): break
        stop=float(cfg["stops_pct"].get(horizon,12.0)); exposure=_current_value(open_pos,prices); sector=str(s.get("sector") or prices.get(isin,{}).get("sector") or "NON CLASSE")
        sector_value=sum((_num(r.get("quantity")) or 0)*(prices.get(str(r.get("isin")),{}).get("price") or (_num(r.get("entry_price")) or 0)) for _,r in open_pos.iterrows() if str(r.get("sector"))==sector)
        max_pos=equity*min(float(cfg["max_position_pct"]),float(cfg.get("max_instrument_exposure_pct",cfg["max_position_pct"])))/100.0
        risk_pos=equity*(float(cfg["risk_budget_per_position_pct"])/100.0)/(stop/100.0); exposure_room=max(0.0,equity*float(cfg["max_total_exposure_pct"])/100.0-exposure); sector_room=max(0.0,equity*float(cfg["max_sector_exposure_pct"])/100.0-sector_value); cash_room=max(0.0,cash-equity*float(cfg["cash_buffer_min_pct"])/100.0); turnover_room=max(0.0,equity*float(cfg.get("max_daily_turnover_pct",100.0))/100.0-daily_turnover); conviction=min(1.0,max(0.70,score/100.0))*throttle
        value=min(max_pos,risk_pos,exposure_room,sector_room,cash_room,turnover_room)*conviction
        if value<=0: continue
        qty=value/p; cost=value*(1.0+fee)
        if cost>cash: continue
        cash-=cost; daily_turnover+=value; pid=f"{book_id}|{today}|{isin}"; row={"position_id":pid,"book_id":book_id,"model_version":model,"open_date":today,"asset_class":s.get("asset_class"),"primary_horizon":horizon,"contributing_horizons":s.get("contributing_horizons"),"isin":isin,"name":s.get("name"),"sector":sector,"entry_price":p,"quantity":qty,"entry_score":score,"entry_coverage_pct":coverage,"stop_pct":stop,"status":"OPEN"}
        positions=pd.concat([positions,pd.DataFrame([row])],ignore_index=True); open_pos=pd.concat([open_pos,pd.DataFrame([row])],ignore_index=True); open_isins.add(isin)
        tx=pd.concat([tx,pd.DataFrame([{"book_id":book_id,"model_version":model,"date":today,"type":"BUY","position_id":pid,"isin":isin,"price":p,"quantity":qty,"gross_eur":value,"cost_eur":value*fee,"reason":"NEXT_RUN_COMMITTEE_BUY"}])],ignore_index=True)
        signals.at[sidx,"status"]="OPEN"; signals.at[sidx,"filled_date"]=today; signals.at[sidx,"filled_price"]=p

    eligible=_eligible_signal_rows(current,cfg); existing_active=set(signals[(signals["book_id"].astype(str)==book_id)&signals["status"].astype(str).isin(["PENDING_ENTRY","OPEN"])] ["isin"].astype(str)) if not signals.empty else set()
    new=[]
    for _,r in eligible.iterrows():
        isin=str(r.get("isin","") or "")
        if not isin or isin in open_isins or isin in existing_active: continue
        pinfo=prices.get(isin,{})
        new.append({"signal_id":f"{book_id}|{today}|{isin}","book_id":book_id,"model_version":model,"signal_date":today,"status":"PENDING_ENTRY","asset_class":r.get("asset_class"),"primary_horizon":r.get("horizon"),"contributing_horizons":r.get("contributing_horizons"),"isin":isin,"name":r.get("name"),"sector":r.get("sector") or pinfo.get("sector"),"signal_score":r.get("_score"),"signal_coverage_pct":r.get("_coverage")})
    if new: signals=pd.concat([signals,pd.DataFrame(new)],ignore_index=True).drop_duplicates("signal_id",keep="first")

    mark_rows=[]; open_pos=positions[(positions["book_id"].astype(str)==book_id)&(positions["status"].astype(str)=="OPEN")].copy()
    for _,r in open_pos.iterrows():
        isin=str(r.get("isin","") or ""); p=prices.get(isin,{}).get("price"); entry=_num(r.get("entry_price"))
        if p is None or entry is None or entry<=0: continue
        mark_rows.append({"book_id":book_id,"model_version":model,"date":today,"position_id":r.get("position_id"),"isin":isin,"entry_price":entry,"current_price":p,"performance_pct":(p/entry-1.0)*100.0})
    if mark_rows: marks=pd.concat([marks,pd.DataFrame(mark_rows)],ignore_index=True).drop_duplicates(["book_id","date","position_id"],keep="last")
    market_value=_current_value(open_pos,prices); current_nav=cash+market_value; initial=float(cfg["initial_capital_eur"]); cumulative=(current_nav/initial-1.0)*100.0
    nav_row={"book_id":book_id,"model_version":model,"date":today,"nav_eur":current_nav,"cash_eur":cash,"market_value_eur":market_value,"exposure_pct":market_value/current_nav*100.0 if current_nav else 0.0,"cumulative_performance_pct":cumulative,"drawdown_pct_at_start":drawdown,"new_position_multiplier":throttle,"daily_turnover_eur":daily_turnover,"open_positions":int(len(open_pos))}
    nav=pd.concat([nav,pd.DataFrame([nav_row])],ignore_index=True).drop_duplicates(["book_id","date"],keep="last")
    for df,path in ((signals,state/"signals.csv"),(positions,state/"positions.csv"),(tx,state/"transactions.csv"),(nav,state/"nav.csv"),(marks,state/"daily_marks.csv")): _save(df,path)

    closed=positions[(positions.get("book_id",pd.Series(index=positions.index,dtype=str)).astype(str)==book_id)&(positions.get("status",pd.Series(index=positions.index,dtype=str)).astype(str)=="CLOSED")].copy(); pending_now=signals[(signals["book_id"].astype(str)==book_id)&(signals["status"].astype(str)=="PENDING_ENTRY")].copy()
    dashboard=pd.DataFrame([{"as_of":today,"book_id":book_id,"model_version":model,"initial_capital_eur":initial,"nav_eur":current_nav,"cumulative_performance_pct":cumulative,"cash_eur":cash,"exposure_pct":nav_row["exposure_pct"],"open_positions":len(open_pos),"pending_signals":len(pending_now),"closed_positions":len(closed),"drawdown_pct_at_start":drawdown,"new_position_multiplier":throttle,"entry_rule":cfg["entry_execution_rule"],"live_orders_enabled":False}])
    assumptions=pd.DataFrame([{"parameter":k,"value":json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v} for k,v in cfg.items()]); xlsx=outdir/"COMMITTEE_BUY_PERFORMANCE.xlsx"
    with pd.ExcelWriter(xlsx,engine="openpyxl") as writer:
        dashboard.to_excel(writer,sheet_name="Dashboard",index=False); nav[nav["book_id"].astype(str)==book_id].to_excel(writer,sheet_name="NAV_Quotidienne",index=False); open_pos.to_excel(writer,sheet_name="Positions_Ouvertes",index=False); pending_now.to_excel(writer,sheet_name="Entrees_En_Attente",index=False); closed.to_excel(writer,sheet_name="Positions_Cloturees",index=False); signals[signals["book_id"].astype(str)==book_id].to_excel(writer,sheet_name="Signaux_BUY",index=False); marks[marks.get("book_id",pd.Series(index=marks.index,dtype=str)).astype(str)==book_id].to_excel(writer,sheet_name="Suivi_Journalier",index=False); tx[tx.get("book_id",pd.Series(index=tx.index,dtype=str)).astype(str)==book_id].to_excel(writer,sheet_name="Transactions_Virtuelles",index=False); assumptions.to_excel(writer,sheet_name="Money_Management",index=False)
    _style_xlsx(xlsx)
    return {"status":"SUCCESS","as_of":today,"book_id":book_id,"model_version":model,"xlsx":str(xlsx.relative_to(root)),"nav_eur":round(current_nav,2),"cumulative_performance_pct":round(cumulative,4),"open_positions":int(len(open_pos)),"pending_signals":int(len(pending_now)),"entry_rule":cfg["entry_execution_rule"],"live_orders_enabled":False}
