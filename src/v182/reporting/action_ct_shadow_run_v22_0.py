from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any
import json
import math

import numpy as np
import pandas as pd

from v182.decision.committee_master import decisions_from_scores, load_registry
from v182.features.action_ct_v22_0 import compute_action_ct_snapshot


ROOT = Path(__file__).resolve().parents[3]
VERSION = "ACTION_CT_V22.0.0_DAILY_WEEKLY_CONFLUENCE_SHADOW"
CONFIG = "ACTION_CT_V22_0_0_SHADOW.json"
STATE_DIR = Path("state/action_ct")
LATEST = STATE_DIR / "ACTION_CT_V22_0_0_LATEST.csv"
PIT_LEDGER = STATE_DIR / "ACTION_CT_V22_0_0_PIT_LEDGER.csv"
EXIT_STATE = STATE_DIR / "ACTION_CT_V22_0_0_EXIT_STATE.csv"
FINGERPRINT_FIELDS = (
    "version", "snapshot_date", "isin", "reference_close",
    "baseline_ct_score", "baseline_ct_decision",
    "entry_score", "entry_state", "entry_confirmation_count",
    "exit_risk_score", "exit_state",
    "trend_score", "momentum_score", "weekly_score",
    "sector_context_score", "volume_score", "catalyst_score", "warnings",
)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _finite(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _extract_histories(cache_dir: Path, wanted: set[str]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    if not cache_dir.exists() or not wanted:
        return histories
    for path in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if frame.empty:
            continue
        if isinstance(frame.columns, pd.MultiIndex):
            level0 = set(map(str, frame.columns.get_level_values(0)))
            level1 = set(map(str, frame.columns.get_level_values(1))) if frame.columns.nlevels > 1 else set()
            for ticker in wanted:
                sub = None
                try:
                    if ticker in level0:
                        sub = frame[ticker]
                    elif ticker in level1:
                        sub = frame.xs(ticker, axis=1, level=1)
                except Exception:
                    sub = None
                if sub is not None and not sub.empty and (ticker not in histories or len(sub) > len(histories[ticker])):
                    histories[ticker] = sub.copy()
            continue
        ticker_col = next((c for c in ("yahoo_ticker", "ticker", "symbol") if c in frame.columns), None)
        if ticker_col is not None:
            for ticker, sub in frame.groupby(frame[ticker_col].astype(str)):
                if ticker in wanted and (ticker not in histories or len(sub) > len(histories[ticker])):
                    histories[ticker] = sub.drop(columns=[ticker_col], errors="ignore").copy()
        elif len(wanted) == 1:
            histories[next(iter(wanted))] = frame.copy()
    return histories


def _completed_daily_history(history: pd.DataFrame, cfg: dict, now: datetime | None = None) -> tuple[pd.DataFrame, bool]:
    if history is None or history.empty:
        return history, False
    policy = cfg["data_policy"]
    if not bool(policy.get("defer_current_day_before_local_close", True)):
        return history, False
    tz = ZoneInfo(str(policy.get("local_close_guard_timezone", "Europe/Paris")))
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    idx = pd.to_datetime(history.index, errors="coerce")
    if len(idx) == 0 or pd.isna(idx[-1]):
        return history, False
    if pd.Timestamp(idx[-1]).date() == local.date() and local.hour < int(policy.get("local_close_guard_hour", 18)):
        return history.iloc[:-1].copy(), True
    return history, False


def _baseline_ct(actions: pd.DataFrame, root: Path) -> pd.DataFrame:
    daily = _read_csv(root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv")
    if not daily.empty and {"asset_class", "horizon", "isin"}.issubset(daily.columns):
        baseline = daily[
            daily["asset_class"].astype(str).str.upper().eq("ACTION")
            & daily["horizon"].astype(str).str.upper().eq("CT")
        ].copy()
        if not baseline.empty:
            return baseline
    reference = load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    return decisions_from_scores(actions, reference, "ACTION", ["CT"])


def _canonical_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "1" if bool(value) else "0"
    if np.isscalar(value):
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and bool(missing):
            return ""
    numeric = _finite(value)
    return format(numeric, ".12g") if numeric is not None else str(value).strip()


def _fingerprint(row: pd.Series | dict) -> str:
    getter = row.get
    text = "|".join(f"{field}={_canonical_value(getter(field))}" for field in FINGERPRINT_FIELDS)
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


def _normalise_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    out = history.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    if not {"open", "high", "low", "close"}.issubset(out.columns):
        return pd.DataFrame()
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out[~out.index.duplicated(keep="last")].dropna(subset=["open", "high", "low", "close"])


def _label_outcomes(ledger: pd.DataFrame, histories_by_isin: dict[str, pd.DataFrame], horizons: list[int]) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    out = ledger.copy()
    for horizon in horizons:
        field = f"return_{horizon}d_pct"
        if field not in out.columns:
            out[field] = np.nan
    for field in ("entry_date", "entry_open", "mfe_20d_pct", "mae_20d_pct"):
        if field not in out.columns:
            out[field] = pd.NA if field == "entry_date" else np.nan
    for idx, row in out.iterrows():
        isin = str(row.get("isin") or "").upper()
        history = _normalise_history(histories_by_isin.get(isin, pd.DataFrame()))
        if history.empty:
            continue
        try:
            snapshot_date = pd.Timestamp(str(row.get("snapshot_date"))).date()
        except Exception:
            continue
        dates = pd.Index(pd.to_datetime(history.index).date)
        future = history[dates > snapshot_date]
        if future.empty:
            continue
        entry_open = _finite(future.iloc[0]["open"])
        if entry_open is None or entry_open <= 0:
            continue
        out.at[idx, "entry_date"] = pd.Timestamp(future.index[0]).date().isoformat()
        out.at[idx, "entry_open"] = entry_open
        for horizon in horizons:
            if len(future) >= horizon:
                terminal = _finite(future.iloc[horizon - 1]["close"])
                if terminal is not None:
                    out.at[idx, f"return_{horizon}d_pct"] = (terminal / entry_open - 1.0) * 100.0
        if len(future) >= 20:
            window = future.iloc[:20]
            high = _finite(pd.to_numeric(window["high"], errors="coerce").max())
            low = _finite(pd.to_numeric(window["low"], errors="coerce").min())
            if high is not None:
                out.at[idx, "mfe_20d_pct"] = (high / entry_open - 1.0) * 100.0
            if low is not None:
                out.at[idx, "mae_20d_pct"] = (low / entry_open - 1.0) * 100.0
    return out


def _temporal_exit_confirmation(output: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    """Require a prior market-date warning; same-day reruns are idempotent."""
    out = output.copy()
    prev_map: dict[str, tuple[str, str]] = {}
    if not previous.empty and {"isin", "snapshot_date", "exit_state"}.issubset(previous.columns):
        for _, row in previous.iterrows():
            isin = str(row.get("isin") or "").upper()
            date = str(row.get("snapshot_date") or "")[:10]
            state = str(row.get("exit_state") or "")
            if isin and date:
                prev_map[isin] = (date, state)
    final_states: list[str] = []
    reasons: list[str] = []
    for _, row in out.iterrows():
        raw = str(row.get("exit_state_raw") or "")
        isin = str(row.get("isin") or "").upper()
        current_date = str(row.get("snapshot_date") or "")[:10]
        prior_date, prior_state = prev_map.get(isin, ("", ""))
        prior_session_warning = bool(
            prior_date and current_date and prior_date < current_date
            and prior_state in {"EXIT_WATCH_SHADOW", "EXIT_RISK_HIGH_SHADOW"}
        )
        if raw == "EXIT_RISK_HIGH_CANDIDATE_SHADOW":
            if prior_session_warning:
                final_states.append("EXIT_RISK_HIGH_SHADOW")
                reasons.append("TEMPORAL_CONFIRMATION_AFTER_PRIOR_SESSION_WARNING")
            else:
                final_states.append("EXIT_WATCH_SHADOW")
                reasons.append("AWAIT_PRIOR_SESSION_CONFIRMATION")
        else:
            final_states.append(raw)
            reasons.append("")
    out["exit_state"] = final_states
    out["exit_temporal_reason"] = reasons
    return out


def _merge_exit_state(previous: pd.DataFrame, output: pd.DataFrame) -> pd.DataFrame:
    cols = ["isin", "snapshot_date", "exit_state"]
    old = previous[cols].copy() if not previous.empty and set(cols).issubset(previous.columns) else pd.DataFrame(columns=cols)
    if output.empty or not set(cols + ["status"]).issubset(output.columns):
        return old
    current = output[output["status"].astype(str).eq("SUCCESS_SHADOW")][cols].dropna(subset=["snapshot_date"]).copy()
    if current.empty:
        return old
    combined = pd.concat([old, current], ignore_index=True)
    combined["isin"] = combined["isin"].astype(str).str.upper()
    combined["snapshot_date"] = combined["snapshot_date"].astype(str).str[:10]
    combined = combined.sort_values(["isin", "snapshot_date"]).drop_duplicates("isin", keep="last")
    return combined.reset_index(drop=True)


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


def _validation_payload(ledger: pd.DataFrame, cfg: dict) -> dict:
    vcfg = cfg["pit_validation"]
    primary = int(cfg["horizon_definition"]["primary_validation_sessions"])
    field = f"return_{primary}d_pct"
    if ledger.empty or field not in ledger.columns:
        return {"status": "IMMATURE_SHADOW", "reason": "NO_LABELED_PIT_ROWS", "promotion_authority": False}
    labeled = ledger[pd.to_numeric(ledger[field], errors="coerce").notna()].copy()
    labeled[field] = pd.to_numeric(labeled[field], errors="coerce")
    labeled["entry_score"] = pd.to_numeric(labeled.get("entry_score"), errors="coerce")
    challenger = labeled[labeled["entry_state"].astype(str).isin(["ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"])].copy()
    baseline = labeled[labeled["baseline_ct_decision"].astype(str).eq("BUY_CANDIDATE")].copy()
    maturity = {
        "labeled_primary_rows": int(len(labeled)),
        "distinct_isins": int(labeled["isin"].astype(str).nunique()) if "isin" in labeled.columns else 0,
        "distinct_snapshot_dates": int(labeled["snapshot_date"].astype(str).nunique()) if "snapshot_date" in labeled.columns else 0,
        "entry_ready_primary_rows": int(len(challenger)),
    }
    mature = bool(
        maturity["labeled_primary_rows"] >= int(vcfg["minimum_labeled_primary_rows"])
        and maturity["distinct_isins"] >= int(vcfg["minimum_distinct_isins"])
        and maturity["distinct_snapshot_dates"] >= int(vcfg["minimum_distinct_snapshot_dates"])
        and maturity["entry_ready_primary_rows"] >= int(vcfg["minimum_entry_ready_primary_rows"])
    )

    def win_rate(frame: pd.DataFrame) -> float | None:
        return None if frame.empty else float((frame[field] > 0).mean() * 100.0)

    def median(frame: pd.DataFrame, column: str) -> float | None:
        if frame.empty or column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return None if values.empty else float(values.median())

    c_win, b_win = win_rate(challenger), win_rate(baseline)
    c_med, b_med = median(challenger, field), median(baseline, field)
    c_mae, b_mae = median(challenger, "mae_20d_pct"), median(baseline, "mae_20d_pct")
    win_delta = None if c_win is None or b_win is None else c_win - b_win
    median_delta = None if c_med is None or b_med is None else c_med - b_med
    mae_delta = None if c_mae is None or b_mae is None else abs(min(c_mae, 0.0)) - abs(min(b_mae, 0.0))
    false_positive = None if challenger.empty else float((challenger[field] <= 0).mean())
    corr_sample = labeled.dropna(subset=["entry_score", field])
    spearman = _finite(corr_sample["entry_score"].corr(corr_sample[field], method="spearman")) if len(corr_sample) >= 3 else None

    gates_cfg = vcfg["primary_metrics"]
    gates = {
        "win_rate_improvement": bool(win_delta is not None and win_delta >= float(gates_cfg["entry_ready_win_rate_improvement_points_vs_baseline"])),
        "median_return_improvement": bool(median_delta is not None and median_delta >= float(gates_cfg["median_return_20d_improvement_points_vs_baseline"])),
        "spearman": bool(spearman is not None and spearman >= float(gates_cfg["spearman_entry_score_return_20d_min"])),
        "false_positive_rate": bool(false_positive is not None and false_positive <= float(gates_cfg["false_positive_rate_max"])),
        "mae_guard": bool(mae_delta is not None and mae_delta <= float(vcfg["risk_guard"]["median_mae_20d_degradation_points_max"])),
    }
    passed = bool(mature and all(gates.values()))
    return {
        "status": "PASS_RESEARCH_GATES_NO_AUTO_PROMOTION" if passed else "FAIL_RESEARCH_GATES" if mature else "IMMATURE_SHADOW",
        "maturity": maturity,
        "mature": mature,
        "samples": {"challenger_entry_ready": int(len(challenger)), "baseline_buy": int(len(baseline))},
        "metrics": {
            "challenger_win_rate_pct": c_win, "baseline_win_rate_pct": b_win,
            "win_rate_improvement_points": win_delta,
            "challenger_median_return_20d_pct": c_med, "baseline_median_return_20d_pct": b_med,
            "median_return_improvement_points": median_delta,
            "spearman_entry_score_return_20d": spearman,
            "false_positive_rate": false_positive,
            "challenger_median_mae_20d_pct": c_mae, "baseline_median_mae_20d_pct": b_mae,
            "mae_degradation_points": mae_delta,
        },
        "gates": gates,
        "promotion_authority": False,
        "holdout_opened": False,
    }


def _android_summary(frame: pd.DataFrame, validation: dict, generated_at: str) -> str:
    lines = [
        "# Actions CT V22.0 — Daily/Weekly SHADOW", "",
        f"Généré UTC : {generated_at}",
        "Horizon : 2 à 12 semaines. Baseline CT V21.0 conservée et comparée séparément.",
        "Aucun T1/T2. Aucun intraday/5m. Aucun take-profit ou stop fixe promu. Aucun ordre réel.",
        f"Validation PIT : {validation.get('status', 'IMMATURE_SHADOW')}", "",
    ]
    if frame.empty:
        return "\n".join(lines + ["Aucun diagnostic CT exploitable."]) + "\n"
    work = frame.copy()
    work["_entry"] = pd.to_numeric(work.get("entry_score"), errors="coerce")
    priority = work[work["entry_state"].astype(str).isin([
        "ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW", "WAIT_RISK_SHADOW", "ENTRY_CONFLICT_SHADOW"
    ])].sort_values("_entry", ascending=False).head(15)
    if priority.empty:
        return "\n".join(lines + ["Aucun ENTRY_READY/STRONG ou warning prioritaire sur ce run."]) + "\n"
    for _, row in priority.iterrows():
        name = str(row.get("name") or row.get("isin") or "N/A")
        score = row.get("_entry")
        score_txt = "N/A" if pd.isna(score) else f"{float(score):.1f}"
        lines.append(
            f"- **{name}** — CT V22 {row.get('entry_state')} ({score_txt}) — "
            f"baseline {row.get('baseline_ct_decision')} — sortie {row.get('exit_state')}"
        )
        warnings = str(row.get("warnings") or "").strip()
        if warnings and warnings.lower() != "nan":
            lines.append(f"  - Warnings : {warnings}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    actions = _read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    outdir, auditdir, mobile = root / "outputs" / "daily_tct_ct", root / "outputs" / "audit", root / "outputs" / "mobile"
    for directory in (outdir, auditdir, mobile, root / STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    generated_at = now.isoformat()
    errors: list[str] = []
    deferred = 0
    histories: dict[str, pd.DataFrame] = {}

    if actions.empty or "isin" not in actions.columns:
        output = pd.DataFrame()
        errors.append("ACTION_MASTER_MISSING_OR_INVALID")
    else:
        work = actions.drop_duplicates("isin", keep="first").copy()
        work["isin"] = work["isin"].astype(str).str.upper()
        ticker_col = "yahoo_ticker" if "yahoo_ticker" in work.columns else "ticker" if "ticker" in work.columns else None
        mapping: dict[str, str] = {}
        if ticker_col:
            for _, row in work.iterrows():
                ticker = str(row.get(ticker_col) or "").strip()
                if ticker not in {"", "nan", "None"}:
                    mapping[str(row["isin"])] = ticker
        histories_by_ticker = _extract_histories(root / cfg["data_policy"]["source_cache"], set(mapping.values()))
        baseline = _baseline_ct(actions, root)
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
                completed, was_deferred = _completed_daily_history(history, cfg, now)
                deferred += int(was_deferred)
                histories[isin] = completed
                try:
                    snap = compute_action_ct_snapshot(completed, cfg, base.to_dict())
                except Exception as exc:
                    errors.append(f"{ticker}:{type(exc).__name__}:{str(exc)[:160]}")
                    snap = {"status": "ERROR_SHADOW", "bars": int(len(completed)), "t1_t2_used": False, "intraday_data_used": False}
            raw_entry_components = snap.pop("entry_components", {})
            raw_exit_components = snap.pop("exit_components", {})
            entry_components: dict[str, Any] = raw_entry_components if isinstance(raw_entry_components, dict) else {}
            exit_components: dict[str, Any] = raw_exit_components if isinstance(raw_exit_components, dict) else {}
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

    previous_exit = _read_csv(root / EXIT_STATE)
    if not output.empty:
        output = _temporal_exit_confirmation(output, previous_exit)
        _write_csv(_merge_exit_state(previous_exit, output), root / EXIT_STATE)
    output_path = outdir / "ACTION_CT_V22_0_0_SHADOW.csv"
    _write_csv(output, output_path)
    _write_csv(output, root / LATEST)

    ledger = _read_csv(root / PIT_LEDGER)
    pit_candidates = output[output.apply(lambda row: _pit_snapshot_eligible(row, cfg, now), axis=1)].copy() if not output.empty else pd.DataFrame()
    ledger, added, mismatches = _append_first_snapshots(ledger, pit_candidates)
    ledger = _label_outcomes(ledger, histories, list(cfg["pit_validation"]["outcome_sessions"]))
    _write_csv(ledger, root / PIT_LEDGER)

    validation = _validation_payload(ledger, cfg)
    validation.update({
        "version": VERSION, "epoch": cfg["pit_validation"]["epoch"], "generated_at_utc": generated_at,
        "snapshot_fingerprint_algorithm": cfg["pit_validation"]["fingerprint_algorithm"],
        "snapshot_fingerprint_mismatches": mismatches, "new_pit_snapshots": int(added),
        "promotion_authority": False,
    })
    if mismatches:
        validation["status"] = "FAIL_CLOSED_FINGERPRINT_MISMATCH"
    (auditdir / "ACTION_CT_V22_0_0_PIT_VALIDATION.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (mobile / "ANDROID_ACTION_CT_V22_SHADOW.md").write_text(_android_summary(output, validation, generated_at), encoding="utf-8")
    (mobile / "ANDROID_ACTION_CT_V22_PIT_VALIDATION.md").write_text(
        "# Actions CT V22.0 — Validation PIT\n\n"
        f"Statut : **{validation.get('status')}**\n\n"
        f"Maturité : `{json.dumps(validation.get('maturity', {}), ensure_ascii=False)}`\n\n"
        "Un PASS de recherche ne déclenche jamais une promotion automatique.\n",
        encoding="utf-8",
    )

    payload = {
        "status": "FAIL_CLOSED_FINGERPRINT_MISMATCH" if mismatches else "SUCCESS_SHADOW_WITH_WARNINGS" if errors else "SUCCESS_SHADOW",
        "version": VERSION, "generated_at_utc": generated_at, "rows": int(len(output)),
        "daily_histories_found": int(len(histories)), "current_day_candles_deferred": int(deferred),
        "baseline_reference": cfg["governance"]["production_reference"], "baseline_unchanged": True,
        "challenger_decision_influence": 0.0, "challenger_score_influence": 0.0,
        "t1_t2_used": False, "t1_t2_forbidden": True,
        "intraday_data_used": False, "five_minute_data_used": False,
        "fixed_take_profit_enabled": False, "fixed_stop_loss_enabled": False,
        "holdout_opened": False, "real_orders_enabled": False,
        "pit_snapshots_added": int(added), "pit_ledger_rows": int(len(ledger)),
        "pit_validation_status": validation.get("status"), "fingerprint_mismatches": mismatches,
        "errors": errors[:50],
        "outputs": {
            "shadow": str(output_path.relative_to(root)), "latest_state": str(LATEST), "pit_ledger": str(PIT_LEDGER),
            "audit": "outputs/audit/ACTION_CT_V22_0_0_AUDIT.json",
            "pit_validation": "outputs/audit/ACTION_CT_V22_0_0_PIT_VALIDATION.json",
            "android": "outputs/mobile/ANDROID_ACTION_CT_V22_SHADOW.md",
        },
    }
    (auditdir / "ACTION_CT_V22_0_0_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))

