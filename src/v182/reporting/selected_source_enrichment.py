from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
import json

import pandas as pd

from v182.sources.boursorama_selected import collect_selected_action_context_cached
from v182.sources.boursorama_selected_etf import collect_selected_etf_context_cached
from v182.sources.tradingview_technical import collect_technical_context_cached
from v182.sources.rate_limit import StartRateLimiter

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = Path("config/SOURCE_FUNCTIONAL_CONTRACT_V21_15.json")


def _read_contract(root: Path) -> dict:
    return json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))


def _score_sort(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["_source_priority_score"] = pd.to_numeric(out.get("score"), errors="coerce")
    if "selected_rank" in out:
        out["_selected_rank"] = pd.to_numeric(out["selected_rank"], errors="coerce")
    else:
        out["_selected_rank"] = pd.NA
    return out.sort_values(["_selected_rank", "_source_priority_score"], ascending=[True, False], na_position="last")


def select_preselected_rows(rows: pd.DataFrame, *, max_unique_instruments: int = 40, accepted_statuses: tuple[str, ...] = ("BUY_CANDIDATE", "WATCH", "REVIEW", "SHADOW_CANDIDATE")) -> pd.DataFrame:
    if rows.empty or "isin" not in rows:
        return pd.DataFrame(columns=rows.columns)
    frame = rows.copy()
    if "decision" in frame:
        selected = frame["decision"].astype(str).str.upper().isin(accepted_statuses)
    elif "dynamic_decision" in frame:
        selected = frame["dynamic_decision"].astype(str).str.upper().isin(accepted_statuses)
    elif "selected_rank" in frame:
        selected = pd.to_numeric(frame["selected_rank"], errors="coerce").notna()
    elif "dynamic_selected" in frame:
        selected = frame["dynamic_selected"].fillna(False).astype(bool)
    else:
        selected = pd.Series(False, index=frame.index)
    frame = frame[selected].copy()
    if frame.empty:
        return frame
    ordered = _score_sort(frame)
    unique_isins = list(dict.fromkeys(ordered["isin"].astype(str).tolist()))[: max(0, int(max_unique_instruments))]
    return frame[frame["isin"].astype(str).isin(unique_isins)].copy()


def attach_master_identity(selected: pd.DataFrame, actions: pd.DataFrame | None, etfs: pd.DataFrame | None) -> pd.DataFrame:
    if selected.empty:
        return selected
    frames = []
    for master, asset in ((actions, "ACTION"), (etfs, "ETF")):
        if master is None or master.empty or "isin" not in master:
            continue
        keep = [c for c in ("isin", "name", "yahoo_ticker", "long_name_yf", "tradingview_symbol", "boursorama_code") if c in master]
        part = master[keep].copy()
        part["asset_class"] = asset
        frames.append(part)
    if not frames:
        return selected
    identity = pd.concat(frames, ignore_index=True, sort=False).drop_duplicates(["isin", "asset_class"])
    result = selected.copy()
    if "asset_class" not in result:
        result["asset_class"] = "ACTION"
    result = result.merge(identity, on=["isin", "asset_class"], how="left", suffixes=("", "_master"))
    for field in ("name", "yahoo_ticker", "long_name_yf", "tradingview_symbol", "boursorama_code"):
        master_field = f"{field}_master"
        if master_field in result:
            if field not in result:
                result[field] = result[master_field]
            else:
                missing = result[field].isna() | result[field].astype(str).str.strip().isin({"", "nan", "None"})
                result.loc[missing, field] = result.loc[missing, master_field]
            result = result.drop(columns=[master_field])
    return result


def _pivot(observations: list[dict]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame()
    frame = pd.DataFrame(observations)
    required = {"isin", "horizon", "field", "value"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    index = [c for c in ("isin", "asset_class", "horizon") if c in frame]
    return frame.pivot_table(index=index, columns="field", values="value", aggfunc="last").reset_index()


def _shared_boursorama_fetcher(limiter: StartRateLimiter, max_inflight: int):
    inflight_gate = BoundedSemaphore(max(1, int(max_inflight)))
    def fetch(url: str, *, timeout: float):
        import requests
        with inflight_gate:
            limiter.wait()
            return requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.15; selected-public-context)"}, timeout=timeout)
    return fetch


def enrich_selected_rows(rows: pd.DataFrame, root: Path = ROOT, *, profile: str = "SELECTED") -> tuple[pd.DataFrame, dict]:
    contract = _read_contract(root)
    scope = contract["scope"]
    selected = select_preselected_rows(rows, max_unique_instruments=int(scope["selected_only_max_unique_instruments"]), accepted_statuses=tuple(scope["preselection_statuses"]))
    if selected.empty:
        return rows.copy(), {"status": "NO_PRESELECTED_ROWS", "profile": profile, "selected_rows": 0, "decision_influence": False, "score_influence": 0.0}

    bcfg = contract["boursorama"]
    tcfg = contract["tradingview"]
    asset_upper = selected["asset_class"].astype(str).str.upper()
    action_selected = selected[asset_upper.eq("ACTION")].copy()
    etf_selected = selected[asset_upper.eq("ETF")].copy()

    def run_boursorama() -> tuple[object | None, object | None]:
        action_enabled = not action_selected.empty and bool(bcfg.get("priority_for_selected_actions", True))
        etf_enabled = not etf_selected.empty and bool(bcfg.get("priority_for_selected_etfs", False))
        if not action_enabled and not etf_enabled:
            return None, None
        provider_workers = max(1, int(bcfg["max_workers"]))
        provider_max_inflight = max(1, int(bcfg.get("provider_max_inflight", provider_workers)))
        shared_limiter = StartRateLimiter(float(bcfg["request_start_interval_seconds"]))
        shared_fetcher = _shared_boursorama_fetcher(shared_limiter, provider_max_inflight)

        def collect_actions():
            return collect_selected_action_context_cached(action_selected, root / "state/provenance/source_cache/BOURSORAMA_SELECTED_V1.json", dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]), deep_ttl_hours=float(bcfg["deep_ttl_hours"]), refresh_budget=int(bcfg["refresh_budget"]), request_start_interval_seconds=0.0, max_workers=provider_workers, fetcher=shared_fetcher)

        def collect_etfs():
            return collect_selected_etf_context_cached(etf_selected, root / "state/provenance/source_cache/BOURSORAMA_SELECTED_ETF_V1.json", dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]), deep_ttl_hours=float(bcfg["deep_ttl_hours"]), refresh_budget=int(bcfg["refresh_budget"]), request_start_interval_seconds=0.0, max_workers=provider_workers, fetcher=shared_fetcher)

        if action_enabled and etf_enabled:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="boursorama-assets") as pool:
                af = pool.submit(collect_actions); ef = pool.submit(collect_etfs)
                return af.result(), ef.result()
        if action_enabled:
            return collect_actions(), None
        return None, collect_etfs()

    def run_tradingview():
        return collect_technical_context_cached(selected, root / "state/provenance/source_cache/TRADINGVIEW_TECHNICAL_V1.json", refresh_budget=int(tcfg["refresh_budget"]), ttl_hours=float(tcfg["ttl_hours"]), request_start_interval_seconds=float(tcfg["request_start_interval_seconds"]), max_workers=int(tcfg["max_workers"]))

    b_action_result = b_etf_result = tv_result = None
    branch_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="selected-source") as pool:
        futures = {"boursorama": pool.submit(run_boursorama), "tradingview": pool.submit(run_tradingview)}
        for name, future in futures.items():
            try:
                result = future.result()
                if name == "boursorama":
                    b_action_result, b_etf_result = result
                else:
                    tv_result = result
            except Exception as exc:
                branch_errors.append({"source": name, "reason": type(exc).__name__, "detail": str(exc)[:240]})

    observations: list[dict] = []
    failures: list[dict] = list(branch_errors)
    for result in (b_action_result, b_etf_result, tv_result):
        if result is None:
            continue
        observations.extend(result.observations)
        failures.extend(result.failures)

    context = _pivot(observations)
    enriched = rows.copy()
    if not context.empty:
        keys = [c for c in ("isin", "asset_class", "horizon") if c in enriched and c in context]
        enriched = enriched.merge(context, on=keys, how="left")

    outdir = root / "outputs/source_context"; auditdir = root / "outputs/audit"
    outdir.mkdir(parents=True, exist_ok=True); auditdir.mkdir(parents=True, exist_ok=True)
    safe_profile = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in profile.upper())
    selected.to_csv(outdir / f"{safe_profile}_PRESELECTED_INPUT.csv", sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame(observations).to_csv(outdir / f"{safe_profile}_SOURCE_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame(failures).to_csv(outdir / f"{safe_profile}_SOURCE_FAILURES.csv", sep=";", index=False, encoding="utf-8-sig")

    payload = {
        "status": "SUCCESS_WITH_CONTEXT" if observations else "SUCCESS_NO_SOURCE_DATA",
        "version": contract["version"], "profile": profile, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_rows": int(len(selected)), "selected_unique_isins": int(selected["isin"].nunique()),
        "boursorama_actions": b_action_result.metrics if b_action_result is not None else {"status": "NO_ACTION_SELECTED_OR_BRANCH_FAILED"},
        "boursorama_etfs": b_etf_result.metrics if b_etf_result is not None else {"status": "NO_ETF_SELECTED_OR_BRANCH_FAILED"},
        "tradingview": tv_result.metrics if tv_result is not None else {"status": "BRANCH_FAILED"},
        "investing": {"status": "REPLACED_BY_TRADINGVIEW", "active": False},
        "failures": int(len(failures)), "weights_unchanged": True, "thresholds_unchanged": True,
        "decision_influence": False, "score_influence": 0.0, "can_create_buy": False,
        "functional_contract": "config/SOURCE_FUNCTIONAL_CONTRACT_V21_15.json",
        "boursorama_asset_overlap": True, "boursorama_shared_start_limiter": True,
        "boursorama_shared_inflight_limit": int(bcfg.get("provider_max_inflight", bcfg["max_workers"])),
        "technical_provider": "TradingView", "tradingview_weekly_validated_module": "TRADINGVIEW_TECHNICAL_V1"
    }
    (auditdir / f"{safe_profile}_SELECTED_SOURCE_CONTEXT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return enriched, payload
