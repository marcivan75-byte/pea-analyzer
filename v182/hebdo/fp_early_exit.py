"""
v182/hebdo/fp_early_exit.py
HEBDO AT META - early exits for false positives with conservative realized P&L semantics.
"""

from typing import Tuple, Optional


class FPEarlyExit:
    def __init__(self, stop_final=-0.09, fail_fast_j2=-0.025):
        self.stop_final = stop_final
        self.fail_fast_j2 = fail_fast_j2

    def check_exit(self, entry_price: float, current_bar: dict, days_held: int, sector_bar: dict = None) -> Tuple[bool, str, Optional[float]]:
        close = current_bar.get('close')
        low = current_bar.get('low', close)
        open_px = current_bar.get('open', close)
        if close is None or low is None or open_px is None or entry_price is None or entry_price <= 0:
            return False, "BLOCK_DATA", None

        pnl = close / entry_price - 1
        pnl_low = low / entry_price - 1
        pnl_open = open_px / entry_price - 1
        if pnl_low <= self.stop_final:
            # Si l'ouverture est déjà sous le stop, l'exécution au stop théorique est impossible.
            realized = pnl_open if pnl_open < self.stop_final else self.stop_final
            reason = "STOP_GAP_THROUGH" if realized < self.stop_final else "STOP_FINAL"
            return True, f"{reason}_{realized:.2%}", realized

        if days_held == 2 and pnl <= self.fail_fast_j2:
            return True, f"FAIL_FAST_J2_{pnl:.2%}", pnl

        if 1 <= days_held <= 5 and sector_bar:
            stock_3d = current_bar.get('ret_3d', pnl)
            sector_3d = sector_bar.get('ret_3d', 0)
            if stock_3d < -0.01 and sector_3d > 0.01:
                return True, f"WEAK_VS_SECTOR_{stock_3d:.1%}_vs_{sector_3d:.1%}", pnl

        vol_z = current_bar.get('vol_z', 0)
        if vol_z is not None and vol_z > 5 and close < entry_price:
            return True, f"CAPITULATION_vol_z_{vol_z:.1f}", pnl

        if days_held == 3:
            rsi = current_bar.get('rsi_14', 60)
            if rsi is not None and rsi < 50 and pnl < 0.02:
                return True, f"MOM_DEAD_RSI_{rsi:.0f}_{pnl:.2%}", pnl

        if days_held >= 5 and pnl > 0.03 and pnl_low < 0.01:
            return True, f"TRAIL_BE_+1%_after_+3%_was_{pnl:.2%}", 0.01

        if days_held == 10:
            mom_sec = current_bar.get('mom_26w_sector', 1)
            if -0.02 < pnl < 0.02 and mom_sec is not None and mom_sec < 0:
                return True, f"TIME_DECAY_J10_FLAT_{pnl:.2%}_mom_sec_{mom_sec:.2f}", pnl

        return False, "HOLD", None
