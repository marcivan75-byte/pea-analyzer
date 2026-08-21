from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo
import argparse
import json

import pandas as pd

from v182.decision.action_mt_decision_v1 import ActionCandidate, MarketRegime, select_action_mt_candidates
from v182.features.action_mt_v1 import ENGINE_VERSION, compute_action_mt_snapshot
from v182.sources.action_mt_cache_v1 import ActionMTHistoryCache, write_cache_manifest


CONTEXT_FIELDS = (
    "quality_score", "morningstar_action_score", "profitability_score", "roe_score",
    "balance_sheet_score", "financial_strength_score", "earnings_growth_score", "eps_growth_score",
    "revenue_growth_score", "free_cash_flow_growth_score", "valuation_discount_score", "valuation_score",
    "analyst_revisions_score", "consensus_score_100_v21", "target_upside_growth_score",
    "sector_rotation_score", "sector_macro_score", "macro_evidence_sufficient", "theme_risk_adjusted_score",
    "market_regime_score", "days_to_earnings",
)


def config_fingerprint(cfg: dict) -> str:
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def completed_bars_only(frame: pd.DataFrame, now: datetime, cfg: dict) -> pd.DataFrame:
    policy = cfg["data_policy"]
    local = now.astimezone(ZoneInfo(policy["local_close_timezone"]))
    current_day = pd.Timestamp(local.date())
    if local.hour < int(policy["local_close_hour"]):
        return frame[pd.to_datetime(frame.index).normalize() < current_day]
    return frame[pd.to_datetime(frame.index).normalize() <= current_day]


def snapshot_fingerprint(row: dict) -> str:
    fields = ("version_engine", "snapshot_date", "isin", "reference_close", "score", "score_coverage", "decision", "warnings")
    canonical = "|".join("" if row.get(field) is None else str(row.get(field)) for field in fields)
    return sha256(canonical.encode("utf-8")).hexdigest()


def append_pit_idempotent(ledger: Path, rows: pd.DataFrame) -> int:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(ledger) if ledger.exists() else pd.DataFrame()
    combined = pd.concat([existing, rows], ignore_index=True)
    before = len(combined)
    if "snapshot_fingerprint" in combined:
        combined = combined.drop_duplicates("snapshot_fingerprint", keep="first")
    combined.to_csv(ledger, index=False)
    return before - len(combined)


def _market_regime(rows: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> MarketRegime:
    valid = rows[rows["status"] == "SUCCESS_SHADOW"] if not rows.empty else rows
    breadth = float((pd.to_numeric(valid.get("reference_close"), errors="coerce") > pd.to_numeric(valid.get("sma200"), errors="coerce")).mean()) if not valid.empty else 0.0
    median1 = float(pd.to_numeric(valid.get("return_1m_pct"), errors="coerce").median()) if "return_1m_pct" in valid else 0.0
    median6 = float(pd.to_numeric(valid.get("return_6m_pct"), errors="coerce").median()) if not valid.empty else 0.0
    closes = [frame["close"].rename(isin) for isin, frame in histories.items() if "close" in frame and len(frame) >= 200]
    if closes:
        panel = pd.concat(closes, axis=1).sort_index()
        proxy = (1.0 + panel.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)).cumprod()
        market_above = bool(len(proxy) >= 200 and proxy.iloc[-1] > proxy.tail(200).mean())
    else:
        market_above = False
    return MarketRegime(breadth, median1, median6, market_above)


