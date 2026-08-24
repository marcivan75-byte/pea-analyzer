from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.decision.tct_baseline_v24_1_7 import WEIGHTS_V24_1_2, SETUP_COMPONENT
from v182.reporting import daily_ci_restitution_v21_15_7 as daily_ci
from v182.reporting import daily_consolidated_runner_v21_15_4 as deployed
from v182.reporting import daily_w09_seed_v21_15_7 as w09_seed


def test_deployed_facade_routes_to_v21_15_7() -> None:
    assert deployed.VERSION == "DAILY_CONSOLIDATED_RUNTIME_V21_15_7"
    assert deployed.run.__module__.endswith("daily_consolidated_runner_v21_15_7")


def test_w09_seed_rehydrates_actions_without_network() -> None:
    frame = pd.DataFrame(
        {
            "isin": ["FR0010208488", "TEST00000001"],
            "country_yf": ["France", "Germany"],
            "sector_yf": ["Energy", "Technology"],
        }
    )
    rows, audit = w09_seed.action_observations(frame)
    assert audit["status"] == "REUSED_VALIDATED_DAILY_W09_SEED"
    assert audit["source_run_id"] == 32626511307
    assert audit["network_calls"] == 0
    assert audit["fred_calls"] == 0
    assert audit["gdelt_calls"] == 0
    assert audit["etf_w09_fabricated"] is False

    values = {(row["isin"], row["field"]): row["value"] for row in rows}
    assert values[("FR0010208488", "funnel_global_macro_score")] == 64.4034
    assert values[("FR0010208488", "funnel_market_sentiment_score")] == 52.8504
    assert values[("FR0010208488", "funnel_country_macro_score")] == 49.9546
    assert values[("FR0010208488", "funnel_sector_news_score")] == 89.5285
    assert values[("FR0010208488", "funnel_instrument_news_score")] == 75.0
    assert values[("FR0010208488", "news_catalyst_score")] == 75.0
    assert values[("TEST00000001", "funnel_country_news_score")] == 50.0


def test_daily_tct_ci_reference_reconstructs_v24_1_8_score(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "daily_tct_ct"
    out.mkdir(parents=True)
    active = {name: weight for name, weight in WEIGHTS_V24_1_2.items() if name != SETUP_COMPONENT}
    row = {"isin": "FRTEST000001"}
    for name in active:
        row[f"tct_baseline_component_{name}"] = 50.0
        row[f"tct_baseline_component_{name}_observed"] = True
    pd.DataFrame([row]).to_csv(out / "TCT_BASELINE_V24_1_8.csv", sep=";", index=False, encoding="utf-8-sig")

    selected = pd.DataFrame(
        [
            {
                "asset_class": "ACTION",
                "horizon": "TCT",
                "isin": "FRTEST000001",
                "name": "Test",
                "decision": "WATCH",
                "score": 50.0,
                "generated_at_utc": "2026-08-23T10:00:00+00:00",
                "t1_quality_score": 70.0,
                "t2_quality_score": 80.0,
            }
        ]
    )
    detail = daily_ci._tct_details(tmp_path, selected)
    active_detail = detail[detail["criterion_status"].eq("ACTIVE")]
    assert len(active_detail) == len(active)
    assert round(float(active_detail["effective_weight_pct"].sum()), 8) == 100.0
    assert round(float(active_detail["weighted_contribution_points"].sum()), 8) == 50.0
    context = detail[detail["criterion_status"].eq("CONTEXT_ONLY")]
    assert set(context["criterion"]) == {"T1_TIMING_CONTEXT", "T2_TIMING_CONTEXT"}
    assert float(context["weighted_contribution_points"].sum()) == 0.0


def test_daily_ci_scope_is_tct_ct_and_etf_ct_only() -> None:
    source = Path(daily_ci.__file__).read_text(encoding="utf-8")
    assert '("ACTION", "TCT")' in source
    assert '("ACTION", "CT")' in source
    assert '("ETF", "CT")' in source
    assert '"external_collection_calls": 0' in source
    assert '"model_reruns": 0' in source
