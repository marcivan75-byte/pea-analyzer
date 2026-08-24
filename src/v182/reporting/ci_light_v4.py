from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import unicodedata

import pandas as pd

from v182.reporting.ci_selection_gate_v4 import _attach_master_context, _master_frames
from v182.reporting.selected_source_enrichment_v4 import enrich_selected_rows_v4
from v182.sources.boursorama_public import action_urls, boursorama_code, etf_urls
from v182.sources.tradingview_technical import technical_url


ROOT = Path(__file__).resolve().parents[3]
UPSTREAM = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
OUTPUT = Path("outputs/committee_master/CI_LIGHT_V4.csv")
REJECTED = Path("outputs/committee_master/CI_LIGHT_REJECTED_V4.csv")
EXCEL = Path("outputs/committee_master/CI_LIGHT_V4.xlsx")
MOBILE = Path("outputs/mobile/ANDROID_CI_LIGHT_V4.md")
AUDIT = Path("outputs/audit/CI_LIGHT_V4.json")
POSITIVE = {"BUY", "STRONG_BUY"}
HORIZON_ORDER = {"TCT": 0, "CT": 1, "MT": 2}
MIN_ANALYSTS_EXCLUSIVE = 10
MIN_UPSIDE_EXCLUSIVE = 20.0
MIN_ETF_MORNINGSTAR = 3.0
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


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


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
        if morningstar is None:
            reasons.append("ETF_MORNINGSTAR_RATING_MISSING")
        elif morningstar < MIN_ETF_MORNINGSTAR:
            reasons.append("ETF_MORNINGSTAR_RATING_LT_3")
    else:
        reasons.append("ASSET_CLASS_UNSUPPORTED")
    for timeframe, signal in signals.items():
        if not signal:
            reasons.append(f"TRADINGVIEW_{timeframe}_SIGNAL_MISSING")
        elif signal not in POSITIVE:
            reasons.append(f"TRADINGVIEW_{timeframe}_NOT_BUY_OR_STRONG_BUY")
    return not reasons, reasons, {
        "recommendation": recommendation,
        "raw_recommendation": raw_recommendation,
        "analyst_count": analyst_count,
        "upside": upside,
        "morningstar": morningstar,
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
        "name", "isin", "asset_class", "horizon", "score", "CI_CONFIDENCE_SCORE_V22_2_1",
        "CI_LIGHT_BOURSORAMA_RECOMMENDATION", "CI_LIGHT_BOURSORAMA_ANALYSTS",
        "CI_LIGHT_BOURSORAMA_UPSIDE_PCT", "CI_LIGHT_MORNINGSTAR_RATING",
        "CI_LIGHT_TRADINGVIEW_DAILY", "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY",
        "CI_LIGHT_BOURSORAMA_URL", "CI_LIGHT_TRADINGVIEW_URL", "CI_LIGHT_TRADINGVIEW_SYMBOL",
        "CI_LIGHT_INCLUDED", "CI_LIGHT_REASON",
    ]
    return [field for field in fields if field in frame]


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["_horizon_order"] = result["horizon"].map(lambda value: HORIZON_ORDER.get(_norm(value), 99))
    result["_score_order"] = pd.to_numeric(result.get("score"), errors="coerce")
    return result.sort_values(["_horizon_order", "_score_order"], ascending=[True, False]).drop(
        columns=["_horizon_order", "_score_order"]
    )


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
        "Actions: Boursorama positif, >10 analystes et >20% de potentiel. ETF: Morningstar >=3, sans consensus analystes. Tous: TradingView BUY/STRONG_BUY en 1D, 1W et 1M.",
        "",
    ]
    for horizon in HORIZON_ORDER:
        lines.extend([f"## {horizon}", ""])
        subset = frame[frame["horizon"].map(_norm).eq(horizon)]
        if subset.empty:
            lines.extend(["Aucun instrument.", ""])
        for _, row in subset.iterrows():
            quality = (
                f"Boursorama={_text(row.get('CI_LIGHT_BOURSORAMA_RECOMMENDATION'))}"
                if _norm(row.get("asset_class")) == "ACTION"
                else f"Morningstar={row.get('CI_LIGHT_MORNINGSTAR_RATING')}"
            )
            lines.append(
                f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} | {quality} | "
                f"TV={_text(row.get('CI_LIGHT_TRADINGVIEW_DAILY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_WEEKLY'))}/{_text(row.get('CI_LIGHT_TRADINGVIEW_MONTHLY'))}"
            )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    frame = _read(root / UPSTREAM)
    generated = datetime.now(timezone.utc).isoformat()
    for relative in (OUTPUT, REJECTED, EXCEL, MOBILE, AUDIT):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        payload = {"status": "NO_UPSTREAM_ROWS", "source": UPSTREAM.as_posix(), "selected": 0}
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    input_isins = set(frame["isin"].astype(str))
    actions, etfs = _master_frames(root)
    frame = _attach_master_context(frame, actions, etfs)
    frame, source_payload = enrich_selected_rows_v4(frame, root=root, profile="CI_LIGHT_V4")
    if set(frame["isin"].astype(str)) != input_isins:
        raise RuntimeError("SOURCE_LAYER_CHANGED_CANDIDATE_SET")

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
    rejected = pd.DataFrame(rejected_rows)
    selected = _ordered(pd.DataFrame(accepted_rows))
    if selected.empty:
        selected = pd.DataFrame(columns=rejected.columns if not rejected.empty else frame.columns)
    selected.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    rejected.to_csv(root / REJECTED, sep=";", index=False, encoding="utf-8-sig")
    _write_excel(selected, root / EXCEL)
    (root / MOBILE).write_text(_markdown(selected, generated), encoding="utf-8")
    payload = {
        "status": "SUCCESS",
        "version": "CI_LIGHT_V4_1",
        "generated_at_utc": generated,
        "input_rows": int(len(frame)),
        "selected": int(len(selected)),
        "rejected": int(len(rejected)),
        "selected_by_horizon": {
            horizon: int(selected["horizon"].map(_norm).eq(horizon).sum()) for horizon in HORIZON_ORDER
        },
        "action_rules": {"boursorama_positive": True, "analysts": ">10", "upside_pct": ">20"},
        "etf_rules": {"morningstar_stars": ">=3", "analyst_consensus_required": False},
        "tradingview_all_three_positive_required": True,
        "investing_enabled": False,
        "source_can_create_candidate": False,
        "selection_score_changed": False,
        "real_orders_enabled": False,
        "source_context": source_payload,
        "outputs": [relative.as_posix() for relative in (OUTPUT, REJECTED, EXCEL, MOBILE)],
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] in {"SUCCESS", "NO_UPSTREAM_ROWS"} else 2)
