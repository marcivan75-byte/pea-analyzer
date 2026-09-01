"""Backtest anti-FP chronologique pour TABPORT Meta.

- Confirmation J+1 : décision au close de la première séance après le signal,
  entrée au plus tôt à l'open de la séance suivante.
- Early exits : règles FPEarlyExit avec uniquement des variables disponibles à la barre courante.
- Aucun préopen/secteur synthétique.
"""
from __future__ import annotations

from math import floor
import numpy as np
import pandas as pd

from v182.hebdo.confirmation_entry import ConfirmationEntry
from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.tabport import Tabport65k, TabportConfig


def add_antifp_features(prices_ohlcv: pd.DataFrame) -> pd.DataFrame:
    p = prices_ohlcv.copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce", utc=True)
    p = p.sort_values(["ticker", "date"]).reset_index(drop=True)
    out=[]
    for _, g in p.groupby("ticker", sort=False):
        x=g.copy()
        vol=pd.to_numeric(x["volume"], errors="coerce")
        avg=vol.rolling(20,min_periods=20).mean(); std=vol.rolling(20,min_periods=20).std()
        x["vol_z"]=(vol-avg)/std.replace(0,np.nan)
        close=pd.to_numeric(x["close"], errors="coerce")
        delta=close.diff(); gain=delta.clip(lower=0); loss=(-delta.clip(upper=0))
        ag=gain.rolling(14,min_periods=14).mean(); al=loss.rolling(14,min_periods=14).mean()
        rs=ag/al.replace(0,np.nan)
        x["rsi_14"]=100-(100/(1+rs))
        x.loc[(al==0)&(ag>0),"rsi_14"]=100.0
        x.loc[(al==0)&(ag==0),"rsi_14"]=50.0
        out.append(x)
    return pd.concat(out,ignore_index=True).sort_values(["date","ticker"]).reset_index(drop=True)


