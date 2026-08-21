from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math

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
    try:
        x = float(value)
        if math.isfinite(x):
            return format(x, ".12g")
    except (TypeError, ValueError):
        pass
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
    except Exception:
        return False


def _android_summary(frame: pd.DataFrame, validation: dict, diagnostics: dict, generated_at: str) -> str:
    lines = [
        "# Actions CT V22.1 — Context Enriched SHADOW", "",
        f"Généré UTC : {generated_at}",
        "Horizon : 2 à 12 semaines. Baseline CT V21.0 inchangée.",
        "Daily/weekly + rotation sectorielle + force relative + qualité/target + thème/macro si observés.",
        "T1/T2 interdits. Intraday interdit. Aucun stop/TP fixe. Aucun ordre réel.",
        f"Contexte dérivé : {diagnostics.get('mapped_actions', 0)} actions.",
        f"Validation PIT : {validation.get('status', 'IMMATURE_SHADOW')}", "",
    ]
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
    deferred = 0
    histories: dict[str, pd.DataFrame] = {}
    context_overlay: dict[str, dict] = {}
    context_diagnostics: dict = {"status": "NO_ACTION_MASTER", "mapped_actions": 0}

    if actions.empty or "isin" not in actions.columns:
        output = pd.DataFrame()
        errors.append("ACTION_MASTER_MISSING_OR_INVALID")
    else:
        work = actions.drop_duplicates("isin", keep="first").copy()
        work["isin"] = work["isin"].astype(str).str.upper()
        context_overlay, context_diagnostics = build_action_ct_context_overlay(work, cfg)
        ticker_col = "yahoo_ticker" if "yahoo_ticker" in work.columns else "ticker" if "ticker" in work.columns else None
        mapping: dict[str, str] = {}
        if ticker_col:
            for _, row in work.iterrows():
                ticker = str(row.get(ticker_col) or "").strip()
                if ticker not in {"", "nan", "None"}:
                    mapping[str(row["isin"])] = ticker
        histories_by_ticker = legacy._extract_histories(root / cfg["data_policy"]["source_cache"], set(mapping.values()))
        baseline = legacy._baseline_ct(actions, root)
        baseline["isin"] = baseline["isin"].astype(str).str.upper()
        baseline = baseline.drop_duplicates("isin", keep="first").set_index("isin", drop=False)
        rows: list[dict] = []
        for _, base in work.iterrows():
            isin = str(base["isin"]).upper()
            ticker = mapping.get(isin, "")
            history = histories_by_ticker.get(ticker) if ticker else None
            if history is None or history.empty:
                snap = {"status": "DATA_INSUFFICIENT", "bars": 0, "t1_t2_used": False, "intraday_data_used": False}
            else:
                completed, was_deferred = legacy._completed_daily_history(history, cfg, now)
                deferred += int(was_deferred)
                histories[isin] = completed
                context = merge_action_ct_context(base, context_overlay.get(isin, {}), cfg)
                try:
                    snap = compute_action_ct_snapshot_v22_1(completed, cfg, context)
                except Exception as exc:
                    errors.append(f"{ticker}:{type(exc).__name__}:{str(exc)[:160]}")
                    snap = {"status": "ERROR_SHADOW", "bars": int(len(completed)), "t1_t2_used": False, "intraday_data_used": False}
            entry_components = snap.pop("entry_components", {}) or {}
            exit_components = snap.pop("exit_components", {}) or {}
            b = baseline.loc[isin] if isin in baseline.index else pd.Series(dtype=object)
            if isinstance(b, pd.DataFrame):
                b = b.iloc[0]
            rows.append({
                "version": VERSION, "asset_class": "ACTION", "horizon": "CT",
                "isin": isin, "name": str(base.get("name") or ""), "yahoo_ticker": ticker,
                "baseline_ct_score": b.get("score"), "baseline_ct_coverage_pct": b.get("coverage_pct"),
                "baseline_ct_status": b.get("status"), "baseline_ct_decision": b.get("decision"),
                **snap,
                **{f"entry_component_{k}": v for k, v in entry_components.items()},
                **{f"exit_component_{k}": v for k, v in exit_components.items()},
                "generated_at_utc": generated_at,
                "decision_influence": 0.0, "score_influence": 0.0, "sizing_influence": 0.0,
                "stop_loss_influence": 0.0, "real_orders_enabled": False,
            })
        output = pd.DataFrame(rows)

    previous_exit = legacy._read_csv(root / EXIT_STATE)
    if not output.empty:
        output = legacy._temporal_exit_confirmation(output, previous_exit)
        legacy._write_csv(legacy._merge_exit_state(previous_exit, output), root / EXIT_STATE)
    output_path = outdir / "ACTION_CT_V22_1_0_SHADOW.csv"
    legacy._write_csv(output, output_path)
    legacy._write_csv(output, root / LATEST)

    ledger = legacy._read_csv(root / PIT_LEDGER)
    pit_candidates = output[output.apply(lambda row: _pit_snapshot_eligible(row, cfg, now), axis=1)].copy() if not output.empty else pd.DataFrame()
    ledger, added, mismatches = _append_first_snapshots(ledger, pit_candidates)
    ledger = legacy._label_outcomes(ledger, histories, list(cfg["pit_validation"]["outcome_sessions"]))
    legacy._write_csv(ledger, root / PIT_LEDGER)

    validation = legacy._validation_payload(ledger, cfg)
    validation.update({
        "version": VERSION,
        "epoch": cfg["pit_validation"]["epoch"],
        "generated_at_utc": generated_at,
        "snapshot_fingerprint_algorithm": cfg["pit_validation"]["fingerprint_algorithm"],
        "snapshot_fingerprint_mismatches": mismatches,
        "new_pit_snapshots": int(added),
        "promotion_authority": False,
    })
    if mismatches:
        validation["status"] = "FAIL_CLOSED_FINGERPRINT_MISMATCH"
    (auditdir / "ACTION_CT_V22_1_0_PIT_VALIDATION.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (auditdir / "ACTION_CT_V22_1_0_CONTEXT.json").write_text(
        json.dumps(context_diagnostics, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (mobile / "ANDROID_ACTION_CT_V22_1_SHADOW.md").write_text(
        _android_summary(output, validation, context_diagnostics, generated_at), encoding="utf-8"
    )

    payload = {
        "status": "FAIL_CLOSED_FINGERPRINT_MISMATCH" if mismatches else "SUCCESS_SHADOW_WITH_WARNINGS" if errors else "SUCCESS_SHADOW",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "rows": int(len(output)),
        "daily_histories_found": int(len(histories)),
        "current_day_candles_deferred": int(deferred),
        "context_mapped_actions": int(context_diagnostics.get("mapped_actions", 0)),
        "context_fields_generated": context_diagnostics.get("fields_generated", []),
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
