import unittest
from datetime import date

from v182.backtest.pit_universe import (
    UniverseMembership,
    coverage_report,
    strict_certification_status,
    universe_as_of,
    validate_memberships,
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

    def test_strict_certification_passes_only_all_gates(self):
        r = strict_certification_status(
            membership_errors=[],
            price_coverage_pct=99.5,
            terminal_event_coverage_pct=100.0,
            unresolved_disappearance_count=0,
            quarantine_count=0,
            current_universe_used_for_history=False,
            silent_held_disappearance_count=0,
        )
        self.assertTrue(r['certified'])
        self.assertEqual(r['status'], 'PIT_STRICT_CERTIFIED')
        self.assertEqual(r['failed_gates'], [])

    def test_strict_certification_is_fail_closed(self):
        r = strict_certification_status(
            membership_errors=['A: overlapping membership intervals'],
            price_coverage_pct=98.9,
            terminal_event_coverage_pct=98.0,
            unresolved_disappearance_count=1,
            quarantine_count=2,
            current_universe_used_for_history=True,
            silent_held_disappearance_count=1,
        )
        self.assertFalse(r['certified'])
        self.assertEqual(r['status'], 'PIT_STRICT_NOT_CERTIFIED')
        self.assertIn('price_coverage', r['failed_gates'])
        self.assertIn('terminal_event_coverage', r['failed_gates'])
        self.assertIn('no_unresolved_disappearances', r['failed_gates'])
        self.assertIn('no_quarantined_exit_evidence', r['failed_gates'])
        self.assertIn('no_current_universe_for_history', r['failed_gates'])
        self.assertIn('no_silent_held_disappearance', r['failed_gates'])


if __name__ == '__main__':
    unittest.main()
