from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from v182.reporting import selected_source_enrichment_v4 as source


@dataclass
class _TVResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def test_v4_disables_investing_and_merges_tradingview(monkeypatch, tmp_path: Path):
    config = Path("config/WEEKLY_V4_SOURCE_CONTRACT.json").read_text(encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / source.CONTRACT).write_text(config, encoding="utf-8")
    rows = pd.DataFrame([
        {
            "isin": "FR0000000001",
            "asset_class": "ACTION",
            "horizon": "CT",
            "decision": "BUY_CANDIDATE",
            "score": 80.0,
            "yahoo_ticker": "AIR.PA",
        }
    ])
    called = {}

    def legacy_enrich(frame, root, *, profile, investing_enabled):
        called["investing_enabled"] = investing_enabled
        outdir = root / "outputs/source_context"
        outdir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {
                "isin": "FR0000000001",
                "asset_class": "ACTION",
                "horizon": "CT",
                "field": "boursorama_consensus",
                "value": "BUY",
            }
        ]).to_csv(outdir / f"{profile}_SOURCE_OBSERVATIONS.csv", sep=";", index=False)
        return frame.assign(boursorama_consensus="BUY"), {"status": "SUCCESS_WITH_CONTEXT"}

    def tv_collect(frame, cache_path, **kwargs):
        return _TVResult(
            observations=[
                {
                    "isin": "FR0000000001",
                    "asset_class": "ACTION",
                    "horizon": "CT",
                    "field": "tradingview_weekly_signal",
                    "value": "STRONG_BUY",
                }
            ],
            failures=[],
            metrics={"usable_rows": 1},
        )

    monkeypatch.setattr(source.legacy, "enrich_selected_rows", legacy_enrich)
    monkeypatch.setattr(source, "collect_technical_context_cached", tv_collect)
    enriched, payload = source.enrich_selected_rows_v4(rows, tmp_path, profile="WEEKLY_V4")
    assert called["investing_enabled"] is False
    assert enriched.iloc[0]["boursorama_consensus"] == "BUY"
    assert enriched.iloc[0]["tradingview_weekly_signal"] == "STRONG_BUY"
    assert payload["investing"]["status"] == "DISABLED_FOR_V4"
    assert payload["source_can_create_candidate"] is False


def test_v4_source_layer_cannot_expand_empty_preselection(monkeypatch, tmp_path: Path):
    config = Path("config/WEEKLY_V4_SOURCE_CONTRACT.json").read_text(encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / source.CONTRACT).write_text(config, encoding="utf-8")
    rows = pd.DataFrame([
        {"isin": "FR0000000001", "asset_class": "ACTION", "horizon": "CT", "decision": "SELL"}
    ])

    def legacy_enrich(frame, root, *, profile, investing_enabled):
        return frame.copy(), {"status": "NO_PRESELECTED_ROWS"}

    monkeypatch.setattr(source.legacy, "enrich_selected_rows", legacy_enrich)
    enriched, payload = source.enrich_selected_rows_v4(rows, tmp_path)
    assert len(enriched) == 1
    assert payload["tradingview"]["status"] == "NO_PRESELECTED_ROWS"
    assert payload["investing"]["status"] == "DISABLED_FOR_V4"


def test_versioned_isin_bound_ticker_alias_is_promoted():
    rows = pd.DataFrame([
        {
            "isin": "NO0010713936",
            "asset_class": "ACTION",
            "yahoo_ticker": None,
            "yahoo_ticker_v22_2": "ZAP.OL",
        }
    ])
    prepared, count = source._canonicalize_identity_aliases(rows)
    assert count == 1
    assert prepared.iloc[0]["yahoo_ticker"] == "ZAP.OL"
