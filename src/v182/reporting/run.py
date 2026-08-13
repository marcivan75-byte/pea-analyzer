from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
import os
import pandas as pd

from v182.io.frames import load_master, save_master
from v182.audit.completeness import completeness
from v182.state.checkpoint import Checkpoint
from v182.reporting import waves, event_sources
from v182.reporting.collection_audit import write_collection_audit

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
STATE = ROOT / "state"
OUTPUTS = ROOT / "outputs"
CACHE = ROOT / "data" / "cache"
DATA_AUDIT = OUTPUTS / "data_audit"


def _load_cfg() -> dict:
    return json.loads((CONFIG / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))


def _fields(df):
    skip = {"isin", "name"}
    return [c for c in df.columns if c not in skip]


def _write_failures(name: str, failures: list[dict]) -> None:
    if failures:
        pd.DataFrame(failures).to_csv(OUTPUTS / "gaps" / f"{name}_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig")


def _audit(actions: pd.DataFrame, etfs: pd.DataFrame, wave_id: str, *, failures: list[dict] | None = None, source_context: str = "") -> None:
    write_collection_audit(actions, etfs, wave_id, DATA_AUDIT, failures=failures, source_context=source_context)


def _apply_canonical_actions(actions_df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    spec=cfg.get("canonical_universe",{})
    path=spec.get("actions_whitelist_path")
    if not path:
        raise RuntimeError("V21_3_CANONICAL_UNIVERSE_PATH_MISSING")
    from v182.audit.canonical_universe import filter_actions
    result=filter_actions(actions_df, ROOT/path)
    if not result.excluded.empty:
        excluded=result.excluded.copy()
        excluded["status"]="EXCLUDED_OUTSIDE_V21_3_CANONICAL_UNIVERSE"
        excluded.to_csv(OUTPUTS/"gaps"/"V21_3_ACTIONS_EXCLUDED_FROM_LEGACY_MASTER.csv",sep=";",index=False,encoding="utf-8-sig")
    audit={
        "status":"PASS",
        "input_rows":int(len(actions_df)),
        "canonical_rows":int(len(result.included)),
        "excluded_rows":int(len(result.excluded)),
        "whitelist_count":int(result.whitelist_count),
        "whitelist_sha256":result.whitelist_sha256,
        "reference_version":spec.get("actions_reference_version","V21.3_1829"),
        "legacy_rule":spec.get("legacy_reference","1429 canonical + 400 validated quarantine recovered"),
    }
    (OUTPUTS/"audit"/"V21_CANONICAL_UNIVERSE.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    return result.included.reset_index(drop=True),audit


def run() -> dict:
    cfg = _load_cfg()
    run_id = os.environ.get("V182_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checkpoint = Checkpoint(STATE / "V18.2_checkpoint.json", run_id=run_id)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "gaps").mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "audit").mkdir(parents=True, exist_ok=True)
    DATA_AUDIT.mkdir(parents=True, exist_ok=True)

    actions_legacy = load_master(INPUTS / "V18.2_PEA_ACTIONS_MASTER.csv")
    actions_df,canonical_audit=_apply_canonical_actions(actions_legacy,cfg)
    etf_df = load_master(INPUTS / "V18.2_PEA_ETF_MASTER.csv")
    expected_rows = {"ACTION": len(actions_df), "ETF": len(etf_df)}

    before = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    print(f"Univers canonique V21.3 — Actions: {expected_rows['ACTION']} (exclus legacy: {canonical_audit['excluded_rows']}) | ETF: {expected_rows['ETF']}")
    print(f"Couverture avant run — Actions: {before['ACTION']['coverage_pct']}% | ETF: {before['ETF']['coverage_pct']}%")
    _audit(actions_df,etf_df,"WAVE_00_INITIAL_STATE",source_context="Etat avant nouvelles collectes")

    quarantine_log: list[dict] = []
    wave_metrics: dict[str, dict] = {}

    if not checkpoint.done("WAVE_00_ETF_TICKERS"):
        map_path = CONFIG / "V18.2_ETF_TICKER_MAP.csv"
        existing_map = pd.read_csv(map_path, sep=";", encoding="utf-8-sig", dtype=str) if map_path.exists() else pd.DataFrame()
        map_complete = (
            len(existing_map) == len(etf_df) and "yahoo_ticker" in existing_map.columns
            and existing_map["isin"].nunique() == len(etf_df)
            and (~existing_map["yahoo_ticker"].apply(lambda v: str(v or "").strip() == "")).all()
        )
        if map_complete:
            summary = {"requested": len(etf_df), "resolved": len(etf_df), "gaps": 0, "source": "VALIDATED_STATIC_MAP"}
        else:
            from v182.mapping.etf_isin_resolver import build_etf_ticker_map
            summary = build_etf_ticker_map(
                etf_master_path=INPUTS / "V18.2_PEA_ETF_MASTER.csv",
                output_map_path=map_path,
                gaps_path=OUTPUTS / "gaps" / "V18.2_ETF_TICKER_OPENFIGI_GAPS.csv",
            )
        checkpoint.mark("WAVE_00_ETF_TICKERS", "DONE", **summary)
        _audit(actions_df,etf_df,"WAVE_00_ETF_TICKERS",source_context=str(summary.get("source","OpenFIGI/static map")))
        print(f"WAVE_00 — {summary['resolved']}/{summary['requested']} tickers ETF résolus")

    if not checkpoint.done("WAVE_01"):
        result = waves.wave_history(actions_df, "ACTION", str(CACHE / "actions"), cfg)
        failures=[{"ticker":t,"source":"yfinance","reason":"OHLCV_UNAVAILABLE"} for t in result.failed]
        checkpoint.mark("WAVE_01", "DONE", requested=result.requested, successful=len(result.successful), failed=len(result.failed))
        wave_metrics["WAVE_01"]={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed)}
        _audit(actions_df,etf_df,"WAVE_01_ACTION_OHLCV",failures=failures,source_context="yfinance OHLCV 5y")
        print(f"WAVE_01 — {len(result.successful)}/{result.requested} tickers Actions récupérés")
    else:
        wave_metrics["WAVE_01"]=checkpoint.wave("WAVE_01")

    etf_with_tickers, etf_gaps = waves.resolve_etf_tickers(etf_df, CONFIG / "V18.2_ETF_TICKER_MAP.csv")
    if not etf_gaps.empty:
        etf_gaps.to_csv(OUTPUTS / "gaps" / "V18.2_ETF_TICKER_GAPS.csv", sep=";", index=False, encoding="utf-8-sig")
    if not checkpoint.done("WAVE_02"):
        result = waves.wave_history(etf_with_tickers, "ETF", str(CACHE / "etf"), cfg)
        failures=[{"ticker":t,"source":"yfinance","reason":"OHLCV_UNAVAILABLE"} for t in result.failed]
        checkpoint.mark("WAVE_02", "DONE", requested=result.requested, successful=len(result.successful), failed=len(result.failed))
        wave_metrics["WAVE_02"]={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed)}
        _audit(actions_df,etf_df,"WAVE_02_ETF_OHLCV",failures=failures,source_context="yfinance OHLCV 5y")
        print(f"WAVE_02 — {len(result.successful)}/{result.requested} tickers ETF récupérés")
    else:
        wave_metrics["WAVE_02"]=checkpoint.wave("WAVE_02")

    actions_map = dict(zip(actions_df["yahoo_ticker"], actions_df["isin"]))
    etf_map = dict(zip(etf_with_tickers["yahoo_ticker"], etf_with_tickers["isin"]))
    if not checkpoint.done("WAVE_03"):
        obs_actions = waves.wave3_derived_features(str(CACHE / "actions"), actions_map, "ACTION")
        obs_etf = waves.wave3_derived_features(str(CACHE / "etf"), etf_map, "ETF")
        obs_beta = waves.wave3_etf_beta3y(str(CACHE / "etf"), etf_map)
        actions_df, q1 = apply_and_track(actions_df, obs_actions)
        etf_df, q2 = apply_and_track(etf_df, obs_etf + obs_beta)
        quarantine_log += q1 + q2
        checkpoint.mark("WAVE_03", "DONE", actions_fields=len(obs_actions), etf_fields=len(obs_etf), etf_beta3y=len(obs_beta))
        _audit(actions_df,etf_df,"WAVE_03_DERIVED_OHLCV",failures=q1+q2,source_context="Calcul interne PIT depuis OHLCV")
        print(f"WAVE_03 — {len(obs_actions)} valeurs Actions + {len(obs_etf)} ETF + {len(obs_beta)} bêta3y")

    if not checkpoint.done("WAVE_04"):
        obs4, failures4 = waves.wave4_info_actions(actions_df, cfg)
        actions_df, q3 = apply_and_track(actions_df, obs4)
        quarantine_log += q3
        _write_failures("V18.2_WAVE04_YFINANCE", failures4)
        checkpoint.mark("WAVE_04", "DONE", observed=len(obs4), failed=len(failures4))
        _audit(actions_df,etf_df,"WAVE_04_ACTION_FUNDAMENTALS",failures=failures4+q3,source_context="yfinance fondamentaux/metadonnees")
        print(f"WAVE_04 — {len(obs4)} champs fondamentaux/métadonnées Actions, {len(failures4)} échecs")

    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if not checkpoint.done("WAVE_05") and finnhub_key:
        obs5, failures5 = waves.wave5_consensus_finnhub(actions_df, finnhub_key)
        actions_df, q5 = apply_and_track(actions_df, obs5)
        quarantine_log += q5
        _write_failures("V18.2_WAVE05_FINNHUB", failures5)
        checkpoint.mark("WAVE_05", "DONE", observed=len(obs5), failed=len(failures5))
        _audit(actions_df,etf_df,"WAVE_05_ACTION_CONSENSUS",failures=failures5+q5,source_context="Finnhub + yfinance consensus")
        print(f"WAVE_05 — {len(obs5)} champs consensus/révisions, {len(failures5)} échecs")
    elif not finnhub_key:
        _audit(actions_df,etf_df,"WAVE_05_ACTION_CONSENSUS_KEY_MISSING",failures=[{"source":"Finnhub","reason":"FINNHUB_API_KEY_MISSING"}],source_context="Finnhub indisponible; données restent manquantes")
        print("WAVE_05 — FINNHUB_API_KEY absent : critères concernés restent N/A")

    if not checkpoint.done("WAVE_05B_FINNHUB_EARNINGS") and finnhub_key:
        obs5b, failures5b, stats5b = event_sources.collect_finnhub_earnings(
            actions_df, finnhub_key, STATE / "finnhub" / "EPS_ESTIMATE_HISTORY.csv",
        )
        actions_df, q5b = apply_and_track(actions_df, obs5b)
        quarantine_log += q5b
        _write_failures("V18.2_WAVE05B_FINNHUB_EARNINGS", failures5b)
        checkpoint.mark("WAVE_05B_FINNHUB_EARNINGS", "DONE", observed=len(obs5b), failed=len(failures5b), **stats5b)
        wave_metrics["WAVE_05B_FINNHUB_EARNINGS"]={"observed":len(obs5b),"failed":len(failures5b),**stats5b}
        _audit(actions_df,etf_df,"WAVE_05B_FINNHUB_EARNINGS",failures=failures5b+q5b,source_context="Finnhub Earnings Calendar + EPS Estimates PIT history")
        print(f"WAVE_05B — Finnhub Earnings/EPS: {len(obs5b)} observations, {len(failures5b)} échecs")
    elif not finnhub_key:
        _audit(actions_df,etf_df,"WAVE_05B_FINNHUB_EARNINGS_KEY_MISSING",failures=[{"source":"Finnhub Earnings","reason":"FINNHUB_API_KEY_MISSING"}],source_context="Calendrier Earnings et EPS Estimates non collectés")

    if not checkpoint.done("WAVE_05C_AMF_SHORT"):
        obs5c, failures5c, stats5c = event_sources.collect_amf_short_positions(actions_df)
        actions_df, q5c = apply_and_track(actions_df, obs5c)
        quarantine_log += q5c
        _write_failures("V18.2_WAVE05C_AMF_SHORT", failures5c)
        checkpoint.mark("WAVE_05C_AMF_SHORT", "DONE", observed=len(obs5c), failed=len(failures5c), **stats5c)
        wave_metrics["WAVE_05C_AMF_SHORT"]={"observed":len(obs5c),"failed":len(failures5c),**stats5c}
        _audit(actions_df,etf_df,"WAVE_05C_AMF_SHORT",failures=failures5c+q5c,source_context="AMF Open Data positions courtes nettes publiques; absence != 0")
        print(f"WAVE_05C — AMF positions courtes: {len(obs5c)} observations sur {stats5c.get('canonical_action_isins_matched',0)} ISIN")

    if not checkpoint.done("WAVE_06"):
        obs6, failures6 = waves.wave6_etf_info(etf_with_tickers, cfg)
        etf_df, q6 = apply_and_track(etf_df, obs6)
        quarantine_log += q6
        _write_failures("V18.2_WAVE06_ETF_YFINANCE", failures6)
        checkpoint.mark("WAVE_06", "DONE", observed=len(obs6), failed=len(failures6))
        _audit(actions_df,etf_df,"WAVE_06_ETF_INFO",failures=failures6+q6,source_context="yfinance ETF")
        print(f"WAVE_06 — {len(obs6)} champs ETF, {len(failures6)} échecs")

    if not checkpoint.done("WAVE_06B_MORNINGSTAR_ACTIONS"):
        from v182.sources.morningstar_actions import load_authorized_snapshot
        ms_cfg=cfg.get("morningstar_actions",{})
        obs_ms,fail_ms=load_authorized_snapshot(actions_df,ROOT/ms_cfg.get("snapshot_path","inputs/V21_ACTION_MORNINGSTAR_RATINGS.csv"),ROOT/ms_cfg.get("worklist_path","outputs/gaps/V21_ACTION_MORNINGSTAR_WORKLIST.csv"))
        actions_df,qms=apply_and_track(actions_df,obs_ms); quarantine_log+=qms
        checkpoint.mark("WAVE_06B_MORNINGSTAR_ACTIONS","DONE",observed=len(obs_ms),failed=len(fail_ms))
        _audit(actions_df,etf_df,"WAVE_06B_MORNINGSTAR_ACTIONS",failures=fail_ms+qms,source_context="Morningstar stock rating attribuée; aucune imputation")
        print(f"WAVE_06B — Morningstar Actions: {len(obs_ms)} observations attribuées")

    selectors_path = CONFIG / "V18.2_SCRAPE_SELECTORS.json"
    raw_selectors = json.loads(selectors_path.read_text(encoding="utf-8")) if selectors_path.exists() else {}
    selectors_cfg = {k: v for k, v in raw_selectors.items() if not k.startswith("_")}
    if not checkpoint.done("WAVE_05_06_SCRAPING_FALLBACK") and selectors_cfg:
        for wave_id, spec in selectors_cfg.items():
            rows = actions_df if spec["universe"] == "ACTION" else etf_with_tickers
            obs, failures = waves.wave_public_table(rows,spec["universe"],spec.get("field_map", {}),spec["url_template"],spec.get("selectors", {}),spec["source_name"],spec.get("evidence", "B"))
            if spec["universe"] == "ACTION": actions_df, q = apply_and_track(actions_df, obs)
            else: etf_df, q = apply_and_track(etf_df, obs)
            quarantine_log += q
            _write_failures(f"{wave_id}_{spec['source_name']}", failures)
            _audit(actions_df,etf_df,f"{wave_id}_{spec['source_name']}",failures=failures+q,source_context=spec["source_name"])
        checkpoint.mark("WAVE_05_06_SCRAPING_FALLBACK", "DONE")

    resolved = waves.wave7_official_validation(quarantine_log, CONFIG / "V18.2_MANUAL_OVERRIDES.csv")
    if resolved:
        actions_iso = {o["isin"] for o in resolved} & set(actions_df["isin"])
        etf_iso = {o["isin"] for o in resolved} & set(etf_df["isin"])
        actions_df, _ = apply_and_track(actions_df, [o for o in resolved if o["isin"] in actions_iso])
        etf_df, _ = apply_and_track(etf_df, [o for o in resolved if o["isin"] in etf_iso])
    from v182.reporting.wave7_worklist import write_worklist
    still_open = [q for q in quarantine_log if q not in resolved]
    write_worklist(still_open, actions_df, OUTPUTS / "gaps" / "V18.2_WAVE07_WORKLIST.csv")
    _audit(actions_df,etf_df,"WAVE_07_VALIDATION",failures=still_open,source_context="Issuer/AMF/Euronext manual overrides + quarantaine")

    shortlist = set(actions_df.loc[actions_df.get("comite_status", "").isin(["COMMITTEE", "WATCH"]), "isin"]) if "comite_status" in actions_df.columns else set()
    obs8 = waves.wave8_scenarios(actions_df, shortlist)
    actions_df, q8 = apply_and_track(actions_df, obs8); quarantine_log += q8
    _audit(actions_df,etf_df,"WAVE_08_SCENARIOS",failures=q8,source_context="Moteur scenarios interne")

    topdown_diagnostics={}
    if not checkpoint.done("WAVE_09_TOPDOWN"):
        obs9a, obs9e, topdown_diagnostics = waves.wave9_topdown(actions_df, etf_df, cfg, os.environ.get("FRED_API_KEY"))
        actions_df, q9a = apply_and_track(actions_df, obs9a)
        etf_df, q9e = apply_and_track(etf_df, obs9e)
        quarantine_log += q9a + q9e
        checkpoint.mark("WAVE_09_TOPDOWN", "DONE", actions_fields=len(obs9a), etf_fields=len(obs9e))
        _audit(actions_df,etf_df,"WAVE_09_TOPDOWN",failures=q9a+q9e,source_context="FRED + GDELT + PIT breadth/momentum")
        print(f"WAVE_09 — Top-Down: {len(obs9a)} valeurs Actions + {len(obs9e)} ETF")
    (OUTPUTS / "audit" / "V21_TOPDOWN_DIAGNOSTICS.json").write_text(json.dumps(topdown_diagnostics,ensure_ascii=False,indent=2,default=str),encoding="utf-8")

    if not checkpoint.done("WAVE_10_SECTOR_ROTATION"):
        from v182.features.sector_rotation import build_rotation_observations
        obs10,sectors,rotation_diag=build_rotation_observations(actions_df)
        actions_df,q10=apply_and_track(actions_df,obs10); quarantine_log+=q10
        sectors.to_csv(OUTPUTS/"V21_3_SECTOR_ROTATION.csv",sep=";",index=False,encoding="utf-8-sig")
        (OUTPUTS/"audit"/"V21_3_SECTOR_ROTATION.json").write_text(json.dumps(rotation_diag,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        checkpoint.mark("WAVE_10_SECTOR_ROTATION","DONE",observed=len(obs10),sectors=len(sectors))
        _audit(actions_df,etf_df,"WAVE_10_SECTOR_ROTATION",failures=q10,source_context="PIT OHLCV + secteurs; recovery gate anti-falling-knife")
        print(f"WAVE_10 — Rotation sectorielle: {len(sectors)} secteurs évalués")

    if not checkpoint.done("WAVE_11_ACTION_DECISION_FACTORS"):
        from v182.features.action_decision_enhancements import build_action_enhancement_observations
        obs11=build_action_enhancement_observations(actions_df)
        actions_df,q11=apply_and_track(actions_df,obs11); quarantine_log+=q11
        checkpoint.mark("WAVE_11_ACTION_DECISION_FACTORS","DONE",observed=len(obs11))
        _audit(actions_df,etf_df,"WAVE_11_ACTION_DECISION_FACTORS",failures=q11,source_context="Morningstar + objectif de cours + dividende >4% + potentiel rendement total")
        print(f"WAVE_11 — Facteurs décisionnels Actions renforcés: {len(obs11)} observations")

    save_master(actions_df, OUTPUTS / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    save_master(etf_df, OUTPUTS / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    if quarantine_log:
        pd.DataFrame(quarantine_log).to_csv(OUTPUTS / "gaps" / "V18.2_QUARANTINE.csv", sep=";", index=False, encoding="utf-8-sig")

    after = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    _audit(actions_df,etf_df,"WAVE_99_FINAL",failures=quarantine_log,source_context="Etat final après toutes les collectes et dérivations")
    (OUTPUTS / "audit" / "V18.2_COVERAGE_BEFORE_AFTER.json").write_text(json.dumps({"canonical_universe":canonical_audit,"expected_rows": expected_rows, "before": before, "after": after}, ensure_ascii=False, indent=2), encoding="utf-8")

    from v182.audit.quality import run_quality_gates
    from v182.reporting.exports import export_master_excel, export_run_report
    quality=run_quality_gates(actions_df, etf_df, before, after, cfg, wave_metrics, expected_rows=expected_rows)
    quality_payload={"passed":quality.passed,"canonical_universe":canonical_audit,"expected_rows":expected_rows,"checks":quality.checks}
    (OUTPUTS / "audit" / "V18.2_QUALITY_GATES.json").write_text(json.dumps(quality_payload,ensure_ascii=False,indent=2),encoding="utf-8")
    export_master_excel(actions_df, OUTPUTS / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx", "V21.3 Actions PEA actualisées — 1829")
    export_master_excel(etf_df, OUTPUTS / "V18.2_PEA_ETF_ACTUALISE.xlsx", "V21.3 ETF PEA actualisés")
    export_run_report(before, after, quality.checks, OUTPUTS / "V18.2_RUN_REPORT.xlsx")
    if not quality.passed:
        failed=[c["check"] for c in quality.checks if not c["passed"]]
        raise RuntimeError(f"QUALITY_GATE_BLOCK: {failed}")
    return {"status":"SUCCESS","run_id":run_id,"canonical_universe":canonical_audit,"expected_rows":expected_rows,"before":before,"after":after,"quality":quality_payload,"collection_audit_latest":"outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx"}


def apply_and_track(frame, observations):
    from v182.io.frames import apply_observations
    return apply_observations(frame, observations)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC PIPELINE: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise