from pathlib import Path

import pandas as pd
import pytest

from v182.tct.preopen_enricher import (
    CANDIDATE_STATE_REL,
    PreopenBlocked,
    prepare_candidates,
)


def _write_decisions(root: Path, rows: list[dict]) -> None:
    path = root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_prepare_candidates_bounds_union_to_40_and_deduplicates(tmp_path: Path):
    rows: list[dict] = []
    for i in range(25):
        rows.append({"isin": f"FR{i:010d}", "ticker": f"TCT{i}.PA", "horizon": "TCT", "score": 100 - i})
    for i in range(25):
        # First five ISINs overlap with TCT and must be deduplicated.
        isin = f"FR{i:010d}" if i < 5 else f"DE{i:010d}"
        rows.append({"isin": isin, "ticker": f"CT{i}.DE", "horizon": "CT", "score": 90 - i})
    _write_decisions(tmp_path, rows)

    bounded = prepare_candidates(tmp_path, max_tct=20, max_ct=20)

    assert len(bounded) <= 40
    assert int((bounded["_horizon"] == "TCT").sum()) <= 20
    assert int((bounded["_horizon"] == "CT").sum()) <= 20
    assert bounded["isin"].nunique() == len(bounded)
    assert (tmp_path / CANDIDATE_STATE_REL).is_file()


def test_prepare_candidates_fails_closed_without_validated_ticker(tmp_path: Path):
    _write_decisions(
        tmp_path,
        [{"isin": "FR0000000001", "horizon": "TCT", "score": 90.0}],
    )
    with pytest.raises(PreopenBlocked, match="validated ticker"):
        prepare_candidates(tmp_path)


def test_prepare_candidates_fails_closed_when_preselection_missing(tmp_path: Path):
    with pytest.raises(PreopenBlocked, match="preselection"):
        prepare_candidates(tmp_path)
