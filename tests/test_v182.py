import pandas as pd
import numpy as np
from v182.core.merge import decide
from v182.enrichment.planner import priority
from v182.features.ohlcv_features import calculate
from v182.io.frames import apply_observations
from v182.reporting.waves import wave8_scenarios, resolve_etf_tickers, _obs


def test_missing_never_replaces():
    existing={"value":10,"evidence_level":"B","as_of":"2026-01-01"}
    incoming={"value":None,"evidence_level":"A","as_of":"2026-02-01","validation_status":"VALIDATED"}
    assert decide(existing,incoming).action=="KEEP"


def test_bulk_history_is_high_priority():
    assert priority("YFINANCE_BULK_HISTORY",5)>priority("AMF_FINNHUB_GDELT",5)


def test_input_required_has_no_fake_gain():
    assert priority("USER_AND_BROKER_INPUT",5)==10


def test_ohlcv_features_from_synthetic_series():
    dates = pd.date_range("2021-01-01", periods=1300, freq="B")
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, len(dates)))
    frame = pd.DataFrame({
        "Open": close, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": rng.integers(1000, 100000, len(dates)),
    }, index=dates)
    feats = calculate(frame)
    assert feats["mm20"] is not None
    assert feats["rsi14"] is not None
    assert feats["perf_1y_pct"] is not None


def test_apply_observations_inserts_on_empty_field():
    master = pd.DataFrame([{"isin": "FR0000000001", "name": "TEST", "rsi14": "NON_OBSERVE",
                             "evidence_level": "D", "as_of_date": "2026-01-01"}])
    obs = [_obs("ACTION", "FR0000000001", "rsi14", 55.3, "INTERNAL_FROM_OHLCV", "C")]
    updated, quarantine = apply_observations(master, obs)
    assert updated.iloc[0]["rsi14"] == "55.3"
    assert quarantine == []


def test_apply_observations_quarantines_equal_evidence_conflict():
    master = pd.DataFrame([{"isin": "FR0000000001", "name": "TEST", "rsi14": "60.0",
                             "evidence_level": "C", "as_of_date": "2026-01-01"}])
    obs = [_obs("ACTION", "FR0000000001", "rsi14", 55.3, "INTERNAL_FROM_OHLCV", "C")]
    obs[0]["as_of"] = "2026-01-01"
    updated, quarantine = apply_observations(master, obs)
    assert updated.iloc[0]["rsi14"] == "60.0"
    assert len(quarantine) == 1


def test_wave8_scenarios_are_symmetric_on_flat_trend():
    actions = pd.DataFrame([{"isin": "FR0000000002", "last_close": "100.0",
                              "atr14": "2.0", "perf_3m_pct": "0.0"}])
    obs = wave8_scenarios(actions, {"FR0000000002"})
    fields = {o["field"]: o["value"] for o in obs}
    assert fields["scenario_bull_pct"] == -fields["scenario_bear_pct"]
    assert fields["invalidation_level"] == 96.0


def test_resolve_etf_tickers_flags_gap_when_mapping_missing():
    etf_df = pd.DataFrame([{"isin": "FR0013380607", "name": "Amundi CAC 40"}])
    merged, gaps = resolve_etf_tickers(etf_df, "/tmp/does_not_exist.csv")
    assert len(gaps) == 1
    assert gaps.iloc[0]["status"] == "INPUT_REQUIRED"


def test_build_etf_ticker_map_writes_gaps_for_unresolved_isin(tmp_path):
    from unittest.mock import patch
    from v182.mapping.etf_isin_resolver import build_etf_ticker_map
    etf_master = tmp_path / "etf_master.csv"
    pd.DataFrame([{"isin": "FR0013380607", "name": "Amundi CAC 40"},
                  {"isin": "XX0000000000", "name": "Unknown"}]).to_csv(
        etf_master, sep=";", index=False, encoding="utf-8-sig")
    fake = {
        "FR0013380607": [{"exchCode": "FP", "ticker": "CAC", "marketSector": "Equity", "securityType2": "ETF"}],
        "XX0000000000": [{"exchCode": "ZZ", "ticker": "NOPE", "marketSector": "Equity", "securityType2": "ETF"}],
    }
    with patch("v182.mapping.etf_isin_resolver.resolve_isins", return_value=fake):
        summary = build_etf_ticker_map(etf_master, tmp_path / "map.csv", tmp_path / "gaps.csv")
    assert summary == {"requested": 2, "resolved": 1, "gaps": 1}
    mapped = pd.read_csv(tmp_path / "map.csv", sep=";", encoding="utf-8-sig")
    assert mapped.iloc[0]["yahoo_ticker"] == "CAC.PA"


