from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import unicodedata

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
UPSTREAM = Path("outputs/committee_master/CI_SELECTION_V22_2_2.csv")
OUTPUT = Path("outputs/committee_master/CI_LIGHT_V22_2_3.csv")
REJECTED = Path("outputs/committee_master/CI_LIGHT_REJECTED_V22_2_3.csv")
EXCEL = Path("outputs/committee_master/CI_LIGHT_V22_2_3.xlsx")
MOBILE = Path("outputs/mobile/ANDROID_CI_LIGHT_V22_2_3.md")
AUDIT = Path("outputs/audit/CI_LIGHT_V22_2_3.json")

BOURSORAMA_POSITIVE = {
    "BUY",
    "STRONG_BUY",
    "ACHETER",
    "RENFORCER",
    "ACHAT",
    "ACCUMULER",
}
INVESTING_POSITIVE = {"BUY", "STRONG_BUY"}
HORIZON_ORDER = {"TCT": 0, "CT": 1, "MT": 2}
ETF_MIN_MORNINGSTAR_STARS = 3.0


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
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return text


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


def _investing_signal(row: pd.Series) -> str:
    direct = _norm(row.get("CI_INVESTING_SIGNAL")) or _norm(row.get("investing_horizon_signal"))
    if direct:
        return direct
    horizon = _norm(row.get("horizon"))
    field = {
        "TCT": "investing_daily_signal",
        "CT": "investing_weekly_signal",
        "MT": "investing_monthly_signal",
    }.get(horizon)
    return _norm(row.get(field)) if field else ""


def _boursorama_consensus(row: pd.Series) -> str:
    for field in (
        "boursorama_consensus",
        "boursorama_analyst_recommendation",
        "boursorama_recommendation",
    ):
        value = _norm(row.get(field))
        if value:
            return value
    return ""


def _morningstar_rating(row: pd.Series) -> float | None:
    for field in (
        "CI_LIGHT_ETF_MORNINGSTAR_RATING",
        "morningstar_rating",
        "boursorama_morningstar_rating",
        "morningstar_stars",
    ):
        value = _num(row.get(field))
        if value is not None:
            return value
    return None


