import unittest
import pandas as pd

from v182.backtest.pit_v15 import (
    PITContractError, normalize_pit_table, qualify_trades,
)


class TestPITV15Contract(unittest.TestCase):
    def base_pit(self):
        return pd.DataFrame([{
            'isin':'FR0000000001','ticker_pit':'AAA','date_signal':'2024-01-05',
            'knowledge_date':'2024-01-04','publication_date':'2024-01-03',
            'target_mean_pit':130.0,'analyst_count':4,'eps_revision_4w':2.0,
            'eps_revision_13w':3.0,'source':'P1_FACTSET',
        }])

    def test_future_knowledge_date_fails(self):
        z=self.base_pit(); z.loc[0,'knowledge_date']='2024-01-08'
        with self.assertRaises(PITContractError):
            normalize_pit_table(z)

    def test_future_publication_date_fails(self):
        z=self.base_pit(); z.loc[0,'publication_date']='2024-01-06'
        with self.assertRaises(PITContractError):
            normalize_pit_table(z)

    def test_p1_requires_three_analysts(self):
        z=self.base_pit(); z.loc[0,'analyst_count']=2
        with self.assertRaises(PITContractError):
            normalize_pit_table(z)

    def test_duplicate_key_fails(self):
        z=pd.concat([self.base_pit(),self.base_pit()],ignore_index=True)
        with self.assertRaises(PITContractError):
            normalize_pit_table(z)

    def test_ticker_only_trade_history_fails(self):
        trades=pd.DataFrame([{'symbol':'AAA','date_signal':'2024-01-05','close':100}])
        with self.assertRaises(PITContractError):
            qualify_trades(trades,self.base_pit())

    def test_p2_revision_never_becomes_fake_percent_target(self):
        trades=pd.DataFrame([{'isin':'FR0000000001','date_signal':'2024-01-05','close':100.0}])
        pit=pd.DataFrame([{
            'isin':'FR0000000001','date_signal':'2024-01-05','knowledge_date':'2024-01-04',
            'publication_date':'2024-01-03','source':'P2_EPS','eps_revision_4w':8.0,
        }])
        audit,report=qualify_trades(trades,pit)
        self.assertTrue(bool(audit.loc[0,'p2_eps_positive']))
        self.assertTrue(pd.isna(audit.loc[0,'potential_pct']))
        self.assertFalse(bool(audit.loc[0,'potential_gt20_eligible']))
        self.assertFalse(report['p2_eps_is_percent_potential'])

    def test_no_go_until_all_data_gates_proven(self):
        trades=pd.DataFrame([{'isin':'FR0000000001','date_signal':'2024-01-05','close':100.0}])
        _,report=qualify_trades(trades,self.base_pit())
        self.assertEqual(report['status'],'NO_GO_DATA_COVERAGE')
        self.assertFalse(report['performance_backtest_authorized'])


if __name__=='__main__':
    unittest.main()