def test_wave3_prefers_longer_fallback_history_over_short_yahoo_fragment(tmp_path):
    from v182.reporting.waves import wave3_derived_features

    short_dates = pd.date_range("2026-01-01", periods=10, freq="B")
    long_dates = pd.date_range("2025-09-01", periods=80, freq="B")
    short = pd.DataFrame({
        "Open": np.arange(10)+1, "High": np.arange(10)+2, "Low": np.arange(10),
        "Close": np.arange(10)+1, "Volume": 1000,
    }, index=short_dates)
    long = pd.DataFrame({
        "Open": np.arange(80)+100, "High": np.arange(80)+101, "Low": np.arange(80)+99,
        "Close": np.arange(80)+100, "Volume": 2000,
    }, index=long_dates)
    pd.concat({"AI.PA": short}, axis=1).to_parquet(tmp_path / "history_yahoo_primary_00000.parquet")
    pd.concat({"AI.PA": long}, axis=1).to_parquet(tmp_path / "history_marketstack_00000.parquet")

    obs = wave3_derived_features(str(tmp_path), {"AI.PA": "FR0000120073"}, "ACTION")
    last_close = [o for o in obs if o["field"] == "last_close"][0]
    assert float(last_close["value"]) == 179.0
    assert "MARKETSTACK" in last_close["source"]


def test_load_seed_master_reuses_valid_previous_enrichment(tmp_path):
    from v182.reporting.run import _load_seed_master

    baseline = tmp_path / "baseline.csv"
    enriched = tmp_path / "enriched.csv"
    pd.DataFrame([{"isin":"FR1","name":"A","field":"NON_OBSERVE"}]).to_csv(
        baseline, sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"isin":"FR1","name":"A","field":"42"}]).to_csv(
        enriched, sep=";", index=False, encoding="utf-8-sig")
    loaded, source = _load_seed_master(baseline, enriched)
    assert source == "PREVIOUS_ENRICHED_OUTPUT"
    assert loaded.iloc[0]["field"] == "42"


def test_wave5_finnhub_targets_committee_watch_only(tmp_path):
    from unittest.mock import patch
    from v182.reporting.waves import wave5_consensus_finnhub
    actions_df = pd.DataFrame([
        {"isin": "FR0000120073", "name": "AIR LIQUIDE", "yahoo_ticker": "AI.PA", "comite_status": "COMMITTEE",
         "recommendation_key_yf": "NON_OBSERVE", "recommendation_mean_yf": "NON_OBSERVE"},
        {"isin": "FR0000000002", "name": "OTHER", "yahoo_ticker": "XX.PA", "comite_status": "NONE",
         "recommendation_key_yf": "NON_OBSERVE", "recommendation_mean_yf": "NON_OBSERVE"},
    ])
    fake_obs = [{"ticker": "AI.PA", "isin": "FR0000120073", "field": "consensus_rating", "value": "BUY", "source": "Finnhub"}]
    with patch("v182.sources.finnhub_consensus.fetch_consensus", return_value=(fake_obs, [])):
        obs, _, meta = wave5_consensus_finnhub(actions_df, api_key="fake", symbol_cache_path=tmp_path / "map.csv", cfg={"finnhub":{"delay_seconds":0}})
    assert {o["isin"] for o in obs} == {"FR0000120073"}
    assert meta["attempted_finnhub"] == 1


