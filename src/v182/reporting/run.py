from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

from v182.io.frames import load_master, save_master, is_missing
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


def _fields(frame):
    skip = {"isin", "name"}
    return [column for column in frame.columns if column not in skip and not str(column).startswith("_")]


def _load_seed_master(input_path: Path, enriched_path: Path):
    baseline = load_master(input_path)
    if not enriched_path.exists():
        return baseline, "BASELINE_INPUT"
    try:
        previous = load_master(enriched_path)
        if len(previous) != len(baseline):
            return baseline, "BASELINE_INPUT_ROW_MISMATCH"
        if set(previous["isin"].astype(str)) != set(baseline["isin"].astype(str)):
            return baseline, "BASELINE_INPUT_ISIN_MISMATCH"

        prev = previous.set_index("isin", drop=False)
        base = baseline.set_index("isin", drop=False)
        for column in baseline.columns:
            if column not in prev.columns:
                prev[column] = base[column]
            else:
                mask = prev[column].apply(is_missing)
                if mask.any():
                    prev.loc[mask, column] = base.loc[mask, column]
        return prev.reset_index(drop=True), "PREVIOUS_ENRICHED_OUTPUT"
    except Exception:
        return baseline, "BASELINE_INPUT_INVALID_PREVIOUS_OUTPUT"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_and_track(frame, observations):
    from v182.io.frames import apply_observations
    return apply_observations(frame, observations)


def _wave_metric(result) -> dict:
    return {
        "requested": result.requested,
        "successful": len(result.successful),
        "failed": len(result.failed),
        "source_counts": result.source_counts,
        "diagnostics": result.diagnostics,
    }


