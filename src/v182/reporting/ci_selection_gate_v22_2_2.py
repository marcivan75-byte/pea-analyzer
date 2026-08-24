from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import json
import math

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2_1 as previous
from v182.reporting import selected_source_enrichment
from v182.sources.boursorama_public import action_urls, boursorama_code, etf_urls

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/CI_SELECTION_GATE_V22_2_2.json")
UPSTREAM = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
OUTPUT = Path("outputs/committee_master/CI_SELECTION_V22_2_2.csv")
REJECTED = Path("outputs/committee_master/CI_SELECTION_REJECTED_V22_2_2.csv")
MOBILE_MD = Path("outputs/mobile/ANDROID_CI_SELECTION_V22_2_2.md")
AUDIT = Path("outputs/audit/CI_SELECTION_GATE_V22_2_2.json")
INVESTING_MAP = Path("state/provenance/source_cache/INVESTING_URL_MAP_V1.json")


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
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _config(root: Path) -> dict:
    return json.loads((root / CONFIG).read_text(encoding="utf-8"))


def _master_frames(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for paths in (
        [root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"],
        [root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"],
    ):
        path = next((candidate for candidate in paths if candidate.exists()), None)
        frames.append(_read_csv(path) if path else pd.DataFrame())
    return frames[0], frames[1]


def _master_metadata(actions: pd.DataFrame, etfs: pd.DataFrame) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for asset, frame in (("ACTION", actions), ("ETF", etfs)):
        if frame.empty or "isin" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            isin = _text(row.get("isin"))
            if isin:
                result[(asset, isin)] = row.to_dict()
    return result


def _investing_map(root: Path) -> dict[str, str]:
    path = root / INVESTING_MAP
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    result: dict[str, str] = {}
    for isin, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        url = _text(entry.get("base_url"))
        validated_isin = _text(entry.get("validated_isin"))
        if url and validated_isin == str(isin) and url.startswith(("https://www.investing.com/", "https://fr.investing.com/")):
            result[str(isin)] = url
    return result


def _boursorama_link(asset: str, row: pd.Series, meta: dict) -> tuple[str, str]:
    combined = dict(meta)
    combined.update({key: value for key, value in row.items() if _text(value)})
    code = boursorama_code(combined, asset)
    if code:
        if asset == "ETF":
            return etf_urls(code)["course"], "DIRECT_DETERMINISTIC"
        return action_urls(code)["consensus"], "DIRECT_DETERMINISTIC_CONSENSUS"
    isin = _text(row.get("isin"))
    query = isin or _text(row.get("name"))
    return f"https://www.boursorama.com/recherche/?query={quote_plus(query)}", "SEARCH_FALLBACK"


def _investing_link(isin: str, row: pd.Series, meta: dict, validated: dict[str, str]) -> tuple[str, str]:
    direct = validated.get(isin)
    if direct:
        return direct, "VALIDATED_ISIN_MAP"
    explicit = _text(row.get("investing_url")) or _text(row.get("investing_technical_url")) or _text(meta.get("investing_url")) or _text(meta.get("investing_technical_url"))
    if explicit.startswith(("https://www.investing.com/", "https://fr.investing.com/")):
        base = explicit.split("?", 1)[0].rstrip("/")
        if base.endswith("-technical"):
            base = base[: -len("-technical")]
        return base, "MASTER_EXPLICIT"
    query = isin or _text(row.get("name"))
    return f"https://www.investing.com/search/?q={quote_plus(query)}", "SEARCH_FALLBACK"


def _gate_row(row: pd.Series, cfg: dict) -> tuple[bool, list[str]]:
    """Preserve the pre-existing V22.2.2 score/confidence/upside gate."""
    policy = cfg["selection_gate"]
    reasons: list[str] = []
    score = _num(row.get("score"))
    confidence = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
    asset = _text(row.get("asset_class")).upper()

    if score is None:
        reasons.append("SELECTION_SCORE_MISSING")
    elif score < float(policy["minimum_selection_score"]):
        reasons.append("SELECTION_SCORE_LT_77")

    if confidence is None:
        reasons.append("CONFIDENCE_SCORE_MISSING")
    elif confidence < float(policy["minimum_confidence_score"]):
        reasons.append("CONFIDENCE_SCORE_LT_66")

    if asset == "ACTION":
        methods = set(str(value) for value in cfg.get("action_consensus_methods", []))
        method = _text(row.get("CI_POTENTIAL_METHOD"))
        upside = _num(row.get("CI_POTENTIAL_UPSIDE_PCT"))
        if method not in methods or upside is None:
            reasons.append("ACTION_ANALYST_CONSENSUS_UPSIDE_MISSING")
        elif upside < float(policy["action_minimum_analyst_consensus_upside_pct"]):
            reasons.append("ACTION_ANALYST_CONSENSUS_UPSIDE_LT_20")

    return not reasons, reasons


def _boursorama_gate(row: pd.Series, cfg: dict) -> tuple[str, str]:
    """Use Boursorama as an Action shortlist quality gate, never a candidate generator."""
    asset = _text(row.get("asset_class")).upper()
    if asset != "ACTION":
        return "PASS_CONTEXT_ONLY", "ETF_ACTION_CONSENSUS_GATE_NOT_APPLICABLE"
    policy = cfg.get("boursorama_action_gate", {})
    if not bool(policy.get("enabled", True)):
        return "PASS_DISABLED", "BOURSORAMA_GATE_DISABLED"
    consensus = _text(row.get("boursorama_consensus")).upper()
    if not consensus:
        return "REVIEW_SOURCE_MISSING", "BOURSORAMA_CONSENSUS_MISSING"
    accepted = {str(value).upper() for value in policy.get("accepted_consensus", ["BUY", "STRONG_BUY"])}
    waiting = {str(value).upper() for value in policy.get("wait_consensus", ["HOLD"])}
    rejected = {str(value).upper() for value in policy.get("rejected_consensus", ["SELL", "STRONG_SELL"])}
    if consensus in accepted:
        return "PASS", f"BOURSORAMA_{consensus}"
    if consensus in waiting:
        return "WAIT", f"BOURSORAMA_{consensus}"
    if consensus in rejected:
        return "REJECT", f"BOURSORAMA_{consensus}"
    return "REVIEW_SOURCE_MISSING", f"BOURSORAMA_UNKNOWN_{consensus}"


def _investing_signal(row: pd.Series) -> tuple[str, float | None]:
    signal = _text(row.get("investing_horizon_signal")).upper()
    score = _num(row.get("investing_horizon_score"))
    if signal:
        return signal, score
    horizon = _text(row.get("horizon")).upper()
    field = {"TCT": "investing_daily_signal", "CT": "investing_weekly_signal", "MT": "investing_monthly_signal"}.get(horizon)
    score_field = {"TCT": "investing_daily_score", "CT": "investing_weekly_score", "MT": "investing_monthly_score"}.get(horizon)
    return (_text(row.get(field)).upper() if field else "", _num(row.get(score_field)) if score_field else None)


def _investing_gate(row: pd.Series, cfg: dict) -> tuple[str, str, str, float | None]:
    """Translate Investing multi-horizon signal into entry confirmation and exit review."""
    policy = cfg.get("investing_timing_gate", {})
    signal, score = _investing_signal(row)
    if not signal:
        return "WAIT_SOURCE_MISSING", "NO_EXIT_SIGNAL", "INVESTING_SIGNAL_MISSING", score
    entry_confirm = {str(value).upper() for value in policy.get("entry_confirm", ["BUY", "STRONG_BUY"])}
    neutral = {str(value).upper() for value in policy.get("neutral", ["NEUTRAL"])}
    exit_review = {str(value).upper() for value in policy.get("exit_review", ["SELL", "STRONG_SELL"])}
    if signal in entry_confirm:
        entry = "STRONG_CONFIRM" if signal == "STRONG_BUY" else "ENTRY_CONFIRM"
        return entry, "NO_EXIT_SIGNAL", f"INVESTING_{signal}", score
    if signal in neutral:
        return "WAIT_NO_NEW_ENTRY", "NO_EXIT_SIGNAL", "INVESTING_NEUTRAL", score
    if signal in exit_review:
        exit_gate = "STRONG_EXIT_REVIEW_IF_HELD" if signal == "STRONG_SELL" else "EXIT_REVIEW_IF_HELD"
        return "BLOCK_ENTRY", exit_gate, f"INVESTING_{signal}", score
    return "WAIT_SOURCE_MISSING", "NO_EXIT_SIGNAL", f"INVESTING_UNKNOWN_{signal}", score


def _effective_states(
    row: pd.Series,
    *,
    base_pass: bool,
    boursorama_gate: str,
    investing_entry_gate: str,
    investing_exit_gate: str,
) -> tuple[str, str, str]:
    """Combine independent quality and timing gates without changing the reference score."""
    if investing_exit_gate in {"EXIT_REVIEW_IF_HELD", "STRONG_EXIT_REVIEW_IF_HELD"}:
        exit_state = investing_exit_gate
    else:
        exit_state = "NO_EXIT_SIGNAL"

    if not base_pass:
        return "REJECTED_BASE", exit_state, "BASE_SELECTION_GATE_FAILED"
    if boursorama_gate == "REJECT":
        return "REJECTED_BOURSORAMA", exit_state, "BOURSORAMA_NEGATIVE_CONSENSUS"
    if boursorama_gate == "WAIT":
        return "WAIT", exit_state, "BOURSORAMA_HOLD_NOT_ENTRY_READY"
    if boursorama_gate == "REVIEW_SOURCE_MISSING":
        return "WAIT", exit_state, "BOURSORAMA_SOURCE_MISSING"

    if investing_entry_gate == "BLOCK_ENTRY":
        return "WAIT", exit_state, "INVESTING_SELL_BLOCKS_ENTRY"

    upstream = _text(row.get("V22_2_1_ENTRY_STATE")).upper()
    if upstream != "READY_FOR_REVIEW":
        return "WAIT", exit_state, "UPSTREAM_TECHNICAL_OR_MARKET_TRIGGER_NOT_READY"
    if investing_entry_gate in {"ENTRY_CONFIRM", "STRONG_CONFIRM"}:
        return "READY_FOR_REVIEW", exit_state, "QUALITY_TRIGGER_AND_INVESTING_CONFIRMED"
    if investing_entry_gate == "WAIT_NO_NEW_ENTRY":
        return "WAIT", exit_state, "INVESTING_NEUTRAL"
    return "WAIT", exit_state, "INVESTING_SOURCE_MISSING"


def _selection_status(base_pass: bool, boursorama_gate: str) -> tuple[str, str]:
    if not base_pass:
        return "REJECTED", "BASE_GATE_FAILED"
    if boursorama_gate in {"PASS", "PASS_CONTEXT_ONLY", "PASS_DISABLED"}:
        return "SELECTED", "BASE_AND_BOURSORAMA_QUALITY_PASSED"
    if boursorama_gate == "WAIT":
        return "REVIEW", "BOURSORAMA_HOLD"
    if boursorama_gate == "REVIEW_SOURCE_MISSING":
        return "REVIEW", "BOURSORAMA_SOURCE_MISSING"
    return "REJECTED", "BOURSORAMA_SELL_OR_STRONG_SELL"


def _markdown(selected: pd.DataFrame, rejected: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI Selection V22.2.2 — Boursorama + Investing",
        "",
        f"Generated: {generated}",
        "",
        "Boursorama gates Action shortlist quality; Investing confirms entry timing or raises exit review. Neither source changes the reference score or can create an order.",
        "",
        f"Selected: {len(selected)} | Non-selected/review: {len(rejected)}",
        "",
    ]
    if selected.empty:
        lines.append("No instrument passes all V22.2.2 selection-quality gates.")
    else:
        ordered = selected.sort_values(["CI_EFFECTIVE_ENTRY_STATE_V22_2_2", "CI_CONFIDENCE_SCORE_V22_2_1", "score"], ascending=[True, False, False])
        for _, row in ordered.iterrows():
            b_consensus = _text(row.get("boursorama_consensus")) or "NA"
            i_signal = _text(row.get("CI_INVESTING_SIGNAL")) or "NA"
            lines.append(
                f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} {_text(row.get('horizon'))} | "
                f"score={row.get('score')} | confidence={row.get('CI_CONFIDENCE_SCORE_V22_2_1')} | "
                f"Boursorama={b_consensus}/{_text(row.get('CI_BOURSORAMA_GATE'))} | Investing={i_signal} | "
                f"entry={_text(row.get('CI_EFFECTIVE_ENTRY_STATE_V22_2_2'))} | exit={_text(row.get('CI_EFFECTIVE_EXIT_STATE_V22_2_2'))}"
            )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT, *, ensure_upstream: bool = True) -> dict:
    cfg = _config(root)
    upstream_path = root / UPSTREAM
    upstream_payload: dict = {}
    if ensure_upstream or not upstream_path.exists():
        upstream_payload = previous.run(root=root)
        if upstream_payload.get("status") != "SUCCESS":
            return {"status": "BLOCKED_UPSTREAM", "upstream": upstream_payload}

    frame = _read_csv(upstream_path)
    if frame.empty:
        return {"status": "NO_CANDIDATES", "selected": 0, "rejected": 0}

    actions, etfs = _master_frames(root)
    frame = selected_source_enrichment.attach_master_identity(frame, actions, etfs)
    frame, source_payload = selected_source_enrichment.enrich_selected_rows(frame, root=root, profile="CI_V22_2_2")
    metadata = _master_metadata(actions, etfs)
    investing_urls = _investing_map(root)

    statuses: list[str] = []
    reasons_out: list[str] = []
    b_gates: list[str] = []
    b_reasons: list[str] = []
    i_signals: list[str] = []
    i_scores: list[float | None] = []
    i_entry_gates: list[str] = []
    i_exit_gates: list[str] = []
    effective_entries: list[str] = []
    effective_exits: list[str] = []
    timing_reasons: list[str] = []
    b_urls: list[str] = []
    b_statuses: list[str] = []
    i_urls: list[str] = []
    i_statuses: list[str] = []

    for _, row in frame.iterrows():
        asset = _text(row.get("asset_class")).upper()
        isin = _text(row.get("isin"))
        meta = metadata.get((asset, isin), {})
        base_pass, base_reasons = _gate_row(row, cfg)
        b_gate, b_reason = _boursorama_gate(row, cfg)
        i_entry, i_exit, i_reason, i_score = _investing_gate(row, cfg)
        i_signal, _ = _investing_signal(row)
        entry_state, exit_state, timing_reason = _effective_states(
            row,
            base_pass=base_pass,
            boursorama_gate=b_gate,
            investing_entry_gate=i_entry,
            investing_exit_gate=i_exit,
        )
        selection_status, selection_reason = _selection_status(base_pass, b_gate)
        reasons = list(base_reasons)
        if selection_status != "SELECTED":
            reasons.append(selection_reason)
        b_url, b_status = _boursorama_link(asset, row, meta)
        i_url, i_status = _investing_link(isin, row, meta, investing_urls)

        statuses.append(selection_status)
        reasons_out.append("PASS_ALL_SELECTION_GATES" if selection_status == "SELECTED" else "|".join(reasons))
        b_gates.append(b_gate); b_reasons.append(b_reason)
        i_signals.append(i_signal); i_scores.append(i_score)
        i_entry_gates.append(i_entry); i_exit_gates.append(i_exit)
        effective_entries.append(entry_state); effective_exits.append(exit_state); timing_reasons.append(timing_reason)
        b_urls.append(b_url); b_statuses.append(b_status); i_urls.append(i_url); i_statuses.append(i_status)

    frame["CI_SELECTION_GATE_STATUS_V22_2_2"] = statuses
    frame["CI_SELECTION_GATE_REASON_V22_2_2"] = reasons_out
    frame["CI_BOURSORAMA_GATE"] = b_gates
    frame["CI_BOURSORAMA_REASON"] = b_reasons
    frame["CI_INVESTING_SIGNAL"] = i_signals
    frame["CI_INVESTING_SCORE"] = i_scores
    frame["CI_INVESTING_ENTRY_GATE"] = i_entry_gates
    frame["CI_INVESTING_EXIT_GATE"] = i_exit_gates
    frame["CI_EFFECTIVE_ENTRY_STATE_V22_2_2"] = effective_entries
    frame["CI_EFFECTIVE_EXIT_STATE_V22_2_2"] = effective_exits
    frame["CI_TIMING_REASON"] = timing_reasons
    frame["CI_BOURSORAMA_URL"] = b_urls
    frame["CI_BOURSORAMA_URL_STATUS"] = b_statuses
    frame["CI_INVESTING_URL"] = i_urls
    frame["CI_INVESTING_URL_STATUS"] = i_statuses
    frame["CI_MIN_SELECTION_SCORE_V22_2_2"] = float(cfg["selection_gate"]["minimum_selection_score"])
    frame["CI_MIN_CONFIDENCE_SCORE_V22_2_2"] = float(cfg["selection_gate"]["minimum_confidence_score"])
    frame["CI_ACTION_MIN_CONSENSUS_UPSIDE_V22_2_2"] = float(cfg["selection_gate"]["action_minimum_analyst_consensus_upside_pct"])
    frame["CI_REFERENCE_SCORE_CHANGED_BY_SOURCES"] = False
    frame["CI_SOURCE_CAN_CREATE_CANDIDATE"] = False
    frame["CI_REAL_ORDER_ALLOWED"] = False
    generated = datetime.now(timezone.utc).isoformat()
    frame["CI_V22_2_2_GENERATED_AT_UTC"] = generated

    selected = frame[frame["CI_SELECTION_GATE_STATUS_V22_2_2"].eq("SELECTED")].copy()
    rejected = frame[~frame["CI_SELECTION_GATE_STATUS_V22_2_2"].eq("SELECTED")].copy()

    output = root / OUTPUT; rejected_path = root / REJECTED; md = root / MOBILE_MD; audit = root / AUDIT
    for path in (output, rejected_path, md, audit):
        path.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(rejected_path, sep=";", index=False, encoding="utf-8-sig")
    md.write_text(_markdown(selected, rejected, generated), encoding="utf-8")

    reason_counts: dict[str, int] = {}
    for reason_text in rejected.get("CI_SELECTION_GATE_REASON_V22_2_2", pd.Series(dtype=str)).astype(str):
        for reason in reason_text.split("|"):
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

    payload = {
        "status": "SUCCESS",
        "version": "V22.2.2_CI_SELECTION_SOURCE_TIMING_GATE",
        "generated_at_utc": generated,
        "input_candidates": int(len(frame)),
        "selected": int(len(selected)),
        "rejected_or_review": int(len(rejected)),
        "ready_for_review": int(frame["CI_EFFECTIVE_ENTRY_STATE_V22_2_2"].eq("READY_FOR_REVIEW").sum()),
        "exit_reviews": int(frame["CI_EFFECTIVE_EXIT_STATE_V22_2_2"].isin(["EXIT_REVIEW_IF_HELD", "STRONG_EXIT_REVIEW_IF_HELD"]).sum()),
        "boursorama_pass_actions": int(((frame["asset_class"].astype(str).str.upper() == "ACTION") & frame["CI_BOURSORAMA_GATE"].eq("PASS")).sum()),
        "boursorama_missing_actions": int(((frame["asset_class"].astype(str).str.upper() == "ACTION") & frame["CI_BOURSORAMA_GATE"].eq("REVIEW_SOURCE_MISSING")).sum()),
        "investing_entry_confirmations": int(frame["CI_INVESTING_ENTRY_GATE"].isin(["ENTRY_CONFIRM", "STRONG_CONFIRM"]).sum()),
        "investing_entry_blocks": int(frame["CI_INVESTING_ENTRY_GATE"].eq("BLOCK_ENTRY").sum()),
        "rejection_reasons": reason_counts,
        "source_context": source_payload,
        "thresholds": {
            "selection_score_min": float(cfg["selection_gate"]["minimum_selection_score"]),
            "confidence_score_min": float(cfg["selection_gate"]["minimum_confidence_score"]),
            "action_analyst_consensus_upside_min_pct": float(cfg["selection_gate"]["action_minimum_analyst_consensus_upside_pct"]),
            "boursorama_action_consensus_accepted": list(cfg["boursorama_action_gate"]["accepted_consensus"]),
            "investing_entry_confirm": list(cfg["investing_timing_gate"]["entry_confirm"]),
            "investing_exit_review": list(cfg["investing_timing_gate"]["exit_review"]),
            "etf_boursorama_action_consensus_gate": False,
        },
        "governance": {
            "base_scoring_formula_changed": False,
            "base_scores_overwritten": False,
            "reference_score_source_influence": 0.0,
            "post_selection_source_gate_influence": True,
            "source_can_create_candidate": False,
            "boursorama_selection_quality_gate": True,
            "investing_entry_exit_timing_gate": True,
            "missing_source_interpreted_as_negative": False,
            "technical_52w_potential_cannot_satisfy_action_consensus_gate": True,
            "etf_action_consensus_rule_applies": False,
            "wave09_reintroduced": False,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "real_orders_enabled": False,
        },
        "outputs": {
            "selected_csv": str(OUTPUT),
            "rejected_csv": str(REJECTED),
            "mobile_markdown": str(MOBILE_MD),
        },
        "upstream": upstream_payload,
    }
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))
