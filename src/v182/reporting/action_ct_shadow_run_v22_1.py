from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo
import json
import math
import traceback

import pandas as pd

from v182.features.action_ct_context_v22_1 import build_action_ct_context_overlay, merge_action_ct_context
from v182.features.action_ct_v22_1 import compute_action_ct_snapshot_v22_1
from v182.reporting import action_ct_shadow_run_v22_0 as legacy


ROOT = Path(__file__).resolve().parents[3]
VERSION = "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW"
CONFIG = "ACTION_CT_V22_1_0_SHADOW.json"
STATE_DIR = Path("state/action_ct_v22_1")
LATEST = STATE_DIR / "ACTION_CT_V22_1_0_LATEST.csv"
PIT_LEDGER = STATE_DIR / "ACTION_CT_V22_1_0_PIT_LEDGER.csv"
EXIT_STATE = STATE_DIR / "ACTION_CT_V22_1_0_EXIT_STATE.csv"
FINGERPRINT_FIELDS = (
    "version", "snapshot_date", "isin", "reference_close",
    "baseline_ct_score", "baseline_ct_decision",
    "entry_score", "entry_state", "entry_confirmation_count", "entry_confirmations",
    "exit_risk_score", "exit_state",
    "trend_score", "momentum_score", "weekly_score", "sector_context_score",
    "volume_score", "catalyst_score", "quality_target_score", "theme_macro_score",
    "valuation_event_risk_score", "warnings",
)


def _canonical(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        return str(value).strip()
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric) and math.isfinite(float(numeric)):
        return format(float(numeric), ".12g")
    return str(value).strip()


def _fingerprint(row: pd.Series | dict) -> str:
    text = "|".join(f"{field}={_canonical(row.get(field))}" for field in FINGERPRINT_FIELDS)
    return sha256(text.encode("utf-8")).hexdigest()


def _append_first_snapshots(ledger: pd.DataFrame, snapshots: pd.DataFrame) -> tuple[pd.DataFrame, int, list[str]]:
    existing = ledger.copy() if ledger is not None else pd.DataFrame()
    incoming = snapshots.copy() if snapshots is not None else pd.DataFrame()
    if incoming.empty:
        return existing, 0, []
    keys = ["snapshot_date", "isin"]
    incoming["isin"] = incoming["isin"].astype(str).str.upper()
    incoming["snapshot_fingerprint"] = incoming.apply(_fingerprint, axis=1)
    incoming = incoming.drop_duplicates(keys, keep="first")
    if existing.empty:
        return incoming.reset_index(drop=True), int(len(incoming)), []
    existing["isin"] = existing["isin"].astype(str).str.upper()
    if "snapshot_fingerprint" not in existing.columns:
        existing["snapshot_fingerprint"] = existing.apply(_fingerprint, axis=1)
    index = {
        (str(row["snapshot_date"]), str(row["isin"]).upper()): str(row.get("snapshot_fingerprint") or "")
        for _, row in existing.iterrows()
    }
    additions: list[pd.Series] = []
    mismatches: list[str] = []
    for _, row in incoming.iterrows():
        key = (str(row["snapshot_date"]), str(row["isin"]).upper())
        fp = str(row["snapshot_fingerprint"])
        if key in index:
            if index[key] != fp:
                mismatches.append(f"{key[0]}:{key[1]}")
            continue
        additions.append(row)
        index[key] = fp
    if additions:
        existing = pd.concat([existing, pd.DataFrame(additions)], ignore_index=True, sort=False)
    return existing, len(additions), mismatches


def _pit_snapshot_eligible(row: pd.Series, cfg: dict, now: datetime) -> bool:
    if str(row.get("status")) != "SUCCESS_SHADOW":
        return False
    tz = ZoneInfo(str(cfg["data_policy"].get("local_close_guard_timezone", "Europe/Paris")))
    local = now.astimezone(tz)
    if local.hour < int(cfg["data_policy"].get("local_close_guard_hour", 18)):
        return False
    try:
        return pd.Timestamp(str(row.get("snapshot_date"))).date() == local.date()
    except (TypeError, ValueError):
        return False


