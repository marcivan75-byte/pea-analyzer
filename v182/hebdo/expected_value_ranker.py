"""
v182/hebdo/expected_value_ranker.py
HEBDO AT META - classement par EV proxy avec garde-fous de calibration et de complétude explicites.
"""

import pandas as pd, numpy as np

META_VALID_STATUS='TRAINED_PURGED_TEMPORAL_OOS'
MAE_VALID_STATUS='CALIBRATED_TEMPORAL_OOS'
EV_VALID_STATUS='EMPIRICAL_PURGED_TEMPORAL_OOS'

class ExpectedValueRanker:
    def __init__(self,avg_win=0.14,avg_loss=-0.09,fee=0.003,parameter_status='PARAMETRIC_UNCALIBRATED'):
        self.avg_win=float(avg_win); self.avg_loss=float(avg_loss); self.fee=float(fee)
        if not all(np.isfinite([self.avg_win,self.avg_loss,self.fee])) or self.avg_win<=0 or self.avg_loss>=0 or self.fee<0:
            raise ValueError('BLOCK_DATA_EV: invalid payoff/fee parameters')
        self.ev_status=str(parameter_status)

    @staticmethod
    def _num(v,default=np.nan):
        x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0]
        return float(x) if pd.notna(x) and np.isfinite(float(x)) else default

    def compute_ev(self,row:pd.Series)->float:
        p_win=float(np.clip(self._num(row.get('prob_meta'),0.5),0,1))
        mae_status=str(row.get('mae_model_status','UNAVAILABLE'))
        raw_loss=self._num(row.get('prob_stop_9'))
        p_loss=float(np.clip(raw_loss,0,1)) if mae_status==MAE_VALID_STATUS and pd.notna(raw_loss) else 0.30
        total=p_win+p_loss
        if total>1: p_win=p_win/total*0.9; p_loss=p_loss/total*0.9
        p_flat=max(0.0,1-p_win-p_loss)
        ev_brut=p_win*self.avg_win+p_loss*self.avg_loss+p_flat*0.02

        mom=self._num(row.get('mom_26w_sector'),0.0); mult=0.5 if mom>1.0 else 1.0
        roe=self._num(row.get('roe')); dd=self._num(row.get('drawdown_4w')); vol_z=self._num(row.get('vol_z')); dte=self._num(row.get('days_to_earnings'))
        malus=0.0
        if pd.notna(roe) and roe<0.03: malus-=0.03*mult
        if pd.notna(dd) and dd<-0.15: malus-=0.02*mult
        if pd.notna(vol_z) and vol_z>4: malus-=0.025*mult
        if pd.notna(dte) and 0<=dte<=3: malus-=0.08
        proxy=self._num(row.get('risk_stop_9_proxy'))
        if mae_status!=MAE_VALID_STATUS and pd.notna(proxy): malus-=max(0.0,proxy-0.5)*0.04
        return float(ev_brut+malus-self.fee)

    def rank_batch(self,df:pd.DataFrame):
        df=df.copy()
        if df.empty:
            for col in ['EV_net','tier','market_quality','q85_threshold','q60_threshold','selection_confidence','ev_model_status']:
                df[col]=pd.Series(dtype='float64' if col in {'EV_net','q85_threshold','q60_threshold'} else 'object')
            return df
        df['EV_net']=df.apply(self.compute_ev,axis=1)
        if not np.isfinite(df['EV_net'].to_numpy(dtype=float)).all(): raise ValueError('BLOCK_DATA_EV: non-finite EV')
        if len(df)>=20: q85=df['EV_net'].quantile(0.85); q60=df['EV_net'].quantile(0.60)
        else: q85=df['EV_net'].median(); q60=df['EV_net'].quantile(0.4)
        df['tier']='EXCLU'; tct_floor=max(float(q85),0.0); ct_floor=max(float(q60),0.0)
        df.loc[df['EV_net']>=tct_floor,'tier']='TCT'; df.loc[(df['EV_net']>=ct_floor)&(df['EV_net']<tct_floor),'tier']='CT_WATCH'; df.loc[df['EV_net']<0,'tier']='EXCLU'

        meta_ok=df.get('meta_model_status',pd.Series('',index=df.index)).eq(META_VALID_STATUS)
        mae_ok=df.get('mae_model_status',pd.Series('',index=df.index)).eq(MAE_VALID_STATUS)
        ev_ok=pd.Series(self.ev_status==EV_VALID_STATUS,index=df.index)
        dte=pd.to_numeric(df['days_to_earnings'],errors='coerce') if 'days_to_earnings' in df.columns else pd.Series(np.nan,index=df.index)
        roe=pd.to_numeric(df['roe'],errors='coerce') if 'roe' in df.columns else pd.Series(np.nan,index=df.index)
        debt=pd.to_numeric(df['debt_to_equity'],errors='coerce') if 'debt_to_equity' in df.columns else pd.Series(np.nan,index=df.index)
        event_ok=dte.notna() & np.isfinite(dte) & (dte>5)
        quality_ok=roe.notna() & debt.notna() & np.isfinite(roe) & np.isfinite(debt)
        full=meta_ok & mae_ok & ev_ok & event_ok & quality_ok
        df.loc[(df['tier']=='TCT') & ~full,'tier']='CT_WATCH'
        df['selection_confidence']=np.where(full,'FULL_PURGED_TEMPORAL_OOS_VALIDATION','DEGRADED_UNVALIDATED_COMPONENTS')
        df['event_data_status']=np.where(event_ok,'KNOWN_CLEAR_GT5D','MISSING_OR_NOT_CLEAR')
        df['quality_data_status']=np.where(quality_ok,'KNOWN','MISSING')
        df['ev_model_status']=self.ev_status
        df['market_quality']='NORMAL' if q85>0.02 else ('POOR' if q85>0.005 else 'CRASH')
        df['q85_threshold']=q85; df['q60_threshold']=q60
        return df.sort_values('EV_net',ascending=False)
