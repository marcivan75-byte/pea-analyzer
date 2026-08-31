"""
v182/hebdo/expected_value_ranker.py
HEBDO AT META - classement par Expected Value avec garde-fous absolus.
"""

import pandas as pd, numpy as np

class ExpectedValueRanker:
    def __init__(self, avg_win=0.14, avg_loss=-0.09, fee=0.003):
        self.avg_win=avg_win; self.avg_loss=avg_loss; self.fee=fee

    def compute_ev(self, row: pd.Series)->float:
        p_win=row.get('prob_meta',0.5); p_loss=row.get('prob_stop_9',0.3)
        p_win=0.5 if pd.isna(p_win) else float(np.clip(p_win,0,1))
        p_loss=0.3 if pd.isna(p_loss) else float(np.clip(p_loss,0,1))
        total=p_win+p_loss
        if total>1:
            p_win=p_win/total*0.9; p_loss=p_loss/total*0.9
        p_flat=max(0.0,1-p_win-p_loss)
        ev_brut=p_win*self.avg_win + p_loss*self.avg_loss + p_flat*0.02
        malus=0.0
        mom=row.get('mom_26w_sector',0)
        mom=0 if pd.isna(mom) else mom
        mult=0.5 if mom>1.0 else 1.0
        roe=row.get('roe',np.nan)
        dd=row.get('drawdown_4w',0)
        vol_z=row.get('vol_z',0)
        dte=row.get('days_to_earnings',np.nan)
        if pd.notna(roe) and roe<0.03: malus-=0.03*mult
        if pd.notna(dd) and dd<-0.15: malus-=0.02*mult
        if pd.notna(vol_z) and vol_z>4: malus-=0.025*mult
        if pd.notna(dte) and 0 <= dte <= 3: malus-=0.08
        return float(ev_brut+malus-self.fee)

    def rank_batch(self, df: pd.DataFrame):
        df=df.copy()
        if df.empty:
            for col in ['EV_net','tier','market_quality','q85_threshold','q60_threshold','selection_confidence']:
                df[col]=pd.Series(dtype='float64' if col in {'EV_net','q85_threshold','q60_threshold'} else 'object')
            return df
        df['EV_net']=df.apply(self.compute_ev, axis=1)
        if len(df)>=20:
            q85=df['EV_net'].quantile(0.85); q60=df['EV_net'].quantile(0.60)
        else:
            q85=df['EV_net'].median(); q60=df['EV_net'].quantile(0.4)
        df['tier']='EXCLU'
        tct_floor=max(float(q85),0.0)
        ct_floor=max(float(q60),0.0)
        df.loc[df['EV_net']>=tct_floor,'tier']='TCT'
        df.loc[(df['EV_net']>=ct_floor)&(df['EV_net']<tct_floor),'tier']='CT_WATCH'
        df.loc[df['EV_net']<0,'tier']='EXCLU'

        # Aucune conviction TCT ne peut reposer sur prob_meta=0.5 par défaut.
        if 'meta_model_status' in df.columns:
            trained = df['meta_model_status'].eq('TRAINED_TEMPORAL_OOS')
            df.loc[(df['tier']=='TCT') & ~trained, 'tier'] = 'CT_WATCH'
            df['selection_confidence'] = np.where(trained, 'TEMPORAL_OOS_MODEL', 'DEGRADED_UNTRAINED_META')
        else:
            df.loc[df['tier']=='TCT','tier']='CT_WATCH'
            df['selection_confidence']='DEGRADED_NO_META_STATUS'

        df['market_quality']='NORMAL' if q85>0.02 else ('POOR' if q85>0.005 else 'CRASH')
        df['q85_threshold']=q85; df['q60_threshold']=q60
        return df.sort_values('EV_net', ascending=False)
