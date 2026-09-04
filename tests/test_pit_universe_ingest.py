import unittest
from datetime import date

from v182.backtest.pit_universe import ExitReason, ListingEventType
from v182.backtest.pit_universe_ingest import build_exit_audit, classify_exit_evidence


class TestPITUniverseIngest(unittest.TestCase):
    def test_market_transfer_is_not_terminal(self):
        r = classify_exit_evidence(
            security_id='FR0000000001', effective_date=date(2011, 9, 14),
            source_code='EURONEXT_NOTICES',
            row={'title': 'Delisting following transfer to Alternext'},
        )
        self.assertEqual(r.listing_event_type, ListingEventType.MARKET_TRANSFER)
        self.assertIsNone(r.terminal_reason)
        self.assertFalse(r.quarantined)

    def test_bankruptcy_is_explicit_terminal_event(self):
        r = classify_exit_evidence(
            security_id='FR0000000002', effective_date=date(2024, 1, 1),
            source_code='EURONEXT_NOTICES',
            row={'reason': 'bankruptcy and delisting'},
        )
        self.assertEqual(r.terminal_reason, ExitReason.BANKRUPTCY)
        self.assertFalse(r.quarantined)

    def test_bare_delisting_is_quarantined(self):
        r = classify_exit_evidence(
            security_id='FR0000000003', effective_date=date(2024, 1, 1),
            source_code='ESMA_FIRDS', row={'event_type': 'termination'},
        )
        self.assertEqual(r.listing_event_type, ListingEventType.DELISTED)
        self.assertIsNone(r.terminal_reason)
        self.assertTrue(r.quarantined)

    def test_audit_blocks_unresolved_evidence(self):
        good = classify_exit_evidence(
            security_id='A', effective_date=date(2024, 1, 1),
            source_code='EURONEXT_NOTICES', row={'reason': 'liquidation'},
        )
        bad = classify_exit_evidence(
            security_id='B', effective_date=date(2024, 1, 1),
            source_code='ESMA_FIRDS', row={'event_type': 'termination'},
        )
        a = build_exit_audit([good, bad])
        self.assertEqual(a['quarantine_count'], 1)
        self.assertEqual(a['quarantine_security_ids'], ['B'])
        self.assertFalse(a['strict_exit_evidence_ready'])


if __name__ == '__main__':
    unittest.main()
