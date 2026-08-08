from __future__ import annotations
import copy
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/V18.3_SMART_MONEY_CONFIG.json").read_text())

from v183.smart_money.calibration import validate_calibration
from v183.smart_money.coverage import build_etf_registry
from v183.smart_money.models import SmartMoneyEvent
from v183.smart_money.events import deduplicate
from v183.smart_money.scoring import (
    recency_factor, insider_score, short_score, wis, ifs, confidence_factor,
    significant_holder_score,
)
from v183.smart_money.features.tape import calculate as tape_features, score as tape_score
from v183.smart_money.features.etf_flows import estimated_flow, score as etf_flow_score
from v183.smart_money.sources.amf_short_open_data import (
    normalize as normalize_amf_short,
    latest_public_positions,
    public_position_history,
    to_events as amf_short_events,
)
from v183.smart_money.sources.etf_flow_import import upsert_history
from v183.smart_money.io.provenance import upsert_provenance


def event(**kw):
    base = dict(universe="ACTION", isin="FR0000120271", event_type="INSIDER", event_subtype="MARKET_BUY",
                source="AMF", evidence_level="A", validation_status="VALIDATED",
                publication_date="2026-08-05", transaction_date="2026-08-04", actor_name="A",
                actor_role="CEO", direction=1, quantity=1000, price=100, value_eur=100000,
                source_document_id="DOC1")
    base.update(kw)
    return SmartMoneyEvent(**base).to_dict()


def test_structural_calibration_contract_is_green():
    report = validate_calibration(CFG)
    assert report["passed"] is True
    assert report["status"] == "STRUCTURAL_CALIBRATED_RC1"
    assert report["empirical_walk_forward_required_for_active_scoring"] is True
    assert report["action_effective_cap"] <= 5.0
    assert report["etf_effective_cap"] <= 3.5


def test_no_lookahead_recency_uses_publication_date():
    bands = CFG["insiders"]["recency_bands"]
    assert recency_factor("2026-08-05", "2026-08-05", bands) == 1.0
    assert recency_factor("2026-05-01", "2026-08-05", bands) < 0.3


def test_insider_cluster_bonus_and_cap():
    events = [event(actor_name="CEO", value_eur=500000), event(actor_name="CFO", actor_role="CFO", source_document_id="DOC2")]
    score, meta = insider_score(events, "2026-08-07", CFG, market_cap=1e9, adv20_eur=5e6)
    assert meta["cluster_flag"] is True
    assert 0 < score <= CFG["caps"]["insider"]


def test_sales_are_less_informative_than_buys():
    buy, _ = insider_score([event(direction=1)], "2026-08-07", CFG)
    sell, _ = insider_score([event(direction=-1, event_subtype="MARKET_SELL")], "2026-08-07", CFG)
    assert abs(sell) < buy


def test_threshold_crossing_decays_instead_of_accumulating_forever():
    recent = event(event_type="THRESHOLD", event_subtype="UP", direction=1, threshold_pct=30,
                   publication_date="2026-08-01", transaction_date="2026-08-01")
    old = event(event_type="THRESHOLD", event_subtype="UP", direction=1, threshold_pct=30,
                publication_date="2024-08-01", transaction_date="2024-08-01", source_document_id="OLD")
    assert significant_holder_score([recent], "2026-08-07", CFG) > 0
    assert significant_holder_score([old], "2026-08-07", CFG) == 0.0


def test_duplicate_amf_beats_finnhub():
    a = event()
    b = {**a, "source": "Finnhub", "evidence_level": "B"}
    kept, quarantine = deduplicate([b, a])
    assert len(kept) == 1 and kept[0]["evidence_level"] == "A"
    assert quarantine == []


def test_economic_dedup_ignores_source_document_identity():
    a = event(source="AMF", source_document_id="AMF-123")
    b = event(source="Finnhub", evidence_level="B", validation_status="ISIN_MATCHED", source_document_id="FH-999")
    assert a["event_id"] != b["event_id"]
    kept, quarantine = deduplicate([b, a])
    assert len(kept) == 1 and kept[0]["source"] == "AMF"
    assert quarantine == []


def test_equal_evidence_conflict_quarantined():
    a = event()
    b = {**a, "value_eur": 999999}
    kept, quarantine = deduplicate([a, b])
    assert len(kept) == 1 and len(quarantine) == 1


