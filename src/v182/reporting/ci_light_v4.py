from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import math
import unicodedata

import pandas as pd

from v182.reporting.ci_light_source_context_v4 import collect_ci_light_context
from v182.risk.beta_metrics import load_cached_prices, to_returns
from v182.sources.boursorama_public import action_urls, boursorama_code, etf_urls
from v182.sources.tradingview_technical import technical_url


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/CI_LIGHT_V4.json")
UPSTREAM = Path("inputs/CI_LIGHT_UNIVERSE_V4.csv")
OUTPUT = Path("outputs/committee_master/CI_LIGHT_V4.csv")
REJECTED = Path("outputs/committee_master/CI_LIGHT_REJECTED_V4.csv")
EXCEL = Path("outputs/committee_master/CI_LIGHT_V4.xlsx")
MOBILE = Path("outputs/mobile/ANDROID_CI_LIGHT_V4.md")
AUDIT = Path("outputs/audit/CI_LIGHT_V4.json")
POSITIVE = {"BUY", "STRONG_BUY"}
HORIZON_ORDER = {"TCT": 0, "CT": 1, "MT": 2}
MIN_ANALYSTS_EXCLUSIVE = 10
MIN_UPSIDE_EXCLUSIVE = 20.0
MIN_ETF_MORNINGSTAR_FALLBACK = 4.0
RECOMMENDATION_LABELS = {
    "STRONG_BUY": "ACHETER",
    "BUY": "RENFORCER",
    "ACHETER": "ACHETER",
    "RENFORCER": "RENFORCER",
}


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
    value = _text(value).upper().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def _num(value: object) -> float | None:
    try:
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _norm(value)
    if normalized in {"TRUE", "1", "YES", "OUI"}:
        return True
    if normalized in {"FALSE", "0", "NO", "NON"}:
        return False
    return None


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _master_frames(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for enriched, base in (
        ("outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", "inputs/V18.2_PEA_ACTIONS_MASTER.csv"),
        ("outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", "inputs/V18.2_PEA_ETF_MASTER.csv"),
    ):
        path = root / enriched if (root / enriched).exists() else root / base
        frames.append(_read(path))
    return frames[0], frames[1]


def _attach_reference_context(rows: pd.DataFrame, actions: pd.DataFrame, etfs: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    identity_fields = (
        "isin", "yahoo_ticker", "long_name_yf", "boursorama_code", "morningstar_rating",
        "source", "source_name", "enrichment_as_of", "as_of_date",
    )
    for asset, master in (("ACTION", actions), ("ETF", etfs)):
        if master.empty or "isin" not in master:
            continue
        keep = [field for field in identity_fields if field in master]
        part = master[keep].copy().drop_duplicates("isin", keep="last")
        part["asset_class"] = asset
        parts.append(part)
    if not parts:
        return rows.copy()
    reference = pd.concat(parts, ignore_index=True, sort=False)
    result = rows.merge(reference, on=["isin", "asset_class"], how="left", suffixes=("", "_reference"))
    for field in identity_fields:
        reference_field = f"{field}_reference"
        if reference_field not in result:
            continue
        if field not in result:
            result[field] = result[reference_field]
        else:
            missing = result[field].isna() | result[field].astype(str).str.strip().isin({"", "nan", "None"})
            result.loc[missing, field] = result.loc[missing, reference_field]
        result = result.drop(columns=[reference_field])
    return result


def _recommendation(row: pd.Series) -> tuple[str, str]:
    raw = ""
    for field in ("boursorama_consensus", "boursorama_analyst_recommendation", "boursorama_recommendation"):
        raw = _norm(row.get(field))
        if raw:
            break
    return RECOMMENDATION_LABELS.get(raw, ""), raw


def _evaluate(row: pd.Series) -> tuple[bool, list[str], dict[str, object]]:
    reasons: list[str] = []
    asset = _norm(row.get("asset_class"))
    horizon = _norm(row.get("horizon"))
    recommendation, raw_recommendation = _recommendation(row)
    analyst_count = _num(row.get("boursorama_n_analysts"))
    upside = _num(row.get("boursorama_target_upside_pct"))
    morningstar = _num(row.get("morningstar_rating"))
    etf_pea_confirmed = _bool(row.get("boursorama_etf_pea_eligible_displayed"))
    signals = {
        "DAILY": _norm(row.get("tradingview_daily_signal")),
        "WEEKLY": _norm(row.get("tradingview_weekly_signal")),
        "MONTHLY": _norm(row.get("tradingview_monthly_signal")),
    }
    if horizon not in HORIZON_ORDER:
        reasons.append("UNSUPPORTED_HORIZON")
    if asset == "ACTION":
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
    elif asset == "ETF":
        if etf_pea_confirmed is not True:
            reasons.append("BOURSORAMA_ETF_PEA_ELIGIBILITY_NOT_CONFIRMED")
    else:
        reasons.append("ASSET_CLASS_UNSUPPORTED")
    fallback_used: list[str] = []
    for timeframe, signal in signals.items():
        if not signal:
            may_fallback = asset == "ETF" and timeframe in {"WEEKLY", "MONTHLY"}
            if may_fallback and morningstar is not None and morningstar >= MIN_ETF_MORNINGSTAR_FALLBACK:
                fallback_used.append(timeframe)
            elif may_fallback and morningstar is None:
                reasons.append(f"ETF_{timeframe}_MISSING_AND_MORNINGSTAR_RATING_MISSING")
            elif may_fallback:
                reasons.append(f"ETF_{timeframe}_MISSING_AND_MORNINGSTAR_RATING_LT_4")
            else:
                reasons.append(f"TRADINGVIEW_{timeframe}_SIGNAL_MISSING")
        elif signal not in POSITIVE:
            reasons.append(f"TRADINGVIEW_{timeframe}_NOT_BUY_OR_STRONG_BUY")
        elif asset == "ETF" and timeframe == "MONTHLY" and signal != "STRONG_BUY":
            reasons.append("ETF_TRADINGVIEW_MONTHLY_NOT_STRONG_BUY")
    return not reasons, reasons, {
        "recommendation": recommendation,
        "raw_recommendation": raw_recommendation,
        "analyst_count": analyst_count,
        "upside": upside,
        "morningstar": morningstar,
        "boursorama_etf_pea_confirmed": etf_pea_confirmed,
        "morningstar_fallback_used": fallback_used,
        **{key.lower(): value for key, value in signals.items()},
    }


def _boursorama_url(row: pd.Series) -> tuple[str, str]:
    explicit = _text(row.get("boursorama_source_url"))
    if explicit.startswith("https://www.boursorama.com/"):
        return explicit, "COLLECTED_EXACT"
    asset = _norm(row.get("asset_class"))
    code = boursorama_code(row.to_dict(), asset)
    if not code:
        return "", "UNRESOLVED_FAIL_CLOSED"
    if asset == "ETF":
        return etf_urls(code)["course"], "DIRECT_DETERMINISTIC"
    return action_urls(code)["consensus"], "DIRECT_DETERMINISTIC_CONSENSUS"


def _tradingview_url(row: pd.Series) -> tuple[str, str]:
    explicit = _text(row.get("tradingview_source_url"))
    if explicit.startswith("https://www.tradingview.com/symbols/"):
        return explicit, "COLLECTED_EXCHANGE_QUALIFIED"
    resolved = technical_url(row)
    return (resolved[0], "DETERMINISTIC_EXCHANGE_TICKER") if resolved else ("", "UNRESOLVED_FAIL_CLOSED")


def _export_columns(frame: pd.DataFrame) -> list[str]:
    fields = [
        "name", "isin", "asset_class", "horizon",
        "CI_LIGHT_BOURSORAMA_RECOMMENDATION", "CI_LIGHT_BOURSORAMA_ANALYSTS",
        "CI_LIGHT_BOURSORAMA_UPSIDE_PCT", "CI_LIGHT_MORNINGSTAR_RATING",
        "CI_LIGHT_TRADINGVIEW_DAILY", "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY",
        "CI_LIGHT_ETF_MORNINGSTAR_FALLBACK_USED", "CI_LIGHT_ETF_MORNINGSTAR_FALLBACK_HORIZONS",
        "CI_LIGHT_MORNINGSTAR_SOURCE", "CI_LIGHT_MORNINGSTAR_AS_OF",
        "CI_LIGHT_BOURSORAMA_URL", "CI_LIGHT_TRADINGVIEW_URL", "CI_LIGHT_TRADINGVIEW_SYMBOL",
        "CI_LIGHT_INCLUDED", "CI_LIGHT_REASON",
    ]
    return [field for field in fields if field in frame]


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["_horizon_order"] = result["horizon"].map(lambda value: HORIZON_ORDER.get(_norm(value), 99))
    result["_upside_order"] = pd.to_numeric(result.get("CI_LIGHT_BOURSORAMA_UPSIDE_PCT"), errors="coerce")
    result["_analyst_order"] = pd.to_numeric(result.get("CI_LIGHT_BOURSORAMA_ANALYSTS"), errors="coerce")
    return result.sort_values(
        ["_horizon_order", "_upside_order", "_analyst_order", "name"],
        ascending=[True, False, False, True], na_position="last",
    ).drop(
        columns=["_horizon_order", "_upside_order", "_analyst_order"]
    )


def _signal_rank(value: object) -> int:
    return {"STRONG_BUY": 2, "BUY": 1}.get(_norm(value), 0)


def _etf_priority(row: pd.Series) -> tuple:
    return (
        _signal_rank(row.get("CI_LIGHT_TRADINGVIEW_WEEKLY")),
        _signal_rank(row.get("CI_LIGHT_TRADINGVIEW_MONTHLY")),
        _signal_rank(row.get("CI_LIGHT_TRADINGVIEW_DAILY")),
        _num(row.get("CI_LIGHT_MORNINGSTAR_RATING")) or 0.0,
        _text(row.get("isin")),
    )


def _explicit_etf_family(row: pd.Series) -> str:
    for field in ("official_benchmark", "benchmark", "index_name", "economic_family"):
        value = _norm(row.get(field))
        if value:
            return f"BENCHMARK:{value}"
    return ""


def _apply_etf_overlap_gate(
    selected: pd.DataFrame,
    prices: dict[str, pd.Series],
    threshold: float = 0.90,
    lookback: int = 126,
    minimum_observations: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    if selected.empty:
        return selected, pd.DataFrame(columns=selected.columns), []
    etfs = selected[selected["asset_class"].map(_norm).eq("ETF")]
    if etfs.empty:
        return selected, pd.DataFrame(columns=selected.columns), []
    representatives = etfs.sort_values("isin").drop_duplicates("isin").copy()
    ranked = sorted((row for _, row in representatives.iterrows()), key=_etf_priority, reverse=True)
    kept: list[pd.Series] = []
    decisions: list[dict[str, object]] = []
    removed_isins: set[str] = set()
    for candidate in ranked:
        candidate_isin = _text(candidate.get("isin"))
        candidate_ticker = _text(candidate.get("yahoo_ticker"))
        candidate_family = _explicit_etf_family(candidate)
        conflict: dict[str, object] | None = None
        for incumbent in kept:
            incumbent_isin = _text(incumbent.get("isin"))
            incumbent_family = _explicit_etf_family(incumbent)
            if candidate_family and candidate_family == incumbent_family:
                conflict = {"method": "EXPLICIT_ECONOMIC_FAMILY", "correlation": None, "kept_isin": incumbent_isin}
                break
            incumbent_ticker = _text(incumbent.get("yahoo_ticker"))
            if candidate_ticker not in prices or incumbent_ticker not in prices:
                continue
            pair = pd.concat(
                [to_returns(prices[candidate_ticker]), to_returns(prices[incumbent_ticker])], axis=1
            ).dropna().tail(lookback)
            if len(pair) < minimum_observations:
                continue
            correlation = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if math.isfinite(correlation) and correlation >= threshold:
                conflict = {"method": "RETURN_CORRELATION_PROXY", "correlation": round(correlation, 6), "kept_isin": incumbent_isin}
                break
        if conflict is None:
            kept.append(candidate)
            continue
        removed_isins.add(candidate_isin)
        decisions.append({"removed_isin": candidate_isin, **conflict})
    if not removed_isins:
        return selected, pd.DataFrame(columns=selected.columns), decisions
    removed = selected[selected["isin"].map(_text).isin(removed_isins)].copy()
    decision_by_isin = {item["removed_isin"]: item for item in decisions}
    for index, row in removed.iterrows():
        decision = decision_by_isin[_text(row.get("isin"))]
        removed.at[index, "CI_LIGHT_INCLUDED"] = False
        removed.at[index, "CI_LIGHT_REASON"] = f"ETF_OVERLAP_HIGHER_RANKED_PEER:{decision['kept_isin']}"
        removed.at[index, "CI_LIGHT_ETF_OVERLAP_METHOD"] = decision["method"]
        removed.at[index, "CI_LIGHT_ETF_OVERLAP_CORRELATION"] = decision["correlation"]
        removed.at[index, "CI_LIGHT_ETF_OVERLAP_KEPT_ISIN"] = decision["kept_isin"]
    filtered = selected[~selected["isin"].map(_text).isin(removed_isins)].copy()
    return filtered, removed, decisions


def _write_excel(frame: pd.DataFrame, path: Path) -> None:
    columns = _export_columns(frame)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame[columns].to_excel(writer, sheet_name="ALL", index=False)
        for horizon in HORIZON_ORDER:
            frame[frame["horizon"].map(_norm).eq(horizon)][columns].to_excel(
                writer, sheet_name=horizon, index=False
            )


def _markdown(frame: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI Light V4",
        "",
        f"Generated: {generated}",
        "",
        "Processus autonome. Actions: Boursorama positif, >10 analystes et >20% de potentiel. ETF: fiche Boursorama exacte avec éligibilité PEA confirmée. Tous: TradingView BUY/STRONG_BUY en 1D, 1W et 1M. Pour un ETF seulement, Morningstar 4 ou 5 étoiles remplace un signal 1W/1M absent, jamais un signal neutre ou négatif.",
        "",
    ]
    for horizon in HORIZON_ORDER:
        lines.extend([f"## {horizon}", ""])
        subset = frame[frame["horizon"].map(_norm).eq(horizon)]
        if subset.empty:
            lines.extend(["Aucun instrument.", ""])
        for _, row in subset.iterrows():
            quality = f"Boursorama={_text(row.get('CI_LIGHT_BOURSORAMA_RECOMMENDATION'))}"
            lines.append(
                f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} | {quality} | "
                f"TV={_text(row.get('CI_LIGHT_TRADINGVIEW_DAILY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_WEEKLY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_MONTHLY'))}"
            )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    timings: dict[str, float] = {}
    phase = perf_counter()
    frame = _read(root / UPSTREAM)
    config_path = root / CONFIG
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    timings["upstream_load_seconds"] = round(perf_counter() - phase, 6)
    generated = datetime.now(timezone.utc).isoformat()
    for relative in (OUTPUT, REJECTED, EXCEL, MOBILE, AUDIT):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        payload = {"status": "NO_UPSTREAM_ROWS", "source": UPSTREAM.as_posix(), "selected": 0}
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    phase = perf_counter()
    actions, etfs = _master_frames(root)
    frame = _attach_reference_context(frame, actions, etfs)
    forbidden = {"decision", "dynamic_decision", "selected_rank", "CI_CONFIDENCE_SCORE_V22_2_1", "score"}
    contaminated = sorted(forbidden.intersection(frame.columns))
    if contaminated:
        raise RuntimeError(f"CI_LIGHT_INPUT_CONTAINS_FORBIDDEN_CI_FIELDS:{','.join(contaminated)}")
    frame, source_payload = collect_ci_light_context(frame, root)
    timings["context_prepare_seconds"] = round(perf_counter() - phase, 6)

    phase = perf_counter()
    accepted_rows: list[dict] = []
    rejected_rows: list[dict] = []
    for _, row in frame.iterrows():
        accepted, reasons, details = _evaluate(row)
        b_url, b_status = _boursorama_url(row)
        tv_url, tv_status = _tradingview_url(row)
        record = row.to_dict()
        record.update(
            {
                "CI_LIGHT_BOURSORAMA_RECOMMENDATION": details["recommendation"],
                "CI_LIGHT_BOURSORAMA_ANALYSTS": details["analyst_count"],
                "CI_LIGHT_BOURSORAMA_UPSIDE_PCT": details["upside"],
                "CI_LIGHT_MORNINGSTAR_RATING": details["morningstar"],
                "CI_LIGHT_ETF_MORNINGSTAR_FALLBACK_USED": bool(details["morningstar_fallback_used"]),
                "CI_LIGHT_ETF_MORNINGSTAR_FALLBACK_HORIZONS": "|".join(details["morningstar_fallback_used"]),
                "CI_LIGHT_MORNINGSTAR_SOURCE": _text(row.get("source_name")) or _text(row.get("source")),
                "CI_LIGHT_MORNINGSTAR_AS_OF": _text(row.get("enrichment_as_of")) or _text(row.get("as_of_date")),
                "CI_LIGHT_TRADINGVIEW_DAILY": details["daily"],
                "CI_LIGHT_TRADINGVIEW_WEEKLY": details["weekly"],
                "CI_LIGHT_TRADINGVIEW_MONTHLY": details["monthly"],
                "CI_LIGHT_BOURSORAMA_URL": b_url,
                "CI_LIGHT_BOURSORAMA_URL_STATUS": b_status,
                "CI_LIGHT_TRADINGVIEW_URL": tv_url,
                "CI_LIGHT_TRADINGVIEW_URL_STATUS": tv_status,
                "CI_LIGHT_TRADINGVIEW_SYMBOL": _text(row.get("tradingview_symbol")),
                "CI_LIGHT_INCLUDED": accepted,
                "CI_LIGHT_REASON": "PASS_ALL_LIGHT_GATES" if accepted else "|".join(reasons),
            }
        )
        (accepted_rows if accepted else rejected_rows).append(record)
    timings["evaluation_seconds"] = round(perf_counter() - phase, 6)
    phase = perf_counter()
    rejected = pd.DataFrame(rejected_rows)
    selected = _ordered(pd.DataFrame(accepted_rows))
    overlap_config = config.get("etf_overlap", {})
    overlap_decisions: list[dict[str, object]] = []
    if overlap_config.get("enabled", True) and not selected.empty:
        prices = load_cached_prices(root / "data/cache/etf")
        selected, overlap_rejected, overlap_decisions = _apply_etf_overlap_gate(
            selected,
            prices,
            threshold=float(overlap_config.get("returns_correlation_threshold", 0.90)),
            lookback=int(overlap_config.get("lookback_sessions", 126)),
            minimum_observations=int(overlap_config.get("minimum_common_observations", 60)),
        )
        if not overlap_rejected.empty:
            rejected = pd.concat([rejected, overlap_rejected], ignore_index=True, sort=False)
    if selected.empty:
        selected = pd.DataFrame(columns=rejected.columns if not rejected.empty else frame.columns)
    selected.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(root / REJECTED, sep=";", index=False, encoding="utf-8-sig")
    excel_status = "SUCCESS"
    try:
        _write_excel(selected, root / EXCEL)
    except ModuleNotFoundError as exc:
        excel_status = f"SKIPPED_OPTIONAL_DEPENDENCY:{exc.name}"
    (root / MOBILE).write_text(_markdown(selected, generated), encoding="utf-8")
    timings["output_write_seconds"] = round(perf_counter() - phase, 6)
    timings["total_seconds"] = round(perf_counter() - started, 6)
    payload = {
        "status": "SUCCESS",
        "version": "CI_LIGHT_V4_2_INDEPENDENT",
        "generated_at_utc": generated,
        "input_rows": int(len(frame)),
        "selected": int(len(selected)),
        "rejected": int(len(rejected)),
        "selected_by_horizon": {
            horizon: int(selected["horizon"].map(_norm).eq(horizon).sum()) for horizon in HORIZON_ORDER
        },
        "universe_source": UPSTREAM.as_posix(),
        "ci_output_dependency": False,
        "ci_selection_used": False,
        "ci_context_reused": False,
        "ci_scores_used": False,
        "action_rules": {"boursorama_positive": True, "analysts": ">10", "upside_pct": ">20"},
        "etf_rules": {
            "boursorama_exact_fiche_required": True,
            "boursorama_pea_eligibility_displayed_required": True,
            "analyst_consensus_required": False,
            "morningstar_fallback": ">=4 only when TradingView weekly/monthly is missing",
            "neutral_or_negative_overridden": False,
            "overlap_control": overlap_config,
        },
        "etf_overlap_removed": len({item["removed_isin"] for item in overlap_decisions}),
        "etf_overlap_decisions": overlap_decisions,
        "tradingview_all_three_positive_required": True,
        "etf_tradingview_monthly_required": "STRONG_BUY",
        "investing_enabled": False,
        "source_can_create_ci_light_candidate": True,
        "source_can_create_ci_candidate": False,
        "real_orders_enabled": False,
        "source_context": source_payload,
        "source_context_reused": False,
        "source_collection_passes": 1,
        "timings_seconds": timings,
        "xlsx_status": excel_status,
        "outputs": [relative.as_posix() for relative in (OUTPUT, REJECTED, MOBILE)]
        + ([EXCEL.as_posix()] if excel_status == "SUCCESS" else []),
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] in {"SUCCESS", "NO_UPSTREAM_ROWS"} else 2)
