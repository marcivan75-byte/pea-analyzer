from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting import selected_source_enrichment as legacy
from v182.sources.tradingview_technical import collect_technical_context_cached


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = Path("config/WEEKLY_V4_SOURCE_CONTRACT.json")
TRADINGVIEW_CACHE = Path("state/provenance/source_cache/TRADINGVIEW_TECHNICAL_V2.json")


def _safe_profile(profile: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in profile.upper())


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _canonicalize_identity_aliases(rows: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Promote only versioned, ISIN-bound ticker aliases to the canonical field."""
    prepared = rows.copy()
    if "yahoo_ticker" not in prepared:
        prepared["yahoo_ticker"] = pd.NA
    missing = prepared["yahoo_ticker"].isna() | prepared["yahoo_ticker"].astype(str).str.strip().isin({"", "nan", "None"})
    hydrated = 0
    for alias in ("yahoo_ticker_v22_2", "yahoo_ticker_v22_1", "ticker_yahoo"):
        if alias not in prepared:
            continue
        candidate = prepared[alias].astype("string").str.strip()
        usable = missing & candidate.notna() & candidate.ne("")
        prepared.loc[usable, "yahoo_ticker"] = candidate.loc[usable]
        hydrated += int(usable.sum())
        missing = prepared["yahoo_ticker"].isna() | prepared["yahoo_ticker"].astype(str).str.strip().isin({"", "nan", "None"})
    return prepared, hydrated


def enrich_selected_rows_v4(
    rows: pd.DataFrame,
    root: Path = ROOT,
    *,
    profile: str = "WEEKLY_V4",
) -> tuple[pd.DataFrame, dict]:
    """Collect Boursorama and TradingView only for the bounded upstream pool.

    The legacy Investing branch is explicitly disabled. TradingView observations
    are admitted only after the collector proves the exact exchange-qualified
    symbol and a complete, fresh 1D/1W/1M summary.
    """
    contract = json.loads((root / CONTRACT).read_text(encoding="utf-8"))
    scope = contract["scope"]
    prepared, aliases_hydrated = _canonicalize_identity_aliases(rows)
    selected = legacy.select_preselected_rows(
        prepared,
        max_unique_instruments=int(scope["maximum_unique_instruments"]),
    )
    enriched, boursorama_payload = legacy.enrich_selected_rows(
        prepared,
        root=root,
        profile=profile,
        investing_enabled=False,
    )
    if selected.empty:
        payload = dict(boursorama_payload)
        payload.update(
            {
                "version": contract["version"],
                "investing": {"status": "DISABLED_FOR_V4"},
                "tradingview": {"status": "NO_PRESELECTED_ROWS"},
                "source_contract": CONTRACT.as_posix(),
                "identity_aliases_hydrated": aliases_hydrated,
            }
        )
        return enriched, payload

    tv_cfg = contract["tradingview"]
    tv_result = collect_technical_context_cached(
        selected,
        root / TRADINGVIEW_CACHE,
        refresh_budget=int(tv_cfg["refresh_budget"]),
        ttl_hours=float(tv_cfg["ttl_hours"]),
        request_start_interval_seconds=float(tv_cfg["request_start_interval_seconds"]),
        max_workers=int(tv_cfg["provider_max_inflight"]),
    )
    tv_context = legacy._pivot(tv_result.observations)
    if not tv_context.empty:
        keys = [column for column in ("isin", "asset_class", "horizon") if column in enriched and column in tv_context]
        enriched = enriched.merge(tv_context, on=keys, how="left")

    safe = _safe_profile(profile)
    outdir = root / "outputs/source_context"
    auditdir = root / "outputs/audit"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    legacy_observations = _read_csv(outdir / f"{safe}_SOURCE_OBSERVATIONS.csv")
    legacy_failures = _read_csv(outdir / f"{safe}_SOURCE_FAILURES.csv")
    combined_observations = pd.concat(
        [legacy_observations, pd.DataFrame(tv_result.observations)],
        ignore_index=True,
        sort=False,
    )
    combined_failures = pd.concat(
        [legacy_failures, pd.DataFrame(tv_result.failures)],
        ignore_index=True,
        sort=False,
    )
    combined_observations.to_csv(
        outdir / f"{safe}_V4_SOURCE_OBSERVATIONS.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    combined_failures.to_csv(
        outdir / f"{safe}_V4_SOURCE_FAILURES.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )

    payload = {
        "status": "SUCCESS_WITH_CONTEXT" if not combined_observations.empty else "SUCCESS_NO_SOURCE_DATA",
        "version": contract["version"],
        "profile": profile,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_rows": int(len(selected)),
        "selected_unique_isins": int(selected["isin"].nunique()),
        "identity_aliases_hydrated": aliases_hydrated,
        "boursorama": boursorama_payload,
        "tradingview": {
            "status": "SUCCESS_WITH_CONTEXT" if tv_result.observations else "SUCCESS_NO_SOURCE_DATA",
            "metrics": tv_result.metrics,
            "failure_count": int(len(tv_result.failures)),
        },
        "investing": {"status": "DISABLED_FOR_V4"},
        "source_contract": CONTRACT.as_posix(),
        "source_can_create_candidate": False,
        "reference_score_influence": 0.0,
        "missing_is_negative_signal": False,
        "raw_html_persisted": False,
        "observations": int(len(combined_observations)),
        "failures": int(len(combined_failures)),
        "outputs": {
            "observations": f"outputs/source_context/{safe}_V4_SOURCE_OBSERVATIONS.csv",
            "failures": f"outputs/source_context/{safe}_V4_SOURCE_FAILURES.csv",
        },
    }
    (auditdir / f"{safe}_V4_SELECTED_SOURCE_CONTEXT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return enriched, payload