def _attach_etf_morningstar(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Attach the governed ETF Morningstar rating by exact ISIN when absent upstream."""
    out = frame.copy()
    if out.empty or "isin" not in out.columns:
        return out
    paths = [
        root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv",
        root / "inputs/V18.2_PEA_ETF_MASTER.csv",
    ]
    master_path = next((path for path in paths if path.exists()), None)
    if master_path is None:
        return out
    master = _read(master_path)
    if master.empty or "isin" not in master.columns or "morningstar_rating" not in master.columns:
        return out
    ratings = master[["isin", "morningstar_rating"]].copy()
    ratings["isin"] = ratings["isin"].map(_text)
    ratings = ratings[ratings["isin"].ne("")].drop_duplicates("isin", keep="last")
    mapping = dict(zip(ratings["isin"], ratings["morningstar_rating"]))
    if "morningstar_rating" not in out.columns:
        out["morningstar_rating"] = out["isin"].map(lambda value: mapping.get(_text(value), pd.NA))
    else:
        missing = pd.to_numeric(out["morningstar_rating"], errors="coerce").isna()
        out.loc[missing, "morningstar_rating"] = out.loc[missing, "isin"].map(
            lambda value: mapping.get(_text(value), pd.NA)
        )
    return out


def _evaluate(row: pd.Series) -> tuple[bool, list[str], str, str, float | None]:
    reasons: list[str] = []
    horizon = _norm(row.get("horizon"))
    asset = _norm(row.get("asset_class"))
    boursorama = _boursorama_consensus(row)
    investing = _investing_signal(row)
    morningstar = _morningstar_rating(row)

    if horizon not in HORIZON_ORDER:
        reasons.append("UNSUPPORTED_HORIZON")

    if asset == "ETF":
        if morningstar is None:
            reasons.append("ETF_MORNINGSTAR_RATING_MISSING")
        elif morningstar < ETF_MIN_MORNINGSTAR_STARS:
            reasons.append("ETF_MORNINGSTAR_RATING_LT_3")
    else:
        if not boursorama:
            reasons.append("BOURSORAMA_ANALYST_RECOMMENDATION_MISSING")
        elif boursorama not in BOURSORAMA_POSITIVE:
            reasons.append("BOURSORAMA_NOT_BUY_OR_REINFORCE")

    if not investing:
        reasons.append("INVESTING_HORIZON_SIGNAL_MISSING")
    elif investing not in INVESTING_POSITIVE:
        reasons.append("INVESTING_HORIZON_NOT_BUY_OR_STRONG_BUY")

    return not reasons, reasons, boursorama, investing, morningstar


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out["_h"] = out["horizon"].map(lambda value: HORIZON_ORDER.get(_norm(value), 99))
    out["_score"] = pd.to_numeric(out.get("score"), errors="coerce")
    confidence_col = "CI_CONFIDENCE_SCORE_V22_2_1" if "CI_CONFIDENCE_SCORE_V22_2_1" in out else "CI_CONFIDENCE_SCORE_0_100"
    out["_confidence"] = pd.to_numeric(out.get(confidence_col), errors="coerce")
    return out.sort_values(["_h", "_confidence", "_score"], ascending=[True, False, False], na_position="last").drop(columns=["_h", "_score", "_confidence"])


def _export_columns(frame: pd.DataFrame) -> list[str]:
    wanted = [
        "name", "isin", "asset_class", "horizon", "score",
        "CI_CONFIDENCE_SCORE_V22_2_1", "CI_CONFIDENCE_SCORE_0_100",
        "CI_LIGHT_BOURSORAMA_RECOMMENDATION", "CI_LIGHT_ETF_MORNINGSTAR_RATING",
        "morningstar_rating", "CI_LIGHT_INVESTING_SIGNAL",
        "CI_EFFECTIVE_ENTRY_STATE_V22_2_2", "V22_2_1_ENTRY_STATE",
        "CI_POTENTIAL_UPSIDE_PCT", "CI_POTENTIAL_METHOD",
        "boursorama_url", "CI_BOURSORAMA_URL", "investing_url", "CI_INVESTING_URL",
    ]
    return [column for column in wanted if column in frame.columns]


def _write_excel(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        all_cols = _export_columns(frame)
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
        "Filtre strict: candidat du process principal + qualité source adaptée à la classe d'actif + Investing BUY/STRONG_BUY sur l'horizon correspondant.",
        "",
        "Actions: recommandation Boursorama ACHETER/RENFORCER (ou BUY/STRONG_BUY). ETF: Morningstar >= 3 étoiles; aucun consensus analystes ETF n'est requis.",
        "",
        "La liste LIGHT ne modifie aucun score, critère, poids, seuil ou décision du CI complet.",
        "",
    ]
    for horizon in ("TCT", "CT", "MT"):
        lines.extend([f"## {horizon}", ""])
        subset = frame[frame["horizon"].map(_norm).eq(horizon)]
        if subset.empty:
            lines.extend(["Aucun instrument ne satisfait simultanément les filtres.", ""])
            continue
        for _, row in subset.iterrows():
            name = _text(row.get("name")) or _text(row.get("isin"))
            score = _num(row.get("score"))
            confidence = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
            asset = _norm(row.get("asset_class"))
            source_quality = (
                f"Morningstar={_morningstar_rating(row)}*" if asset == "ETF"
                else f"Boursorama={_text(row.get('CI_LIGHT_BOURSORAMA_RECOMMENDATION'))}"
            )
            lines.append(
                f"- {name} | {_text(row.get('asset_class'))} | score={score if score is not None else 'NA'} | "
                f"confiance={confidence if confidence is not None else 'NA'} | {source_quality} | "
                f"Investing={_text(row.get('CI_LIGHT_INVESTING_SIGNAL'))}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    source = root / UPSTREAM
    frame = _attach_etf_morningstar(_read(source), root)
    generated = datetime.now(timezone.utc).isoformat()
    outdir = root / "outputs/committee_master"
    auditdir = root / "outputs/audit"
    mobiledir = root / "outputs/mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobiledir.mkdir(parents=True, exist_ok=True)

    if frame.empty:
        payload = {"status": "NO_UPSTREAM_ROWS", "source": str(UPSTREAM), "selected": 0}
        (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    accepted_rows: list[dict] = []
    rejected_rows: list[dict] = []
    for _, row in frame.iterrows():
        accepted, reasons, boursorama, investing, morningstar = _evaluate(row)
        record = row.to_dict()
        record["CI_LIGHT_BOURSORAMA_RECOMMENDATION"] = boursorama
        record["CI_LIGHT_ETF_MORNINGSTAR_RATING"] = morningstar
        record["CI_LIGHT_INVESTING_SIGNAL"] = investing
        record["CI_LIGHT_INCLUDED"] = bool(accepted)
        asset = _norm(row.get("asset_class"))
        if accepted:
            record["CI_LIGHT_REASON"] = (
                "PASS_ETF_MORNINGSTAR_AND_INVESTING" if asset == "ETF"
                else "PASS_ACTION_BOURSORAMA_AND_INVESTING"
            )
        else:
            record["CI_LIGHT_REASON"] = "|".join(reasons)
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
        "action_boursorama_positive_values": sorted(BOURSORAMA_POSITIVE),
        "etf_minimum_morningstar_stars": ETF_MIN_MORNINGSTAR_STARS,
        "etf_analyst_consensus_required": False,
        "etf_missing_morningstar_policy": "EXCLUDE_FAIL_CLOSED",
        "investing_positive_values": sorted(INVESTING_POSITIVE),
        "investing_horizon_mapping": {"TCT": "DAILY", "CT": "WEEKLY", "MT": "MONTHLY"},
        "source_can_create_candidate": False,
        "full_ci_changed": False,
        "selection_score_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
        "outputs": [str(OUTPUT), str(REJECTED), str(EXCEL), str(MOBILE)],
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
