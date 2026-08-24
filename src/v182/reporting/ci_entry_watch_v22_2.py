from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

from v182.reporting import ci_entry_confidence_v22_2 as core

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2.csv")
MARKET_ORIENTATION = Path("outputs/committee_master/CI_MARKET_ORIENTATION_V22_2.csv")
MOBILE_MD = Path("outputs/mobile/ANDROID_CI_ENTRY_WATCH_V22_2.md")
AUDIT = Path("outputs/audit/CI_ENTRY_WATCH_V22_2.json")


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _number(value) -> float | None:
    try:
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _evidence_gaps(row: pd.Series) -> list[str]:
    gaps: list[str] = []
    reasons = _text(row.get("v22_2_entry_reasons"))
    if "MISSING" in reasons:
        gaps.append("ENTRY_TIMING_DATA")
    provenance = _number(row.get("v22_2_component_provenance_quality"))
    context = _number(row.get("v22_2_component_market_sector_context"))
    stability = _number(row.get("v22_2_component_temporal_stability"))
    if provenance is None or provenance < 60.0:
        gaps.append("PROVENANCE_QUALITY")
    if context is None or context <= 0.0:
        gaps.append("MARKET_SECTOR_CONTEXT")
    if stability is None or stability < 70.0:
        gaps.append("TEMPORAL_STABILITY")
    if _text(row.get("v22_2_entry_state")) != "READY_FOR_REVIEW":
        if "TCT_EXACT_T2" in reasons:
            gaps.append("TCT_T2_CONFIRMATION")
        elif _text(row.get("horizon")).upper() == "CT":
            gaps.append("CT_PRICE_MOMENTUM_VOLUME_TRIGGER")
        elif _text(row.get("horizon")).upper() == "MT":
            gaps.append("MT_CLOSE_TREND_MOMENTUM_CONFIRMATION")
    return list(dict.fromkeys(gaps))


def _next_check(row: pd.Series) -> tuple[str, str, str]:
    if _text(row.get("v22_2_entry_state")) == "READY_FOR_REVIEW":
        return "NOW", "CI_REVIEW_NOW", "Entry evidence is complete enough for governed CI review; no automatic order."
    horizon = _text(row.get("horizon")).upper()
    asset = _text(row.get("asset_class")).upper()
    if horizon == "TCT":
        return "PREOPEN_THEN_INTRADAY", "NEXT_SESSION", "Recheck exact T2 at preopen context and intraday confirmation."
    if asset == "ACTION" and horizon == "CT":
        return "PREOPEN_THEN_CLOSE", "NEXT_SESSION", "Recheck extension/gap at preopen and breakout/reclaim, momentum and volume at close."
    if horizon == "MT":
        return "CLOSE", "NEXT_TRADING_CLOSE", "Recheck close above SMA50>SMA200 with positive MT momentum."
    return "CLOSE", "NEXT_TRADING_CLOSE", "Recheck governed timing evidence."


def _market_context(root: Path) -> dict:
    path = root / MARKET_ORIENTATION
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    except (OSError, ValueError, pd.errors.ParserError):
        return {}
    if frame.empty:
        return {}
    row = frame.iloc[0]
    return {
        "global": _text(row.get("orientation_global")),
        "us": _text(row.get("orientation_us")),
        "europe": _text(row.get("orientation_europe")),
        "vix": _number(row.get("vix")),
        "cnn_fear_greed": _number(row.get("cnn_fear_greed")),
        "cnn_overheat_warning": bool(row.get("cnn_overheat_warning")) if pd.notna(row.get("cnn_overheat_warning")) else False,
        "vstoxx": _number(row.get("vstoxx")),
    }


