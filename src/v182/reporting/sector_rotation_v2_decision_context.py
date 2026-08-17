from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]

ETF_SECTOR_MAP = {
    "FINANCE": "Financial Services",
    "SANTE": "Healthcare",
    "TECHNOLOGIE": "Technology",
    "COMMUNICATION / MEDIAS": "Communication Services",
    "UTILITIES": "Utilities",
    "IMMOBILIER": "Real Estate",
    "INDUSTRIE": "Industrials",
}

CONTEXT_COLUMNS = [
    "asset_class",
    "horizon",
    "isin",
    "name",
    "decision",
    "score",
    "sector_v2_context_status",
    "sector_v2_name",
    "sector_v2_rank",
    "sector_v2_RLS",
    "sector_v2_RARS",
    "sector_v2_AVCR",
    "sector_v2_state",
    "sector_v2_valuation_state",
    "sector_v2_warnings",
    "sector_v2_correction_alert",
    "sector_v2_new_position_action",
    "sector_v2_existing_position_action",
    "sector_v2_as_of",
    "sector_v2_model_version",
    "theme_v2_ids",
    "theme_v2_states",
    "theme_v2_warning_ids",
    "theme_v2_correction_alert_ids",
    "theme_v2_max_RLS",
    "theme_v2_max_AVCR",
    "sector_v2_pit_oos_status",
    "sector_v2_holdout_locked",
    "sector_v2_promotion_ready",
    "sector_v2_decision_influence",
    "sector_v2_score_influence",
    "sector_v2_sizing_influence",
    "sector_v2_stop_loss_influence",
    "live_orders_enabled",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _action_sector_map(actions: pd.DataFrame) -> dict[str, str]:
    if actions.empty or "isin" not in actions.columns:
        return {}
    sector_field = next(
        (field for field in ("sector_yf", "sector_yahoo") if field in actions.columns),
        None,
    )
    if sector_field is None:
        return {}
    out: dict[str, str] = {}
    for _, row in actions[["isin", sector_field]].drop_duplicates("isin").iterrows():
        isin = _normalize(row.get("isin"))
        sector = _normalize(row.get(sector_field))
        if isin and sector:
            out[isin] = sector
    return out


def _resolve_sector(row: pd.Series, action_sectors: dict[str, str]) -> tuple[str, str]:
    asset_class = _normalize(row.get("asset_class")).upper()
    isin = _normalize(row.get("isin"))
    if asset_class == "ACTION":
        sector = action_sectors.get(isin, "")
        return sector, "ACTION_MASTER_SECTOR_YF" if sector else "ACTION_SECTOR_MISSING"
    if asset_class == "ETF":
        raw = _normalize(row.get("sector")).upper()
        if raw in ETF_SECTOR_MAP:
            return ETF_SECTOR_MAP[raw], "ETF_EXPLICIT_SINGLE_SECTOR"
        if raw == "ETF MULTISECTORIEL / PAYS":
            return "", "NO_SINGLE_SECTOR_CONTEXT"
        return "", "ETF_SECTOR_UNMAPPED"
    return "", "NOT_APPLICABLE"


def _theme_context(tags: pd.DataFrame, themes: pd.DataFrame) -> dict[str, dict]:
    if tags.empty or "isin" not in tags.columns or "theme_id" not in tags.columns:
        return {}
    theme_lookup = themes.set_index("theme_id", drop=False) if not themes.empty and "theme_id" in themes.columns else pd.DataFrame()
    out: dict[str, dict] = {}
    for isin, group in tags.groupby(tags["isin"].astype(str), dropna=False):
        ids = sorted({str(value) for value in group["theme_id"].dropna().astype(str) if str(value)})
        states: list[str] = []
        warning_ids: list[str] = []
        correction_ids: list[str] = []
        rls_values: list[float] = []
        avcr_values: list[float] = []
        for theme_id in ids:
            if isinstance(theme_lookup, pd.DataFrame) and not theme_lookup.empty and theme_id in theme_lookup.index:
                theme_row = theme_lookup.loc[theme_id]
                if isinstance(theme_row, pd.DataFrame):
                    theme_row = theme_row.iloc[0]
                state = _normalize(theme_row.get("state"))
                if state:
                    states.append(f"{theme_id}:{state}")
                warnings = _normalize(theme_row.get("warnings"))
                if warnings and warnings not in {"[]", "nan", "None"}:
                    warning_ids.append(theme_id)
                correction = _normalize(theme_row.get("correction_alert")).lower() in {"true", "1", "yes"}
                if correction:
                    correction_ids.append(theme_id)
                rls = pd.to_numeric(pd.Series([theme_row.get("RLS")]), errors="coerce").iloc[0]
                avcr = pd.to_numeric(pd.Series([theme_row.get("AVCR")]), errors="coerce").iloc[0]
                if pd.notna(rls):
                    rls_values.append(float(rls))
                if pd.notna(avcr):
                    avcr_values.append(float(avcr))
        out[str(isin)] = {
            "theme_v2_ids": "|".join(ids),
            "theme_v2_states": "|".join(states),
            "theme_v2_warning_ids": "|".join(sorted(set(warning_ids))),
            "theme_v2_correction_alert_ids": "|".join(sorted(set(correction_ids))),
            "theme_v2_max_RLS": round(max(rls_values), 4) if rls_values else None,
            "theme_v2_max_AVCR": round(max(avcr_values), 4) if avcr_values else None,
        }
    return out


def build_decision_context(
    decisions: pd.DataFrame,
    actions: pd.DataFrame,
    sectors: pd.DataFrame,
    tags: pd.DataFrame,
    themes: pd.DataFrame,
    pit_status: dict,
) -> tuple[pd.DataFrame, dict]:
    """Publish per-decision Sector/Theme V2 diagnostics without mutating decisions."""
    if decisions.empty:
        return pd.DataFrame(columns=CONTEXT_COLUMNS), {
            "status": "EMPTY_DECISIONS",
            "decision_rows": 0,
            "decision_influence": 0.0,
            "score_influence": 0.0,
        }

    required = {"asset_class", "horizon", "isin", "name", "decision", "score"}
    missing = required - set(decisions.columns)
    if missing:
        raise ValueError(f"MISSING_DECISION_COLUMNS:{sorted(missing)}")

    sector_lookup = sectors.set_index("sector", drop=False) if not sectors.empty and "sector" in sectors.columns else pd.DataFrame()
    action_sectors = _action_sector_map(actions)
    theme_by_isin = _theme_context(tags, themes)
    pit_name = str(pit_status.get("status") or "WAIT_FOR_PIT_HISTORY")
    promotion_ready = bool(pit_status.get("promotion_ready", False))
    decision_influence = float(pit_status.get("decision_influence", 0.0) or 0.0)
    governance_breach = promotion_ready or decision_influence != 0.0

    rows: list[dict] = []
    for _, row in decisions.iterrows():
        sector_name, mapping_status = _resolve_sector(row, action_sectors)
        sector_payload: dict = {}
        if sector_name and isinstance(sector_lookup, pd.DataFrame) and not sector_lookup.empty and sector_name in sector_lookup.index:
            sector_row = sector_lookup.loc[sector_name]
            if isinstance(sector_row, pd.DataFrame):
                sector_row = sector_row.iloc[0]
            sector_payload = {
                "sector_v2_name": sector_name,
                "sector_v2_rank": sector_row.get("rank"),
                "sector_v2_RLS": sector_row.get("RLS"),
                "sector_v2_RARS": sector_row.get("RARS"),
                "sector_v2_AVCR": sector_row.get("AVCR"),
                "sector_v2_state": sector_row.get("state"),
                "sector_v2_valuation_state": sector_row.get("valuation_state"),
                "sector_v2_warnings": sector_row.get("warnings"),
                "sector_v2_correction_alert": sector_row.get("correction_alert"),
                "sector_v2_new_position_action": sector_row.get("new_position_action"),
                "sector_v2_existing_position_action": sector_row.get("existing_position_action"),
                "sector_v2_as_of": sector_row.get("as_of"),
                "sector_v2_model_version": sector_row.get("model_version"),
            }
        else:
            sector_payload = {
                "sector_v2_name": sector_name or None,
                "sector_v2_rank": None,
                "sector_v2_RLS": None,
                "sector_v2_RARS": None,
                "sector_v2_AVCR": None,
                "sector_v2_state": None,
                "sector_v2_valuation_state": None,
                "sector_v2_warnings": None,
                "sector_v2_correction_alert": None,
                "sector_v2_new_position_action": None,
                "sector_v2_existing_position_action": None,
                "sector_v2_as_of": None,
                "sector_v2_model_version": None,
            }

        isin = _normalize(row.get("isin"))
        theme_payload = theme_by_isin.get(isin, {}) if _normalize(row.get("asset_class")).upper() == "ACTION" else {}
        has_sector = bool(sector_payload.get("sector_v2_name")) and sector_payload.get("sector_v2_RLS") is not None
        has_theme = bool(theme_payload.get("theme_v2_ids"))
        if governance_breach:
            context_status = "GOVERNANCE_BREACH_BLOCKED"
        elif has_sector and has_theme:
            context_status = "SECTOR_AND_THEME_CONTEXT"
        elif has_sector:
            context_status = "SECTOR_CONTEXT_ONLY"
        elif has_theme:
            context_status = "THEME_CONTEXT_ONLY"
        else:
            context_status = mapping_status

        rows.append(
            {
                "asset_class": row.get("asset_class"),
                "horizon": row.get("horizon"),
                "isin": row.get("isin"),
                "name": row.get("name"),
                "decision": row.get("decision"),
                "score": row.get("score"),
                "sector_v2_context_status": context_status,
                **sector_payload,
                "theme_v2_ids": theme_payload.get("theme_v2_ids"),
                "theme_v2_states": theme_payload.get("theme_v2_states"),
                "theme_v2_warning_ids": theme_payload.get("theme_v2_warning_ids"),
                "theme_v2_correction_alert_ids": theme_payload.get("theme_v2_correction_alert_ids"),
                "theme_v2_max_RLS": theme_payload.get("theme_v2_max_RLS"),
                "theme_v2_max_AVCR": theme_payload.get("theme_v2_max_AVCR"),
                "sector_v2_pit_oos_status": pit_name,
                "sector_v2_holdout_locked": bool(pit_status.get("holdout_locked", True)),
                "sector_v2_promotion_ready": False,
                "sector_v2_decision_influence": 0.0,
                "sector_v2_score_influence": 0.0,
                "sector_v2_sizing_influence": 0.0,
                "sector_v2_stop_loss_influence": 0.0,
                "live_orders_enabled": False,
            }
        )

    context = pd.DataFrame(rows, columns=CONTEXT_COLUMNS)
    if len(context) != len(decisions):
        raise RuntimeError("SECTOR_V2_CONTEXT_ROW_COUNT_MUTATION")
    keys = ["asset_class", "horizon", "isin"]
    left = decisions[keys].fillna("").astype(str).reset_index(drop=True)
    right = context[keys].fillna("").astype(str).reset_index(drop=True)
    if not left.equals(right):
        raise RuntimeError("SECTOR_V2_CONTEXT_KEY_MUTATION")

    summary = {
        "status": "GOVERNANCE_BREACH_BLOCKED" if governance_breach else "SUCCESS",
        "decision_rows": int(len(context)),
        "rows_with_sector_context": int(context["sector_v2_RLS"].notna().sum()),
        "rows_with_theme_context": int(context["theme_v2_ids"].fillna("").ne("").sum()),
        "action_rows_with_theme_context": int(
            (context["asset_class"].astype(str).eq("ACTION") & context["theme_v2_ids"].fillna("").ne("")).sum()
        ),
        "etf_rows_no_single_sector_context": int(context["sector_v2_context_status"].eq("NO_SINGLE_SECTOR_CONTEXT").sum()),
        "pit_oos_status": pit_name,
        "holdout_locked": bool(pit_status.get("holdout_locked", True)),
        "promotion_ready": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_influence": 0.0,
        "stop_loss_influence": 0.0,
        "live_orders_enabled": False,
    }
    return context, summary


def run(root: Path = ROOT) -> dict:
    decisions_path = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    action_path = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    if not action_path.exists():
        action_path = root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv"
    sectors_path = root / "outputs" / "sector_rotation" / "V2_SECTOR_ROTATION_SHADOW.csv"
    tags_path = root / "outputs" / "sector_rotation" / "V2_THEME_DIRECT_TAGS.csv"
    themes_path = root / "outputs" / "sector_rotation" / "V2_THEME_ROTATION_SHADOW.csv"
    pit_path = root / "outputs" / "audit" / "V2_SECTOR_ROTATION_PIT_OOS_STATUS.json"

    required_paths = [decisions_path, action_path, sectors_path, pit_path]
    missing_paths = [str(path.relative_to(root)) for path in required_paths if not path.exists()]
    if missing_paths:
        raise FileNotFoundError(f"SECTOR_V2_CONTEXT_REQUIRED_INPUT_MISSING:{missing_paths}")

    decisions = _read_csv(decisions_path)
    actions = _read_csv(action_path)
    sectors = _read_csv(sectors_path)
    tags = _read_csv(tags_path)
    themes = _read_csv(themes_path)
    pit_status = _read_json(pit_path)

    context, summary = build_decision_context(decisions, actions, sectors, tags, themes, pit_status)
    outdir = root / "outputs" / "committee_master"
    auditdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / "COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv"
    audit_path = auditdir / "SECTOR_ROTATION_V2_DECISION_CONTEXT.json"
    context.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        **summary,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_path": str(output_path.relative_to(root)),
        "audit_path": str(audit_path.relative_to(root)),
        "governance": [
            "This file is a diagnostic join keyed to Committee decisions; COMMITTEE_DECISIONS.csv is not modified.",
            "Sector Rotation V2 remains SHADOW_ONLY until governed PIT/OOS promotion.",
            "ETF multisector/country products never receive a fabricated single-sector mapping.",
            "Theme context is limited to governed direct Action industry tags; low-confidence AI/data-center/grid/cyber hypotheses remain disabled.",
        ],
    }
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    committee_summary_path = outdir / "SUMMARY.json"
    if committee_summary_path.exists():
        try:
            committee_summary = json.loads(committee_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            committee_summary = {}
        if isinstance(committee_summary, dict):
            committee_summary["sector_rotation_v2_decision_context"] = payload
            committee_summary.setdefault("outputs", {})["sector_rotation_v2_decision_context"] = str(
                output_path.relative_to(root)
            )
            committee_summary["outputs"]["sector_rotation_v2_decision_context_audit"] = str(
                audit_path.relative_to(root)
            )
            committee_summary.setdefault("notes", []).append(
                "Sector Rotation V2 is attached per Committee row in a separate context-only diagnostic file; score/decision/sizing/stop influence remains zero."
            )
            committee_summary_path.write_text(
                json.dumps(committee_summary, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