def test_wave5_normalizes_existing_yahoo_consensus_without_finnhub_call(tmp_path):
    from unittest.mock import patch
    from v182.reporting.waves import wave5_consensus_finnhub
    actions_df = pd.DataFrame([{
        "isin": "FR0000120073", "name": "AIR LIQUIDE", "yahoo_ticker": "AI.PA", "comite_status": "COMMITTEE",
        "recommendation_key_yf": "buy", "recommendation_mean_yf": "2.0", "n_analysts_yf": "18", "target_mean_yf": "190"
    }])
    with patch("v182.sources.finnhub_consensus.fetch_consensus", return_value=([], [])) as fetch:
        obs, _, meta = wave5_consensus_finnhub(actions_df, api_key="fake", symbol_cache_path=tmp_path / "map.csv", cfg={"finnhub":{"delay_seconds":0}})
    fetch.assert_called_once()
    assert fetch.call_args.args[0] == []
    fields = {o["field"]: o["value"] for o in obs}
    assert fields["consensus_rating"] == "BUY"
    assert fields["n_analysts"] == "18"
    assert meta["normalized_yf_tickers"] == 1


def test_wave6_etf_info_maps_dividend_yield_only():
    from unittest.mock import patch
    from v182.reporting.waves import wave6_etf_info
    etf_df = pd.DataFrame([{"isin": "FR0013380607", "yahoo_ticker": "CAC.PA"}])
    cfg = {"yfinance": {"info_delay_seconds": 0}}
    fake_obs = [{"ticker": "CAC.PA", "field": "dividend_yield_pct", "value": 1.8, "source": "yfinance"}]
    with patch("v182.reporting.waves.collect_info", return_value=(fake_obs, [])):
        obs, _ = wave6_etf_info(etf_df, cfg)
    assert {o["field"] for o in obs} == {"dividend_yield_pct", "dividend_data_status"}


def test_wave7_worklist_includes_conflicts_and_critical_pea_gaps():
    from v182.reporting.wave7_worklist import build_worklist
    actions_df = pd.DataFrame([
        {"isin": "FR0000120404", "name": "ACCOR", "euronext_link": "/en/product/equities/FR0000120404-XPAR",
         "euronext_mic": "XPAR", "pea_confidence": "HIGH", "broker_pea_confirmed": "OUI", "corporate_status": "ACTIVE"},
        {"isin": "BE0974293251", "name": "AB INBEV", "euronext_link": "/en/product/equities/BE0974293251-XBRU",
         "euronext_mic": "XBRU", "pea_confidence": "NON_OBSERVE", "broker_pea_confirmed": "NON_OBSERVE", "corporate_status": "ACTIVE"},
    ])
    quarantine = [{"isin": "FR0000120404", "field": "rsi14", "reason": "EQUAL_EVIDENCE_CONFLICT"}]
    worklist = build_worklist(quarantine, actions_df)
    assert len(worklist) == 3
    assert worklist.iloc[0]["euronext_link"] == "https://live.euronext.com/en/product/equities/FR0000120404-XPAR"
    assert set(worklist[worklist["isin"] == "BE0974293251"]["field"]) == {"pea_confidence", "broker_pea_confirmed"}


def test_checkpoint_is_scoped_per_run(tmp_path):
    from v182.state.checkpoint import Checkpoint
    a=Checkpoint(tmp_path/'checkpoint.json',run_id='day-a')
    a.mark('WAVE_01','DONE')
    b=Checkpoint(tmp_path/'checkpoint.json',run_id='day-b')
    assert a.done('WAVE_01') is True
    assert b.done('WAVE_01') is False


def test_quality_gate_accepts_complete_referentials():
    from v182.audit.quality import run_quality_gates
    actions=pd.DataFrame([{'isin':f'FR{i:010d}','yahoo_ticker':'X.PA'} for i in range(1486)])
    etf=pd.DataFrame([{'isin':f'LU{i:010d}','yahoo_ticker':'Y.PA'} for i in range(102)])
    cov={'ACTION':{'coverage_pct':80.0},'ETF':{'coverage_pct':70.0}}
    cfg={'quality_gates':{'actions_min_rows':1486,'etf_min_rows':102,'ticker_coverage_min_pct':100.0,'ohlcv_success_min_pct':90.0,'coverage_regression_tolerance_points':0.0,'fundamentals_availability_min_pct':40.0,'consensus_availability_min_pct':40.0}}
    metrics={'WAVE_01':{'requested':100,'successful':95},'WAVE_02':{'requested':100,'successful':90},'WAVE_04':{'requested':300,'available':150,'available_pct':50.0},'WAVE_05':{'requested':300,'available':140,'available_pct':46.67}}
    result=run_quality_gates(actions,etf,cov,cov,cfg,metrics)
    assert result.passed


