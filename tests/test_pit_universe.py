import unittest
from datetime import date

from v182.backtest.pit_universe import (
    UniverseMembership, coverage_report, universe_as_of, validate_memberships,
)


class TestPITUniverse(unittest.TestCase):
    def test_universe_is_effective_dated(self):
        rows = [
            UniverseMembership('A', date(2020, 1, 1), date(2021, 12, 31)),
            UniverseMembership('B', date(2021, 1, 1), None),
        ]
        self.assertEqual(universe_as_of('2020-06-01', rows), {'A'})
        self.assertEqual(universe_as_of('2021-06-01', rows), {'A', 'B'})
        self.assertEqual(universe_as_of('2022-06-01', rows), {'B'})

    def test_strict_mode_excludes_uncertain_reconstruction(self):
        rows = [
            UniverseMembership('CONFIRMED', date(2020, 1, 1), confidence=1.0),
            UniverseMembership('ESTIMATED', date(2020, 1, 1), confidence=0.8),
        ]
        self.assertEqual(universe_as_of('2020-06-01', rows), {'CONFIRMED'})
        self.assertEqual(
            universe_as_of('2020-06-01', rows, min_confidence=0.8),
            {'CONFIRMED', 'ESTIMATED'},
        )

    def test_overlapping_intervals_fail_validation(self):
        rows = [
            UniverseMembership('A', date(2020, 1, 1), date(2021, 12, 31)),
            UniverseMembership('A', date(2021, 1, 1), None),
        ]
        errors = validate_memberships(rows)
        self.assertTrue(any('overlapping membership intervals' in e for e in errors))

    def test_price_coverage_reports_missing_historical_member(self):
        rows = [
            UniverseMembership('SURVIVOR', date(2020, 1, 1), None),
            UniverseMembership('DELISTED_LATER', date(2020, 1, 1), date(2020, 12, 31)),
        ]
        prices = {date(2020, 6, 1): {'SURVIVOR'}}
        r = coverage_report(['2020-06-01'], rows, prices)[0]
        self.assertEqual(r['universe_count'], 2)
        self.assertEqual(r['missing_security_ids'], ['DELISTED_LATER'])
        self.assertEqual(r['coverage_price_pct'], 50.0)


if __name__ == '__main__':
    unittest.main()
