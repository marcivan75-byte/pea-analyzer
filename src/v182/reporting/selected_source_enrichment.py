from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

from v182.sources.boursorama_selected import collect_selected_action_context_cached
from v182.sources.boursorama_selected_etf import collect_selected_etf_context_cached
from v182.sources.investing_technical import _safe_investing_url, collect_technical_context_cached
from v182.sources.rate_limit import StartRateLimiter

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = Path("config/SOURCE_FUNCTIONAL_CONTRACT_V21_16.json")
NETWORK_POLICIES = {"LIVE_IF_DUE", "CACHE_ONLY"}
DEFAULT_PRESELECTED_STATUSES = (
    "BUY_CANDIDATE",
    "T2_CONFIRM_75_SHADOW",
    "T1_STARTER_25_SHADOW",
    "SHADOW_CANDIDATE",
    "WATCH",
    "T1_WATCH_SHADOW",
    "WATCH_NOT_TOP2",
    "REVIEW",
)
SOURCE_DECISION_PRIORITY = {
    "BUY_CANDIDATE": 0,
    "T2_CONFIRM_75_SHADOW": 1,
    "T1_STARTER_25_SHADOW": 2,
    "SHADOW_CANDIDATE": 3,
    "WATCH": 4,
    "T1_WATCH_SHADOW": 5,
    "WATCH_NOT_TOP2": 6,
    "REVIEW": 7,
}


def _read_contract(root: Path) -> dict:
    return json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))


def _json_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _migrate_cache_version(path: Path, *, old_version: str, new_version: str) -> bool:
    payload = _json_cache(path)
    if payload.get("version") != old_version or not isinstance(payload.get("entries"), dict):
        return False
    payload["version"] = new_version
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return True