def run() -> None:
    cfg = _load_cfg()
    run_id = os.environ.get("V182_RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    checkpoint = Checkpoint(STATE / "V18.2_checkpoint.json", run_id=run_id)

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "gaps").mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "audit").mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "context").mkdir(parents=True, exist_ok=True)

    actions_df, actions_seed = _load_seed_master(
        INPUTS / "V18.2_PEA_ACTIONS_MASTER.csv",
        OUTPUTS / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
    )
    etf_df, etf_seed = _load_seed_master(
        INPUTS / "V18.2_PEA_ETF_MASTER.csv",
        OUTPUTS / "V18.2_PEA_ETF_MASTER_ENRICHED.csv",
    )
    print(f"Sources de départ — Actions: {actions_seed} | ETF: {etf_seed}")

    before = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    print(
        f"Couverture avant run — Actions: {before['ACTION']['coverage_pct']}% | "
        f"ETF: {before['ETF']['coverage_pct']}%"
    )

    quarantine_log: list[dict] = []
    wave_metrics: dict[str, dict] = {}

    # WAVE 00 — persistent identity cache. Transient OpenFIGI failures are not
    # negative-cached and stale ticker/MIC identities are invalidated.
    openfigi_map_path = CONFIG / "V18.2_OPENFIGI_MASTER_MAP.csv"
    from v182.mapping.etf_isin_resolver import build_openfigi_master_map

    of_cfg = cfg.get("openfigi", {})
    openfigi_summary = build_openfigi_master_map(
        actions_df,
        etf_df,
        openfigi_map_path,
        api_key=os.environ.get("OPENFIGI_API_KEY"),
        negative_cache_days=int(of_cfg.get("negative_cache_days", 30) or 30),
    )
    wave_metrics["WAVE_00_OPENFIGI"] = openfigi_summary
    checkpoint.mark("WAVE_00_OPENFIGI", "DONE", **openfigi_summary)
    print(
        f"WAVE_00_OPENFIGI — {openfigi_summary['resolved']}/{openfigi_summary['records']} résolus "
        f"({openfigi_summary['coverage_pct']}%), API={openfigi_summary['api_isins_requested']}, "
        f"transitoires={openfigi_summary['transient_failures']}"
    )

    # ETF Yahoo map is an authoritative static input when complete; OpenFIGI is
    # used only to repair missing mapping rows.
    import pandas as pd

    map_path = CONFIG / "V18.2_ETF_TICKER_MAP.csv"
    existing_map = (
        pd.read_csv(map_path, sep=";", encoding="utf-8-sig", dtype=str)
        if map_path.exists()
        else pd.DataFrame()
    )
    map_complete = (
        len(existing_map) == len(etf_df)
        and "isin" in existing_map.columns
        and "yahoo_ticker" in existing_map.columns
        and existing_map["isin"].nunique() == len(etf_df)
        and (~existing_map["yahoo_ticker"].apply(is_missing)).all()
    )
    if map_complete:
        etf_map_summary = {
            "requested": len(etf_df),
            "resolved": len(etf_df),
            "gaps": 0,
            "source": "VALIDATED_STATIC_MAP",
        }
    else:
        from v182.mapping.etf_isin_resolver import build_etf_ticker_map

        etf_map_summary = build_etf_ticker_map(
            etf_master_path=INPUTS / "V18.2_PEA_ETF_MASTER.csv",
            output_map_path=map_path,
            gaps_path=OUTPUTS / "gaps" / "V18.2_ETF_TICKER_OPENFIGI_GAPS.csv",
            api_key=os.environ.get("OPENFIGI_API_KEY"),
        )
    checkpoint.mark("WAVE_00_ETF_TICKERS", "DONE", **etf_map_summary)
    wave_metrics["WAVE_00_ETF_TICKERS"] = etf_map_summary
    print(
        f"WAVE_00_ETF_TICKERS — {etf_map_summary['resolved']}/"
        f"{etf_map_summary['requested']} résolus"
    )

    # WAVE 01/02 — one coherent OHLCV chain:
    # Yahoo -> OpenFIGI/Yahoo repair -> Marketstack -> Alpha Vantage.
    from v182.sources.history_orchestrator import download_history_with_fallback

    if not checkpoint.done("WAVE_01"):
        action_history = download_history_with_fallback(
            actions_df,
            "ACTION",
            str(CACHE / "actions"),
            cfg,
            openfigi_map_path,
        )
        wave_metrics["WAVE_01"] = _wave_metric(action_history)
        checkpoint.mark("WAVE_01", "DONE", **wave_metrics["WAVE_01"])
    else:
        wave_metrics["WAVE_01"] = checkpoint.wave("WAVE_01")
    print(
        f"WAVE_01 — {wave_metrics['WAVE_01'].get('successful', 0)}/"
        f"{wave_metrics['WAVE_01'].get('requested', 0)} Actions OHLCV"
    )

    etf_with_tickers, etf_gaps = waves.resolve_etf_tickers(etf_df, map_path)
    if not etf_gaps.empty:
        etf_gaps.to_csv(
            OUTPUTS / "gaps" / "V18.2_ETF_TICKER_GAPS.csv",
            sep=";",
            index=False,
            encoding="utf-8-sig",
        )

    if not checkpoint.done("WAVE_02"):
        etf_history = download_history_with_fallback(
            etf_with_tickers,
            "ETF",
            str(CACHE / "etf"),
            cfg,
            openfigi_map_path,
        )
        wave_metrics["WAVE_02"] = _wave_metric(etf_history)
        checkpoint.mark("WAVE_02", "DONE", **wave_metrics["WAVE_02"])
    else:
        wave_metrics["WAVE_02"] = checkpoint.wave("WAVE_02")
    print(
        f"WAVE_02 — {wave_metrics['WAVE_02'].get('successful', 0)}/"
        f"{wave_metrics['WAVE_02'].get('requested', 0)} ETF OHLCV"
    )

    # WAVE 03 — internal technical/risk features from the best cached history.
    actions_map = {
        ticker: isin
        for ticker, isin in zip(actions_df["yahoo_ticker"], actions_df["isin"])
        if not is_missing(ticker)
    }
    etf_map = {
        ticker: isin
        for ticker, isin in zip(etf_with_tickers["yahoo_ticker"], etf_with_tickers["isin"])
        if not is_missing(ticker)
    }
    obs_actions = waves.wave3_derived_features(str(CACHE / "actions"), actions_map, "ACTION")
    obs_etf = waves.wave3_derived_features(str(CACHE / "etf"), etf_map, "ETF")
    actions_df, q1 = apply_and_track(actions_df, obs_actions)
    etf_df, q2 = apply_and_track(etf_df, obs_etf)
    quarantine_log.extend(q1 + q2)
    checkpoint.mark("WAVE_03", "DONE", actions_fields=len(obs_actions), etf_fields=len(obs_etf))
    print(f"WAVE_03 — {len(obs_actions)} champs Actions + {len(obs_etf)} champs ETF")

    # WAVE 04 — Yahoo fundamentals only. Alpha Vantage is deliberately not used
    # for fundamentals because the live audit validated it only for global OHLCV.
    obs4, failures4, meta4 = waves.wave4_info_actions(actions_df, cfg)
    actions_df, q4 = apply_and_track(actions_df, obs4)
    quarantine_log.extend(q4)
    available4, total4, pct4 = waves.fundamentals_availability(actions_df)
    wave_metrics["WAVE_04"] = {
        **meta4,
        "available": available4,
        "requested": total4,
        "available_pct": pct4,
        "failed": len(failures4),
    }
    checkpoint.mark("WAVE_04", "DONE", **wave_metrics["WAVE_04"])
    print(
        f"WAVE_04 — fondamentaux {available4}/{total4} ({pct4}%), "
        f"Yahoo interrogés={meta4['attempted']}"
    )

    # Official macro context — FRED is not a per-security quote source, but its
    # two fields already exist in the Action master and therefore apply equally
    # to every Action for the same as-of date.
    fred_key = os.environ.get("FRED_API_KEY")
    if fred_key and cfg.get("fred", {}).get("enabled", True):
        try:
            obs_macro, macro_context = waves.wave_macro_fred(actions_df, fred_key)
            actions_df, q_macro = apply_and_track(actions_df, obs_macro)
            quarantine_log.extend(q_macro)
            wave_metrics["WAVE_MACRO_FRED"] = {
                "key_present": True,
                "success": True,
                "api_calls": macro_context.get("api_calls", 0),
                "as_of": macro_context.get("macro_as_of", ""),
            }
            _write_json(OUTPUTS / "context" / "V18.2_MACRO_CONTEXT.json", macro_context)
            print(
                f"WAVE_MACRO_FRED — VIX={macro_context['macro_vix']} | "
                f"10Y-2Y={macro_context['macro_curve_10y2y']} | "
                f"as_of={macro_context['macro_as_of']}"
            )
        except Exception as exc:
            wave_metrics["WAVE_MACRO_FRED"] = {
                "key_present": True,
                "success": False,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
            print(f"WAVE_MACRO_FRED — échec contrôlé: {type(exc).__name__}")
    else:
        wave_metrics["WAVE_MACRO_FRED"] = {
            "key_present": bool(fred_key),
            "success": False,
            "missing_key": not bool(fred_key),
            "disabled": not cfg.get("fred", {}).get("enabled", True),
        }

    # Official energy context — run-level only because the master has no valid
    # security-level EIA fields. No fake duplication onto all securities.
    eia_key = os.environ.get("EIA_API_KEY")
    if eia_key and cfg.get("eia", {}).get("enabled", True):
        try:
            from v182.sources.eia_energy import fetch_energy_context

            energy_context = fetch_energy_context(eia_key)
            wave_metrics["WAVE_ENERGY_EIA"] = {
                "key_present": True,
                "success": True,
                "api_calls": energy_context.get("api_calls", 0),
                "as_of": energy_context.get("energy_as_of", ""),
            }
            _write_json(OUTPUTS / "context" / "V18.2_ENERGY_CONTEXT.json", energy_context)
            print(
                f"WAVE_ENERGY_EIA — WTI={energy_context['wti_spot_usd_bbl']} | "
                f"Brent={energy_context['brent_spot_usd_bbl']} | "
                f"spread={energy_context['brent_wti_spread_usd_bbl']}"
            )
        except Exception as exc:
            wave_metrics["WAVE_ENERGY_EIA"] = {
                "key_present": True,
                "success": False,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
            print(f"WAVE_ENERGY_EIA — échec contrôlé: {type(exc).__name__}")
    else:
        wave_metrics["WAVE_ENERGY_EIA"] = {
            "key_present": bool(eia_key),
            "success": False,
            "missing_key": not bool(eia_key),
            "disabled": not cfg.get("eia", {}).get("enabled", True),
        }

    # WAVE 05 — use Yahoo consensus when already present, otherwise Finnhub with
    # guarded symbol lookup and cache.
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if finnhub_key:
        obs5, failures5, meta5 = waves.wave5_consensus_finnhub(
            actions_df,
            finnhub_key,
            symbol_cache_path=CONFIG / "V18.2_FINNHUB_SYMBOL_MAP.csv",
            cfg=cfg,
        )
        actions_df, q5 = apply_and_track(actions_df, obs5)
        quarantine_log.extend(q5)
        available5, total5, pct5 = waves.consensus_availability(actions_df)
        wave_metrics["WAVE_05"] = {
            **meta5,
            "available": available5,
            "requested": total5,
            "available_pct": pct5,
            "failed": len(failures5),
            "key_present": True,
        }
        checkpoint.mark("WAVE_05", "DONE", **wave_metrics["WAVE_05"])
        print(
            f"WAVE_05 — consensus {available5}/{total5} ({pct5}%), "
            f"Finnhub tentés={meta5['attempted_finnhub']}"
        )
    else:
        available5, total5, pct5 = waves.consensus_availability(actions_df)
        wave_metrics["WAVE_05"] = {
            "available": available5,
            "requested": total5,
            "available_pct": pct5,
            "missing_key": True,
            "key_present": False,
        }
        checkpoint.mark("WAVE_05", "SKIPPED_NO_KEY", **wave_metrics["WAVE_05"])

    # WAVE 06 — limited ETF info available through yfinance.
    obs6, failures6 = waves.wave6_etf_info(etf_with_tickers, cfg)
    etf_df, q6 = apply_and_track(etf_df, obs6)
    quarantine_log.extend(q6)
    wave_metrics["WAVE_06"] = {"observed": len(obs6), "failed": len(failures6)}
    checkpoint.mark("WAVE_06", "DONE", **wave_metrics["WAVE_06"])
    print(f"WAVE_06 — {len(obs6)} champs ETF, {len(failures6)} échecs")

    # Public-table fallback remains disabled unless explicit selectors are
    # configured. Empty configuration cannot silently claim source coverage.
    selectors_path = CONFIG / "V18.2_SCRAPE_SELECTORS.json"
    raw_selectors = (
        json.loads(selectors_path.read_text(encoding="utf-8"))
        if selectors_path.exists()
        else {}
    )
    selectors_cfg = {
        key: value
        for key, value in raw_selectors.items()
        if not key.startswith("_")
    }
    if selectors_cfg:
        for wave_id, spec in selectors_cfg.items():
            rows = actions_df if spec["universe"] == "ACTION" else etf_with_tickers
            observations, failures = waves.wave_public_table(
                rows,
                spec["universe"],
                spec.get("field_map", {}),
                spec["url_template"],
                spec.get("selectors", {}),
                spec["source_name"],
                spec.get("evidence", "B"),
            )
            if spec["universe"] == "ACTION":
                actions_df, quarantined = apply_and_track(actions_df, observations)
            else:
                etf_df, quarantined = apply_and_track(etf_df, observations)
            quarantine_log.extend(quarantined)
            print(f"{wave_id} — {len(observations)} valeurs, {len(failures)} échecs")
        checkpoint.mark("WAVE_05_06_SCRAPING_FALLBACK", "DONE")
    else:
        checkpoint.mark("WAVE_05_06_SCRAPING_FALLBACK", "SKIPPED_NO_SELECTORS")

    # WAVE 07 — manual A-level overrides only; this is deliberately not labelled
    # as automatic AMF/Euronext ingestion.
    resolved = waves.wave7_official_validation(
        quarantine_log,
        CONFIG / "V18.2_MANUAL_OVERRIDES.csv",
    )
    if resolved:
        action_isins = set(actions_df["isin"])
        etf_isins = set(etf_df["isin"])
        actions_df, _ = apply_and_track(
            actions_df,
            [item for item in resolved if item["isin"] in action_isins],
        )
        etf_df, _ = apply_and_track(
            etf_df,
            [item for item in resolved if item["isin"] in etf_isins],
        )

    from v182.reporting.wave7_worklist import write_worklist

    resolved_keys = {(item["isin"], item["field"]) for item in resolved}
    still_open = [
        item
        for item in quarantine_log
        if (item.get("isin"), item.get("field")) not in resolved_keys
    ]
    n_worklist = write_worklist(
        still_open,
        actions_df,
        OUTPUTS / "gaps" / "V18.2_WAVE07_WORKLIST.csv",
    )
    print(f"WAVE_07 — overrides={len(resolved)}, worklist={n_worklist}")

    # WAVE 08 — internal scenarios for Committee/Watch only.
    shortlist = (
        set(
            actions_df.loc[
                actions_df["comite_status"].isin(["COMMITTEE", "WATCH"]),
                "isin",
            ]
        )
        if "comite_status" in actions_df.columns
        else set()
    )
    obs8 = waves.wave8_scenarios(actions_df, shortlist)
    actions_df, q8 = apply_and_track(actions_df, obs8)
    quarantine_log.extend(q8)
    print(f"WAVE_08 — scénarios={len(shortlist)} valeurs")

    save_master(actions_df, OUTPUTS / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    save_master(etf_df, OUTPUTS / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    if quarantine_log:
        pd.DataFrame(quarantine_log).to_csv(
            OUTPUTS / "gaps" / "V18.2_QUARANTINE.csv",
            sep=";",
            index=False,
            encoding="utf-8-sig",
        )

    after = {
        "ACTION": completeness(actions_df.to_dict("records"), _fields(actions_df)),
        "ETF": completeness(etf_df.to_dict("records"), _fields(etf_df)),
    }
    _write_json(
        OUTPUTS / "audit" / "V18.2_COVERAGE_BEFORE_AFTER.json",
        {"before": before, "after": after},
    )
    _write_json(
        OUTPUTS / "audit" / "V18.2_SOURCE_FALLBACK_METRICS.json",
        {
            "seed": {"actions": actions_seed, "etf": etf_seed},
            "openfigi": wave_metrics.get("WAVE_00_OPENFIGI", {}),
            "etf_ticker_map": wave_metrics.get("WAVE_00_ETF_TICKERS", {}),
            "wave01_actions": wave_metrics.get("WAVE_01", {}),
            "wave02_etf": wave_metrics.get("WAVE_02", {}),
            "wave04_yfinance": wave_metrics.get("WAVE_04", {}),
            "macro_fred": wave_metrics.get("WAVE_MACRO_FRED", {}),
            "energy_eia": wave_metrics.get("WAVE_ENERGY_EIA", {}),
            "wave05_finnhub": wave_metrics.get("WAVE_05", {}),
            "wave06_etf_info": wave_metrics.get("WAVE_06", {}),
        },
    )
    print(
        f"Couverture après run — Actions: {after['ACTION']['coverage_pct']}% | "
        f"ETF: {after['ETF']['coverage_pct']}%"
    )

    from v182.audit.quality import run_quality_gates
    from v182.reporting.exports import export_master_excel, export_run_report

    quality = run_quality_gates(actions_df, etf_df, before, after, cfg, wave_metrics)
    _write_json(
        OUTPUTS / "audit" / "V18.2_QUALITY_GATES.json",
        {"passed": quality.passed, "checks": quality.checks},
    )
    export_master_excel(
        actions_df,
        OUTPUTS / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2 Actions PEA actualisées",
    )
    export_master_excel(
        etf_df,
        OUTPUTS / "V18.2_PEA_ETF_ACTUALISE.xlsx",
        "V18.2 ETF PEA actualisés",
    )
    export_run_report(
        before,
        after,
        quality.checks,
        OUTPUTS / "V18.2_RUN_REPORT.xlsx",
    )

    if not quality.passed:
        failed = [check["check"] for check in quality.checks if not check["passed"]]
        raise RuntimeError(f"QUALITY_GATE_BLOCK: {failed}")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ECHEC PIPELINE: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
