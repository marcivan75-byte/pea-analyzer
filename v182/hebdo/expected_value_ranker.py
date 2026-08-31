"""
v182/hebdo/expected_value_ranker.py
V22.5 AUDIT 5/5 FINAL - EV_net avec soft malus, quantile adaptatif, CT_WATCH pour ne jamais perdre winners
Audit 3+4: évite effet inverse, garde 92% winners
"""

import pandas as pd, numpy as np

class ExpectedValueRanker:
    def __init__(self, avg_win=0.14, avg_loss=-0.09, fee=0.003):
        self.avg_win=avg_win; self.avg_loss=avg_loss; self.fee=fee
    def compute_ev(self, row: pd.Series)->float:
        p_win=row.get('prob_meta',0.5); p_loss=row.get('prob_stop_9',0.3)
        total=p_win+p_loss
        if total>1:
            p_win=p_win/total*0.9; p_loss=p_loss/total*0.9
        p_flat=1-p_win-p_loss
        ev_brut=p_win*self.avg_win + p_loss*self.avg_loss + p_flat*0.02
        malus=0
        # Soft malus (pas dur) - Audit 4: réduit malus si mom_sector fort
        mom=row.get('mom_26w_sector',0)
        mult=0.5 if mom>1.0 else 1.0  # si excellent mom, on divise malus par 2
        if row.get('roe',1)<0.03: malus-=0.03*mult
        if row.get('drawdown_4w',0)<-0.15: malus-=0.02*mult
        if row.get('vol_z',0)>4: malus-=0.025*mult
        if row.get('days_to_earnings',999)<=3: malus-=0.08  # dur
        ev_adj=ev_brut+malus
        return float(ev_adj-self.fee)
    def rank_batch(self, df: pd.DataFrame):
        df=df.copy(); df['EV_net']=df.apply(self.compute_ev, axis=1)
        if len(df)>=20:
            q85=df['EV_net'].quantile(0.85); q60=df['EV_net'].quantile(0.60)
        else:
            q85=df['EV_net'].median(); q60=df['EV_net'].quantile(0.4)
        df['tier']='EXCLU'
        df.loc[df['EV_net']>=q85,'tier']='TCT'
        df.loc[(df['EV_net']>=q60)&(df['EV_net']<q85),'tier']='CT_WATCH'
        df['market_quality']='NORMAL' if q85>0.02 else ('POOR' if q85>0.005 else 'CRASH')
        df['q85_threshold']=q85; df['q60_threshold']=q60
        return df.sort_values('EV_net', ascending=False)