def _short_df():
    return pd.DataFrame([
        {"Nom du détenteur": "Fund A", "Nom de l'émetteur": "X", "ISIN": "FR0000120271",
         "Position courte nette": "0,65", "Date de début de position": "01/08/2026",
         "Date de début de publication de la position": "02/08/2026", "Date de fin de publication de la position": ""},
        {"Nom du détenteur": "Fund A", "Nom de l'émetteur": "X", "ISIN": "FR0000120271",
         "Position courte nette": "0,45", "Date de début de position": "05/08/2026",
         "Date de début de publication de la position": "06/08/2026", "Date de fin de publication de la position": ""},
    ])


def test_amf_short_schema_and_censoring():
    out = normalize_amf_short(_short_df())
    assert out.iloc[-1]["short_position_pct"] == 0.45
    assert bool(out.iloc[-1]["public_censored_below_05"]) is True


def test_amf_short_history_preserves_previous_observation_for_delta():
    history = public_position_history(_short_df(), as_of="2026-08-07", depth_per_holder=4)
    assert len(history) == 2
    events = amf_short_events(_short_df(), as_of="2026-08-07", history_depth_per_holder=4)
    rows = [{"holder": e["actor_name"], "publication_date": e["publication_date"],
             "position_date": e["position_date"], "short_position_pct": e["short_position_pct"]} for e in events]
    score, meta = short_score(rows, CFG)
    assert meta["comparable_holders"] == 1
    assert meta["delta"] == -0.2
    assert score > 0


def test_short_below_public_threshold_is_not_zero():
    rows = [
        {"holder": "A", "publication_date": "2026-08-02", "position_date": "2026-08-01", "short_position_pct": 0.65},
        {"holder": "A", "publication_date": "2026-08-06", "position_date": "2026-08-05", "short_position_pct": 0.45},
    ]
    score, meta = short_score(rows, CFG)
    assert meta["censored"] is True
    assert meta["current_public_pct"] == 0.45
    assert score > 0


def test_tape_accumulation_detection_and_corporate_action_neutralization():
    n = 80
    close = np.linspace(100, 110, n)
    volume = np.full(n, 100000.0); volume[-1] = 500000.0
    frame = pd.DataFrame({"Open": close - 0.2, "High": close + 0.2, "Low": close - 1.0,
                          "Close": close, "Volume": volume})
    feats = tape_features(frame)
    assert feats["volume_z20"] > CFG["tape"]["min_volume_z"]
    assert tape_score(feats, CFG) > 0
    assert tape_score(feats, CFG, {"corporate_action": True}) == 0.0


def test_etf_flow_adjusts_for_nav_performance():
    flow, pct = estimated_flow(110.0, 100.0, 110.0, 100.0)
    assert abs(flow) < 1e-12 and abs(pct) < 1e-12


def test_etf_flow_bootstrap_is_neutral_until_enough_history():
    dates = pd.date_range("2026-08-01", periods=8, freq="D")
    frame = pd.DataFrame({"date": dates, "aum": np.linspace(1000, 1010, 8), "nav": np.linspace(100, 101, 8)})
    core, persistence, meta = etf_flow_score(frame, CFG, as_of="2026-08-08")
    assert core == 0 and persistence == 0
    assert meta["flow_status"] == "INSUFFICIENT_HISTORY"


def test_etf_flow_score_uses_persistence():
    dates = pd.date_range("2026-07-01", periods=25, freq="B")
    nav = np.linspace(100, 102, len(dates))
    aum = np.linspace(1000, 1100, len(dates))
    core, persistence, meta = etf_flow_score(pd.DataFrame({"date": dates, "aum": aum, "nav": nav}), CFG, as_of="2026-08-07")
    assert core > 0 and persistence > 0 and meta["flow_pct_20d"] > 0
    assert meta["flow_status"] == "OK"


def test_etf_flow_stale_history_is_neutral():
    dates = pd.date_range("2026-05-01", periods=25, freq="D")
    frame = pd.DataFrame({"date": dates, "aum": np.linspace(1000, 1100, 25), "nav": np.linspace(100, 102, 25)})
    core, persistence, meta = etf_flow_score(frame, CFG, as_of="2026-08-07")
    assert core == 0 and persistence == 0
    assert meta["flow_status"] == "STALE_HISTORY"


