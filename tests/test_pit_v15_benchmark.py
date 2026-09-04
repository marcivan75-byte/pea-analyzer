import unittest
import pandas as pd

from v182.backtest.benchmark_pit_v15 import (
    BenchmarkPITError, normalize_benchmark_universe, qualify_benchmark,
    snapshot, survivorship_audit,
)


class TestPITV15Benchmark(unittest.TestCase):
    def base(self):
        return pd.DataFrame([
            {
                'date_signal':'2024-01-05','isin':'FR0000000001','pea_eligible_of_record':True,
                'knowledge_date':'2024-01-05','source':'BOFIP_MAPPING','delisted':False,
                'last_trading_date':None,
            },
            {
                'date_signal':'2024-01-05','isin':'FR0000000002','pea_eligible_of_record':True,
                'knowledge_date':'2024-01-04','source':'BOFIP_MAPPING','delisted':True,
                'last_trading_date':'2024-06-30',
            },
        ])

    def two_snapshots(self, documented=True):
        rows = self.base().to_dict('records')
        rows.append({
            'date_signal':'2024-07-05','isin':'FR0000000001','pea_eligible_of_record':True,
            'knowledge_date':'2024-07-05','source':'BOFIP_MAPPING','delisted':False,
            'last_trading_date':None,
        })
        if not documented:
            rows[1]['delisted'] = False
            rows[1]['last_trading_date'] = None
        return pd.DataFrame(rows)

    def test_future_knowledge_date_fails(self):
        z=self.base(); z.loc[0,'knowledge_date']='2024-01-06'
        with self.assertRaises(BenchmarkPITError):
            normalize_benchmark_universe(z)

    def test_duplicate_member_fails(self):
        z=pd.concat([self.base(),self.base().iloc[[0]]],ignore_index=True)
        with self.assertRaises(BenchmarkPITError):
            normalize_benchmark_universe(z)

    def test_delisted_after_last_trading_date_fails(self):
        z=self.base(); z.loc[1,'date_signal']='2024-07-05'
        with self.assertRaises(BenchmarkPITError):
            normalize_benchmark_universe(z)

    def test_delisted_history_is_preserved_before_exit(self):
        r=qualify_benchmark(self.base())
        self.assertEqual(r['status'],'BENCHMARK_PIT_READY')
        self.assertTrue(r['delisted_rows_present'])
        self.assertFalse(r['performance_backtest_authorized_by_this_module'])

    def test_snapshot_is_pit_eligible_only(self):
        z=self.base(); z.loc[0,'pea_eligible_of_record']=False
        q=snapshot(z,'2024-01-05')
        self.assertEqual(list(q['isin']),['FR0000000002'])

    def test_documented_historical_disappearance_is_accepted(self):
        r = survivorship_audit(self.two_snapshots(documented=True))
        self.assertEqual(r['absent_from_latest_count'], 1)
        self.assertEqual(r['documented_terminal_or_exit_count'], 1)
        self.assertEqual(r['unresolved_disappearance_count'], 0)
        self.assertTrue(r['survivorship_control_ready'])

    def test_silent_disappearance_blocks_pit_readiness(self):
        z = self.two_snapshots(documented=False)
        r = survivorship_audit(z)
        self.assertEqual(r['unresolved_disappearance_isins'], ['FR0000000002'])
        self.assertFalse(r['survivorship_control_ready'])
        q = qualify_benchmark(z)
        self.assertEqual(q['status'], 'BENCHMARK_PIT_NOT_READY')


if __name__=='__main__':
    unittest.main()
