from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd

from v182.features.etf_rank_trajectory import update_etf_rank_trajectories


def _value(observations:list[dict],field:str):
    return next(row["value"] for row in observations if row["field"]==field)


def test_first_snapshot_creates_history_but_no_trajectory(tmp_path):
    etfs=pd.DataFrame([{"isin":"LU0000000001","rank_cat_1y":20,"rank_cat_3y":30,"rank_cat_5y":40}])
    history=tmp_path/"rank_history.csv"
    observations,failures=update_etf_rank_trajectories(
        etfs,history,observed_at=datetime(2026,8,14,tzinfo=timezone.utc)
    )
    assert failures==[]
    assert observations==[]
    saved=pd.read_csv(history)
    assert len(saved)==1
    assert float(saved.loc[0,"rank_cat_1y"])==20.0


def test_positive_trajectory_means_rank_improved(tmp_path):
    history=tmp_path/"rank_history.csv"
    old=pd.DataFrame([
        {"isin":"LU0000000001","observed_at":"2025-08-14T00:00:00+00:00","rank_cat_1y":35,"rank_cat_3y":None,"rank_cat_5y":None},
        {"isin":"LU0000000001","observed_at":"2024-08-14T00:00:00+00:00","rank_cat_1y":None,"rank_cat_3y":45,"rank_cat_5y":None},
        {"isin":"LU0000000001","observed_at":"2023-08-15T00:00:00+00:00","rank_cat_1y":None,"rank_cat_3y":None,"rank_cat_5y":55},
    ])
    old.to_csv(history,index=False)
    etfs=pd.DataFrame([{"isin":"LU0000000001","rank_cat_1y":20,"rank_cat_3y":30,"rank_cat_5y":40}])
    observations,failures=update_etf_rank_trajectories(
        etfs,history,observed_at=datetime(2026,8,14,tzinfo=timezone.utc),tolerance_days=5
    )
    assert failures==[]
    assert _value(observations,"rank_cat_trajectory_12m")==15.0
    assert _value(observations,"rank_cat_trajectory_24m")==15.0
    assert _value(observations,"rank_cat_trajectory_36m")==15.0


def test_invalid_rank_outside_1_100_is_not_snapshotted_or_imputed(tmp_path):
    etfs=pd.DataFrame([{"isin":"LU0000000001","rank_cat_1y":0,"rank_cat_3y":101,"rank_cat_5y":None}])
    history=tmp_path/"rank_history.csv"
    observations,failures=update_etf_rank_trajectories(etfs,history,observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert failures==[]
    assert observations==[]
    assert not history.exists()