def _validate_master_schema(actions: pd.DataFrame) -> dict:
    missing_required = sorted({"isin"} - set(actions.columns))
    ticker_columns = [field for field in ("yahoo_ticker", "ticker") if field in actions.columns]
    return {
        "valid": not missing_required,
        "missing_required_columns": missing_required,
        "ticker_columns_present": ticker_columns,
        "rows": int(len(actions)),
    }


def _state_metrics(output: pd.DataFrame) -> dict:
    if output.empty:
        return {
            "entry_state_counts": {},
            "exit_state_counts": {},
            "median_entry_coverage": None,
            "median_exit_coverage": None,
            "median_context_richness": None,
        }
    entry_coverage = pd.to_numeric(output.get("entry_coverage"), errors="coerce")
    exit_coverage = pd.to_numeric(output.get("exit_coverage"), errors="coerce")
    richness = pd.to_numeric(output.get("context_richness_score"), errors="coerce")
    return {
        "entry_state_counts": output.get("entry_state", pd.Series(dtype=object)).astype(str).value_counts().to_dict(),
        "exit_state_counts": output.get("exit_state", pd.Series(dtype=object)).astype(str).value_counts().to_dict(),
        "median_entry_coverage": None if entry_coverage.dropna().empty else round(float(entry_coverage.median()), 6),
        "median_exit_coverage": None if exit_coverage.dropna().empty else round(float(exit_coverage.median()), 6),
        "median_context_richness": None if richness.dropna().empty else round(float(richness.median()), 6),
    }


def _divergence_summary(output: pd.DataFrame, parent: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    if output.empty:
        return {"status": "NO_OUTPUT"}, pd.DataFrame()
    work = output.copy()
    challenger_entry = work["entry_state"].astype(str).isin({"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"})
    baseline_buy = work.get("baseline_ct_decision", pd.Series("", index=work.index)).astype(str).str.contains("BUY", case=False, na=False)
    agreement = challenger_entry.eq(baseline_buy)
    divergence = work.loc[~agreement, [column for column in (
        "isin", "name", "baseline_ct_decision", "entry_state", "entry_score", "entry_coverage", "warnings"
    ) if column in work.columns]].copy()
    summary = {
        "status": "OK",
        "baseline_buy_count": int(baseline_buy.sum()),
        "challenger_ready_count": int(challenger_entry.sum()),
        "agreement_v21_vs_v22_1_pct": round(float(agreement.mean() * 100.0), 4),
        "divergence_count": int((~agreement).sum()),
    }
    if not parent.empty and {"isin", "entry_state"}.issubset(parent.columns):
        left = work[["isin", "entry_state"]].copy()
        right = parent[["isin", "entry_state"]].drop_duplicates("isin", keep="first").copy()
        right = right.rename(columns={"entry_state": "parent_entry_state"})
        merged = left.merge(right, on="isin", how="inner")
        if not merged.empty:
            summary["agreement_v22_0_vs_v22_1_pct"] = round(
                float(merged["entry_state"].astype(str).eq(merged["parent_entry_state"].astype(str)).mean() * 100.0), 4
            )
            summary["parent_comparison_rows"] = int(len(merged))
    return summary, divergence


def _compute_snapshot_safe(completed: pd.DataFrame, cfg: dict, context: dict, ticker: str) -> tuple[dict, dict | None]:
    try:
        return compute_action_ct_snapshot_v22_1(completed, cfg, context), None
    except Exception as exc:  # fail closed; full details are persisted in the audit error file
        frames = int(cfg.get("runtime_observability", {}).get("error_traceback_frames", 3))
        error = {
            "ticker": ticker,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=max(frames, 1)),
        }
        return {
            "status": "ERROR_SHADOW",
            "bars": int(len(completed)),
            "t1_t2_used": False,
            "intraday_data_used": False,
        }, error


