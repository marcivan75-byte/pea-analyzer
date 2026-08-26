from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from v182.risk.beta_metrics import load_cached_prices, to_returns

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/OBJECTIVES_RISK_CHALLENGER_V2.json")
INPUT = Path("outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
OUTPUT = Path("outputs/committee_master/PORTFOLIO_BUDGET_CHALLENGER_V2.csv")
AUDIT = Path("outputs/audit/PORTFOLIO_BUDGET_CHALLENGER_V2.json")


def _text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _family(row: pd.Series) -> str:
    for field in ("official_benchmark", "category", "boursorama_sector", "sector"):
        value = _text(row.get(field)).upper()
        if value:
            return f"{field.upper()}:{value}"
    return f"ISIN:{_text(row.get('isin'))}"


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    budget = cfg["portfolio_budget"]
    frame = pd.read_csv(root / INPUT, sep=";", encoding="utf-8-sig", low_memory=False)
    source = frame.get("SIM_SELECTION_SOURCE", pd.Series("", index=frame.index)).astype(str)
    actual = source.str.contains(r"CI_LIGHT|HYPER_SELECTION|\bCI\b", regex=True) & ~source.str.contains("CI_POST_GATE_UNIVERSE")
    candidates = frame[actual & frame["CHALLENGER_RR_GATE"].astype(str).str.lower().eq("true")].copy()
    candidates = candidates.sort_values("CHALLENGER_RANK_SCORE_RISK_ADJUSTED", ascending=False, na_position="last")
    prices = {**load_cached_prices(root / "data/cache/actions"), **load_cached_prices(root / "data/cache/etf")}
    kept: list[int] = []
    families: set[str] = set()
    tickers: list[str] = []
    decisions: list[str] = []
    max_corrs: list[float | None] = []
    for index, row in candidates.iterrows():
        family = _family(row)
        ticker = _text(row.get("yahoo_ticker"))
        reason = "PASS"
        maximum_corr: float | None = None
        if family in families:
            reason = "REJECT_ECONOMIC_FAMILY_DUPLICATE"
        else:
            for incumbent in tickers:
                if ticker not in prices or incumbent not in prices:
                    continue
                pair = pd.concat([to_returns(prices[ticker]), to_returns(prices[incumbent])], axis=1).dropna().tail(int(budget["correlation_lookback_sessions"]))
                if len(pair) < int(budget["minimum_pair_observations"]):
                    continue
                corr = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
                maximum_corr = corr if maximum_corr is None else max(maximum_corr, corr)
                if corr >= float(budget["maximum_mean_correlation"]):
                    reason = "REJECT_CORRELATION_BUDGET"
                    break
        decisions.append(reason)
        max_corrs.append(maximum_corr)
        if reason == "PASS":
            kept.append(index); families.add(family); tickers.append(ticker)
    candidates["PORTFOLIO_BUDGET_DECISION"] = decisions
    candidates["PORTFOLIO_MAX_PAIR_CORRELATION"] = max_corrs
    candidates["PORTFOLIO_MAX_THEME_WEIGHT_PCT"] = 100.0 * float(budget["maximum_theme_weight"])
    candidates["PORTFOLIO_REAL_ORDER_ALLOWED"] = False
    (root / OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    payload = {"status": "SUCCESS", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "input": len(candidates), "kept": len(kept), "rejected": len(candidates) - len(kept), "beta_target": [budget["target_beta_min"], budget["target_beta_max"]], "beta_enforcement": "PENDING_RELIABLE_POSITION_WEIGHTS", "maximum_theme_weight": budget["maximum_theme_weight"], "reference_modified": False, "real_orders_enabled": False}
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
