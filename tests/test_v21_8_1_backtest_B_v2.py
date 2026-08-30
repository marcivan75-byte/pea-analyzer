import unittest
import numpy as np
import pandas as pd

from v182.backtest.v21_8_1_backtest_B_v2 import (
    compute_true_26w_pnl,
    detect_B_v2,
    run_backtest_B_v2,
)


class TestBacktestBV2(unittest.TestCase):
    def test_true_pnl_hits_intraday_stop(self):
        idx = pd.date_range('2024-01-02', periods=5, freq='D')
        hist = pd.DataFrame({'low':[99, 97, 90, 95, 96], 'close':[100, 98, 92, 96, 97]}, index=idx)
        pnl, hit, day_stop, exit_price = compute_true_26w_pnl(100.0, hist, 0.09)
        self.assertAlmostEqual(pnl, -0.09)
        self.assertTrue(hit)
        self.assertEqual(day_stop, 2)
        self.assertAlmostEqual(exit_price, 91.0)

    def test_true_pnl_uses_final_close_without_stop(self):
        idx = pd.date_range('2024-01-02', periods=5, freq='D')
        hist = pd.DataFrame({'low':[99, 98, 97, 96, 95], 'close':[101, 102, 103, 104, 110]}, index=idx)
        pnl, hit, day_stop, exit_price = compute_true_26w_pnl(100.0, hist, 0.09)
        self.assertAlmostEqual(pnl, 0.10)
        self.assertFalse(hit)
        self.assertIsNone(day_stop)
        self.assertAlmostEqual(exit_price, 110.0)

    def test_b1_and_b2_daily_detection(self):
        idx = pd.date_range('2024-01-01', periods=4, freq='D')
        df = pd.DataFrame({
            'close':[100.0, 97.0, 97.5, 98.0],
            'volume':[100.0, 250.0, 100.0, 100.0],
            'volume_avg20':[100.0, 100.0, 100.0, 100.0],
        }, index=idx)
        z = detect_B_v2(df)
        self.assertTrue(bool(z.loc[idx[1], 'B1_vol']))
        self.assertFalse(bool(z.loc[idx[1], 'B2_daily']))
        self.assertTrue(bool(z.loc[idx[2], 'B2_daily']))
        self.assertEqual(z.loc[idx[1], 'B_signal_type'], 'B1_VOL')
        self.assertEqual(z.loc[idx[2], 'B_signal_type'], 'B2_DAILY')

    def test_backtest_records_real_stop_exit_date(self):
        idx = pd.date_range('2024-01-01', periods=12, freq='D')
        df = pd.DataFrame({
            'open':np.full(12, 100.0),
            'high':np.full(12, 101.0),
            'low':np.full(12, 95.0),
            'close':[100.0, 97.0] + [97.0]*10,
            'volume':[100.0, 250.0] + [100.0]*10,
            'volume_avg20':np.full(12, 100.0),
        }, index=idx)
        df.loc[idx[3], 'low'] = 80.0
        out = run_backtest_B_v2(df, stop_pct=0.09, forward_days=5)
        b1 = out[out['B1_vol']].iloc[0]
        self.assertTrue(bool(b1['hit_stop']))
        self.assertEqual(pd.Timestamp(b1['exit_date']), idx[3])
        self.assertAlmostEqual(float(b1['pnl_true']), -0.09)


if __name__ == '__main__':
    unittest.main()
