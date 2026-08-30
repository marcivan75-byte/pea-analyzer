import unittest
from v182.backtest.at_weekly_exit_opt_v7_protective_stop import fixed_stop_fill


class ProtectiveStopContractTests(unittest.TestCase):
    def test_touch_fills_at_stop(self):
        fill = fixed_stop_fill(98.0, 94.0, 95.0)
        self.assertEqual(fill, (95.0, 'TOUCH'))

    def test_gap_through_fills_at_actual_open(self):
        fill = fixed_stop_fill(92.0, 90.0, 95.0)
        self.assertEqual(fill, (92.0, 'GAP_OPEN'))

    def test_no_touch_no_fill(self):
        self.assertIsNone(fixed_stop_fill(98.0, 96.0, 95.0))

    def test_exact_open_at_stop(self):
        self.assertEqual(fixed_stop_fill(95.0, 94.0, 95.0), (95.0, 'GAP_OPEN'))

    def test_five_pct_stop_level(self):
        entry = 100.0
        stop = entry * 0.95
        self.assertEqual(stop, 95.0)
        self.assertEqual(fixed_stop_fill(100.0, 94.9, stop), (95.0, 'TOUCH'))

    def test_invalid_stop_rejected(self):
        with self.assertRaises(ValueError):
            fixed_stop_fill(100.0, 90.0, 0.0)


if __name__ == '__main__':
    unittest.main()
