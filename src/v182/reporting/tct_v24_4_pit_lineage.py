from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4.0_PIT_LINEAGE_V1"

_MUTABLE_PREFIXES = ("realized_", "raw_realized_", "outcome_", "pit_")
_MUTABLE_FIELDS = {"snapshot_payload_sha256"}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _normalise(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        x = float(value)
        return None if not math.isfinite(x) else round(x, 10)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value)


def _prediction_payload(row: pd.Series) -> dict:
    payload: dict[str, object] = {}
    for key in sorted(str(k) for k in row.index):
        if key in _MUTABLE_FIELDS or key.startswith(_MUTABLE_PREFIXES):
            continue
        payload[key] = _normalise(row.get(key))
    return payload


def prediction_fingerprint(row: pd.Series) -> str:
    raw = json.dumps(_prediction_payload(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _prepare_close_ledger(close_ledger: pd.DataFrame) -> pd.DataFrame:
    if close_ledger.empty:
        return close_ledger
    work = close_ledger.copy()
    work["isin"] = work.get("isin", pd.Series(index=work.index, dtype=object)).astype(str).str.upper()
    work["as_of_date"] = pd.to_datetime(work.get("as_of_date"), errors="coerce")
    work["reference_close"] = pd.to_numeric(work.get("reference_close"), errors="coerce")
    work = work.dropna(subset=["as_of_date", "reference_close"])
    work = work[work["reference_close"] > 0]
    return work.sort_values(["isin", "as_of_date"])


def first_subsequent_close(close_ledger: pd.DataFrame, isin: str, source_date: object) -> tuple[str, float] | None:
    if close_ledger.empty:
        return None
    source = pd.to_datetime(source_date, errors="coerce")
    if pd.isna(source):
        return None
    rows = close_ledger[
        close_ledger["isin"].eq(str(isin).upper())
        & (close_ledger["as_of_date"] > pd.Timestamp(source))
    ]
    if rows.empty:
        return None
    first = rows.iloc[0]
    return pd.Timestamp(first["as_of_date"]).date().isoformat(), float(first["reference_close"])


def apply_lineage(
    catalyst_ledger: pd.DataFrame,
    close_ledger: pd.DataFrame,
    *,
    minimum_snapshot_coverage: float,
    labeled_at_utc: str,
) -> tuple[pd.DataFrame, dict]:
    if catalyst_ledger.empty:
        return catalyst_ledger.copy(), {
            "snapshot_rows": 0,
            "fingerprints_created": 0,
            "fingerprint_mismatches": 0,
            "raw_next_session_labels": 0,
            "validator_labels": 0,
            "qualified_snapshots": 0,
            "pending_snapshots": 0,
        }

    out = catalyst_ledger.copy()
    prepared_closes = _prepare_close_ledger(close_ledger)

    existing_hash = out.get("snapshot_payload_sha256", pd.Series(pd.NA, index=out.index, dtype=object)).copy()
    recalculated = out.apply(prediction_fingerprint, axis=1)
    existing_text = existing_hash.astype(str).str.strip()
    had_hash = existing_hash.notna() & ~existing_text.isin({"", "nan", "None"})
    mismatch = had_hash & existing_text.ne(recalculated)
    created = ~had_hash
    out["snapshot_payload_sha256"] = existing_hash.where(had_hash, recalculated)
    out["pit_fingerprint_valid"] = ~mismatch

    for column in [
        "raw_realized_close_to_close_return_pct",
        "raw_realized_abs_return_pct",
        "outcome_as_of_date",
        "outcome_labeled_at_utc",
        "outcome_label_source",
        "outcome_step",
        "outcome_snapshot_coverage",
        "pit_label_evaluable",
        "realized_close_to_close_return_pct",
        "realized_abs_return_pct",
        "realized_direction_hit",
        "realized_abs_move_rank_within_snapshot",
    ]:
        if column not in out.columns:
            out[column] = pd.NA

    # Rebuild every PREOPEN outcome from the compact close ledger. This repairs
    # any older label that may have used a later seed instead of the first
    # subsequent observed daily close.
    pre_mask = out.get("phase", pd.Series(index=out.index, dtype=object)).astype(str).eq("PREOPEN")
    for idx in out.index[pre_mask]:
        row = out.loc[idx]
        result = first_subsequent_close(prepared_closes, str(row.get("isin") or ""), row.get("as_of_date"))
        out.at[idx, "raw_realized_close_to_close_return_pct"] = pd.NA
        out.at[idx, "raw_realized_abs_return_pct"] = pd.NA
        out.at[idx, "outcome_as_of_date"] = pd.NA
        out.at[idx, "outcome_labeled_at_utc"] = pd.NA
        out.at[idx, "outcome_label_source"] = "FIRST_SUBSEQUENT_OBSERVED_DAILY_CLOSE"
        out.at[idx, "outcome_step"] = pd.NA
        if result is None:
            continue
        outcome_date, after = result
        before = pd.to_numeric(pd.Series([row.get("reference_close")]), errors="coerce").iloc[0]
        if pd.isna(before) or float(before) <= 0 or after <= 0:
            continue
        realized = (after / float(before) - 1.0) * 100.0
        out.at[idx, "raw_realized_close_to_close_return_pct"] = round(realized, 6)
        out.at[idx, "raw_realized_abs_return_pct"] = round(abs(realized), 6)
        out.at[idx, "outcome_as_of_date"] = outcome_date
        out.at[idx, "outcome_labeled_at_utc"] = labeled_at_utc
        out.at[idx, "outcome_step"] = 1

    # Validator-facing labels are fail-closed until enough rows of the same
    # PREOPEN snapshot have a true first-next-session outcome.
    out.loc[pre_mask, "realized_close_to_close_return_pct"] = pd.NA
    out.loc[pre_mask, "realized_abs_return_pct"] = pd.NA
    out.loc[pre_mask, "realized_direction_hit"] = pd.NA
    out.loc[pre_mask, "realized_abs_move_rank_within_snapshot"] = pd.NA
    out.loc[pre_mask, "pit_label_evaluable"] = False
    out.loc[pre_mask, "outcome_snapshot_coverage"] = 0.0

    qualified = 0
    pending = 0
    group_key = "snapshot_generated_at_utc" if "snapshot_generated_at_utc" in out.columns else "snapshot_key"
    for _, group in out[pre_mask].groupby(group_key, dropna=False):
        raw = pd.to_numeric(group["raw_realized_abs_return_pct"], errors="coerce")
        coverage = 0.0 if len(group) == 0 else float(raw.notna().sum() / len(group))
        out.loc[group.index, "outcome_snapshot_coverage"] = round(coverage, 6)
        if coverage < float(minimum_snapshot_coverage):
            pending += 1
            continue
        qualified += 1
        for idx in group.index:
            raw_ret = pd.to_numeric(pd.Series([out.at[idx, "raw_realized_close_to_close_return_pct"]]), errors="coerce").iloc[0]
            raw_abs = pd.to_numeric(pd.Series([out.at[idx, "raw_realized_abs_return_pct"]]), errors="coerce").iloc[0]
            if pd.isna(raw_ret) or pd.isna(raw_abs):
                continue
            out.at[idx, "realized_close_to_close_return_pct"] = float(raw_ret)
            out.at[idx, "realized_abs_return_pct"] = float(raw_abs)
            bias = pd.to_numeric(pd.Series([out.at[idx, "direction_bias_score"]]), errors="coerce").iloc[0]
            if not pd.isna(bias) and abs(float(bias)) >= 25.0 and float(raw_ret) != 0:
                out.at[idx, "realized_direction_hit"] = 1.0 if np.sign(float(bias)) == np.sign(float(raw_ret)) else 0.0
            out.at[idx, "pit_label_evaluable"] = True
        eligible = group.index[pd.to_numeric(out.loc[group.index, "realized_abs_return_pct"], errors="coerce").notna()]
        if len(eligible):
            ranks = pd.to_numeric(out.loc[eligible, "realized_abs_return_pct"], errors="coerce").rank(method="min", ascending=False)
            for idx, rank in ranks.items():
                out.at[idx, "realized_abs_move_rank_within_snapshot"] = float(rank)

    stats = {
        "snapshot_rows": int(len(out)),
        "fingerprints_created": int(created.sum()),
        "fingerprint_mismatches": int(mismatch.sum()),
        "raw_next_session_labels": int(pd.to_numeric(out.loc[pre_mask, "raw_realized_abs_return_pct"], errors="coerce").notna().sum()),
        "validator_labels": int(pd.to_numeric(out.loc[pre_mask, "realized_abs_return_pct"], errors="coerce").notna().sum()),
        "qualified_snapshots": int(qualified),
        "pending_snapshots": int(pending),
        "minimum_snapshot_outcome_coverage": float(minimum_snapshot_coverage),
    }
    return out, stats


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat()
    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    close_path = root / cfg["state"].get("daily_close_ledger_path", "state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv")
    ledger = _read_csv(ledger_path)
    closes = _read_csv(close_path)
    coverage = float(cfg.get("pit_lineage", {}).get("minimum_snapshot_outcome_coverage", 0.80))
    enriched, stats = apply_lineage(
        ledger,
        closes,
        minimum_snapshot_coverage=coverage,
        labeled_at_utc=generated_at,
    )
    _write_csv(enriched, ledger_path)

    mismatch = int(stats["fingerprint_mismatches"])
    payload = {
        "status": "FAIL_CLOSED_FINGERPRINT_MISMATCH" if mismatch else "SUCCESS_PIT_LINEAGE",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "ledger_path": str(ledger_path.relative_to(root)),
        "close_ledger_path": str(close_path.relative_to(root)),
        **stats,
        "first_subsequent_close_only": True,
        "snapshot_fingerprint_required": True,
        "production_influence": 0.0,
        "holdout_opened": False,
        "promotion_authority": False,
    }
    audit = root / "outputs" / "audit" / "TCT_V24_4_0_PIT_LINEAGE_AUDIT.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if mismatch and bool(cfg.get("pit_lineage", {}).get("fail_closed_on_fingerprint_mismatch", True)):
        raise RuntimeError(f"V24.4 PIT fingerprint mismatch on {mismatch} stored snapshot rows")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