def run(master: pd.DataFrame, cfg: dict, cache: ActionMTHistoryCache, output_dir: Path, now: datetime | None = None) -> dict:
    started = perf_counter()
    now = now or datetime.now(timezone.utc)
    if "isin" not in master:
        raise ValueError("master must contain isin")
    eligible = master.copy()
    if "asset_class" in eligible:
        eligible = eligible[eligible["asset_class"].astype(str).str.upper().eq("ACTION")]
    if "data_status" in eligible:
        eligible = eligible[~eligible["data_status"].astype(str).str.upper().isin({"BLOCK_DATA", "QUARANTINE"})]

    histories: dict[str, pd.DataFrame] = {}
    cache_meta: dict[str, dict] = {}
    for isin in eligible["isin"].astype(str):
        frame, metadata = cache.load(isin, as_of=pd.Timestamp(now))
        cache_meta[isin] = metadata
        if not frame.empty:
            histories[isin] = completed_bars_only(frame, now, cfg)

    def compute(item: tuple[int, pd.Series]) -> dict:
        _, row = item
        isin = str(row["isin"])
        context = {field: row.get(field) for field in CONTEXT_FIELDS if field in row.index}
        snap = compute_action_mt_snapshot(histories.get(isin, pd.DataFrame()), cfg, context)
        snap.update({"isin": isin, "sector": str(row.get("sector") or "UNCLASSIFIED"), "cache_status": cache_meta[isin]["status"]})
        snap["snapshot_date"] = max(histories[isin].index).date().isoformat() if isin in histories and not histories[isin].empty else None
        return snap

    worker_count = max(1, min(int(cfg["runtime"]["maximum_workers"]), len(eligible) or 1))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        snapshots = list(pool.map(compute, eligible.iterrows()))
    frame = pd.DataFrame(snapshots)
    regime = _market_regime(frame, histories)

    successful = frame[frame["status"] == "SUCCESS_SHADOW"].copy() if not frame.empty else frame
    if not successful.empty:
        successful["score_rank_pct"] = pd.to_numeric(successful["score"], errors="coerce").rank(method="average", pct=True) * 100.0
    candidates = [
        ActionCandidate(str(row.isin), float(row.score), float(row.score_rank_pct), str(row.sector), str(row.decision), float(row.score_coverage), str(row.warnings))
        for row in successful.itertuples()
        if pd.notna(row.score) and pd.notna(row.score_rank_pct)
    ]
    committee = select_action_mt_candidates(candidates, regime, cfg)
    selected = {candidate.isin: rank for rank, candidate in enumerate(committee.selected, start=1)}
    frame["selected_rank"] = frame["isin"].map(selected)
    frame["version_engine"] = frame.get("version_engine", ENGINE_VERSION)
    frame["config_sha256"] = config_fingerprint(cfg)
    frame["snapshot_fingerprint"] = frame.apply(lambda row: snapshot_fingerprint(row.to_dict()), axis=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "ACTION_MT_LATEST.csv", index=False)
    duplicates = append_pit_idempotent(output_dir / "ACTION_MT_PIT_LEDGER.csv", frame)
    exclusions = frame[frame["selected_rank"].isna()][[column for column in ("isin", "decision", "warnings", "score", "score_coverage") if column in frame]]
    exclusions.to_csv(output_dir / "ACTION_MT_EXCLUSIONS.csv", index=False)
    write_cache_manifest(output_dir / "ACTION_MT_CACHE_MANIFEST.json", cache.manifest())
    report = {
        "version": ENGINE_VERSION,
        "generated_at": now.isoformat(),
        "config_sha256": config_fingerprint(cfg),
        "status": "SUCCESS_SHADOW",
        "universe_rows": int(len(eligible)),
        "successful_rows": int((frame["status"] == "SUCCESS_SHADOW").sum()),
        "selected_isins": [candidate.isin for candidate in committee.selected],
        "abstention_reason": committee.abstention_reason,
        "rejected_counts": committee.rejected_counts,
        "regime": regime.__dict__,
        "cache": cache.manifest(),
        "pit_duplicates_ignored": duplicates,
        "duration_seconds": perf_counter() - started,
    }
    (output_dir / "ACTION_MT_RUN_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "ACTION_MT_COMMITTEE.txt").write_text(
        f"ACTION MT {report['generated_at']}\nREGIME={committee.abstention_reason or 'ALLOWED'}\nSELECTED={','.join(report['selected_isins']) or 'NONE'}\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ACTION MT V1 shadow from governed local caches")
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/ACTION_MT_V1_0_0_SHADOW.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/actions"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/action_mt_v1"))
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    master = pd.read_csv(args.master)
    cache = ActionMTHistoryCache(args.cache_dir, cfg["data_policy"]["maximum_cache_staleness_days"])
    report = run(master, cfg, cache, args.output_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

