import unittest
from datetime import date

from v182.backtest.pit_universe_sources import (
    EvidenceKind, source_by_code, sources_for, validate_registry,
)


class TestPITUniverseSources(unittest.TestCase):
    def test_registry_is_structurally_valid(self):
        self.assertEqual(validate_registry(), [])

    def test_2011_termination_uses_euronext_not_esma(self):
        codes = [s.code for s in sources_for(EvidenceKind.TERMINATION, date(2011, 9, 14))]
        self.assertIn('EURONEXT_NOTICES', codes)
        self.assertNotIn('ESMA_FIRDS', codes)

    def test_2024_termination_has_esma_and_euronext_priority_one(self):
        rows = sources_for(EvidenceKind.TERMINATION, date(2024, 1, 1))
        codes = [s.code for s in rows]
        self.assertIn('EURONEXT_NOTICES', codes)
        self.assertIn('ESMA_FIRDS', codes)
        self.assertEqual(source_by_code('EURONEXT_NOTICES').priority, 1)
        self.assertEqual(source_by_code('ESMA_FIRDS').priority, 1)

    def test_pea_eligibility_does_not_fall_back_to_market_listing(self):
        codes = [s.code for s in sources_for(EvidenceKind.PEA_ELIGIBILITY, date(2015, 1, 1))]
        self.assertEqual(codes, ['PEA_OFFICIAL_EVIDENCE'])


if __name__ == '__main__':
    unittest.main()
