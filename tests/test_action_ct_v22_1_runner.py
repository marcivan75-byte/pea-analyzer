from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.reporting.action_ct_shadow_run_v22_1 import run


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_CT_V22_1_0_SHADOW.json").read_text(encoding="utf-8"))


def _master() -> pd.DataFrame:
    rows = []
    for i in range(30):
        rows.append(
            {
                "isin": f"FR{i:010d}",
                "name": f"Synthetic {i}",
                "yahoo_ticker": f"SYN{i}.PA",
                "sector": "Technology" if i < 15 else "Industrials",
                "distance_high_52w_pct": 5.0 + i / 2.0,
                "perf_1m_pct": -2.0 + i * 0.4,
                "perf_3m_pct": -4.0 + i * 0.8,
                "perf_6m_pct": -6.0 + i * 1.1,
                "above_mm50": i >= 8,
                "above_mm200": i >= 12,
                "catchup_52w_score": 40.0 + i,
                "morningstar_rating": 4 if i % 2 == 0 else 3,
                "target_upside_pct_v21": 8.0 + i / 3.0,
                "dividend_yield_pct": 2.0,
                "consensus_score_100_v21": 70.0,
                "consensus_delta_4w": 1.0,
                "net_upgrades_30d_v21": 1.0,
                "days_to_earnings": 10.0,
            }
        )
    return pd.DataFrame(rows)


def _history(ticker: str) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-08-21", periods=180)
    close = np.linspace(100.0, 150.0, len(idx)) + np.sin(np.linspace(0, 10, len(idx)))
    volume = np.full(len(idx), 1_000_000.0)
    volume[-5:] = 1_350_000.0
    return pd.DataFrame(
        {
            "open": close * 0.996,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": volume,
            "yahoo_ticker": ticker,
        },
        index=idx,
    )


def test_v22_1_runner_builds_context_shadow_and_immutable_pit(tmp_path: Path):
    root = tmp_path
    (root / "config").mkdir(parents=True)
    (root / "outputs" / "daily_tct_ct").mkdir(parents=True)
    (root / "data" / "cache" / "actions").mkdir(parents=True)
    (root / "config" / "ACTION_CT_V22_1_0_SHADOW.json").write_text(
        json.dumps(_cfg(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    master = _master()
    master.to_csv(
        root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )
    baseline = pd.DataFrame(
        [
            {
                "asset_class": "ACTION", "horizon": "CT", "isin": row["isin"],
                "score": 72.0, "coverage_pct": 90.0, "status": "OK",
                "decision": "BUY_CANDIDATE" if i % 3 == 0 else "WAIT",
            }
            for i, row in master.iterrows()
        ]
    )
    baseline.to_csv(
        root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )

    for i in range(30):
        ticker = f"SYN{i}.PA"
        _history(ticker).to_parquet(root / "data" / "cache" / "actions" / f"history_{i}.parquet")

    payload = run(root=root, now=datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc))
    assert payload["status"] == "SUCCESS_SHADOW"
    assert payload["rows"] == 30
    assert payload["daily_histories_found"] == 30
    assert payload["context_mapped_actions"] >= 20
    assert "relative_strength" in payload["context_fields_generated"]
    assert "sector_rotation_score" in payload["context_fields_generated"]
    assert "morningstar_action_score" in payload["context_fields_generated"]
    assert payload["pit_snapshots_added"] == 30
    assert payload["pit_ledger_rows"] == 30
    assert payload["baseline_unchanged"] is True
    assert payload["t1_t2_used"] is False
    assert payload["fixed_take_profit_enabled"] is False
    assert payload["fixed_stop_loss_enabled"] is False
    assert payload["real_orders_enabled"] is False
    assert payload["runtime_patch_version"] == "ACTION_CT_V22.1.1_PERFORMANCE_OBSERVABILITY_PATCH"
    assert payload["runtime"]["schema"]["valid"] is True
    assert payload["runtime"]["timings_seconds"]["total_seconds"] > 0.0
    assert "agreement_v21_vs_v22_1_pct" in payload["runtime"]["divergence"]
    assert payload["context_coverage"]["field_coverage_pct"]["relative_strength"] > 0.0

    shadow_path = root / "outputs" / "daily_tct_ct" / "ACTION_CT_V22_1_0_SHADOW.csv"
    ledger_path = root / "state" / "action_ct_v22_1" / "ACTION_CT_V22_1_0_PIT_LEDGER.csv"
    context_path = root / "outputs" / "audit" / "ACTION_CT_V22_1_0_CONTEXT.json"
    runtime_path = root / "outputs" / "audit" / "ACTION_CT_V22_1_0_RUNTIME.json"
    errors_path = root / "outputs" / "audit" / "ACTION_CT_V22_1_0_ERRORS.json"
    divergences_path = root / "outputs" / "audit" / "ACTION_CT_V22_1_0_DIVERGENCES.csv"
    mobile_path = root / "outputs" / "mobile" / "ANDROID_ACTION_CT_V22_1_SHADOW.md"
    assert shadow_path.exists()
    assert ledger_path.exists()
    assert context_path.exists()
    assert runtime_path.exists()
    assert errors_path.exists()
    assert divergences_path.exists()
    assert mobile_path.exists()

    shadow = pd.read_csv(shadow_path, sep=";", encoding="utf-8-sig")
    ledger = pd.read_csv(ledger_path, sep=";", encoding="utf-8-sig")
    assert len(shadow) == 30
    assert len(ledger) == 30
    assert shadow["entry_component_quality_target"].notna().any()
    assert shadow["entry_component_relative_strength_sector"].notna().any()
    assert "valuation_risk_score" in shadow.columns
    assert shadow["valuation_risk_score"].isna().all()
    assert shadow["asymmetric_risk_score"].notna().any()
    assert shadow["context_richness_score"].notna().any()
    assert ledger["snapshot_fingerprint"].astype(str).str.len().eq(64).all()

    second = run(root=root, now=datetime(2026, 8, 21, 17, 5, tzinfo=timezone.utc))
    assert second["status"] == "SUCCESS_SHADOW"
    assert second["pit_snapshots_added"] == 0
    assert second["pit_ledger_rows"] == 30
    assert second["fingerprint_mismatches"] == []