def apply_j1_confirmation(signals: pd.DataFrame, prices_features: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    """Retourne uniquement les signaux confirmés, redatés au close de confirmation.

    TABPORT exécutera ensuite à l'open strictement postérieur : aucune utilisation du close J+1
    pour entrer à l'open de cette même séance.
    """
    c=ConfirmationEntry()
    p=prices_features.copy(); p["date"]=pd.to_datetime(p["date"],utc=True)
    rows=[]; audit=[]
    for _,sig in signals.iterrows():
        ticker=str(sig["ticker"]); sd=pd.to_datetime(sig["date"],utc=True)
        bars=p[(p["ticker"].astype(str)==ticker)&(p["date"]>sd)].sort_values("date")
        if bars.empty:
            audit.append({"ticker":ticker,"signal_date":sd,"status":"BLOCK","reason":"NO_CONFIRMATION_BAR"}); continue
        bar=bars.iloc[0]
        decision,reason=c.should_enter(sig,bar)
        status="CONFIRMED" if decision is True else ("REJECT" if decision is False else "WAIT")
        audit.append({"ticker":ticker,"signal_date":sd,"confirmation_date":bar["date"],"status":status,"reason":reason})
        if decision is True:
            r=sig.copy(); r["original_signal_date"]=sd; r["date"]=bar["date"]
            r["confirmation_reason"]=reason; r["confirmation_status"]="CONFIRMED"; rows.append(r)
    confirmed=pd.DataFrame(rows)
    if not confirmed.empty:
        confirmed=confirmed.sort_values(["date","EV_net","ticker"],ascending=[True,False,True]).reset_index(drop=True)
        confirmed=confirmed.drop_duplicates(["date","ticker"],keep="first")
    return confirmed,pd.DataFrame(audit)


class TabportAntiFP65k(Tabport65k):
    """TABPORT avec FPEarlyExit appliqué chronologiquement à chaque barre."""
    def __init__(self, config: TabportConfig|None=None):
        super().__init__(config); self.fp_exit=FPEarlyExit(stop_final=-self.cfg.stop_pct)

    def run(self, signals: pd.DataFrame, prices: pd.DataFrame) -> dict:
        s=self._normalize_signals(signals)
        required={"date","ticker","open","high","low","close","vol_z","rsi_14"}
        miss=required-set(prices.columns)
        if miss: raise ValueError(f"BLOCK_DATA_TABPORT_ANTIFP: prices missing {sorted(miss)}")
        p=self._normalize_prices(prices[["date","ticker","open","high","low","close"]]).merge(
            prices[["date","ticker","vol_z","rsi_14"]],on=["date","ticker"],how="left",validate="one_to_one")
        if "tier" in s.columns: s=s[s["tier"].isin(self.cfg.allowed_tiers)].copy()
        s=s[s["EV_net"]>=0].copy()
        if s.empty: raise ValueError("BLOCK_DATA_TABPORT_ANTIFP: no eligible signals")
        price_dates=p.groupby("ticker")["date"].apply(list).to_dict(); last_date=p.groupby("ticker")["date"].max().to_dict()
        scheduled={}; skipped=[]
        for _,row in s.iterrows():
            nxt=next((d for d in price_dates.get(row["ticker"],[]) if d>row["date"]),None)
            if nxt is None: skipped.append({"signal_date":row["date"],"ticker":row["ticker"],"reason":"NO_J1_BAR"})
            else: scheduled.setdefault(nxt,[]).append(row.to_dict())
        bars={d:g.set_index("ticker") for d,g in p.groupby("date",sort=True)}; dates=sorted(bars)
        cash=float(self.cfg.initial_cash); positions={}; ledger=[]; equity=[]; em={}; ey={}

        def close_pos(ticker,date,reason,raw_exit):
            nonlocal cash
            pos=positions.pop(ticker); sell=float(raw_exit)*(1-self.cfg.slippage_rate); gross=sell*pos["shares"]; fee=gross*self.cfg.fee_rate
            cash+=gross-fee; pnl=(gross-fee)-pos["cash_out"]
            ledger.append({"ticker":ticker,"signal_date":pos["signal_date"],"entry_date":pos["entry_date"],"exit_date":date,
                "shares":pos["shares"],"entry_price":pos["entry_price"],"exit_price":sell,"entry_fee":pos["entry_fee"],"exit_fee":fee,
                "fees_total":pos["entry_fee"]+fee,"slippage_rate_side":self.cfg.slippage_rate,"cash_invested":pos["cash_out"],"pnl_net":pnl,
                "return_net":pnl/pos["cash_out"],"exit_reason":reason,"sessions_held":pos["sessions"],"mae":pos["mae"],"mfe":pos["mfe"],"EV_net_signal":pos["EV_net"]})

        def evaluate(ticker,date,bar):
            pos=positions[ticker]; pos["sessions"]+=1; entry=pos["entry_price"]
            prior_peak=pos["mfe"]
            pos["last_close"]=float(bar["close"]); pos["mae"]=min(pos["mae"],float(bar["low"])/entry-1); pos["mfe"]=max(pos["mfe"],float(bar["high"])/entry-1)
            cur={"open":bar["open"],"low":bar["low"],"close":bar["close"],"vol_z":bar.get("vol_z",np.nan),"rsi_14":bar.get("rsi_14",np.nan),"peak_pnl_prior":prior_peak}
            exit_now,reason,realized=self.fp_exit.check_exit(entry,cur,pos["sessions"],None)
            if exit_now:
                close_pos(ticker,date,reason,entry*(1+float(realized))); return True
            if pos["sessions"]>=self.cfg.max_hold_sessions: close_pos(ticker,date,"TIME_26W",float(bar["close"])); return True
            if date==last_date[ticker]: close_pos(ticker,date,"EOP_DATA_END",float(bar["close"])); return True
            return False

        for date in dates:
            day=bars[date]
            for ticker in list(positions):
                if ticker in day.index: evaluate(ticker,date,day.loc[ticker])
            for sig in sorted(scheduled.get(date,[]),key=lambda r:(-float(r["EV_net"]),str(r["ticker"]))):
                ticker=str(sig["ticker"])
                if ticker in positions: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"ALREADY_OPEN"}); continue
                if ticker not in day.index: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"NO_ENTRY_BAR"}); continue
                if len(positions)>=self.cfg.max_positions: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"MAX_POSITIONS"}); continue
                ym=(date.year,date.month)
                if em.get(ym,0)>=self.cfg.max_entries_month: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"MAX_ENTRIES_MONTH"}); continue
                if ey.get(date.year,0)>=self.cfg.max_entries_year: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"MAX_ENTRIES_YEAR"}); continue
                bar=day.loc[ticker]; buy=float(bar["open"])*(1+self.cfg.slippage_rate); affordable=min(self.cfg.max_position_eur,cash)
                shares=floor(affordable/(buy*(1+self.cfg.fee_rate)))
                if shares<1: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"INSUFFICIENT_CASH"}); continue
                gross=shares*buy; fee=gross*self.cfg.fee_rate; cash_out=gross+fee
                if cash_out>cash+1e-9: skipped.append({"signal_date":sig["date"],"ticker":ticker,"reason":"INSUFFICIENT_CASH"}); continue
                cash-=cash_out; positions[ticker]={"signal_date":sig["date"],"entry_date":date,"shares":shares,"entry_price":buy,"entry_fee":fee,"cash_out":cash_out,
                    "EV_net":float(sig["EV_net"]),"sessions":0,"mae":0.0,"mfe":0.0,"last_close":float(bar["close"])}
                em[ym]=em.get(ym,0)+1; ey[date.year]=ey.get(date.year,0)+1
                evaluate(ticker,date,bar)
            mv=sum(pos["shares"]*pos["last_close"] for pos in positions.values()); equity.append({"date":date,"cash":cash,"market_value":mv,"equity":cash+mv,"open_positions":len(positions)})
        if positions: raise ValueError(f"BLOCK_DATA_TABPORT_ANTIFP: unclosed positions {sorted(positions)}")
        l=pd.DataFrame(ledger); e=pd.DataFrame(equity).sort_values("date").reset_index(drop=True); sk=pd.DataFrame(skipped)
        return {"ledger":l,"equity":e,"skipped":sk,"metrics":self._metrics(l,e),"quarterly":self._period_returns(e,"Q",self.cfg.initial_cash),"yearly":self._period_returns(e,"Y",self.cfg.initial_cash)}
