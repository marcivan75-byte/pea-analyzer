from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd


BLOCK = "BLOCK"
WARN = "WARN"
DOWNGRADE = "DOWNGRADE"
PASS = "PASS"

RANKING_COLUMNS = (
    "v21_gate_status",
    "v21_gate_reasons",
    "v21_gate_warnings",
    "v21_thesis_eligible",
)


def _finite(value: Any) -> float | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _category_bucket(category: Any) -> str:
    text = _norm(category)
    if any(token in text for token in ("THEME", "THEMAT", "SECTOR", "FACTOR", "SMART")):
        return "THEME"
    if any(token in text for token in ("WORLD", "ACWI", "REGION", "EURO", "USA", "EM", "BETA", "CORE")):
        return "BROAD"
    return "OTHER"


def _truthy_selected(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _norm(value)
    return text in {"1", "TRUE", "YES", "OUI", "BUY_CANDIDATE"}


@dataclass(frozen=True)
class GateResult:
    status: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    book: str = "THESIS_MT"

    @property
    def allowed(self) -> bool:
        return self.status != BLOCK


def apply_operational_gates(
    snapshot: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    stale_dynamic_days: float | None = None,
    book: str = "THESIS_MT",
) -> GateResult:
    """Current-snapshot vehicle gates. Never changes V20.8.1 scores.

    Missing fields warn; they are not imputed and they cannot create a signal.
    book=RANKING_TAG skips thesis text and does not require a BUY_CANDIDATE.
    """
    gates = config["operational_gates"]["vetoes"]
    reasons: list[str] = []
    warnings: list[str] = []

    pea = snapshot.get("pea_eligible", True)
    if pea is False or _norm(pea) in {"NO", "FALSE", "0", "NON"}:
        reasons.append("NOT_PEA_ELIGIBLE")

    aum = _finite(snapshot.get("fund_total_assets_eur_m", snapshot.get("aum_eur_m", snapshot.get("aum_m"))))
    aum_block = float(gates["aum_eur_m_below"]["threshold"])
    aum_watch = float(gates["aum_eur_m_watch"]["threshold"])
    if aum is None:
        warnings.append("AUM_MISSING")
    elif aum < aum_block:
        reasons.append(f"AUM_BELOW_{aum_block:.0f}M")
    elif aum < aum_watch:
        warnings.append(f"AUM_WATCH_BELOW_{aum_watch:.0f}M")

    ter = _finite(snapshot.get("ter_pct"))
    bucket = _category_bucket(snapshot.get("category", snapshot.get("etf_category")))
    if ter is None:
        warnings.append("TER_MISSING")
    elif bucket == "BROAD" and ter > float(gates["ter_pct_broad_above"]["threshold"]):
        warnings.append("TER_BROAD_ABOVE_CAP")
    elif bucket == "THEME" and ter > float(gates["ter_pct_theme_above"]["threshold"]):
        warnings.append("TER_THEME_ABOVE_CAP")

    sri = _finite(snapshot.get("risk_indicator", snapshot.get("sri")))
    if sri is not None and sri >= float(gates["sri_risk_indicator_ge"]["threshold"]):
        warnings.append("SRI_DOWNGRADE")

    if stale_dynamic_days is None:
        stale_dynamic_days = _finite(snapshot.get("staleness_days"))
    stale_limit = float(gates["stale_dynamic_data_calendar_days"]["threshold"])
    if stale_dynamic_days is not None and stale_dynamic_days > stale_limit:
        reasons.append("STALE_DYNAMIC_DATA")

    if book == "THESIS_MT" and snapshot.get("precision_selected") is False:
        reasons.append("NOT_PRECISION_BUY_CANDIDATE")

    if book == "THESIS_MT":
        thesis = str(snapshot.get("thesis") or "").strip()
        invalidation = str(snapshot.get("invalidation") or "").strip()
        if len(thesis) < 40:
            reasons.append("THESIS_MISSING")
        if len(invalidation) < 20:
            reasons.append("INVALIDATION_MISSING")

    if reasons:
        return GateResult(BLOCK, tuple(reasons), tuple(warnings), book)
    if warnings:
        return GateResult(WARN, (), tuple(warnings), book)
    return GateResult(PASS, (), (), book)


def annotate_ranking(
    ranking: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict]:
    """Tag a V20.8.1 ranking without mutating score_final or selected."""
    out = ranking.copy()
    for column in RANKING_COLUMNS:
        out[column] = ""

    statuses: list[str] = []
    thesis_ok = 0
    for idx, row in out.iterrows():
        payload = row.to_dict()
        selected = bool(row.get("selected", False)) or _norm(row.get("decision")) == "BUY_CANDIDATE"
        payload["precision_selected"] = selected
        result = apply_operational_gates(payload, config, book="RANKING_TAG")
        out.at[idx, "v21_gate_status"] = result.status
        out.at[idx, "v21_gate_reasons"] = "|".join(result.reasons)
        out.at[idx, "v21_gate_warnings"] = "|".join(result.warnings)
        eligible = selected and result.status != BLOCK
        out.at[idx, "v21_thesis_eligible"] = "YES" if eligible else "NO"
        if eligible:
            thesis_ok += 1
        statuses.append(result.status)

    summary = {
        "version": config.get("version"),
        "rows": int(len(out)),
        "gate_status_counts": {status: statuses.count(status) for status in (PASS, WARN, BLOCK)},
        "precision_selected": int((out["v21_thesis_eligible"] == "YES").sum()) if not out.empty else 0,
        "thesis_eligible_after_gates": thesis_ok,
        "score_influence": 0.0,
        "selected_column_unchanged": True,
        "live_orders_enabled": False,
    }
    return out, summary
