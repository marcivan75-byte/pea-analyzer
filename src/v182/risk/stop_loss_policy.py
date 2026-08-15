from __future__ import annotations

from pathlib import Path
import json
import math

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PRICE_FIELDS = (
    "price",
    "last_price",
    "last_close",
    "close",
    "current_price",
    "cours",
    "price_eur",
)


def _num(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def stop_pct_for(asset_class: str, horizon: str, cfg: dict) -> float | None:
    asset = str(asset_class or "").upper()
    hz = str(horizon or "").upper()
    if asset == "ETF":
        value = cfg.get("etf_stop_loss_pct", {}).get(hz)
        return float(value) if value is not None else None
    if asset == "ACTION":
        value = cfg.get("action_stop_loss_pct", {}).get(hz)
        return float(value) if value is not None else None
    return None


def _reference_price(row: pd.Series) -> tuple[float | None, str | None]:
    for field in PRICE_FIELDS:
        if field not in row.index:
            continue
        value = _num(row.get(field))
        if value is not None and value > 0:
            return value, field
    return None, None


def apply_stop_loss_plan(decisions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = decisions.copy()
    stop_pcts = []
    stop_returns = []
    refs = []
    ref_fields = []
    stop_prices = []
    statuses = []
    rules = []

    for _, row in out.iterrows():
        stop_pct = stop_pct_for(row.get("asset_class"), row.get("horizon"), cfg)
        ref, ref_field = _reference_price(row)
        if stop_pct is None:
            stop_pcts.append(None)
            stop_returns.append(None)
            refs.append(ref)
            ref_fields.append(ref_field)
            stop_prices.append(None)
            statuses.append("NOT_APPLICABLE")
            rules.append(None)
            continue

        stop_pcts.append(stop_pct)
        stop_returns.append(-stop_pct / 100.0)
        refs.append(ref)
        ref_fields.append(ref_field)
        stop_prices.append(round(ref * (1.0 - stop_pct / 100.0), 8) if ref is not None else None)
        statuses.append("ACTIVE_REFERENCE_PRICE_AVAILABLE" if ref is not None else "ACTIVE_REFERENCE_PRICE_MISSING")
        rules.append("EXIT_IF_OBSERVED_CLOSE_AT_OR_BELOW_STOP_LEVEL")

    out["stop_loss_pct"] = stop_pcts
    out["stop_loss_return"] = stop_returns
    out["stop_reference_price"] = refs
    out["stop_reference_price_field"] = ref_fields
    out["stop_loss_price"] = stop_prices
    out["stop_loss_rule"] = rules
    out["stop_loss_status"] = statuses
    out["stop_loss_gap_slippage_warning"] = bool(cfg.get("execution", {}).get("gap_and_slippage_can_exceed_stop", True))
    return out


def run(root: Path = ROOT) -> dict:
    cfg_path = root / "config" / "STOP_LOSS_GOVERNANCE.json"
    decisions_path = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    if not cfg_path.exists():
        return {"status": "BLOCKED_STOP_CONFIG_MISSING"}
    if not decisions_path.exists():
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING"}

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    decisions = pd.read_csv(decisions_path, sep=";", encoding="utf-8-sig", low_memory=False)
    enriched = apply_stop_loss_plan(decisions, cfg)
    enriched.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")

    outdir = root / "outputs" / "committee_master"
    audit_dir = root / "outputs" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    plan_cols = [
        col
        for col in (
            "asset_class",
            "horizon",
            "isin",
            "name",
            "decision",
            "score",
            "stop_loss_pct",
            "stop_reference_price",
            "stop_reference_price_field",
            "stop_loss_price",
            "stop_loss_rule",
            "stop_loss_status",
        )
        if col in enriched.columns
    ]
    enriched[plan_cols].to_csv(outdir / "STOP_LOSS_PLAN.csv", sep=";", index=False, encoding="utf-8-sig")

    applicable = enriched[enriched["stop_loss_pct"].notna()]
    payload = {
        "status": "SUCCESS",
        "version": cfg.get("version"),
        "rows": int(len(enriched)),
        "stop_applicable_rows": int(len(applicable)),
        "rows_with_reference_price": int(applicable["stop_reference_price"].notna().sum()),
        "rows_without_reference_price": int(applicable["stop_reference_price"].isna().sum()),
        "single_position_per_isin": bool(cfg.get("single_position_per_isin", True)),
        "action_stop_loss_pct": cfg.get("action_stop_loss_pct", {}),
        "etf_stop_loss_pct": cfg.get("etf_stop_loss_pct", {}),
        "loss_cap_guaranteed": bool(cfg.get("execution", {}).get("loss_cap_guaranteed", False)),
        "gap_and_slippage_can_exceed_stop": bool(cfg.get("execution", {}).get("gap_and_slippage_can_exceed_stop", True)),
        "real_orders_enabled": False,
    }
    (audit_dir / "STOP_LOSS_GOVERNANCE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
