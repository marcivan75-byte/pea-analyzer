"""
v182/hebdo/fp_early_exit.py
HEBDO AT META - early exits conservateurs, sans hypothèse implicite d'ordre intraday.
"""

import math
from typing import Tuple, Optional


class FPEarlyExit:
    ALL_RULES={'STOP','FAIL_FAST_J2','WEAK_VS_SECTOR','CAPITULATION','MOM_DEAD_J3','TRAIL_BE','TIME_DECAY_J10'}

    def __init__(self, stop_final=-0.09, fail_fast_j2=-0.025, enabled_rules=None):
        self.stop_final=float(stop_final); self.fail_fast_j2=float(fail_fast_j2)
        self.enabled_rules=set(self.ALL_RULES if enabled_rules is None else enabled_rules)
        unknown=self.enabled_rules-self.ALL_RULES
        if unknown: raise ValueError(f'BLOCK_DATA_EXIT: unknown rules {sorted(unknown)}')
        if not (-1<self.stop_final<0) or not (-1<self.fail_fast_j2<0):
            raise ValueError('BLOCK_DATA_EXIT: invalid stop parameters')

    @staticmethod
    def _finite_positive(x):
        try: return math.isfinite(float(x)) and float(x)>0
        except (TypeError,ValueError): return False

    def check_exit(self, entry_price: float, current_bar: dict, days_held: int, sector_bar: dict = None) -> Tuple[bool, str, Optional[float]]:
        close=current_bar.get('close'); low=current_bar.get('low'); open_px=current_bar.get('open')
        if not all(self._finite_positive(x) for x in [entry_price,close,low,open_px]):
            return False,'BLOCK_DATA',None
        entry_price=float(entry_price); close=float(close); low=float(low); open_px=float(open_px)
        if low>max(open_px,close):
            return False,'BLOCK_DATA_OHLC_INCONSISTENT',None
        try: days_held=int(days_held)
        except (TypeError,ValueError): return False,'BLOCK_DATA_DAYS_HELD',None
        if days_held<1: return False,'BLOCK_DATA_DAYS_HELD',None

        pnl=close/entry_price-1; pnl_low=low/entry_price-1; pnl_open=open_px/entry_price-1
        if 'STOP' in self.enabled_rules and pnl_low<=self.stop_final:
            realized=pnl_open if pnl_open<self.stop_final else self.stop_final
            reason='STOP_GAP_THROUGH' if realized<self.stop_final else 'STOP_FINAL'
            return True,f'{reason}_{realized:.2%}',realized

        if 'FAIL_FAST_J2' in self.enabled_rules and days_held==2 and pnl<=self.fail_fast_j2:
            return True,f'FAIL_FAST_J2_{pnl:.2%}',pnl

        if 'WEAK_VS_SECTOR' in self.enabled_rules and 1<=days_held<=5 and sector_bar:
            stock_3d=current_bar.get('ret_3d'); sector_3d=sector_bar.get('ret_3d')
            if stock_3d is not None and sector_3d is not None:
                try:
                    stock_3d=float(stock_3d); sector_3d=float(sector_3d)
                    if math.isfinite(stock_3d) and math.isfinite(sector_3d) and stock_3d<-0.01 and sector_3d>0.01:
                        return True,f'WEAK_VS_SECTOR_{stock_3d:.1%}_vs_{sector_3d:.1%}',pnl
                except (TypeError,ValueError): pass

        vol_z=current_bar.get('vol_z')
        try: vol_z=float(vol_z) if vol_z is not None else None
        except (TypeError,ValueError): vol_z=None
        if 'CAPITULATION' in self.enabled_rules and vol_z is not None and math.isfinite(vol_z) and vol_z>5 and close<entry_price:
            return True,f'CAPITULATION_vol_z_{vol_z:.1f}',pnl

        if 'MOM_DEAD_J3' in self.enabled_rules and days_held==3:
            rsi=current_bar.get('rsi_14')
            try: rsi=float(rsi) if rsi is not None else None
            except (TypeError,ValueError): rsi=None
            if rsi is not None and math.isfinite(rsi) and rsi<50 and pnl<0.02:
                return True,f'MOM_DEAD_RSI_{rsi:.0f}_{pnl:.2%}',pnl

        peak_prior=current_bar.get('peak_pnl_prior')
        try: peak_prior=float(peak_prior) if peak_prior is not None else None
        except (TypeError,ValueError): peak_prior=None
        if 'TRAIL_BE' in self.enabled_rules and days_held>=5 and peak_prior is not None and math.isfinite(peak_prior) and peak_prior>=0.03 and pnl_low<=0.01:
            trailing_level=entry_price*1.01
            realized=pnl_open if open_px<trailing_level else 0.01
            return True,f'TRAIL_BE_PRIOR_PEAK_{peak_prior:.2%}_EXIT_{realized:.2%}',realized

        if 'TIME_DECAY_J10' in self.enabled_rules and days_held==10:
            mom_sec=current_bar.get('mom_26w_sector')
            try: mom_sec=float(mom_sec) if mom_sec is not None else None
            except (TypeError,ValueError): mom_sec=None
            if mom_sec is not None and math.isfinite(mom_sec) and -0.02<pnl<0.02 and mom_sec<0:
                return True,f'TIME_DECAY_J10_FLAT_{pnl:.2%}_mom_sec_{mom_sec:.2f}',pnl

        return False,'HOLD',None