def _markdown(frame: pd.DataFrame, generated: str, market: dict) -> str:
    lines = [
        "# CI Entry Watch V22.2",
        "",
        f"Generated: {generated}",
        "",
        "Selection scores and decisions are unchanged. READY_FOR_REVIEW is decision support only; no real order is generated.",
        "",
    ]
    if market:
        lines += [
            f"Market orientation (shadow): GLOBAL={market.get('global') or 'UNKNOWN'} | US={market.get('us') or 'UNKNOWN'} | EUROPE={market.get('europe') or 'UNKNOWN'}",
            f"VIX={market.get('vix')} | CNN Fear & Greed={market.get('cnn_fear_greed')} | VSTOXX={market.get('vstoxx')}",
            "",
        ]
    if frame.empty:
        lines.append("No monitored CI candidates.")
        return "\n".join(lines) + "\n"
    ready = frame[frame["v22_2_entry_state"].astype(str).eq("READY_FOR_REVIEW")]
    wait = frame[frame["v22_2_entry_state"].astype(str).eq("WAIT")]
    lines += [f"- Candidates: {len(frame)}", f"- Ready for CI review: {len(ready)}", f"- Waiting for trigger/evidence: {len(wait)}", ""]
    ordered = frame.sort_values(["v22_2_entry_state", "CI_CONFIDENCE_SCORE_0_100"], ascending=[True, False]).head(25)
    for _, row in ordered.iterrows():
        name = _text(row.get("name")) or _text(row.get("isin"))
        lines.append(
            f"- {name} | {_text(row.get('asset_class'))} {_text(row.get('horizon'))} | "
            f"{_text(row.get('v22_2_entry_state'))} | confidence {row.get('CI_CONFIDENCE_SCORE_0_100')} "
            f"({_text(row.get('CI_CONFIDENCE_LEVEL'))}) | next: {_text(row.get('CI_NEXT_CHECK_PHASE'))} | "
            f"gaps: {_text(row.get('CI_EVIDENCE_GAPS')) or 'NONE'}"
        )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    core_payload = core.run(root=root)
    if core_payload.get("status") != "SUCCESS":
        return {"status": "BLOCKED_CORE", "core": core_payload}
    src = root / core.OUTPUT
    frame = pd.read_csv(src, sep=";", encoding="utf-8-sig", low_memory=False) if src.exists() else pd.DataFrame()
    gaps_col: list[str] = []
    phases: list[str] = []
    whens: list[str] = []
    instructions: list[str] = []
    for _, row in frame.iterrows():
        gaps_col.append("|".join(_evidence_gaps(row)))
        phase, when, instruction = _next_check(row)
        phases.append(phase); whens.append(when); instructions.append(instruction)
    frame["CI_EVIDENCE_GAPS"] = gaps_col
    frame["CI_NEXT_CHECK_PHASE"] = phases
    frame["CI_NEXT_CHECK_WHEN"] = whens
    frame["CI_NEXT_VALIDATION_ACTION"] = instructions
    frame["CI_AUTOMATIC_ORDER_ALLOWED"] = False
    frame["CI_V22_2_SHADOW"] = True
    generated = datetime.now(timezone.utc).isoformat()
    frame["CI_WATCH_GENERATED_AT_UTC"] = generated

    market = _market_context(root)
    frame["CI_MARKET_ORIENTATION_GLOBAL"] = market.get("global") if market else ""
    frame["CI_MARKET_ORIENTATION_US"] = market.get("us") if market else ""
    frame["CI_MARKET_ORIENTATION_EUROPE"] = market.get("europe") if market else ""
    frame["CI_MARKET_VIX"] = market.get("vix") if market else pd.NA
    frame["CI_MARKET_CNN_FEAR_GREED"] = market.get("cnn_fear_greed") if market else pd.NA
    frame["CI_MARKET_VSTOXX"] = market.get("vstoxx") if market else pd.NA
    frame["CI_MARKET_CONTEXT_SHADOW_ONLY"] = True

    out = root / OUTPUT; md = root / MOBILE_MD; audit = root / AUDIT
    for path in (out, md, audit):
        path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    md.write_text(_markdown(frame, generated, market), encoding="utf-8")
    payload = {
        "status": "SUCCESS",
        "version": "V22.2_CI_ENTRY_WATCH",
        "generated_at_utc": generated,
        "candidate_rows": int(len(frame)),
        "ready_for_review": int((frame.get("v22_2_entry_state") == "READY_FOR_REVIEW").sum()) if not frame.empty else 0,
        "wait": int((frame.get("v22_2_entry_state") == "WAIT").sum()) if not frame.empty else 0,
        "rows_with_evidence_gaps": int(frame["CI_EVIDENCE_GAPS"].astype(str).ne("").sum()) if not frame.empty else 0,
        "market_orientation": market,
        "market_orientation_shadow_only": True,
        "broad_universe_network_collection_added": False,
        "selection_score_changed": False,
        "selection_decision_changed": False,
        "real_orders_enabled": False,
        "outputs": {"csv": str(OUTPUT), "market_orientation_csv": str(MARKET_ORIENTATION), "mobile_markdown": str(MOBILE_MD)},
        "core": core_payload,
    }
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = run(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload.get("status") == "SUCCESS" else 2)


if __name__ == "__main__":
    main()
