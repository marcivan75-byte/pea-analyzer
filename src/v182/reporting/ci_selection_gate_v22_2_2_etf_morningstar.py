from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import ci_selection_gate_v22_2_2 as base
from v182.reporting import selected_source_enrichment

ROOT = Path(__file__).resolve().parents[3]


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _num(value: object) -> float | None:
    try:
        value = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return value if pd.notna(value) else None


def _attach_master_identity_with_morningstar(selected, actions, etfs, original):
    result = original(selected, actions, etfs)
    if result.empty or etfs is None or etfs.empty or "isin" not in etfs or "morningstar_rating" not in etfs:
        return result
    source = etfs[["isin", "morningstar_rating"]].copy()
    source["isin"] = source["isin"].map(_text)
    source = source[source["isin"].ne("")].drop_duplicates("isin", keep="last")
    mapping = dict(zip(source["isin"], source["morningstar_rating"]))
    if "morningstar_rating" not in result:
        result["morningstar_rating"] = pd.NA
    asset = result.get("asset_class", pd.Series("", index=result.index)).astype(str).str.upper()
    missing = pd.to_numeric(result["morningstar_rating"], errors="coerce").isna() & asset.eq("ETF")
    result.loc[missing, "morningstar_rating"] = result.loc[missing, "isin"].map(
        lambda value: mapping.get(_text(value), pd.NA)
    )
    return result


def _gate_row_with_etf_morningstar(row: pd.Series, cfg: dict, original):
    passed, reasons = original(row, cfg)
    reasons = list(reasons)
    asset = _text(row.get("asset_class")).upper()
    if asset == "ETF":
        policy = cfg.get("etf_morningstar_gate", {})
        if bool(policy.get("enabled", True)):
            minimum = float(policy.get("minimum_stars", cfg.get("selection_gate", {}).get("etf_minimum_morningstar_stars", 3.0)))
            rating = _num(row.get(str(policy.get("field", "morningstar_rating"))))
            if rating is None:
                reasons.append("ETF_MORNINGSTAR_RATING_MISSING")
            elif rating < minimum:
                reasons.append("ETF_MORNINGSTAR_RATING_LT_3")
    return not reasons, reasons


def run(root: Path = ROOT, *, ensure_upstream: bool = True) -> dict:
    original_attach = selected_source_enrichment.attach_master_identity
    original_gate = base._gate_row

    def attach(selected, actions, etfs):
        return _attach_master_identity_with_morningstar(selected, actions, etfs, original_attach)

    def gate(row, cfg):
        return _gate_row_with_etf_morningstar(row, cfg, original_gate)

    selected_source_enrichment.attach_master_identity = attach
    base._gate_row = gate
    try:
        payload = base.run(root=root, ensure_upstream=ensure_upstream)
    finally:
        selected_source_enrichment.attach_master_identity = original_attach
        base._gate_row = original_gate

    payload = dict(payload)
    payload["etf_analyst_consensus_required"] = False
    payload["etf_minimum_morningstar_stars"] = 3.0
    payload["etf_missing_morningstar_policy"] = "EXCLUDE_FAIL_CLOSED"

    audit_path = root / base.AUDIT
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            audit = {}
        audit["etf_analyst_consensus_required"] = False
        audit["etf_minimum_morningstar_stars"] = 3.0
        audit["etf_missing_morningstar_policy"] = "EXCLUDE_FAIL_CLOSED"
        audit["etf_morningstar_replaces_analyst_consensus"] = True
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
