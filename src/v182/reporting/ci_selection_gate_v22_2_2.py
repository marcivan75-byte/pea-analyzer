from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import json
import math

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2_1 as previous
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


def _master_metadata(root: Path) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    sources = (
        ("ACTION", [root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"]),
        ("ETF", [root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"]),
    )
    for asset, paths in sources:
        path = next((candidate for candidate in paths if candidate.exists()), None)
        frame = _read_csv(path) if path else pd.DataFrame()
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


def _markdown(selected: pd.DataFrame, rejected: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI Selection V22.2.2",
        "",
        f"Generated: {generated}",
        "",
        "Gates: selection score >=77; confidence >=66; Actions analyst-consensus upside >=20%. ETF are exempt from the analyst-consensus gate.",
        "",
        f"Selected: {len(selected)} | Rejected: {len(rejected)}",
        "",
    ]
    if selected.empty:
        lines.append("No instrument passes all V22.2.2 selection gates.")
    else:
        ordered = selected.sort_values(["CI_CONFIDENCE_SCORE_V22_2_1", "score"], ascending=[False, False])
        for _, row in ordered.iterrows():
            potential = _num(row.get("CI_POTENTIAL_UPSIDE_PCT"))
            potential_text = "NA" if potential is None else f"{potential:.1f}%"
            lines.append(
                f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} {_text(row.get('horizon'))} | "
                f"score={row.get('score')} | confidence={row.get('CI_CONFIDENCE_SCORE_V22_2_1')} | consensus/potential={potential_text} | "
                f"Boursorama={_text(row.get('CI_BOURSORAMA_URL'))} | Investing={_text(row.get('CI_INVESTING_URL'))}"
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

    metadata = _master_metadata(root)
    investing = _investing_map(root)
    statuses: list[str] = []
    reasons_out: list[str] = []
    b_urls: list[str] = []
    b_statuses: list[str] = []
    i_urls: list[str] = []
    i_statuses: list[str] = []

    for _, row in frame.iterrows():
        asset = _text(row.get("asset_class")).upper()
        isin = _text(row.get("isin"))
        meta = metadata.get((asset, isin), {})
        passed, reasons = _gate_row(row, cfg)
        b_url, b_status = _boursorama_link(asset, row, meta)
        i_url, i_status = _investing_link(isin, row, meta, investing)
        statuses.append("SELECTED" if passed else "REJECTED")
        reasons_out.append("PASS_ALL_GATES" if passed else "|".join(reasons))
        b_urls.append(b_url); b_statuses.append(b_status)
        i_urls.append(i_url); i_statuses.append(i_status)

    frame["CI_SELECTION_GATE_STATUS_V22_2_2"] = statuses
    frame["CI_SELECTION_GATE_REASON_V22_2_2"] = reasons_out
    frame["CI_BOURSORAMA_URL"] = b_urls
    frame["CI_BOURSORAMA_URL_STATUS"] = b_statuses
    frame["CI_INVESTING_URL"] = i_urls
    frame["CI_INVESTING_URL_STATUS"] = i_statuses
    frame["CI_MIN_SELECTION_SCORE_V22_2_2"] = float(cfg["selection_gate"]["minimum_selection_score"])
    frame["CI_MIN_CONFIDENCE_SCORE_V22_2_2"] = float(cfg["selection_gate"]["minimum_confidence_score"])
    frame["CI_ACTION_MIN_CONSENSUS_UPSIDE_V22_2_2"] = float(cfg["selection_gate"]["action_minimum_analyst_consensus_upside_pct"])
    frame["CI_REAL_ORDER_ALLOWED"] = False
    generated = datetime.now(timezone.utc).isoformat()
    frame["CI_V22_2_2_GENERATED_AT_UTC"] = generated

    selected = frame[frame["CI_SELECTION_GATE_STATUS_V22_2_2"].eq("SELECTED")].copy()
    rejected = frame[frame["CI_SELECTION_GATE_STATUS_V22_2_2"].eq("REJECTED")].copy()

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
        "version": "V22.2.2_CI_SELECTION_GATE",
        "generated_at_utc": generated,
        "input_candidates": int(len(frame)),
        "selected": int(len(selected)),
        "rejected": int(len(rejected)),
        "rejection_reasons": reason_counts,
        "thresholds": {
            "selection_score_min": float(cfg["selection_gate"]["minimum_selection_score"]),
            "confidence_score_min": float(cfg["selection_gate"]["minimum_confidence_score"]),
            "action_analyst_consensus_upside_min_pct": float(cfg["selection_gate"]["action_minimum_analyst_consensus_upside_pct"]),
            "etf_consensus_gate": False,
        },
        "links": {
            "selected_with_boursorama_url": int(selected["CI_BOURSORAMA_URL"].astype(str).str.startswith("http").sum()) if not selected.empty else 0,
            "selected_with_investing_url": int(selected["CI_INVESTING_URL"].astype(str).str.startswith("http").sum()) if not selected.empty else 0,
            "investing_direct_validated": int(selected["CI_INVESTING_URL_STATUS"].eq("VALIDATED_ISIN_MAP").sum()) if not selected.empty else 0,
        },
        "governance": {
            "base_scoring_formula_changed": False,
            "base_scores_overwritten": False,
            "effective_shortlist_gate_changed": True,
            "action_missing_consensus_fail_closed": True,
            "technical_52w_potential_cannot_satisfy_action_consensus_gate": True,
            "etf_consensus_rule_applies": False,
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
