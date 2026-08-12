from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
import os

from v182.io.frames import load_master, save_master
from v182.audit.completeness import completeness
from v182.state.checkpoint import Checkpoint
from v182.reporting import waves

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
STATE = ROOT / "state"
OUTPUTS = ROOT / "outputs"
CACHE = ROOT / "data" / "cache"


def _load_cfg() -> dict:
    return json.loads((CONFIG / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))


def _fields(df):
    skip = {"isin", "name"}
    return [c for c in df.columns if c not in skip]


def run() -> None:
    cfg = _load_cfg()
    run_id = os.environ.get("V182_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checkpoint = Checkpoint(STATE / "V18.2_checkpoint.json", run_id=run_id)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "gaps").mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "audit").mkdir(parents=True, exist_ok=True)

    actions_df = load_master(INPUTS / "V18.2_PEA_ACTIONS_MASTER.csv")
    etf_df = load_master(INPUTS / "V18.2_PEA_ETF_MASTER.csv")
    expected_rows = {"ACTION": len(actions_df), "ETF": len(etf_df)}

    before = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    print(f"Univers canonique gelé au début du run — Actions: {expected_rows['ACTION']} | ETF: {expected_rows['ETF']}")
    print(f"Couverture avant run — Actions: {before['ACTION']['coverage_pct']}% | ETF: {before['ETF']['coverage_pct']}%")

    quarantine_log: list[dict] = []
    wave_metrics: dict[str, dict] = {}

    # WAVE 00 — résolution ISIN -> ticker Yahoo pour les ETF (OpenFIGI, gratuit)
    if not checkpoint.done("WAVE_00_ETF_TICKERS"):
        import pandas as pd
        map_path = CONFIG / "V18.2_ETF_TICKER_MAP.csv"
        existing_map = pd.read_csv(map_path, sep=";", encoding="utf-8-sig", dtype=str) if map_path.exists() else pd.DataFrame()
        map_complete = (len(existing_map) == len(etf_df) and "yahoo_ticker" in existing_map.columns
                        and existing_map["isin"].nunique() == len(etf_df)
                        and (~existing_map["yahoo_ticker"].apply(lambda v: str(v or "").strip() == "")).all())
        if map_complete:
            summary = {"requested": len(etf_df), "resolved": len(etf_df), "gaps": 0, "source": "VALIDATED_STATIC_MAP"}
            print("WAVE_00 — table ETF validée complète, OpenFIGI non appelé")
        else:
            from v182.mapping.etf_isin_resolver import build_etf_ticker_map
            summary = build_etf_ticker_map(
                etf_master_path=INPUTS / "V18.2_PEA_ETF_MASTER.csv",
                output_map_path=map_path,
                gaps_path=OUTPUTS / "gaps" / "V18.2_ETF_TICKER_OPENFIGI_GAPS.csv",
            )
            print(f"WAVE_00 — OpenFIGI: {summary['resolved']}/{summary['requested']} tickers ETF résolus, "
                  f"{summary['gaps']} restent en gap")
        checkpoint.mark("WAVE_00_ETF_TICKERS", "DONE", **summary)
    else:
        print("WAVE_00 déjà DONE (checkpoint), skip")

    # WAVE 01 — OHLCV Actions
    if not checkpoint.done("WAVE_01"):
        result = waves.wave_history(actions_df, "ACTION", str(CACHE / "actions"), cfg)
        checkpoint.mark("WAVE_01", "DONE", requested=result.requested,
                         successful=len(result.successful), failed=len(result.failed))
        wave_metrics["WAVE_01"]={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed)}
        print(f"WAVE_01 — {len(result.successful)}/{result.requested} tickers Actions récupérés")
    else:
        wave_metrics["WAVE_01"]=checkpoint.wave("WAVE_01")
        print("WAVE_01 déjà DONE (checkpoint), skip")

    # WAVE 02 — OHLCV ETF (nécessite la table de correspondance ISIN -> ticker)
    etf_with_tickers, etf_gaps = waves.resolve_etf_tickers(etf_df, CONFIG / "V18.2_ETF_TICKER_MAP.csv")
    if not etf_gaps.empty:
        etf_gaps.to_csv(OUTPUTS / "gaps" / "V18.2_ETF_TICKER_GAPS.csv", sep=";", index=False, encoding="utf-8-sig")
        print(f"WAVE_02 — {len(etf_gaps)} ISIN ETF sans ticker mappé -> INPUT_REQUIRED (voir outputs/gaps)")
    if not checkpoint.done("WAVE_02"):
        result = waves.wave_history(etf_with_tickers, "ETF", str(CACHE / "etf"), cfg)
        checkpoint.mark("WAVE_02", "DONE", requested=result.requested,
                         successful=len(result.successful), failed=len(result.failed))
        wave_metrics["WAVE_02"]={"requested":result.requested,"successful":len(result.successful),"failed":len(result.failed)}
        print(f"WAVE_02 — {len(result.successful)}/{result.requested} tickers ETF récupérés")
    else:
        wave_metrics["WAVE_02"]=checkpoint.wave("WAVE_02")
        print("WAVE_02 déjà DONE (checkpoint), skip")

    # WAVE 03 — indicateurs dérivés (aucun appel réseau)
    if not checkpoint.done("WAVE_03"):
        actions_map = dict(zip(actions_df["yahoo_ticker"], actions_df["isin"]))
        etf_map = dict(zip(etf_with_tickers["yahoo_ticker"], etf_with_tickers["isin"]))

        obs_actions = waves.wave3_derived_features(str(CACHE / "actions"), actions_map, "ACTION")
        obs_etf = waves.wave3_derived_features(str(CACHE / "etf"), etf_map, "ETF")

        actions_df, q1 = apply_and_track(actions_df, obs_actions)
        etf_df, q2 = apply_and_track(etf_df, obs_etf)
        quarantine_log += q1 + q2
        checkpoint.mark("WAVE_03", "DONE", actions_fields=len(obs_actions), etf_fields=len(obs_etf))
        print(f"WAVE_03 — {len(obs_actions)} valeurs Actions + {len(obs_etf)} valeurs ETF calculées")
    else:
        print("WAVE_03 déjà DONE (checkpoint), skip")

    # WAVE 04 — fondamentaux yfinance sur Actions prioritaires
    if not checkpoint.done("WAVE_04"):
        obs4, failures4 = waves.wave4_info_actions(actions_df, cfg)
        actions_df, q3 = apply_and_track(actions_df, obs4)
        quarantine_log += q3
        checkpoint.mark("WAVE_04", "DONE", observed=len(obs4), failed=len(failures4))
        print(f"WAVE_04 — {len(obs4)} champs fondamentaux Actions, {len(failures4)} échecs tickers")
    else:
        print("WAVE_04 déjà DONE (checkpoint), skip")

    # WAVE 05 — consensus analystes Actions (Finnhub, remplace le scraping)
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if not checkpoint.done("WAVE_05") and finnhub_key:
        obs5, failures5 = waves.wave5_consensus_finnhub(actions_df, finnhub_key)
        actions_df, q5 = apply_and_track(actions_df, obs5)
        quarantine_log += q5
        checkpoint.mark("WAVE_05", "DONE", observed=len(obs5), failed=len(failures5))
        print(f"WAVE_05 — {len(obs5)} champs consensus (Finnhub), {len(failures5)} échecs tickers")
    elif not finnhub_key:
        print("WAVE_05 — FINNHUB_API_KEY absent, vague ignorée (voir secrets GitHub)")
    else:
        print("WAVE_05 déjà DONE (checkpoint), skip")

    # WAVE 06 — dividend yield ETF (yfinance) ; morningstar_rating/rank_cat_*
    # restent hors périmètre (pas de source gratuite fiable identifiée)
    if not checkpoint.done("WAVE_06"):
        obs6, failures6 = waves.wave6_etf_info(etf_with_tickers, cfg)
        etf_df, q6 = apply_and_track(etf_df, obs6)
        quarantine_log += q6
        checkpoint.mark("WAVE_06", "DONE", observed=len(obs6), failed=len(failures6))
        print(f"WAVE_06 — {len(obs6)} champs ETF (yfinance), {len(failures6)} échecs tickers")
    else:
        print("WAVE_06 déjà DONE (checkpoint), skip")

    # WAVE 05/06 bis — repli scraping optionnel (désactivé tant que
    # config/V18.2_SCRAPE_SELECTORS.json ne définit aucun sélecteur validé)
    selectors_path = CONFIG / "V18.2_SCRAPE_SELECTORS.json"
    raw_selectors = json.loads(selectors_path.read_text(encoding="utf-8")) if selectors_path.exists() else {}
    selectors_cfg = {k: v for k, v in raw_selectors.items() if not k.startswith("_")}
    if not checkpoint.done("WAVE_05_06_SCRAPING_FALLBACK") and selectors_cfg:
        for wave_id, spec in selectors_cfg.items():
            rows = actions_df if spec["universe"] == "ACTION" else etf_with_tickers
            obs, failures = waves.wave_public_table(
                rows, spec["universe"], spec.get("field_map", {}),
                spec["url_template"], spec.get("selectors", {}),
                spec["source_name"], spec.get("evidence", "B"),
            )
            if spec["universe"] == "ACTION":
                actions_df, q = apply_and_track(actions_df, obs)
            else:
                etf_df, q = apply_and_track(etf_df, obs)
            quarantine_log += q
            print(f"{wave_id} — {len(obs)} valeurs, {len(failures)} échecs ({spec['source_name']})")
        checkpoint.mark("WAVE_05_06_SCRAPING_FALLBACK", "DONE")
    else:
        print("WAVE_05_06_SCRAPING_FALLBACK — aucun sélecteur configuré (config/V18.2_SCRAPE_SELECTORS.json), skip")

    # WAVE 07 — validation officielle des conflits en quarantaine
    resolved = waves.wave7_official_validation(quarantine_log, CONFIG / "V18.2_MANUAL_OVERRIDES.csv")
    if resolved:
        actions_iso = {o["isin"] for o in resolved} & set(actions_df["isin"])
        etf_iso = {o["isin"] for o in resolved} & set(etf_df["isin"])
        actions_df, _ = apply_and_track(actions_df, [o for o in resolved if o["isin"] in actions_iso])
        etf_df, _ = apply_and_track(etf_df, [o for o in resolved if o["isin"] in etf_iso])
    print(f"WAVE_07 — {len(resolved)} conflits résolus par override officiel, "
          f"{len(quarantine_log) - len(resolved)} restent en quarantaine")

    from v182.reporting.wave7_worklist import write_worklist
    still_open = [q for q in quarantine_log if q not in resolved]
    n_worklist = write_worklist(still_open, actions_df, OUTPUTS / "gaps" / "V18.2_WAVE07_WORKLIST.csv")
    print(f"WAVE_07 — check-list humaine écrite ({n_worklist} lignes : conflits restants + "
          f"gaps critiques PEA) dans outputs/gaps/V18.2_WAVE07_WORKLIST.csv")

    # WAVE 08 — scénarios internes sur la short-list
    shortlist = set(actions_df.loc[actions_df.get("comite_status", "").isin(["COMMITTEE", "WATCH"]), "isin"]) \
        if "comite_status" in actions_df.columns else set()
    obs8 = waves.wave8_scenarios(actions_df, shortlist)
    actions_df, q8 = apply_and_track(actions_df, obs8)
    quarantine_log += q8
    print(f"WAVE_08 — scénarios calculés pour {len(shortlist)} valeurs de la short-list")

    # Sauvegarde des référentiels enrichis + audit de couverture
    save_master(actions_df, OUTPUTS / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    save_master(etf_df, OUTPUTS / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")

    if quarantine_log:
        import pandas as pd
        pd.DataFrame(quarantine_log).to_csv(OUTPUTS / "gaps" / "V18.2_QUARANTINE.csv",
                                             sep=";", index=False, encoding="utf-8-sig")

    after = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    (OUTPUTS / "audit" / "V18.2_COVERAGE_BEFORE_AFTER.json").write_text(
        json.dumps({"expected_rows": expected_rows, "before": before, "after": after}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Couverture après run — Actions: {after['ACTION']['coverage_pct']}% "
          f"(+{round(after['ACTION']['coverage_pct'] - before['ACTION']['coverage_pct'], 1)} pts) | "
          f"ETF: {after['ETF']['coverage_pct']}% "
          f"(+{round(after['ETF']['coverage_pct'] - before['ETF']['coverage_pct'], 1)} pts)")

    from v182.audit.quality import run_quality_gates
    from v182.reporting.exports import export_master_excel, export_run_report
    quality=run_quality_gates(actions_df, etf_df, before, after, cfg, wave_metrics, expected_rows=expected_rows)
    (OUTPUTS / "audit" / "V18.2_QUALITY_GATES.json").write_text(
        json.dumps({"passed":quality.passed,"expected_rows":expected_rows,"checks":quality.checks},ensure_ascii=False,indent=2),encoding="utf-8")
    export_master_excel(actions_df, OUTPUTS / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx", "V18.2 Actions PEA actualisées")
    export_master_excel(etf_df, OUTPUTS / "V18.2_PEA_ETF_ACTUALISE.xlsx", "V18.2 ETF PEA actualisés")
    export_run_report(before, after, quality.checks, OUTPUTS / "V18.2_RUN_REPORT.xlsx")
    if not quality.passed:
        failed=[c["check"] for c in quality.checks if not c["passed"]]
        raise RuntimeError(f"QUALITY_GATE_BLOCK: {failed}")


def apply_and_track(frame, observations):
    from v182.io.frames import apply_observations
    return apply_observations(frame, observations)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC PIPELINE: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
