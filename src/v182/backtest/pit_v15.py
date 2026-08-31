"""PIT V15 data-qualification contract for the weekly PEA research process.

This module implements the CDC PIT V15 rules only. It deliberately does NOT run or
optimize portfolio performance. The sole purpose is to qualify genuine point-in-time
fundamental/consensus data before the >20% growth-potential gate can be backtested.

Locked rules implemented here:
- ISIN is the historical pivot; ticker-only rows are rejected.
- knowledge_date <= signal_date and publication_date <= signal_date.
- P1/P2 coverage must be >80% before V15 performance testing is authorized.
- P1 consensus requires >=3 analysts.
- >20% is the locked eligibility threshold; <=20% is rejected.
- P2 EPS fallback is binary evidence only; it is never converted into a fake % target.
- P3 internal model is last-resort and cannot be used to satisfy the P1/P2 coverage gate.
- historical mapping and delisted names must be preserved to avoid survivorship bias.
- no current consensus backfill is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import math
import pandas as pd
import numpy as np

MIN_COVERAGE_PCT = 80.0
TARGET_THRESHOLD_PCT = 20.0
MIN_ANALYSTS_P1 = 3
MAX_MEDIAN_LAG_GO_DAYS = 5.0
MAX_MEDIAN_LAG_HARD_NO_GO_DAYS = 7.0
MIN_MAPPING_OK_PCT = 95.0
MAX_CURRENT_CORRELATION = 0.30
ALLOWED_SOURCES = {"P1_FACTSET", "P2_EPS", "P3_MODEL"}
PRIMARY_SOURCES = {"P1_FACTSET", "P2_EPS"}

REQUIRED_PIT_COLUMNS = {
    "isin", "date_signal", "knowledge_date", "publication_date", "source"
}


class PITContractError(ValueError):
    """Raised when a row violates a locked PIT contract."""


@dataclass(frozen=True)
class PITRecord:
    isin: str
    date_signal: pd.Timestamp
    knowledge_date: pd.Timestamp
    publication_date: pd.Timestamp
    source: str
    ticker_pit: Optional[str] = None
    target_mean_pit: Optional[float] = None
    analyst_count: Optional[int] = None
    eps_revision_4w: Optional[float] = None
    eps_revision_13w: Optional[float] = None

    def validate(self) -> "PITRecord":
        if not isinstance(self.isin, str) or len(self.isin.strip()) != 12:
            raise PITContractError("ISIN must be a non-empty 12-character historical pivot")
        if self.source not in ALLOWED_SOURCES:
            raise PITContractError(f"unsupported PIT source: {self.source}")
        if pd.Timestamp(self.knowledge_date) > pd.Timestamp(self.date_signal):
            raise PITContractError("look-ahead: knowledge_date > date_signal")
        if pd.Timestamp(self.publication_date) > pd.Timestamp(self.date_signal):
            raise PITContractError("look-ahead: publication_date > date_signal")
        if self.source == "P1_FACTSET":
            if self.analyst_count is None or int(self.analyst_count) < MIN_ANALYSTS_P1:
                raise PITContractError("P1 consensus requires at least 3 analysts")
            if self.target_mean_pit is None or not math.isfinite(float(self.target_mean_pit)):
                raise PITContractError("P1 consensus requires a dated target_mean_pit")
        return self


class PITConnector:
    """Versioned deterministic reader over a supplied PIT table.

    No network access is performed here. Data acquisition is intentionally separated from
    qualification so that every supplied row can be audited and replayed deterministically.
    """

    version = "15.1.0"

    def __init__(self, pit_table: pd.DataFrame):
        self.table = normalize_pit_table(pit_table)

    def get_target_PIT(self, isin: str, date_signal) -> Optional[dict]:
        t = pd.Timestamp(date_signal).normalize()
        q = self.table[(self.table["isin"] == str(isin)) & (self.table["date_signal"] == t)].copy()
        if q.empty:
            return None
        q["source_rank"] = q["source"].map({"P1_FACTSET": 1, "P2_EPS": 2, "P3_MODEL": 3})
        q = q.sort_values(["source_rank", "knowledge_date"], ascending=[True, False])
        return q.iloc[0].drop(labels=["source_rank"]).to_dict()


def normalize_pit_table(df: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_PIT_COLUMNS - set(df.columns))
    if missing:
        raise PITContractError("missing PIT columns: " + ", ".join(missing))
    z = df.copy()
    if "ticker_pit" not in z.columns:
        z["ticker_pit"] = None
    if "target_mean_pit" not in z.columns:
        z["target_mean_pit"] = np.nan
    if "analyst_count" not in z.columns:
        z["analyst_count"] = np.nan
    if "eps_revision_4w" not in z.columns:
        z["eps_revision_4w"] = np.nan
    if "eps_revision_13w" not in z.columns:
        z["eps_revision_13w"] = np.nan
    for c in ["date_signal", "knowledge_date", "publication_date"]:
        z[c] = pd.to_datetime(z[c], errors="coerce").dt.normalize()
    if z[["date_signal", "knowledge_date", "publication_date"]].isna().any().any():
        raise PITContractError("invalid or missing PIT dates")
    z["isin"] = z["isin"].astype(str)
    if (~z["isin"].str.len().eq(12)).any():
        raise PITContractError("all PIT rows require a 12-character ISIN")
    if (~z["source"].isin(ALLOWED_SOURCES)).any():
        bad = sorted(set(z.loc[~z["source"].isin(ALLOWED_SOURCES), "source"].astype(str)))
        raise PITContractError("unsupported PIT source(s): " + ", ".join(bad))
    future = (z["knowledge_date"] > z["date_signal"]) | (z["publication_date"] > z["date_signal"])
    if future.any():
        raise PITContractError("look-ahead row(s) detected")
    p1 = z["source"].eq("P1_FACTSET")
    if (p1 & (pd.to_numeric(z["analyst_count"], errors="coerce") < MIN_ANALYSTS_P1)).any():
        raise PITContractError("P1 row with analyst_count < 3")
    if (p1 & pd.to_numeric(z["target_mean_pit"], errors="coerce").isna()).any():
        raise PITContractError("P1 row without target_mean_pit")
    if z.duplicated(["isin", "date_signal", "source"]).any():
        raise PITContractError("duplicate (isin, date_signal, source)")
    return z


def validate_mapping(mapping: pd.DataFrame) -> pd.DataFrame:
    required = {"isin", "ticker", "valid_from", "valid_to", "delisted", "last_trading_date"}
    missing = sorted(required - set(mapping.columns))
    if missing:
        raise PITContractError("missing historical mapping columns: " + ", ".join(missing))
    z = mapping.copy()
    z["valid_from"] = pd.to_datetime(z["valid_from"], errors="coerce")
    z["valid_to"] = pd.to_datetime(z["valid_to"], errors="coerce")
    if (~z["isin"].astype(str).str.len().eq(12)).any():
        raise PITContractError("mapping requires ISIN pivot")
    return z


def compute_potential_pct(target_mean_pit, close):
    if pd.isna(target_mean_pit) or pd.isna(close) or float(close) <= 0:
        return np.nan
    return (float(target_mean_pit) / float(close) - 1.0) * 100.0


def qualify_trades(trades: pd.DataFrame, pit_table: pd.DataFrame, mapping: Optional[pd.DataFrame] = None,
                   current_potential: Optional[pd.DataFrame] = None) -> tuple[pd.DataFrame, dict]:
    """Join 1 historical trade to the best admissible PIT row and compute CDC gates."""
    required = {"isin", "date_signal", "close"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise PITContractError("trade audit missing columns: " + ", ".join(missing))
    t = trades.copy()
    t["date_signal"] = pd.to_datetime(t["date_signal"], errors="coerce").dt.normalize()
    t["isin"] = t["isin"].astype(str)
    if (~t["isin"].str.len().eq(12)).any():
        raise PITContractError("ticker-only trade history rejected: ISIN required")

    p = normalize_pit_table(pit_table)
    p["source_rank"] = p["source"].map({"P1_FACTSET": 1, "P2_EPS": 2, "P3_MODEL": 3})
    p = p.sort_values(["isin", "date_signal", "source_rank", "knowledge_date"], ascending=[True, True, True, False])
    p = p.drop_duplicates(["isin", "date_signal"], keep="first")
    cols = ["isin", "date_signal", "source", "knowledge_date", "publication_date", "target_mean_pit",
            "analyst_count", "eps_revision_4w", "eps_revision_13w"]
    a = t.merge(p[cols], on=["isin", "date_signal"], how="left")
    a["lag_days"] = (a["date_signal"] - a["knowledge_date"]).dt.days
    a["potential_pct"] = [compute_potential_pct(x, c) for x, c in zip(a["target_mean_pit"], a["close"])]
    a["p2_eps_positive"] = a["source"].eq("P2_EPS") & (pd.to_numeric(a["eps_revision_4w"], errors="coerce") > 5.0)
    a["covered_primary"] = a["source"].isin(PRIMARY_SOURCES)
    a["potential_gt20_eligible"] = a["source"].eq("P1_FACTSET") & (a["potential_pct"] > TARGET_THRESHOLD_PCT)
    a["potential_le20_rejected"] = a["source"].eq("P1_FACTSET") & (a["potential_pct"] <= TARGET_THRESHOLD_PCT)
    a["mapping_valid"] = False

    if mapping is not None:
        m = validate_mapping(mapping)
        ok = []
        for _, r in a.iterrows():
            q = m[m["isin"].eq(r["isin"])]
            sd = r["date_signal"]
            valid = q[(q["valid_from"].isna() | (q["valid_from"] <= sd)) & (q["valid_to"].isna() | (q["valid_to"] >= sd))]
            ok.append(not valid.empty)
        a["mapping_valid"] = ok

    n = len(a)
    coverage = 100.0 * float(a["covered_primary"].mean()) if n else 0.0
    median_lag = float(a.loc[a["covered_primary"], "lag_days"].median()) if a["covered_primary"].any() else None
    mapping_ok = 100.0 * float(a["mapping_valid"].mean()) if n and mapping is not None else 0.0

    corr = None
    corr_ok = None
    if current_potential is not None and {"isin", "current_potential_pct"}.issubset(current_potential.columns):
        c = current_potential[["isin", "current_potential_pct"]].copy()
        c["isin"] = c["isin"].astype(str)
        x = a.merge(c, on="isin", how="inner")
        x = x[pd.to_numeric(x["potential_pct"], errors="coerce").notna() & pd.to_numeric(x["current_potential_pct"], errors="coerce").notna()]
        if len(x) >= 3:
            corr = float(x["potential_pct"].corr(pd.to_numeric(x["current_potential_pct"], errors="coerce")))
            corr_ok = bool(abs(corr) < MAX_CURRENT_CORRELATION)

    coverage_ok = coverage > MIN_COVERAGE_PCT
    lag_ok = median_lag is not None and median_lag <= MAX_MEDIAN_LAG_GO_DAYS
    lag_hard_fail = median_lag is not None and median_lag > MAX_MEDIAN_LAG_HARD_NO_GO_DAYS
    mapping_gate_ok = mapping is not None and mapping_ok > MIN_MAPPING_OK_PCT
    anti_leak_ok = corr_ok is True
    go = bool(coverage_ok and lag_ok and mapping_gate_ok and anti_leak_ok)

    report = {
        "status": "GO_DATA_QUALIFIED" if go else "NO_GO_DATA_COVERAGE",
        "trades": n,
        "primary_p1_p2_covered": int(a["covered_primary"].sum()),
        "primary_coverage_pct": round(coverage, 3),
        "coverage_gate_strict_gt_pct": MIN_COVERAGE_PCT,
        "median_lag_days": None if median_lag is None else round(median_lag, 3),
        "median_lag_go_le_days": MAX_MEDIAN_LAG_GO_DAYS,
        "lag_hard_fail_gt_days": MAX_MEDIAN_LAG_HARD_NO_GO_DAYS,
        "mapping_ok_pct": round(mapping_ok, 3),
        "mapping_gate_strict_gt_pct": MIN_MAPPING_OK_PCT,
        "pit_vs_current_corr": None if corr is None else round(corr, 6),
        "anti_leak_abs_corr_lt": MAX_CURRENT_CORRELATION,
        "gates": {
            "coverage_ok": coverage_ok,
            "lag_ok": lag_ok,
            "lag_hard_fail": lag_hard_fail,
            "mapping_ok": mapping_gate_ok,
            "anti_leak_ok": anti_leak_ok,
        },
        "performance_backtest_authorized": go,
        "locked_threshold_pct": TARGET_THRESHOLD_PCT,
        "p3_counts_for_coverage": False,
        "p2_eps_is_percent_potential": False,
    }
    return a, report


def write_audit_outputs(audit: pd.DataFrame, report: dict, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    audit.to_csv(outdir / "audit_v15.csv", index=False)
    (outdir / "coverage_v15.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# PIT V15 data qualification",
        "",
        f"Status: **{report['status']}**",
        f"P1/P2 coverage: **{report['primary_coverage_pct']}%** (GO requires >80%)",
        f"Median lag: **{report['median_lag_days']} days** (GO <=5; hard NO-GO >7)",
        f"Mapping OK: **{report['mapping_ok_pct']}%** (GO requires >95%)",
        f"PIT/current correlation: **{report['pit_vs_current_corr']}** (required |corr| <0.30)",
        "",
        "No V15 performance test is authorized unless every data gate is proven by artifacts.",
    ]
    (outdir / "PIT_V15_QUALIFICATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
