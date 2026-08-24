from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import json
import math
import unicodedata

import pandas as pd

from v182.reporting import selected_source_enrichment
from v182.sources.boursorama_public import action_urls, boursorama_code, etf_urls
from v182.sources.tradingview_technical import collect_technical_context_cached, technical_url

ROOT = Path(__file__).resolve().parents[3]
# CI LIGHT is deliberately upstream of the full V22.2.2 score/confidence selection gate.
# It therefore does not inherit the full-CI score>=77/confidence>=66 filters.
UPSTREAM = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
OUTPUT = Path("outputs/committee_master/CI_LIGHT_V22_2_3.csv")
REJECTED = Path("outputs/committee_master/CI_LIGHT_REJECTED_V22_2_3.csv")
EXCEL = Path("outputs/committee_master/CI_LIGHT_V22_2_3.xlsx")
MOBILE = Path("outputs/mobile/ANDROID_CI_LIGHT_V22_2_3.md")
AUDIT = Path("outputs/audit/CI_LIGHT_V22_2_3.json")
INVESTING_MAP = Path("state/provenance/source_cache/INVESTING_URL_MAP_V1.json")
TRADINGVIEW_CACHE = Path("state/provenance/source_cache/TRADINGVIEW_TECHNICAL_V1.json")

# Canonical Boursorama consensus values currently emitted by the governed parser.
# They correspond to the requested positive recommendation classes:
# STRONG_BUY -> ACHETER, BUY -> RENFORCER.
BOURSORAMA_CANONICAL_TO_LIGHT = {
    "STRONG_BUY": "ACHETER",
    "BUY": "RENFORCER",
    "ACHETER": "ACHETER",
    "RENFORCER": "RENFORCER",
}
INVESTING_POSITIVE = {"BUY", "STRONG_BUY"}
HORIZON_ORDER = {"TCT": 0, "CT": 1, "MT": 2}
MIN_ANALYSTS_EXCLUSIVE = 10
MIN_UPSIDE_EXCLUSIVE = 20.0


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _norm(value: object) -> str:
    text = _text(value).upper().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _num(value: object) -> float | None:
    try:
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _master_frames(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for paths in (
        [root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"],
        [root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"],
    ):
        path = next((candidate for candidate in paths if candidate.exists()), None)
        frames.append(_read(path) if path else pd.DataFrame())
    return frames[0], frames[1]


def _metadata(actions: pd.DataFrame, etfs: pd.DataFrame) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for asset, frame in (("ACTION", actions), ("ETF", etfs)):
        if frame.empty or "isin" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            isin = _text(row.get("isin"))
            if isin:
                out[(asset, isin)] = row.to_dict()
    return out


def _validated_investing_map(root: Path) -> dict[str, str]:
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
    out: dict[str, str] = {}
    for isin, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        url = _text(entry.get("base_url"))
        if _text(entry.get("validated_isin")) != str(isin):
            continue
        if url.startswith(("https://www.investing.com/", "https://fr.investing.com/")):
            base = url.split("?", 1)[0].rstrip("/")
            if base.endswith("-technical"):
                base = base[: -len("-technical")]
            out[str(isin)] = base + "-technical"
    return out


def _boursorama_url(asset: str, row: pd.Series, meta: dict) -> tuple[str, str]:
    # Prefer the exact URL that produced the observation when present.
    for field in ("CI_BOURSORAMA_URL", "boursorama_source_url", "source_url"):
        url = _text(row.get(field))
        if url.startswith("https://www.boursorama.com/"):
            return url, "SOURCE_OR_UPSTREAM"
    combined = dict(meta)
    combined.update({key: value for key, value in row.items() if _text(value)})
    code = boursorama_code(combined, asset)
    if code:
        if asset == "ETF":
            return etf_urls(code)["course"], "DIRECT_DETERMINISTIC"
        return action_urls(code)["consensus"], "DIRECT_DETERMINISTIC_CONSENSUS"
    query = _text(row.get("isin")) or _text(row.get("name"))
    return f"https://www.boursorama.com/recherche/?query={quote_plus(query)}", "SEARCH_FALLBACK"


def _investing_url(row: pd.Series, meta: dict, validated: dict[str, str]) -> tuple[str, str]:
    isin = _text(row.get("isin"))
    for field in ("CI_INVESTING_URL", "investing_source_url"):
        url = _text(row.get(field))
        if url.startswith(("https://www.investing.com/", "https://fr.investing.com/")):
            base = url.split("?", 1)[0].rstrip("/")
            return (base if base.endswith("-technical") else base + "-technical"), "SOURCE_OR_UPSTREAM"
    if isin in validated:
        return validated[isin], "VALIDATED_ISIN_MAP"
    explicit = _text(row.get("investing_technical_url")) or _text(row.get("investing_url")) or _text(meta.get("investing_technical_url")) or _text(meta.get("investing_url"))
    if explicit.startswith(("https://www.investing.com/", "https://fr.investing.com/")):
        base = explicit.split("?", 1)[0].rstrip("/")
        return (base if base.endswith("-technical") else base + "-technical"), "MASTER_EXPLICIT"
    query = isin or _text(row.get("name"))
    return f"https://www.investing.com/search/?q={quote_plus(query)}", "SEARCH_FALLBACK"


def _tradingview_url(row: pd.Series) -> tuple[str, str]:
    explicit = _text(row.get("tradingview_source_url"))
    if explicit.startswith("https://www.tradingview.com/symbols/"):
        return explicit, "COLLECTED_EXCHANGE_QUALIFIED"
    resolved = technical_url(row)
    if resolved is not None:
        return resolved[0], "DETERMINISTIC_EXCHANGE_TICKER"
    return "", "UNRESOLVED_FAIL_CLOSED"


def _attach_tradingview_context(frame: pd.DataFrame, root: Path) -> tuple[pd.DataFrame, dict]:
    selected = selected_source_enrichment.select_preselected_rows(frame, max_unique_instruments=40)
    result = collect_technical_context_cached(
        selected,
        root / TRADINGVIEW_CACHE,
        refresh_budget=40,
        ttl_hours=6.0,
        request_start_interval_seconds=1.0,
        max_workers=8,
    )
    context = selected_source_enrichment._pivot(result.observations)
    enriched = frame.copy()
    if not context.empty:
        keys = [column for column in ("isin", "asset_class", "horizon") if column in enriched and column in context]
        enriched = enriched.merge(context, on=keys, how="left")
    payload = {
        "status": "SUCCESS_WITH_CONTEXT" if result.observations else "SUCCESS_NO_SOURCE_DATA",
        "metrics": result.metrics,
        "failures": result.failures,
        "selected_only": True,
        "identity_fail_closed": True,
        "source_can_create_candidate": False,
    }
    return enriched, payload


def _boursorama_recommendation(row: pd.Series) -> tuple[str, str]:
    raw = ""
    for field in ("boursorama_consensus", "boursorama_analyst_recommendation", "boursorama_recommendation"):
        raw = _norm(row.get(field))
        if raw:
            break
    return BOURSORAMA_CANONICAL_TO_LIGHT.get(raw, ""), raw


def _evaluate(row: pd.Series) -> tuple[bool, list[str], dict[str, object]]:
    reasons: list[str] = []
    horizon = _norm(row.get("horizon"))
    recommendation, raw_recommendation = _boursorama_recommendation(row)
    analyst_count = _num(row.get("boursorama_n_analysts"))
    upside = _num(row.get("boursorama_target_upside_pct"))
    daily = _norm(row.get("tradingview_daily_signal"))
    weekly = _norm(row.get("tradingview_weekly_signal"))
    monthly = _norm(row.get("tradingview_monthly_signal"))

    if horizon not in HORIZON_ORDER:
        reasons.append("UNSUPPORTED_HORIZON")
    if not recommendation:
        reasons.append("BOURSORAMA_NOT_ACHETER_OR_RENFORCER")
    if analyst_count is None:
        reasons.append("BOURSORAMA_ANALYST_COUNT_MISSING")
    elif analyst_count <= MIN_ANALYSTS_EXCLUSIVE:
        reasons.append("BOURSORAMA_ANALYST_COUNT_NOT_GT_10")
    if upside is None:
        reasons.append("BOURSORAMA_TARGET_UPSIDE_MISSING")
    elif upside <= MIN_UPSIDE_EXCLUSIVE:
        reasons.append("BOURSORAMA_TARGET_UPSIDE_NOT_GT_20")

    signals = {"DAILY": daily, "WEEKLY": weekly, "MONTHLY": monthly}
    for timeframe, signal in signals.items():
        if not signal:
            reasons.append(f"TRADINGVIEW_{timeframe}_SIGNAL_MISSING")
        elif signal not in INVESTING_POSITIVE:
            reasons.append(f"TRADINGVIEW_{timeframe}_NOT_BUY_OR_STRONG_BUY")

    details = {
        "recommendation": recommendation,
        "raw_recommendation": raw_recommendation,
        "analyst_count": analyst_count,
        "upside": upside,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
    }
    return not reasons, reasons, details


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out["_h"] = out["horizon"].map(lambda value: HORIZON_ORDER.get(_norm(value), 99))
    out["_upside"] = pd.to_numeric(out.get("CI_LIGHT_BOURSORAMA_UPSIDE_PCT"), errors="coerce")
    out["_analysts"] = pd.to_numeric(out.get("CI_LIGHT_BOURSORAMA_ANALYSTS"), errors="coerce")
    return out.sort_values(["_h", "_upside", "_analysts"], ascending=[True, False, False], na_position="last").drop(columns=["_h", "_upside", "_analysts"])


def _export_columns(frame: pd.DataFrame) -> list[str]:
    wanted = [
        "name", "isin", "asset_class", "horizon",
        "CI_LIGHT_BOURSORAMA_RECOMMENDATION", "CI_LIGHT_BOURSORAMA_RAW_CONSENSUS",
        "CI_LIGHT_BOURSORAMA_ANALYSTS", "CI_LIGHT_BOURSORAMA_UPSIDE_PCT",
        "CI_LIGHT_TRADINGVIEW_DAILY", "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY",
        "CI_LIGHT_BOURSORAMA_URL", "CI_LIGHT_TRADINGVIEW_URL", "CI_LIGHT_TRADINGVIEW_SYMBOL", "CI_LIGHT_TRADINGVIEW_COLLECTED_AT_UTC",
        "score", "CI_CONFIDENCE_SCORE_V22_2_1", "CI_CONFIDENCE_SCORE_0_100",
        "CI_EFFECTIVE_ENTRY_STATE_V22_2_2", "V22_2_1_ENTRY_STATE",
        "CI_LIGHT_INCLUDED", "CI_LIGHT_REASON",
    ]
    return [column for column in wanted if column in frame.columns]


def _write_excel(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    all_cols = _export_columns(frame)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame[all_cols].to_excel(writer, sheet_name="ALL", index=False)
        for horizon in ("TCT", "CT", "MT"):
            subset = frame[frame["horizon"].map(_norm).eq(horizon)]
            subset[all_cols].to_excel(writer, sheet_name=horizon, index=False)


def _markdown(frame: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI LIGHT V22.2.3",
        "",
        f"Generated: {generated}",
        "",
        "Liste parallèle au CI complet. Aucun score, poids, seuil ou décision du CI complet n'est modifié.",
        "",
        "Admission LIGHT stricte: recommandation Boursorama ACHETER/RENFORCER, plus de 10 analystes, potentiel strictement supérieur à 20%, et TradingView BUY/STRONG_BUY simultanément en Daily, Weekly et Monthly.",
        "",
        "Les ETF sont soumis aux mêmes règles Boursorama; Morningstar n'est pas utilisé comme substitut de consensus analystes.",
        "",
    ]
    for horizon in ("TCT", "CT", "MT"):
        lines.extend([f"## {horizon}", ""])
        subset = frame[frame["horizon"].map(_norm).eq(horizon)]
        if subset.empty:
            lines.extend(["Aucun instrument ne satisfait simultanément les filtres LIGHT.", ""])
            continue
        for _, row in subset.iterrows():
            name = _text(row.get("name")) or _text(row.get("isin"))
            lines.extend([
                f"- {name} | {_text(row.get('asset_class'))} | Boursorama={_text(row.get('CI_LIGHT_BOURSORAMA_RECOMMENDATION'))} | analystes={row.get('CI_LIGHT_BOURSORAMA_ANALYSTS')} | potentiel={row.get('CI_LIGHT_BOURSORAMA_UPSIDE_PCT')}% | TradingView D/W/M={_text(row.get('CI_LIGHT_TRADINGVIEW_DAILY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_WEEKLY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_MONTHLY'))}",
                f"  - Boursorama: {_text(row.get('CI_LIGHT_BOURSORAMA_URL'))}",
                f"  - TradingView: {_text(row.get('CI_LIGHT_TRADINGVIEW_URL'))}",
            ])
        lines.append("")
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    source = root / UPSTREAM
    frame = _read(source)
    generated = datetime.now(timezone.utc).isoformat()
    outdir = root / "outputs/committee_master"
    auditdir = root / "outputs/audit"
    mobiledir = root / "outputs/mobile"
    for path in (outdir, auditdir, mobiledir):
        path.mkdir(parents=True, exist_ok=True)

    if frame.empty:
        payload = {"status": "NO_UPSTREAM_ROWS", "source": str(UPSTREAM), "selected": 0}
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    actions, etfs = _master_frames(root)
    frame = selected_source_enrichment.attach_master_identity(frame, actions, etfs)
    # Reuse the same selected-only governed caches. This can refresh only the bounded
    # CI pool and never expands into an all-universe Boursorama/Investing scrape.
    frame, source_payload = selected_source_enrichment.enrich_selected_rows(frame, root=root, profile="CI_LIGHT_V22_2_3")
    frame, tradingview_payload = _attach_tradingview_context(frame, root)
    meta = _metadata(actions, etfs)

    accepted_rows: list[dict] = []
    rejected_rows: list[dict] = []
    for _, row in frame.iterrows():
        accepted, reasons, details = _evaluate(row)
        record = row.to_dict()
        asset = _norm(row.get("asset_class")) or "ACTION"
        isin = _text(row.get("isin"))
        b_url, b_url_status = _boursorama_url(asset, row, meta.get((asset, isin), {}))
        tv_url, tv_url_status = _tradingview_url(row)
        record.update({
            "CI_LIGHT_BOURSORAMA_RECOMMENDATION": details["recommendation"],
            "CI_LIGHT_BOURSORAMA_RAW_CONSENSUS": details["raw_recommendation"],
            "CI_LIGHT_BOURSORAMA_ANALYSTS": details["analyst_count"],
            "CI_LIGHT_BOURSORAMA_UPSIDE_PCT": details["upside"],
            "CI_LIGHT_TRADINGVIEW_DAILY": details["daily"],
            "CI_LIGHT_TRADINGVIEW_WEEKLY": details["weekly"],
            "CI_LIGHT_TRADINGVIEW_MONTHLY": details["monthly"],
            "CI_LIGHT_BOURSORAMA_URL": b_url,
            "CI_LIGHT_BOURSORAMA_URL_STATUS": b_url_status,
            "CI_LIGHT_TRADINGVIEW_URL": tv_url,
            "CI_LIGHT_TRADINGVIEW_URL_STATUS": tv_url_status,
            "CI_LIGHT_TRADINGVIEW_SYMBOL": _text(row.get("tradingview_symbol")),
            "CI_LIGHT_TRADINGVIEW_COLLECTED_AT_UTC": _text(row.get("tradingview_collected_at_utc")),
            "CI_LIGHT_INCLUDED": bool(accepted),
            "CI_LIGHT_REASON": "PASS_ALL_LIGHT_GATES" if accepted else "|".join(reasons),
        })
        (accepted_rows if accepted else rejected_rows).append(record)

    selected = _ordered(pd.DataFrame(accepted_rows))
    rejected = pd.DataFrame(rejected_rows)
    selected.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(root / REJECTED, sep=";", index=False, encoding="utf-8-sig")
    _write_excel(selected, root / EXCEL)
    (root / MOBILE).write_text(_markdown(selected, generated), encoding="utf-8")

    by_horizon = {h: int(selected["horizon"].map(_norm).eq(h).sum()) if not selected.empty else 0 for h in ("TCT", "CT", "MT")}
    payload = {
        "status": "SUCCESS",
        "version": "CI_LIGHT_V22_2_3",
        "generated_at_utc": generated,
        "source": str(UPSTREAM),
        "input_rows": int(len(frame)),
        "selected": int(len(selected)),
        "rejected": int(len(rejected)),
        "selected_by_horizon": by_horizon,
        "filters": {
            "boursorama_recommendation": ["ACHETER", "RENFORCER"],
            "boursorama_analyst_count": ">10",
            "boursorama_target_upside_pct": ">20",
            "tradingview_daily": ["BUY", "STRONG_BUY"],
            "tradingview_weekly": ["BUY", "STRONG_BUY"],
            "tradingview_monthly": ["BUY", "STRONG_BUY"],
            "all_three_tradingview_timeframes_required": True,
        },
        "recommendation_mapping": {"STRONG_BUY": "ACHETER", "BUY": "RENFORCER"},
        "etf_same_boursorama_rules_as_actions": True,
        "morningstar_used_as_consensus_substitute": False,
        "source_can_create_candidate": False,
        "full_ci_changed": False,
        "full_ci_weighted_analysis_preserved": True,
        "full_ci_final_score_confidence_gate_inherited_by_light": False,
        "selection_score_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
        "source_context": source_payload,
        "tradingview_context": tradingview_payload,
        "outputs": [str(OUTPUT), str(REJECTED), str(EXCEL), str(MOBILE)],
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
