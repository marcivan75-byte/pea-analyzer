from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/V18.3_SMART_MONEY_CONFIG.json").read_text())

from v183.smart_money.models import SmartMoneyEvent
from v183.smart_money.events import deduplicate
from v183.smart_money.scoring import (
    recency_factor, insider_score, short_score, wis, ifs, confidence_factor
)
from v183.smart_money.features.tape import calculate as tape_features, score as tape_score
from v183.smart_money.features.etf_flows import estimated_flow, score as etf_flow_score
from v183.smart_money.sources.amf_short_open_data import normalize as normalize_amf_short, latest_public_positions
from v183.smart_money.io.provenance import upsert_provenance


def event(**kw):
    base = dict(universe="ACTION", isin="FR0000120271", event_type="INSIDER", event_subtype="MARKET_BUY",
                source="AMF", evidence_level="A", validation_status="VALIDATED",
                publication_date="2026-08-05", transaction_date="2026-08-04", actor_name="A",
                actor_role="CEO", direction=1, quantity=1000, price=100, value_eur=100000,
                source_document_id="DOC1")
    base.update(kw)
    return SmartMoneyEvent(**base).to_dict()


def test_no_lookahead_recency_uses_publication_date():
    bands = CFG["insiders"]["recency_bands"]
    assert recency_factor("2026-08-05", "2026-08-05", bands) == 1.0
    # The engine caller must pass publication date, not transaction date.
    assert recency_factor("2026-05-01", "2026-08-05", bands) < 0.35


def test_insider_cluster_bonus_and_cap():
    events = [event(actor_name="CEO", value_eur=500000), event(actor_name="CFO", actor_role="CFO", source_document_id="DOC2")]
    score, meta = insider_score(events, "2026-08-07", CFG, market_cap=1e9, adv20_eur=5e6)
    assert meta["cluster_flag"] is True
    assert 0 < score <= CFG["caps"]["insider"]


def test_sales_are_less_informative_than_buys():
    buy, _ = insider_score([event(direction=1)], "2026-08-07", CFG)
    sell, _ = insider_score([event(direction=-1, event_subtype="MARKET_SELL")], "2026-08-07", CFG)
    assert abs(sell) < buy


def test_duplicate_amf_beats_finnhub():
    a = event()
    b = {**a, "source": "Finnhub", "evidence_level": "B"}
    kept, quarantine = deduplicate([b, a])
    assert len(kept) == 1 and kept[0]["evidence_level"] == "A"
    assert quarantine == []


def test_equal_evidence_conflict_quarantined():
    a = event()
    b = {**a, "value_eur": 999999}
    kept, quarantine = deduplicate([a, b])
    assert len(kept) == 1 and len(quarantine) == 1


def test_amf_short_schema_and_censoring():
    df = pd.DataFrame([
        {"Nom du détenteur": "Fund A", "Nom de l'émetteur": "X", "ISIN": "FR0000120271",
         "Position courte nette": "0,45", "Date de début de position": "05/08/2026",
         "Date de début de publication de la position": "06/08/2026", "Date de fin de publication de la position": ""}
    ])
    out = normalize_amf_short(df)
    assert out.iloc[0]["short_position_pct"] == 0.45
    assert bool(out.iloc[0]["public_censored_below_05"]) is True


def test_short_below_public_threshold_is_not_zero():
    rows = [
        {"holder": "A", "position_date": "2026-08-01", "short_position_pct": 0.65},
        {"holder": "A", "position_date": "2026-08-05", "short_position_pct": 0.45},
    ]
    score, meta = short_score(rows, CFG)
    assert meta["censored"] is True
    assert meta["current_public_pct"] == 0.45
    assert score > 0


def test_tape_accumulation_detection():
    n = 80
    close = np.linspace(100, 110, n)
    volume = np.full(n, 100000.0); volume[-1] = 500000.0
    frame = pd.DataFrame({"Open": close - 0.2, "High": close + 0.2, "Low": close - 1.0,
                          "Close": close, "Volume": volume})
    feats = tape_features(frame)
    assert feats["volume_z20"] > 1.5
    assert tape_score(feats, CFG) > 0


def test_etf_flow_adjusts_for_nav_performance():
    flow, pct = estimated_flow(110.0, 100.0, 110.0, 100.0)
    assert abs(flow) < 1e-12 and abs(pct) < 1e-12


def test_etf_flow_score_uses_persistence():
    dates = pd.date_range("2026-07-01", periods=25, freq="B")
    nav = np.linspace(100, 102, len(dates))
    # AUM grows faster than NAV -> positive estimated creations.
    aum = np.linspace(1000, 1100, len(dates))
    core, persistence, meta = etf_flow_score(pd.DataFrame({"date": dates, "aum": aum, "nav": nav}), CFG)
    assert core > 0 and persistence > 0 and meta["flow_pct_20d"] > 0


def test_wis_low_confidence_cap():
    raw, effective = wis(3, 2, 1.5, 1.5, 0.4, CFG)
    assert raw == 7.0 and abs(effective) <= CFG["confidence"]["low_confidence_effective_cap"]


def test_ifs_cap():
    raw, effective = ifs(3, 1, 1, 1.0, CFG)
    assert raw == 5.0 and effective == 5.0


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


def test_wave9_filters_future_publication():
    from v183.smart_money.wave9 import score_action
    future = event(publication_date="2026-08-08")
    row = score_action("FR0000120271", 80.0, [future], None, "2026-08-07", CFG,
                       {"insiders": True, "thresholds": True, "shorts": True, "tape": False})
    assert row["insider_score"] == 0.0
