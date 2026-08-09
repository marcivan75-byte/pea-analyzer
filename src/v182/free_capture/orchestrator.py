from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from .core import CaptureStore, load_config, load_universe, priority_frame, write_csv, clean_text
from .openfigi_v3 import capture as capture_openfigi
from .official_delayed import capture as capture_official
from .twelvedata_free import capture as capture_twelve
from .alpha_free import capture as capture_alpha
from .marketstack_free import capture as capture_marketstack
from .derive_market import capture as derive_market

ROOT = Path(__file__).resolve().parents[3]
INPUT = Path(os.getenv("V211_INPUT", str(ROOT / "outputs/V21.0_ACTIONS_PEA_REFERENCE_MASTER.csv")))


def _nonempty(s: pd.Series) -> pd.Series:
    return ~s.astype(str).str.strip().str.lower().isin({"", "nan", "none", "<na>"})


def _materialize(base: pd.DataFrame, store: CaptureStore, cfg: dict) -> tuple[pd.DataFrame, dict]:
    out = base.copy()
    facts = store.facts()
    source_priority = {
        "INTERNAL_FROM_FREE_OHLCV": 1, "ALPHA_VANTAGE_FREE": 3, "TWELVEDATA_FREE": 4,
        "MARKETSTACK_FREE": 5, "OPENFIGI_V3": 2,
    }
    if not facts.empty:
        facts = facts.copy()
        facts["_p"] = facts["source"].map(source_priority).fillna(9)
        facts["_asof"] = pd.to_datetime(facts["as_of"], errors="coerce")
        best = facts.sort_values(["isin", "field", "_asof", "_p"], ascending=[True, True, False, True])
        best = best.drop_duplicates(["isin", "field"], keep="first")
        idx = out["isin"].astype(str)
        for field in sorted(set(best["field"].astype(str))):
            sub = best[best["field"].astype(str).eq(field)].set_index("isin")
            out[f"free_{field}"] = idx.map(sub["value"].to_dict())
            out[f"free_{field}_source"] = idx.map(sub["source"].to_dict())
            out[f"free_{field}_as_of"] = idx.map(sub["as_of"].to_dict())

    identity = store.identity()
    if not identity.empty:
        ident = identity.sort_values(["isin", "source"]).drop_duplicates("isin", keep="last").set_index("isin")
        idx = out["isin"].astype(str)
        for f in ["figi", "composite_figi", "share_class_figi", "ticker", "exchange", "mic", "resolution_status"]:
            out[f"free_identity_{f}"] = idx.map(ident[f].to_dict())

    metrics: dict[str, float] = {}
    groups = {
        "market": cfg["key_fields"]["market"],
        "fundamentals": cfg["key_fields"]["fundamentals"],
        "valuation": cfg["key_fields"]["valuation"],
        "prospective": cfg["key_fields"]["prospective"],
    }
    for group, fields in groups.items():
        per_row = pd.Series(0.0, index=out.index)
        for field in fields:
            base_ok = _nonempty(out[field]) if field in out else pd.Series(False, index=out.index)
            free_col = f"free_{field}"
            free_ok = _nonempty(out[free_col]) if free_col in out else pd.Series(False, index=out.index)
            per_row += (base_ok | free_ok).astype(float)
        frac = per_row / max(1, len(fields))
        out[f"free_capture_{group}_coverage_pct"] = (frac * 100).round(2)
        metrics[f"{group}_mean_field_coverage_pct"] = round(float(frac.mean() * 100), 2)
        metrics[f"{group}_adequate_rows_pct"] = round(float(frac.ge(0.75).mean() * 100), 2)
    return out, metrics


def _queues(prioritized: pd.DataFrame, store: CaptureStore) -> None:
    qroot = store.root / "queues"
    qroot.mkdir(parents=True, exist_ok=True)
    cols = [c for c in ["isin", "name", "country", "euronext_symbol", "euronext_mic", "yahoo_ticker", "score_mt", "score_lt", "free_capture_priority"] if c in prioritized]
    write_csv(prioritized.head(600)[cols], qroot / "V21.1_ESEF_ISSUER_QUEUE.csv", ["free_capture_priority"])
    public = prioritized.head(250)[cols].copy()
    public["boursorama_status"] = "PENDING_SELECTOR_VALIDATION"
    public["zonebourse_status"] = "PENDING_SELECTOR_VALIDATION"
    public["capture_policy"] = "TARGETED_ONLY_NO_MASS_SCRAPING"
    write_csv(public, qroot / "V21.1_PUBLIC_PROSPECTIVE_QUEUE.csv", ["free_capture_priority"])


def main() -> None:
    cfg = load_config()
    if not cfg.get("free_only") or cfg.get("execution_mode") != "RESEARCH_ONLY":
        raise RuntimeError("V21.1 FREE_ONLY safety gate")
    base = load_universe(INPUT)
    prioritized = priority_frame(base, cfg)
    store = CaptureStore(Path(os.getenv("V211_STORE", str(ROOT / cfg["cache"]["root"]))))
    store.root.mkdir(parents=True, exist_ok=True)

    results = {}
    results["openfigi"] = capture_openfigi(base, store, int(os.getenv("V211_OPENFIGI_MAX_REQUESTS", "30")))
    results["official_delayed"] = capture_official(store)
    results["twelvedata"] = capture_twelve(
        prioritized, store,
        max_symbols=int(os.getenv("V211_TWELVE_MAX_SYMBOLS", "40")),
        credit_guard=int(os.getenv("V211_TWELVE_CREDIT_GUARD", "760")),
    )
    results["marketstack"] = capture_marketstack(prioritized, store, int(os.getenv("V211_MARKETSTACK_MAX_SYMBOLS", "3")))
    results["alpha_vantage"] = capture_alpha(
        prioritized, store,
        max_symbols=int(os.getenv("V211_ALPHA_MAX_SYMBOLS", "10")),
        max_calls=int(os.getenv("V211_ALPHA_MAX_CALLS", "23")),
    )
    results["derived_market"] = derive_market(store)

    overlay, metrics = _materialize(base, store, cfg)
    _queues(prioritized, store)
    overlay_path = store.root / "V21.1_FREE_CAPTURE_OVERLAY.csv"
    write_csv(overlay, overlay_path, ["isin"])

    health = store.health()
    source_status = health.groupby("source").tail(1).set_index("source")["status"].to_dict() if not health.empty else {}
    identity = store.identity()
    identity_isins = set(identity["isin"].astype(str)) if not identity.empty else set()
    market = store.market()
    market_isins = set(market["isin"].astype(str)) if not market.empty else set()
    audit = {
        "passed": True,
        "version": cfg["version"],
        "execution": cfg["execution_mode"],
        "free_only": True,
        "rows": len(base),
        "unique_isin": base["isin"].astype(str).nunique(),
        "cache_identity_isin": len(identity_isins),
        "cache_market_isin": len(market_isins),
        "cache_market_rows": len(market),
        "cache_fact_rows": len(store.facts()),
        "metrics_pct_base_plus_free": metrics,
        "source_status": source_status,
        "results": results,
        "yfinance_called_by_v211": False,
        "paid_sources_called": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (store.root / "V21.1_FREE_CAPTURE_AUDIT.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("V21_1_FREE_CAPTURE_OK", json.dumps({k: audit[k] for k in ["rows", "cache_identity_isin", "cache_market_isin", "cache_market_rows", "cache_fact_rows", "metrics_pct_base_plus_free"]}))


if __name__ == "__main__":
    main()
