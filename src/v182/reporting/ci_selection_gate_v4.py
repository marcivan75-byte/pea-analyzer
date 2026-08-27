from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import math

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2_1 as upstream
from v182.reporting import selected_source_enrichment as identity
from v182.reporting.selected_source_enrichment_v4 import enrich_selected_rows_v4
from v182.risk.beta_metrics import load_cached_prices, to_returns


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/WEEKLY_V4_GOVERNANCE.json")
UPSTREAM = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
OUTPUT = Path("outputs/committee_master/CI_SELECTION_V4.csv")
REJECTED = Path("outputs/committee_master/CI_SELECTION_REJECTED_V4.csv")
ALL_ROWS = Path("outputs/committee_master/CI_SELECTION_ALL_V4.csv")
MOBILE_MD = Path("outputs/mobile/ANDROID_CI_SELECTION_V4.md")
AUDIT = Path("outputs/audit/CI_SELECTION_GATE_V4.json")


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
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _load_config(root: Path) -> dict:
    return json.loads((root / CONFIG).read_text(encoding="utf-8"))


def _master_frames(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for paths in (
        (root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"),
        (root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"),
    ):
        path = next((candidate for candidate in paths if candidate.exists()), None)
        frames.append(_read_csv(path) if path else pd.DataFrame())
    return frames[0], frames[1]


def _attach_master_context(rows: pd.DataFrame, actions: pd.DataFrame, etfs: pd.DataFrame) -> pd.DataFrame:
    result = identity.attach_master_identity(rows, actions, etfs)
    if result.empty or etfs.empty or "isin" not in etfs:
        return result
    context_fields = [
        field for field in ("isin", "morningstar_rating", "official_benchmark", "category", "geo_exposure")
        if field in etfs
    ]
    ratings = etfs[context_fields].copy()
    ratings["isin"] = ratings["isin"].map(_text)
    ratings = ratings[ratings["isin"].ne("")].drop_duplicates("isin", keep="last")
    asset = result.get("asset_class", pd.Series("", index=result.index)).astype(str).str.upper()
    for field in context_fields:
        if field == "isin":
            continue
        mapping = dict(zip(ratings["isin"], ratings[field]))
        if field not in result:
            result[field] = pd.NA
        missing = result[field].isna() | result[field].astype(str).str.strip().isin({"", "nan", "None"})
        missing &= asset.eq("ETF")
        result.loc[missing, field] = result.loc[missing, "isin"].map(lambda value: mapping.get(_text(value), pd.NA))
    return result


def _etf_priority(row: pd.Series) -> tuple:
    signal_rank = {"STRONG_BUY": 2, "BUY": 1}.get(_text(row.get("CI_TRADINGVIEW_SIGNAL")).upper(), 0)
    return (
        _num(row.get("score")) or 0.0,
        _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1")) or 0.0,
        signal_rank,
        _num(row.get("morningstar_rating")) or 0.0,
        _text(row.get("isin")),
    )


def _explicit_etf_family(row: pd.Series) -> str:
    benchmark = _text(row.get("official_benchmark")).upper()
    return f"BENCHMARK:{benchmark}" if benchmark else ""


def _apply_etf_overlap_gate(
    selected: pd.DataFrame,
    prices: dict[str, pd.Series],
    *,
    threshold: float = 0.90,
    lookback: int = 126,
    minimum_observations: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    if selected.empty:
        return selected, pd.DataFrame(columns=selected.columns), []
    etfs = selected[selected["asset_class"].astype(str).str.upper().eq("ETF")]
    representatives = etfs.sort_values("isin").drop_duplicates("isin")
    ranked = sorted((row for _, row in representatives.iterrows()), key=_etf_priority, reverse=True)
    kept: list[pd.Series] = []
    decisions: list[dict[str, object]] = []
    for candidate in ranked:
        removed_isin = _text(candidate.get("isin"))
        candidate_ticker = _text(candidate.get("yahoo_ticker"))
        conflict = None
        for incumbent in kept:
            kept_isin = _text(incumbent.get("isin"))
            family = _explicit_etf_family(candidate)
            if family and family == _explicit_etf_family(incumbent):
                conflict = {"removed_isin": removed_isin, "kept_isin": kept_isin, "method": "EXPLICIT_ECONOMIC_FAMILY", "correlation": None}
                break
            incumbent_ticker = _text(incumbent.get("yahoo_ticker"))
            if candidate_ticker not in prices or incumbent_ticker not in prices:
                continue
            pair = pd.concat([to_returns(prices[candidate_ticker]), to_returns(prices[incumbent_ticker])], axis=1).dropna().tail(lookback)
            if len(pair) < minimum_observations:
                continue
            correlation = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if math.isfinite(correlation) and correlation >= threshold:
                conflict = {"removed_isin": removed_isin, "kept_isin": kept_isin, "method": "RETURN_CORRELATION_PROXY", "correlation": round(correlation, 6)}
                break
        if conflict:
            decisions.append(conflict)
        else:
            kept.append(candidate)
    removed_isins = {item["removed_isin"] for item in decisions}
    removed = selected[selected["isin"].map(_text).isin(removed_isins)].copy()
    by_isin = {item["removed_isin"]: item for item in decisions}
    for index, row in removed.iterrows():
        decision = by_isin[_text(row.get("isin"))]
        removed.at[index, "CI_SELECTION_GATE_STATUS_V4"] = "REJECTED_ETF_OVERLAP"
        removed.at[index, "CI_SELECTION_GATE_REASON_V4"] = f"ETF_OVERLAP_HIGHER_RANKED_PEER:{decision['kept_isin']}"
        removed.at[index, "CI_ETF_OVERLAP_METHOD_V4"] = decision["method"]
        removed.at[index, "CI_ETF_OVERLAP_CORRELATION_V4"] = decision["correlation"]
        removed.at[index, "CI_ETF_OVERLAP_KEPT_ISIN_V4"] = decision["kept_isin"]
    filtered = selected[~selected["isin"].map(_text).isin(removed_isins)].copy()
    return filtered, removed, decisions


def _base_gate(row: pd.Series, selection: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    score = _num(row.get("score"))
    confidence = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
    asset = _text(row.get("asset_class")).upper()
    if score is None:
        reasons.append("SELECTION_SCORE_MISSING")
    elif score < float(selection["minimum_selection_score"]):
        reasons.append("SELECTION_SCORE_LT_77")
    if confidence is None:
        reasons.append("CONFIDENCE_SCORE_MISSING")
    elif confidence < float(selection["minimum_confidence_score"]):
        reasons.append("CONFIDENCE_SCORE_LT_66")

    if asset == "ACTION":
        method = _text(row.get("CI_POTENTIAL_METHOD"))
        upside = _num(row.get("CI_POTENTIAL_UPSIDE_PCT"))
        if method not in set(selection["action_consensus_methods"]) or upside is None:
            reasons.append("ACTION_ANALYST_CONSENSUS_UPSIDE_MISSING")
        elif upside < float(selection["action_minimum_consensus_upside_pct"]):
            reasons.append("ACTION_ANALYST_CONSENSUS_UPSIDE_LT_20")
    elif asset == "ETF":
        rating = _num(row.get("morningstar_rating"))
        if rating is None:
            reasons.append("ETF_MORNINGSTAR_RATING_MISSING")
        elif rating < float(selection["etf_minimum_morningstar_stars"]):
            reasons.append("ETF_MORNINGSTAR_RATING_LT_3")
    else:
        reasons.append("ASSET_CLASS_UNSUPPORTED")
    return not reasons, reasons


def _boursorama_gate(row: pd.Series, selection: dict) -> tuple[str, str]:
    if _text(row.get("asset_class")).upper() == "ETF":
        return "PASS_CONTEXT_ONLY", "ETF_ANALYST_CONSENSUS_NOT_APPLICABLE"
    consensus = _text(row.get("boursorama_consensus")).upper()
    if not consensus:
        return "WAIT_SOURCE_MISSING", "BOURSORAMA_CONSENSUS_MISSING"
    if consensus in {str(value).upper() for value in selection["action_boursorama_positive"]}:
        return "PASS", f"BOURSORAMA_{consensus}"
    if consensus in {str(value).upper() for value in selection["action_boursorama_hold"]}:
        return "WAIT", f"BOURSORAMA_{consensus}"
    if consensus in {str(value).upper() for value in selection["action_boursorama_negative"]}:
        return "REJECT", f"BOURSORAMA_{consensus}"
    return "WAIT_SOURCE_MISSING", f"BOURSORAMA_UNKNOWN_{consensus}"


def _tradingview_signal(row: pd.Series, selection: dict) -> tuple[str, str]:
    horizon = _text(row.get("horizon")).upper()
    timeframe = selection["technical_horizon_mapping"].get(horizon, "")
    field = {
        "DAILY": "tradingview_daily_signal",
        "WEEKLY": "tradingview_weekly_signal",
        "MONTHLY": "tradingview_monthly_signal",
    }.get(timeframe, "")
    return (_text(row.get(field)).upper() if field else "", timeframe)


def _tradingview_gate(row: pd.Series, selection: dict) -> tuple[str, str, str, str]:
    signal, timeframe = _tradingview_signal(row, selection)
    if not timeframe:
        return "WAIT_SOURCE_MISSING", "NO_EXIT_SIGNAL", "TRADINGVIEW_HORIZON_UNSUPPORTED", signal
    if not signal:
        return "WAIT_SOURCE_MISSING", "NO_EXIT_SIGNAL", "TRADINGVIEW_SIGNAL_MISSING", signal
    if signal in {str(value).upper() for value in selection["technical_entry_positive"]}:
        state = "STRONG_CONFIRM" if signal == "STRONG_BUY" else "ENTRY_CONFIRM"
        return state, "NO_EXIT_SIGNAL", f"TRADINGVIEW_{timeframe}_{signal}", signal
    if signal in {str(value).upper() for value in selection["technical_neutral"]}:
        return "WAIT_NO_NEW_ENTRY", "NO_EXIT_SIGNAL", f"TRADINGVIEW_{timeframe}_NEUTRAL", signal
    if signal in {str(value).upper() for value in selection["technical_exit_review"]}:
        exit_state = "STRONG_EXIT_REVIEW_IF_HELD" if signal == "STRONG_SELL" else "EXIT_REVIEW_IF_HELD"
        return "BLOCK_ENTRY", exit_state, f"TRADINGVIEW_{timeframe}_{signal}", signal
    return "WAIT_SOURCE_MISSING", "NO_EXIT_SIGNAL", f"TRADINGVIEW_{timeframe}_UNKNOWN_{signal}", signal


def _effective_states(
    row: pd.Series,
    *,
    base_pass: bool,
    boursorama_gate: str,
    technical_entry_gate: str,
    technical_exit_gate: str,
) -> tuple[str, str, str]:
    exit_state = technical_exit_gate if technical_exit_gate != "NO_EXIT_SIGNAL" else "NO_EXIT_SIGNAL"
    if not base_pass:
        return "REJECTED_BASE", exit_state, "BASE_SELECTION_GATE_FAILED"
    if boursorama_gate == "REJECT":
        return "REJECTED_BOURSORAMA", exit_state, "BOURSORAMA_NEGATIVE_CONSENSUS"
    if boursorama_gate in {"WAIT", "WAIT_SOURCE_MISSING"}:
        return "WAIT", exit_state, "BOURSORAMA_NOT_ENTRY_READY"
    if technical_entry_gate == "BLOCK_ENTRY":
        return "WAIT", exit_state, "TRADINGVIEW_SELL_BLOCKS_ENTRY"
    upstream_state = _text(row.get("V22_2_1_ENTRY_STATE")).upper()
    if upstream_state != "READY_FOR_REVIEW":
        return "WAIT", exit_state, "UPSTREAM_TECHNICAL_OR_MARKET_TRIGGER_NOT_READY"
    if technical_entry_gate in {"ENTRY_CONFIRM", "STRONG_CONFIRM"}:
        return "READY_FOR_REVIEW", exit_state, "QUALITY_TRIGGER_AND_TRADINGVIEW_CONFIRMED"
    if technical_entry_gate == "WAIT_NO_NEW_ENTRY":
        return "WAIT", exit_state, "TRADINGVIEW_NEUTRAL"
    return "WAIT", exit_state, "TRADINGVIEW_SOURCE_MISSING"


def _selection_status(base_pass: bool, boursorama_gate: str) -> tuple[str, str]:
    if not base_pass:
        return "REJECTED", "BASE_GATE_FAILED"
    if boursorama_gate in {"PASS", "PASS_CONTEXT_ONLY"}:
        return "SELECTED", "BASE_AND_SOURCE_QUALITY_PASSED"
    if boursorama_gate in {"WAIT", "WAIT_SOURCE_MISSING"}:
        return "REVIEW", "BOURSORAMA_NOT_ENTRY_READY"
    return "REJECTED", "BOURSORAMA_NEGATIVE_CONSENSUS"


def _markdown(selected: pd.DataFrame, rejected: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI Selection V4 — Boursorama + TradingView",
        "",
        f"Generated: {generated}",
        "",
        "Boursorama contrôle la qualité Actions; TradingView confirme le timing 1D/1W/1M. Les ETF utilisent Morningstar et n'exigent aucun consensus analystes.",
        "",
        f"Selected: {len(selected)} | Review/rejected: {len(rejected)}",
        "",
    ]
    for _, row in selected.sort_values("score", ascending=False).iterrows():
        lines.append(
            f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} {_text(row.get('horizon'))} | "
            f"score={row.get('score')} | Boursorama={_text(row.get('boursorama_consensus')) or 'NA'} | "
            f"TradingView={_text(row.get('CI_TRADINGVIEW_SIGNAL')) or 'NA'} | entry={_text(row.get('CI_EFFECTIVE_ENTRY_STATE_V4'))}"
        )
    if selected.empty:
        lines.append("Aucun instrument ne satisfait les gates de qualité V4.")
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT, *, ensure_upstream: bool = True) -> dict:
    started = perf_counter()
    timings: dict[str, float] = {}
    cfg = _load_config(root)
    selection = cfg["selection"]
    upstream_path = root / UPSTREAM
    upstream_payload: dict = {}
    if ensure_upstream or not upstream_path.exists():
        phase = perf_counter()
        upstream_payload = upstream.run(root=root)
        timings["upstream_seconds"] = round(perf_counter() - phase, 6)
        if upstream_payload.get("status") != "SUCCESS":
            return {"status": "BLOCKED_UPSTREAM", "upstream": upstream_payload}
    phase = perf_counter()
    frame = _read_csv(upstream_path)
    timings["input_load_seconds"] = round(perf_counter() - phase, 6)
    if frame.empty:
        return {"status": "NO_CANDIDATES", "selected": 0, "rejected": 0}

    input_isins = set(frame["isin"].astype(str))
    phase = perf_counter()
    actions, etfs = _master_frames(root)
    frame = _attach_master_context(frame, actions, etfs)
    timings["master_context_seconds"] = round(perf_counter() - phase, 6)
    phase = perf_counter()
    frame, source_payload = enrich_selected_rows_v4(frame, root=root, profile="WEEKLY_V4")
    timings["source_collection_seconds"] = round(perf_counter() - phase, 6)
    if set(frame["isin"].astype(str)) != input_isins or len(frame) != len(_read_csv(upstream_path)):
        raise RuntimeError("SOURCE_LAYER_CHANGED_CANDIDATE_SET")

    phase = perf_counter()
    records: list[dict] = []
    for _, row in frame.iterrows():
        base_pass, base_reasons = _base_gate(row, selection)
        b_gate, b_reason = _boursorama_gate(row, selection)
        t_entry, t_exit, t_reason, t_signal = _tradingview_gate(row, selection)
        entry, exit_state, timing_reason = _effective_states(
            row,
            base_pass=base_pass,
            boursorama_gate=b_gate,
            technical_entry_gate=t_entry,
            technical_exit_gate=t_exit,
        )
        status, status_reason = _selection_status(base_pass, b_gate)
        reasons = base_reasons + ([] if status == "SELECTED" else [status_reason])
        records.append(
            {
                "CI_SELECTION_GATE_STATUS_V4": status,
                "CI_SELECTION_GATE_REASON_V4": "PASS_ALL_SELECTION_GATES" if not reasons else "|".join(reasons),
                "CI_BOURSORAMA_GATE_V4": b_gate,
                "CI_BOURSORAMA_REASON_V4": b_reason,
                "CI_TRADINGVIEW_SIGNAL": t_signal,
                "CI_TRADINGVIEW_ENTRY_GATE": t_entry,
                "CI_TRADINGVIEW_EXIT_GATE": t_exit,
                "CI_TRADINGVIEW_REASON": t_reason,
                "CI_EFFECTIVE_ENTRY_STATE_V4": entry,
                "CI_EFFECTIVE_EXIT_STATE_V4": exit_state,
                "CI_TIMING_REASON_V4": timing_reason,
            }
        )
    frame = pd.concat([frame.reset_index(drop=True), pd.DataFrame(records)], axis=1)
    timings["gate_evaluation_seconds"] = round(perf_counter() - phase, 6)
    frame["CI_REFERENCE_SCORE_CHANGED_BY_SOURCES"] = False
    frame["CI_SOURCE_CAN_CREATE_CANDIDATE"] = False
    frame["CI_REAL_ORDER_ALLOWED"] = False
    generated = datetime.now(timezone.utc).isoformat()
    frame["CI_V4_GENERATED_AT_UTC"] = generated
    selected = frame[frame["CI_SELECTION_GATE_STATUS_V4"].eq("SELECTED")].copy()
    rejected = frame[~frame["CI_SELECTION_GATE_STATUS_V4"].eq("SELECTED")].copy()
    overlap_cfg = selection.get("etf_overlap", {})
    overlap_decisions: list[dict[str, object]] = []
    if overlap_cfg.get("enabled", True) and not selected.empty:
        selected, overlap_rejected, overlap_decisions = _apply_etf_overlap_gate(
            selected,
            load_cached_prices(root / "data/cache/etf"),
            threshold=float(overlap_cfg.get("returns_correlation_threshold", 0.90)),
            lookback=int(overlap_cfg.get("lookback_sessions", 126)),
            minimum_observations=int(overlap_cfg.get("minimum_common_observations", 60)),
        )
        if not overlap_rejected.empty:
            rejected = pd.concat([rejected, overlap_rejected], ignore_index=True, sort=False)
            overlap_indexes = frame["isin"].map(_text).isin({item["removed_isin"] for item in overlap_decisions})
            frame.loc[overlap_indexes, "CI_SELECTION_GATE_STATUS_V4"] = "REJECTED_ETF_OVERLAP"
            frame.loc[overlap_indexes, "CI_SELECTION_GATE_REASON_V4"] = frame.loc[overlap_indexes, "isin"].map(
                lambda isin: f"ETF_OVERLAP_HIGHER_RANKED_PEER:{next(item['kept_isin'] for item in overlap_decisions if item['removed_isin'] == _text(isin))}"
            )

    phase = perf_counter()
    for relative in (OUTPUT, REJECTED, ALL_ROWS, MOBILE_MD, AUDIT):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(root / ALL_ROWS, sep=";", index=False, encoding="utf-8-sig")
    selected.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(root / REJECTED, sep=";", index=False, encoding="utf-8-sig")
    (root / MOBILE_MD).write_text(_markdown(selected, rejected, generated), encoding="utf-8")
    timings["output_write_seconds"] = round(perf_counter() - phase, 6)
    timings["total_seconds"] = round(perf_counter() - started, 6)
    payload = {
        "status": "SUCCESS",
        "version": "WEEKLY_V4_SELECTION_GATE_1",
        "generated_at_utc": generated,
        "input_candidates": int(len(frame)),
        "selected": int(len(selected)),
        "rejected_or_review": int(len(rejected)),
        "ready_for_review": int(frame["CI_EFFECTIVE_ENTRY_STATE_V4"].eq("READY_FOR_REVIEW").sum()),
        "etf_overlap_removed": len({item["removed_isin"] for item in overlap_decisions}),
        "etf_overlap_decisions": overlap_decisions,
        "exit_reviews": int(frame["CI_EFFECTIVE_EXIT_STATE_V4"].isin({"EXIT_REVIEW_IF_HELD", "STRONG_EXIT_REVIEW_IF_HELD"}).sum()),
        "source_context": source_payload,
        "timings_seconds": timings,
        "source_collection_passes": 1,
        "thresholds": selection,
        "governance": {
            "investing_enabled": False,
            "tradingview_timing_gate": True,
            "base_scores_overwritten": False,
            "reference_score_source_influence": 0.0,
            "source_can_create_candidate": False,
            "missing_source_interpreted_as_negative": False,
            "etf_analyst_consensus_required": False,
            "real_orders_enabled": False,
        },
        "outputs": {
            "selected_csv": OUTPUT.as_posix(),
            "rejected_csv": REJECTED.as_posix(),
            "all_rows_csv": ALL_ROWS.as_posix(),
            "mobile_markdown": MOBILE_MD.as_posix(),
        },
        "upstream": upstream_payload,
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = run(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] in {"SUCCESS", "NO_CANDIDATES"} else 2)


if __name__ == "__main__":
    main()
