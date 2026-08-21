from datetime import date

from v182.sources.euronext_ipo_v1_3 import _parse_euronext_table_date


def test_euronext_ambiguous_table_date_is_day_first() -> None:
    assert _parse_euronext_table_date("04/05/2023") == date(2023, 5, 4)
    assert _parse_euronext_table_date("05/04/2023") == date(2023, 4, 5)


def test_iso_table_date_remains_iso() -> None:
    assert _parse_euronext_table_date("2023-05-04") == date(2023, 5, 4)