def test_etf_ticker_map_is_complete():
    m=pd.read_csv('config/V18.2_ETF_TICKER_MAP.csv',sep=';',encoding='utf-8-sig',dtype=str)
    assert len(m)==102
    assert m['isin'].nunique()==102
    assert m['yahoo_ticker'].fillna('').str.strip().ne('').all()


def test_resolve_etf_tickers_works_when_master_already_has_ticker(tmp_path):
    from v182.reporting.waves import resolve_etf_tickers
    master=pd.DataFrame([{"isin":"FR0013380607","name":"CAC","yahoo_ticker":"OLD.PA"}])
    mapping=tmp_path/'map.csv'
    pd.DataFrame([{"isin":"FR0013380607","yahoo_ticker":"CACC.PA"}]).to_csv(mapping,sep=';',index=False,encoding='utf-8-sig')
    merged,gaps=resolve_etf_tickers(master,mapping)
    assert merged.iloc[0]['yahoo_ticker']=='CACC.PA'
    assert gaps.empty


def test_quality_gate_blocks_empty_critical_wave():
    from v182.audit.quality import run_quality_gates
    actions=pd.DataFrame([{'isin':f'FR{i:010d}','yahoo_ticker':'X.PA'} for i in range(1486)])
    etf=pd.DataFrame([{'isin':f'LU{i:010d}','yahoo_ticker':'Y.PA'} for i in range(102)])
    cov={'ACTION':{'coverage_pct':80.0},'ETF':{'coverage_pct':70.0}}
    cfg={'quality_gates':{'actions_min_rows':1486,'etf_min_rows':102,'ticker_coverage_min_pct':100.0,'ohlcv_success_min_pct':90.0,'coverage_regression_tolerance_points':0.0,'fundamentals_availability_min_pct':40.0,'consensus_availability_min_pct':40.0}}
    metrics={'WAVE_01':{'requested':100,'successful':100},'WAVE_02':{'requested':100,'successful':100},'WAVE_04':{'requested':300,'available':0,'available_pct':0.0},'WAVE_05':{'requested':300,'available':150,'available_pct':50.0}}
    result=run_quality_gates(actions,etf,cov,cov,cfg,metrics)
    assert not result.passed
    assert any(c['check']=='wave_04_availability_pct' and not c['passed'] for c in result.checks)


def test_finnhub_lookup_prefers_matching_exchange_symbol():
    from v182.sources.finnhub_consensus import _pick_lookup_result
    rows=[{'symbol':'AI.US','displaySymbol':'AI','type':'Common Stock'},
          {'symbol':'AI.PA','displaySymbol':'AI.PA','type':'Common Stock'}]
    assert _pick_lookup_result(rows,'AI.PA')['symbol']=='AI.PA'


def test_yfinance_info_opens_circuit_after_repeated_rate_limits():
    from unittest.mock import patch, MagicMock
    import types, sys
    from v182.sources.yfinance_info import collect_info
    class RateLimitError(Exception): pass
    fake=MagicMock()
    fake.get_info.side_effect=RateLimitError('Too Many Requests')
    fake.fast_info={}
    fake_module=types.SimpleNamespace(Ticker=lambda ticker: fake)
    with patch.dict(sys.modules, {'yfinance': fake_module}), patch('time.sleep'):
        obs, failures=collect_info(['A.PA','B.PA','C.PA','D.PA'],delay_seconds=0,max_retries=0,max_consecutive_rate_limits=2)
    assert obs==[]
    assert any(f['reason']=='RATE_LIMIT_CIRCUIT_OPEN' for f in failures)
