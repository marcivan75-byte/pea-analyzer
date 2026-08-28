import pandas as pd

from v182.reporting.etf_morningstar_hydration import hydrate_etf_morningstar_from_boursorama


def test_edeu_ok_stars_fill_morningstar_rating():
    frame = pd.DataFrame(
        [
            {
                "asset_class": "ETF",
                "isin": "LU1615090864",
                "morningstar_rating": pd.NA,
                "boursorama_etf_morningstar_stars": 4.0,
                "boursorama_etf_morningstar_parse_status": "OK",
            }
        ]
    )
    out = hydrate_etf_morningstar_from_boursorama(frame)
    assert float(out.loc[0, "morningstar_rating"]) == 4.0
    assert out.loc[0, "morningstar_rating_source"] == "BOURSORAMA_ETF_STARS_OK"


def test_unresolved_stars_stay_missing():
    frame = pd.DataFrame(
        [
            {
                "asset_class": "ETF",
                "isin": "FR0010405431",
                "morningstar_rating": pd.NA,
                "boursorama_etf_morningstar_stars": 5.0,
                "boursorama_etf_morningstar_parse_status": "BLOCK_MISSING",
            }
        ]
    )
    out = hydrate_etf_morningstar_from_boursorama(frame)
    assert pd.isna(out.loc[0, "morningstar_rating"])


def test_existing_master_rating_is_not_overwritten():
    frame = pd.DataFrame(
        [
            {
                "asset_class": "ETF",
                "isin": "X",
                "morningstar_rating": 3.0,
                "boursorama_etf_morningstar_stars": 5.0,
                "boursorama_etf_morningstar_parse_status": "OK",
            }
        ]
    )
    out = hydrate_etf_morningstar_from_boursorama(frame)
    assert float(out.loc[0, "morningstar_rating"]) == 3.0


def test_missing_boursorama_columns_do_not_crash():
    frame = pd.DataFrame([{"asset_class": "ETF", "isin": "LU1615090864", "morningstar_rating": pd.NA}])
    out = hydrate_etf_morningstar_from_boursorama(frame)
    assert pd.isna(out.loc[0, "morningstar_rating"])