def _android_summary(
    frame: pd.DataFrame,
    validation: dict,
    diagnostics: dict,
    generated_at: str,
    runtime: dict | None = None,
    divergence: dict | None = None,
) -> str:
    runtime = runtime or {}
    divergence = divergence or {}
    lines = [
        "# Actions CT V22.1 — Context Enriched SHADOW", "",
        f"Généré UTC : {generated_at}",
        "Horizon : 2 à 12 semaines. Baseline CT V21.0 inchangée.",
        "Daily/weekly + rotation sectorielle + force relative + qualité/target + thème/macro si observés.",
        "T1/T2 interdits. Intraday interdit. Aucun stop/TP fixe. Aucun ordre réel.",
        f"Contexte dérivé : {diagnostics.get('mapped_actions', 0)} actions.",
        f"Validation PIT : {validation.get('status', 'IMMATURE_SHADOW')}",
    ]
    total_seconds = runtime.get("total_seconds")
    if total_seconds is not None:
        lines.append(f"Durée moteur : {float(total_seconds):.2f} s.")
    agreement = divergence.get("agreement_v21_vs_v22_1_pct")
    if agreement is not None:
        lines.append(f"Accord V21/V22.1 : {float(agreement):.1f} %.")
    lines.append("")
    if frame.empty:
        return "\n".join(lines + ["Aucun diagnostic CT exploitable."]) + "\n"
    work = frame.copy()
    work["_entry"] = pd.to_numeric(work.get("entry_score"), errors="coerce")
    priority = work[work["entry_state"].astype(str).isin([
        "ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW", "WAIT_RISK_SHADOW",
        "WAIT_CONTEXT_RISK_SHADOW", "ENTRY_CONFLICT_SHADOW",
    ])].sort_values("_entry", ascending=False).head(20)
    if priority.empty:
        return "\n".join(lines + ["Aucun signal ou warning prioritaire sur ce run."]) + "\n"
    for _, row in priority.iterrows():
        name = str(row.get("name") or row.get("isin") or "N/A")
        score = row.get("_entry")
        score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
        lines.append(
            f"- **{name}** — {row.get('entry_state')} ({score_txt}) — "
            f"baseline {row.get('baseline_ct_decision')} — sortie {row.get('exit_state')}"
        )
        confirmations = str(row.get("entry_confirmations") or "").strip()
        if confirmations and confirmations.lower() != "nan":
            lines.append(f"  - Confirmations : {confirmations}")
        warnings = str(row.get("warnings") or "").strip()
        if warnings and warnings.lower() != "nan":
            lines.append(f"  - Warnings : {warnings}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT, now: datetime | None = None) -> dict:
    started = perf_counter()
    timings: dict[str, float] = {}
    now = now or datetime.now(timezone.utc)
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    actions = legacy._read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    for directory in (outdir, auditdir, mobile, root / STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    generated_at = now.isoformat()
    errors: list[str] = []
    error_records: list[dict] = []
    deferred = 0
    histories: dict[str, pd.DataFrame] = {}
    context_overlay: dict[str, dict] = {}
    context_diagnostics: dict = {"status": "NO_ACTION_MASTER", "mapped_actions": 0}
    schema = _validate_master_schema(actions)

    if actions.empty or not schema["valid"]:
        output = pd.DataFrame()
        errors.append("ACTION_MASTER_MISSING_OR_INVALID")
    else:
        work = actions.drop_duplicates("isin", keep="first").copy()
        work["isin"] = work["isin"].astype(str).str.upper()

        stage = perf_counter()
        context_overlay, context_diagnostics = build_action_ct_context_overlay(work, cfg)
        timings["context_overlay_seconds"] = perf_counter() - stage

        ticker_col = "yahoo_ticker" if "yahoo_ticker" in work.columns else "ticker" if "ticker" in work.columns else None
        mapping: dict[str, str] = {}
        if ticker_col:
            tickers = work[ticker_col].astype(str).str.strip()
            valid_ticker = ~tickers.str.lower().isin({"", "nan", "none"})
            mapping = dict(zip(work.loc[valid_ticker, "isin"].astype(str), tickers.loc[valid_ticker], strict=False))

        stage = perf_counter()
        histories_by_ticker = legacy._extract_histories(root / cfg["data_policy"]["source_cache"], set(mapping.values()))
        timings["history_batch_load_seconds"] = perf_counter() - stage

        stage = perf_counter()
        baseline = legacy._baseline_ct(actions, root)
        if "isin" not in baseline.columns:
            baseline = pd.DataFrame(columns=["isin"])
        baseline["isin"] = baseline["isin"].astype(str).str.upper()
        baseline = baseline.drop_duplicates("isin", keep="first").set_index("isin", drop=False)
        timings["baseline_seconds"] = perf_counter() - stage

        prepared: list[tuple[dict, str, str, pd.DataFrame | None, dict | None]] = []
        for base in work.to_dict("records"):
            isin = str(base.get("isin") or "").upper()
            ticker = mapping.get(isin, "")
            history = histories_by_ticker.get(ticker) if ticker else None
            if history is None or history.empty:
                prepared.append((base, isin, ticker, None, None))
                continue
            completed, was_deferred = legacy._completed_daily_history(history, cfg, now)
            deferred += int(was_deferred)
            histories[isin] = completed
            context = merge_action_ct_context(base, context_overlay.get(isin, {}), cfg)
            prepared.append((base, isin, ticker, completed, context))

        compute_inputs = [(completed, cfg, context, ticker) for _, _, ticker, completed, context in prepared if completed is not None and context is not None]
        runtime_cfg = cfg.get("runtime_observability", {})
        workers = int(runtime_cfg.get("parallel_compute_workers", 4))
        parallel_min = int(runtime_cfg.get("parallel_min_actions", 50))
        stage = perf_counter()
        if workers > 1 and len(compute_inputs) >= parallel_min:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="action-ct") as executor:
                computed = list(executor.map(lambda args: _compute_snapshot_safe(*args), compute_inputs))
        else:
            computed = [_compute_snapshot_safe(*args) for args in compute_inputs]
        timings["snapshot_compute_seconds"] = perf_counter() - stage
        computed_iter = iter(computed)

        rows: list[dict] = []
        for base, isin, ticker, completed, context in prepared:
            if completed is None or context is None:
                snap = {"status": "DATA_INSUFFICIENT", "bars": 0, "t1_t2_used": False, "intraday_data_used": False}
            else:
                snap, error_record = next(computed_iter)
                if error_record:
                    error_record["isin"] = isin
                    error_records.append(error_record)
                    errors.append(f"{ticker}:{error_record['type']}:{error_record['message'][:160]}")
            entry_components = snap.pop("entry_components", {}) or {}
            exit_components = snap.pop("exit_components", {}) or {}
            baseline_row = baseline.loc[isin] if isin in baseline.index else pd.Series(dtype=object)
            if isinstance(baseline_row, pd.DataFrame):
                baseline_row = baseline_row.iloc[0]
            rows.append({
                "version": VERSION, "asset_class": "ACTION", "horizon": "CT",
                "isin": isin, "name": str(base.get("name") or ""), "yahoo_ticker": ticker,
                "baseline_ct_score": baseline_row.get("score"), "baseline_ct_coverage_pct": baseline_row.get("coverage_pct"),
                "baseline_ct_status": baseline_row.get("status"), "baseline_ct_decision": baseline_row.get("decision"),
                **snap,
                **{f"entry_component_{key}": value for key, value in entry_components.items()},
                **{f"exit_component_{key}": value for key, value in exit_components.items()},
                "generated_at_utc": generated_at,
                "decision_influence": 0.0, "score_influence": 0.0, "sizing_influence": 0.0,
                "stop_loss_influence": 0.0, "real_orders_enabled": False,
            })
        output = pd.DataFrame(rows)

    stage = perf_counter()
    previous_exit = legacy._read_csv(root / EXIT_STATE)
    if not output.empty:
        output = legacy._temporal_exit_confirmation(output, previous_exit)
        legacy._write_csv(legacy._merge_exit_state(previous_exit, output), root / EXIT_STATE)
    output_path = outdir / "ACTION_CT_V22_1_0_SHADOW.csv"
    legacy._write_csv(output, output_path)
    legacy._write_csv(output, root / LATEST)
    timings["state_and_output_write_seconds"] = perf_counter() - stage

    stage = perf_counter()
    ledger = legacy._read_csv(root / PIT_LEDGER)
    pit_candidates = output[output.apply(lambda row: _pit_snapshot_eligible(row, cfg, now), axis=1)].copy() if not output.empty else pd.DataFrame()
    ledger, added, mismatches = _append_first_snapshots(ledger, pit_candidates)
    ledger = legacy._label_outcomes(ledger, histories, list(cfg["pit_validation"]["outcome_sessions"]))
    legacy._write_csv(ledger, root / PIT_LEDGER)
    timings["pit_ledger_seconds"] = perf_counter() - stage

    validation = legacy._validation_payload(ledger, cfg)
    validation.update({
        "version": VERSION,
        "runtime_patch_version": cfg.get("runtime_patch_version"),
        "epoch": cfg["pit_validation"]["epoch"],
        "generated_at_utc": generated_at,
        "snapshot_fingerprint_algorithm": cfg["pit_validation"]["fingerprint_algorithm"],
        "snapshot_fingerprint_mismatches": mismatches,
        "new_pit_snapshots": int(added),
        "promotion_authority": False,
    })
    if mismatches:
        validation["status"] = "FAIL_CLOSED_FINGERPRINT_MISMATCH"

    parent = legacy._read_csv(outdir / "ACTION_CT_V22_0_0_SHADOW.csv")
    divergence, divergence_rows = _divergence_summary(output, parent)
    state_metrics = _state_metrics(output)
    timings["total_seconds"] = perf_counter() - started

    runtime_audit = {
        "runtime_patch_version": cfg.get("runtime_patch_version"),
        "schema": schema,
        "timings_seconds": {key: round(float(value), 6) for key, value in timings.items()},
        "state_metrics": state_metrics,
        "divergence": divergence,
        "parallel_compute_workers": int(cfg.get("runtime_observability", {}).get("parallel_compute_workers", 4)),
        "errors_count": int(len(error_records)),
    }

    (auditdir / "ACTION_CT_V22_1_0_PIT_VALIDATION.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (auditdir / "ACTION_CT_V22_1_0_CONTEXT.json").write_text(
        json.dumps(context_diagnostics, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (auditdir / "ACTION_CT_V22_1_0_RUNTIME.json").write_text(
        json.dumps(runtime_audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (auditdir / "ACTION_CT_V22_1_0_ERRORS.json").write_text(
        json.dumps(error_records, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    legacy._write_csv(divergence_rows, auditdir / "ACTION_CT_V22_1_0_DIVERGENCES.csv")
    (mobile / "ANDROID_ACTION_CT_V22_1_SHADOW.md").write_text(
        _android_summary(output, validation, context_diagnostics, generated_at, timings, divergence), encoding="utf-8"
    )

    payload = {
        "status": "FAIL_CLOSED_FINGERPRINT_MISMATCH" if mismatches else "SUCCESS_SHADOW_WITH_WARNINGS" if errors else "SUCCESS_SHADOW",
        "version": VERSION,
        "runtime_patch_version": cfg.get("runtime_patch_version"),
        "generated_at_utc": generated_at,
        "rows": int(len(output)),
        "daily_histories_found": int(len(histories)),
        "current_day_candles_deferred": int(deferred),
        "context_mapped_actions": int(context_diagnostics.get("mapped_actions", 0)),
        "context_fields_generated": context_diagnostics.get("fields_generated", []),
        "context_coverage": context_diagnostics.get("coverage", {}),
        "runtime": runtime_audit,
        "baseline_reference": cfg["governance"]["production_reference"],
        "baseline_unchanged": True,
        "challenger_decision_influence": 0.0,
        "challenger_score_influence": 0.0,
        "t1_t2_used": False,
        "t1_t2_forbidden": True,
        "intraday_data_used": False,
        "five_minute_data_used": False,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "pit_snapshots_added": int(added),
        "pit_ledger_rows": int(len(ledger)),
        "pit_validation_status": validation.get("status"),
        "fingerprint_mismatches": mismatches,
        "errors": errors[:50],
        "outputs": {
            "shadow": str(output_path.relative_to(root)),
            "latest_state": str(LATEST),
            "pit_ledger": str(PIT_LEDGER),
            "audit": "outputs/audit/ACTION_CT_V22_1_0_AUDIT.json",
            "context_audit": "outputs/audit/ACTION_CT_V22_1_0_CONTEXT.json",
            "runtime_audit": "outputs/audit/ACTION_CT_V22_1_0_RUNTIME.json",
            "errors_audit": "outputs/audit/ACTION_CT_V22_1_0_ERRORS.json",
            "divergences": "outputs/audit/ACTION_CT_V22_1_0_DIVERGENCES.csv",
            "pit_validation": "outputs/audit/ACTION_CT_V22_1_0_PIT_VALIDATION.json",
            "android": "outputs/mobile/ANDROID_ACTION_CT_V22_1_SHADOW.md",
        },
    }
    (auditdir / "ACTION_CT_V22_1_0_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