def test_etf_provider_registry_covers_every_row_without_claiming_flow_readiness():
    master = pd.DataFrame([
        {"isin": "FR0013380607", "name": "Amundi CAC 40", "provider": "Amundi"},
        {"isin": "FR0011550177", "name": "BNP Paribas Easy S&P 500", "provider": "BNP Paribas Easy"},
        {"isin": "IE0000000001", "name": "Other ETF", "provider": "Other"},
    ])
    registry, metrics = build_etf_registry(master, None, min_flow_observations=20)
    assert metrics["registry_coverage_pct"] == 100.0
    assert metrics["flow_ready_20d_pct"] == 0.0
    assert registry["registry_supported"].all()


def test_etf_flow_state_prefers_higher_evidence_for_same_day():
    low = pd.DataFrame([{"date": "2026-08-07", "isin": "FR0013380607", "aum": 100, "nav": 10,
                         "source": "YF", "evidence_level": "C", "provider": "Amundi"}])
    high = pd.DataFrame([{"date": "2026-08-07", "isin": "FR0013380607", "aum": 101, "nav": 10,
                          "source": "ISSUER", "evidence_level": "A", "provider": "Amundi"}])
    out = upsert_history(low, high)
    assert len(out) == 1 and out.iloc[0]["aum"] == 101


def test_wis_low_confidence_cap():
    raw, effective = wis(3, 2, 1.5, 1.5, 0.4, CFG)
    assert raw == CFG["caps"]["wis"]
    assert abs(effective) <= CFG["confidence"]["low_confidence_effective_cap"]


def test_ifs_cap():
    raw, effective = ifs(3, 1, 1, 1.0, CFG)
    assert raw == CFG["caps"]["ifs"] and effective == CFG["caps"]["ifs"]


def test_evidence_d_does_not_contribute_to_confidence():
    c = confidence_factor([event(evidence_level="D")], 1.0, CFG)
    assert c == 0.0


def test_field_level_provenance_prevents_row_level_evidence_bug():
    obs = [
        {"universe": "ACTION", "isin": "FR0000120271", "field": "rsi14", "source": "OHLCV", "evidence_level": "C", "validation_status": "AUTO_MATCH", "as_of": "2026-08-07", "collected_at": "x"},
        {"universe": "ACTION", "isin": "FR0000120271", "field": "insider_score", "source": "AMF", "evidence_level": "A", "validation_status": "VALIDATED", "as_of": "2026-08-07", "collected_at": "x"},
    ]
    p = upsert_provenance(None, obs)
    levels = dict(zip(p["field"], p["evidence_level"]))
    assert levels == {"rsi14": "C", "insider_score": "A"}


def test_amf_short_no_lookahead_uses_publication_start():
    df = pd.DataFrame([
        {"Nom du détenteur": "Fund A", "Nom de l'émetteur": "X", "ISIN": "FR0000120271",
         "Position courte nette": "0,70", "Date de début de position": "05/08/2026",
         "Date de début de publication de la position": "08/08/2026", "Date de fin de publication de la position": ""}
    ])
    assert latest_public_positions(df, as_of="2026-08-07").empty
    assert len(latest_public_positions(df, as_of="2026-08-08")) == 1


def test_wave9_shadow_mode_does_not_change_final_score():
    from v183.smart_money.wave9 import score_action
    n = 40
    close = np.linspace(100, 105, n)
    volume = np.full(n, 100000.0); volume[-1] = 400000.0
    ohlcv = pd.DataFrame({"Open": close - .2, "High": close + .2, "Low": close - .8, "Close": close, "Volume": volume})
    row = score_action("FR0000120271", 82.0, [event()], ohlcv, "2026-08-07", CFG,
                       {"insiders": True, "thresholds": True, "shorts": True, "tape": True})
    assert row["score_final"] == 82.0
    assert row["score_shadow"] >= 82.0
    assert row["smart_money_active_scoring_allowed"] is False


def test_active_scoring_cannot_be_enabled_by_shadow_flag_alone():
    from v183.smart_money.wave9 import score_action
    cfg = copy.deepcopy(CFG)
    cfg["shadow_mode"] = False
    cfg["score_application"] = "ACTIVE"
    row = score_action("FR0000120271", 80.0, [event()], None, "2026-08-07", cfg,
                       {"insiders": True, "thresholds": False, "shorts": True, "tape": False})
    assert row["score_final"] == 80.0
    assert row["smart_money_active_scoring_allowed"] is False


def test_wave9_filters_future_publication():
    from v183.smart_money.wave9 import score_action
    future = event(publication_date="2026-08-08")
    row = score_action("FR0000120271", 80.0, [future], None, "2026-08-07", CFG,
                       {"insiders": True, "thresholds": True, "shorts": True, "tape": False})
    assert row["insider_score"] == 0.0
