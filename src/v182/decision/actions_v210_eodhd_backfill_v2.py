from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import numpy as np
import pandas as pd

from v182.decision import actions_v210_eodhd_backfill as base

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv"
AUDIT = ROOT / "outputs/audit/V21.0_ACTIONS_EODHD_BACKFILL.json"

PERIOD_MAP = {
    "0q": "current_q",
    "+1q": "next_q",
    "1q": "next_q",
    "0y": "current_y",
    "+1y": "next_y",
    "1y": "next_y",
}


def _flatten(value) -> list[dict]:
    out: list[dict] = []
    if isinstance(value, dict):
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten(item))
    return out


def _apply_trends(df: pd.DataFrame, token: str, audit: dict) -> None:
    symbol_to_idx: dict[str, int] = {}
    if "eodhd_symbol_v21" in df.columns:
        for i, value in df["eodhd_symbol_v21"].items():
            symbol = str(value or "").strip()
            if symbol and symbol.lower() not in {"nan", "none", "n/a", "<na>"}:
                symbol_to_idx[symbol] = i
    symbols = list(symbol_to_idx)
    if not symbols:
        return

    audit["trend_batches"] = 0
    audit["trend_symbols"] = 0
    audit["trend_rows"] = 0
    audit["trend_parser_version"] = "V2_NESTED_LIST_SAFE"
    audit["errors"] = [e for e in audit.get("errors", []) if e.get("stage") != "earnings_trends"]

    batch_size = base.TREND_BATCH_SIZE
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        try:
            body = base._get_json("/calendar/trends", token, {"symbols": ",".join(batch)})
            audit["trend_batches"] += 1
            audit["trend_symbols"] += len(batch)
            container = body
            if isinstance(body, dict):
                for key in ["trends", "data", "results"]:
                    if key in body:
                        container = body[key]
                        break
            rows = _flatten(container)
            for raw in rows:
                symbol = str(raw.get("code") or raw.get("symbol") or raw.get("Code") or raw.get("Symbol") or "").strip()
                i = symbol_to_idx.get(symbol)
                if i is None and "." not in symbol:
                    matches = [idx for s, idx in symbol_to_idx.items() if s.split(".", 1)[0] == symbol]
                    i = matches[0] if len(matches) == 1 else None
                if i is None:
                    continue
                trend = base._parse_trend_row(raw)
                suffix = PERIOD_MAP.get(trend["period"])
                if not suffix:
                    continue
                audit["trend_rows"] += 1
                mapping = {
                    f"eps_estimate_{suffix}_v21": trend["earnings_avg"],
                    f"eps_estimate_low_{suffix}_v21": trend["earnings_low"],
                    f"eps_estimate_high_{suffix}_v21": trend["earnings_high"],
                    f"eps_estimate_analysts_{suffix}_v21": trend["earnings_analysts"],
                    f"revenue_estimate_{suffix}_v21": trend["revenue_avg"],
                    f"revenue_estimate_low_{suffix}_v21": trend["revenue_low"],
                    f"revenue_estimate_high_{suffix}_v21": trend["revenue_high"],
                    f"revenue_estimate_analysts_{suffix}_v21": trend["revenue_analysts"],
                    f"eps_estimate_growth_{suffix}_pct_v21": trend["earnings_growth"],
                    f"revenue_estimate_growth_{suffix}_pct_v21": trend["revenue_growth"],
                    f"eps_trend_current_{suffix}_v21": trend["eps_current"],
                    f"eps_trend_7d_{suffix}_v21": trend["eps_7d"],
                    f"eps_trend_30d_{suffix}_v21": trend["eps_30d"],
                    f"eps_trend_60d_{suffix}_v21": trend["eps_60d"],
                    f"eps_trend_90d_{suffix}_v21": trend["eps_90d"],
                    f"eps_revisions_up_30d_{suffix}_v21": trend["up_30d"],
                    f"eps_revisions_down_30d_{suffix}_v21": trend["down_30d"],
                }
                for field, value in mapping.items():
                    audit["filled_cells"] = int(audit.get("filled_cells", 0)) + base._fill(df, i, field, value)
                if trend["up_30d"] is not None or trend["down_30d"] is not None:
                    net = (trend["up_30d"] or 0.0) - (trend["down_30d"] or 0.0)
                    audit["filled_cells"] += base._fill(df, i, f"net_eps_revisions_30d_{suffix}_v21", net)
                if trend["eps_current"] is not None and trend["eps_30d"] not in (None, 0):
                    change = (trend["eps_current"] - trend["eps_30d"]) / abs(trend["eps_30d"]) * 100.0
                    audit["filled_cells"] += base._fill(df, i, f"eps_estimate_change_30d_pct_{suffix}_v21", change)
                    if suffix in {"current_y", "next_y"}:
                        audit["filled_cells"] += base._fill(
                            df,
                            i,
                            "estimate_revision_score_v21",
                            max(0.0, min(100.0, 50.0 + change * 5.0)),
                        )
        except Exception as exc:
            audit.setdefault("errors", []).append({
                "stage": "earnings_trends_v2",
                "batch_start": start,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            })


def main() -> None:
    # Base V1 remains responsible for ISIN mapping, fundamentals and financial statements.
    # V2 then replays only Earnings Trends with the documented nested-list response shape.
    base.main()
    token = str(os.getenv("EODHD_API_KEY") or "").strip()
    if not token:
        return
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    audit = json.loads(AUDIT.read_text(encoding="utf-8")) if AUDIT.exists() else {"passed": True, "status": "ACTIVE"}
    _apply_trends(df, token, audit)

    last = pd.to_numeric(df.get("last_close"), errors="coerce")
    target = pd.to_numeric(df.get("target_mean_v21"), errors="coerce")
    df["target_upside_pct_v21"] = ((target / last) - 1.0) * 100.0
    df.loc[last.le(0) | last.isna() | target.isna(), "target_upside_pct_v21"] = np.nan
    df["potential_gt_15_flag"] = df["target_upside_pct_v21"].ge(15).where(df["target_upside_pct_v21"].notna())
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")

    audit["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V21_ACTIONS_EODHD_BACKFILL_V2_OK", json.dumps({
        "status": audit.get("status"),
        "resolved": audit.get("resolved"),
        "trend_symbols": audit.get("trend_symbols"),
        "trend_rows": audit.get("trend_rows"),
        "filled_cells": audit.get("filled_cells"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
