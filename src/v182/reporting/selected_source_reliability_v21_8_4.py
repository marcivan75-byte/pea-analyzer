from __future__ import annotations

import pandas as pd

from v182.reporting import selected_source_enrichment as selected_source
from v182.sources import boursorama_selected_etf as boursorama_etf


VERSION = "SELECTED_SOURCE_RELIABILITY_V21_8_6_TRADINGVIEW"
ETF_RESERVED_SLOTS = 10
_ORIGINAL_SELECT = selected_source.select_preselected_rows
_ORIGINAL_ETF_PARSE = boursorama_etf.parse_etf_sheet_html
_INSTALLED = False


def _parse_etf_sheet_without_false_proof_overwrite(html: str) -> dict[str, object]:
    """Preserve a prior positive Boursorama Morningstar proof when another ETF page lacks the widget."""
    fields = dict(_ORIGINAL_ETF_PARSE(html) or {})
    if fields.get("boursorama_morningstar_rating_proof_valid") is False:
        fields.pop("boursorama_morningstar_rating_proof_valid", None)
    return fields


def _select_with_etf_reserve(
    rows: pd.DataFrame,
    *,
    max_unique_instruments: int = 40,
    accepted_statuses: tuple[str, ...] = ("BUY_CANDIDATE", "WATCH", "REVIEW", "SHADOW_CANDIDATE"),
) -> pd.DataFrame:
    if rows.empty or "isin" not in rows or "asset_class" not in rows:
        return _ORIGINAL_SELECT(rows, max_unique_instruments=max_unique_instruments, accepted_statuses=accepted_statuses)
    frame = rows.copy()
    if "decision" in frame:
        mask = frame["decision"].astype(str).str.upper().isin(accepted_statuses)
    elif "dynamic_decision" in frame:
        mask = frame["dynamic_decision"].astype(str).str.upper().isin(accepted_statuses)
    else:
        return _ORIGINAL_SELECT(rows, max_unique_instruments=max_unique_instruments, accepted_statuses=accepted_statuses)
    frame = frame[mask].copy()
    if frame.empty:
        return frame
    ordered = selected_source._score_sort(frame)
    limit = max(0, int(max_unique_instruments))
    if limit == 0:
        return frame.iloc[0:0].copy()
    asset = ordered["asset_class"].astype(str).str.upper()
    etf_ordered = ordered[asset.eq("ETF")]
    reserve = min(int(ETF_RESERVED_SLOTS), limit)
    chosen = list(dict.fromkeys(etf_ordered["isin"].astype(str).tolist()))[:reserve]
    for isin in ordered["isin"].astype(str).tolist():
        if isin not in chosen:
            chosen.append(isin)
        if len(chosen) >= limit:
            break
    return frame[frame["isin"].astype(str).isin(chosen)].copy()


def install() -> dict:
    global _INSTALLED
    if not _INSTALLED:
        boursorama_etf.parse_etf_sheet_html = _parse_etf_sheet_without_false_proof_overwrite
        selected_source.select_preselected_rows = _select_with_etf_reserve
        _INSTALLED = True
    return {
        "version": VERSION,
        "installed": True,
        "technical_provider": "TradingView",
        "tradingview_implementation": "TRADINGVIEW_TECHNICAL_V1_WEEKLY_VALIDATED",
        "investing_active": False,
        "etf_reserved_source_slots": ETF_RESERVED_SLOTS,
        "morningstar_positive_proof_preserved_across_course_and_risk_pages": True,
        "score_influence": 0.0,
        "decision_influence": False,
        "can_create_buy": False,
    }