def _score_sort(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    decision = out["decision"] if "decision" in out else out.get("dynamic_decision", pd.Series("", index=out.index))
    out["_source_decision_priority"] = decision.astype(str).str.upper().map(SOURCE_DECISION_PRIORITY).fillna(9)
    out["_source_priority_score"] = pd.to_numeric(out.get("score"), errors="coerce")
    out["_selected_rank"] = pd.to_numeric(out["selected_rank"], errors="coerce") if "selected_rank" in out else pd.NA
    return out.sort_values(
        ["_source_decision_priority", "_selected_rank", "_source_priority_score"],
        ascending=[True, True, False],
        na_position="last",
        kind="stable",
    )


def select_preselected_rows(
    rows: pd.DataFrame,
    *,
    max_unique_instruments: int = 40,
    accepted_statuses: tuple[str, ...] = DEFAULT_PRESELECTED_STATUSES,
) -> pd.DataFrame:
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
    return ordered[ordered["isin"].astype(str).isin(unique_isins)].copy()


def attach_master_identity(selected: pd.DataFrame, actions: pd.DataFrame | None, etfs: pd.DataFrame | None) -> pd.DataFrame:
    if selected.empty:
        return selected
    frames = []
    identity_fields = ("isin", "name", "yahoo_ticker", "long_name_yf", "investing_url", "investing_technical_url", "boursorama_code")
    for master, asset in ((actions, "ACTION"), (etfs, "ETF")):
        if master is None or master.empty or "isin" not in master:
            continue
        keep = [c for c in identity_fields if c in master]
        part = master[keep].copy()
        part["asset_class"] = asset
        frames.append(part)
    if not frames:
        return selected
    identity = pd.concat(frames, ignore_index=True, sort=False).drop_duplicates(["isin", "asset_class"])
    result = selected.copy()
    if "asset_class" not in result:
        result["asset_class"] = "ACTION"
    result = result.merge(
        identity,
        on=["isin", "asset_class"],
        how="left",
        suffixes=("", "_master"),
        sort=False,
        validate="many_to_one",
    )
    for field in identity_fields[1:]:
        master_field = f"{field}_master"
        if master_field not in result:
            continue
        if field not in result:
            result[field] = result[master_field]
        else:
            missing = result[field].isna() | result[field].astype(str).str.strip().isin({"", "nan", "None"})
            result.loc[missing, field] = result.loc[missing, master_field]
        result = result.drop(columns=[master_field])
    return result


def _append_source_metadata(observations: list[dict]) -> list[dict]:
    if not observations:
        return observations
    frame = pd.DataFrame(observations)
    keys = [c for c in ("isin", "asset_class", "horizon") if c in frame]
    synthetic: list[dict] = []
    for key_values, group in frame.groupby(keys, dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        base = dict(zip(keys, key_values))
        factual = group[
            group.get("validation_status", pd.Series("", index=group.index)).astype(str).ne("SOURCE_FRESHNESS_METADATA")
        ]
        for provider, prefix in (("boursorama", "Boursorama"), ("investing", "Investing")):
            subset = factual[factual["source"].astype(str).str.startswith(prefix)]
            if subset.empty:
                continue
            urls = sorted({str(v) for v in subset.get("source_url", pd.Series(dtype=object)).dropna() if str(v).strip()})
            dates = sorted({str(v) for v in subset.get("collected_at", pd.Series(dtype=object)).dropna() if str(v).strip()})
            for field, value in (
                (f"{provider}_source_urls", " | ".join(urls)),
                (f"{provider}_latest_collected_at", dates[-1] if dates else ""),
            ):
                if value:
                    synthetic.append(
                        {
                            **base,
                            "field": field,
                            "value": value,
                            "source": f"{provider} provenance aggregate",
                            "source_url": urls[0] if urls else None,
                            "collected_at": dates[-1] if dates else None,
                            "validation_status": "SOURCE_PROVENANCE_AGGREGATE",
                        }
                    )
    return observations + synthetic


def _pivot(observations: list[dict]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame()
    frame = pd.DataFrame(observations)
    if not {"isin", "horizon", "field", "value"}.issubset(frame.columns):
        return pd.DataFrame()
    index = [c for c in ("isin", "asset_class", "horizon") if c in frame]
    result = frame.pivot_table(index=index, columns="field", values="value", aggfunc="last").reset_index()
    if result.duplicated(index).any():
        raise RuntimeError("SOURCE_CONTEXT_PIVOT_DUPLICATE_KEYS")
    return result


def _age_hours(value: object, now: datetime) -> float:
    text = str(value or "").strip()
    if not text:
        return math.inf
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return math.inf
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _mapping_is_resolved(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    base = str(entry.get("base_url") or "").strip()
    return bool(
        str(entry.get("status") or "RESOLVED").upper() != "UNRESOLVED"
        and _safe_investing_url(base, allow_technical=False)
    )


def _mapping_in_cooldown(entry: object, now: datetime, retry_ttl_hours: float) -> bool:
    if not isinstance(entry, dict) or str(entry.get("status") or "").upper() != "UNRESOLVED":
        return False
    return _age_hours(entry.get("last_failed_at_utc"), now) < max(0.0, float(retry_ttl_hours))


def _investing_budgeted_rows(
    selected: pd.DataFrame,
    root: Path,
    max_unmapped: int,
    *,
    unmapped_retry_ttl_hours: float = 24.0,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, int, int]:
    """Keep resolved/cache ISINs, skip cooled failures, then allocate discovery slots by decision priority."""
    if selected.empty:
        return selected, 0, 0
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mapping = _json_cache(root / "state" / "provenance" / "source_cache" / "INVESTING_URL_MAP_V1.json").get("entries", {})
    technical = _json_cache(root / "state" / "provenance" / "source_cache" / "INVESTING_TECHNICAL_V1.json").get("entries", {})
    mapping = mapping if isinstance(mapping, dict) else {}
    technical = technical if isinstance(technical, dict) else {}
    known_isins = {str(isin) for isin in technical} | {str(isin) for isin, entry in mapping.items() if _mapping_is_resolved(entry)}
    cooldown_isins = {
        str(isin)
        for isin, entry in mapping.items()
        if _mapping_in_cooldown(entry, current, unmapped_retry_ttl_hours) and str(isin) not in known_isins
    }
    ordered = _score_sort(selected)
    unknown_order: list[str] = []
    seen: set[str] = set()
    selected_isins = set(ordered["isin"].astype(str))
    for isin in ordered["isin"].astype(str):
        if not isin or isin in known_isins or isin in cooldown_isins or isin in seen:
            continue
        seen.add(isin)
        unknown_order.append(isin)
    allowance = max(0, int(max_unmapped))
    allowed_new = set(unknown_order[:allowance])
    keep_isins = known_isins | allowed_new
    filtered = ordered[ordered["isin"].astype(str).isin(keep_isins)].copy()
    deferred = max(0, len(unknown_order) - len(allowed_new))
    cooldown_skipped = len(cooldown_isins & selected_isins)
    return filtered, deferred, cooldown_skipped


def enrich_selected_rows(
    rows: pd.DataFrame,
    root: Path = ROOT,
    *,
    profile: str = "SELECTED",
    network_policy: str = "LIVE_IF_DUE",
    persist_outputs: bool = True,
) -> tuple[pd.DataFrame, dict]:
    policy = str(network_policy).upper()
    if policy not in NETWORK_POLICIES:
        raise ValueError(f"UNSUPPORTED_SOURCE_NETWORK_POLICY:{network_policy}")
    allow_network = policy == "LIVE_IF_DUE"
    contract = _read_contract(root)
    scope = contract["scope"]
    selected = select_preselected_rows(
        rows,
        max_unique_instruments=int(scope["selected_only_max_unique_instruments"]),
        accepted_statuses=tuple(scope["preselection_statuses"]),
    )
    if selected.empty:
        return rows.copy(), {
            "status": "NO_PRESELECTED_ROWS",
            "profile": profile,
            "selected_rows": 0,
            "network_policy": policy,
            "decision_influence": False,
            "score_influence": 0.0,
        }

    bcfg = contract["boursorama"]
    icfg = contract["investing"]
    retry_ttl = float(icfg.get("unmapped_retry_ttl_hours", 24.0))
    technical_retry_ttl = float(icfg.get("technical_failure_retry_ttl_hours", 2.0))
    action_cache = root / "state" / "provenance" / "source_cache" / "BOURSORAMA_SELECTED_V1.json"
    action_cache_migrated = _migrate_cache_version(
        action_cache,
        old_version="BOURSORAMA_SELECTED_V1",
        new_version="BOURSORAMA_SELECTED_V2",
    )
    asset_upper = selected["asset_class"].astype(str).str.upper()
    action_selected = selected[asset_upper.eq("ACTION")].copy()
    etf_selected = selected[asset_upper.eq("ETF")].copy()
    investing_input, deferred_unmapped, cooldown_skipped = (
        _investing_budgeted_rows(
            selected,
            root,
            int(icfg.get("max_unmapped_resolution_per_run", 8)),
            unmapped_retry_ttl_hours=retry_ttl,
        )
        if allow_network
        else (selected, 0, 0)
    )

    def run_boursorama():
        shared_limiter = StartRateLimiter(float(bcfg["request_start_interval_seconds"]))
        total_inflight = max(1, int(bcfg.get("max_provider_inflight", 4)))
        per_branch_workers = max(1, total_inflight // 2)

        def actions_branch():
            if action_selected.empty or not bool(bcfg.get("priority_for_selected_actions", True)):
                return None
            return collect_selected_action_context_cached(
                action_selected,
                action_cache,
                dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]),
                performance_ttl_hours=float(bcfg.get("performance_ttl_hours", 72)),
                deep_ttl_hours=float(bcfg["deep_ttl_hours"]),
                refresh_budget=int(bcfg["refresh_budget"]),
                request_start_interval_seconds=float(bcfg["request_start_interval_seconds"]),
                max_workers=per_branch_workers,
                limiter=shared_limiter,
                allow_network=allow_network,
            )

        def etfs_branch():
            if etf_selected.empty or not bool(bcfg.get("priority_for_selected_etfs", True)):
                return None
            return collect_selected_etf_context_cached(
                etf_selected,
                root / "state" / "provenance" / "source_cache" / "BOURSORAMA_SELECTED_ETF_V1.json",
                dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]),
                deep_ttl_hours=float(bcfg["deep_ttl_hours"]),
                refresh_budget=int(bcfg["refresh_budget"]),
                request_start_interval_seconds=float(bcfg["request_start_interval_seconds"]),
                max_workers=per_branch_workers,
                limiter=shared_limiter,
                allow_network=allow_network,
            )

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="boursorama-asset") as pool:
            fa = pool.submit(actions_branch)
            fe = pool.submit(etfs_branch)
            return fa.result(), fe.result()

    def run_investing():
        return collect_technical_context_cached(
            investing_input,
            root / "state" / "provenance" / "source_cache" / "INVESTING_TECHNICAL_V1.json",
            root / "state" / "provenance" / "source_cache" / "INVESTING_URL_MAP_V1.json",
            refresh_budget=int(icfg["refresh_budget"]),
            ttl_hours=float(icfg["ttl_hours"]),
            unmapped_retry_ttl_hours=retry_ttl,
            technical_failure_retry_ttl_hours=technical_retry_ttl,
            request_start_interval_seconds=float(icfg["request_start_interval_seconds"]),
            max_workers=int(icfg["max_workers"]),
            allow_network=allow_network,
        )

    b_action = b_etf = investing = None
    branch_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="selected-source-provider") as pool:
        futures = {
            "boursorama": pool.submit(run_boursorama),
            "investing": pool.submit(run_investing),
        }
        for name, future in futures.items():
            try:
                result = future.result()
                if name == "boursorama":
                    b_action, b_etf = result
                else:
                    investing = result
            except Exception as exc:
                branch_errors.append({"source": name, "reason": type(exc).__name__, "detail": str(exc)[:240]})

    observations: list[dict] = []
    failures: list[dict] = list(branch_errors)
    for result in (b_action, b_etf, investing):
        if result is not None:
            observations.extend(result.observations)
            failures.extend(result.failures)
    if deferred_unmapped:
        failures.append(
            {
                "source": "Investing.com",
                "reason": "UNMAPPED_RESOLUTION_BUDGET_DEFERRED",
                "count": int(deferred_unmapped),
            }
        )
    if cooldown_skipped:
        failures.append(
            {
                "source": "Investing.com",
                "reason": "UNRESOLVED_COOLDOWN_SKIPPED_NO_NETWORK",
                "count": int(cooldown_skipped),
            }
        )
    observations = _append_source_metadata(observations)
    context = _pivot(observations)
    enriched = rows.copy()
    if not context.empty:
        keys = [c for c in ("isin", "asset_class", "horizon") if c in enriched and c in context]
        before_count = len(enriched)
        enriched = enriched.merge(context, on=keys, how="left", sort=False, validate="many_to_one")
        if len(enriched) != before_count:
            raise RuntimeError("SOURCE_CONTEXT_JOIN_ROW_COUNT_MUTATION")

    safe_profile = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in profile.upper())
    if persist_outputs:
        outdir = root / "outputs" / "source_context"
        auditdir = root / "outputs" / "audit"
        outdir.mkdir(parents=True, exist_ok=True)
        auditdir.mkdir(parents=True, exist_ok=True)
        selected.to_csv(
            outdir / f"{safe_profile}_PRESELECTED_INPUT.csv",
            sep=";",
            index=False,
            encoding="utf-8-sig",
        )
        pd.DataFrame(observations).to_csv(
            outdir / f"{safe_profile}_SOURCE_OBSERVATIONS.csv",
            sep=";",
            index=False,
            encoding="utf-8-sig",
        )
        pd.DataFrame(failures).to_csv(
            outdir / f"{safe_profile}_SOURCE_FAILURES.csv",
            sep=";",
            index=False,
            encoding="utf-8-sig",
        )

    payload = {
        "status": "SUCCESS_WITH_CONTEXT" if observations else "SUCCESS_NO_SOURCE_DATA",
        "version": contract["version"],
        "profile": profile,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_policy": policy,
        "network_allowed": allow_network,
        "selected_rows": int(len(selected)),
        "selected_unique_isins": int(selected["isin"].nunique()),
        "source_priority_order": list(SOURCE_DECISION_PRIORITY),
        "boursorama_action_cache_migrated_v1_to_v2": action_cache_migrated,
        "investing_unmapped_resolution_deferred": int(deferred_unmapped),
        "investing_unresolved_cooldown_skipped": int(cooldown_skipped),
        "investing_unmapped_retry_ttl_hours": retry_ttl,
        "investing_technical_failure_retry_ttl_hours": technical_retry_ttl,
        "boursorama_actions": b_action.metrics if b_action is not None else {"status": "NO_ACTION_SELECTED_OR_BRANCH_FAILED"},
        "boursorama_etfs": b_etf.metrics if b_etf is not None else {"status": "NO_ETF_SELECTED_OR_BRANCH_FAILED"},
        "investing": investing.metrics if investing is not None else {"status": "BRANCH_FAILED"},
        "failures": int(len(failures)),
        "weights_unchanged": True,
        "thresholds_unchanged": True,
        "decision_influence": False,
        "score_influence": 0.0,
        "can_create_buy": False,
        "functional_contract": str(CONTRACT_PATH),
        "persisted_context_outputs": bool(persist_outputs),
    }
    if persist_outputs:
        (root / "outputs" / "audit" / f"{safe_profile}_SELECTED_SOURCE_CONTEXT.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return enriched, payload
