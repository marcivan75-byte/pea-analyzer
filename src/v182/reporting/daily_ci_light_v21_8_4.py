from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import daily_ci_light_v21_8_2 as base


ROOT = base.ROOT
VERSION = "DAILY_CI_LIGHT_V21_8_4"
MORNINGSTAR_ACCEPTED = {3.0, 4.0}


def _build_etfs(root: Path, decisions: pd.DataFrame, investing: dict[str, dict], url_map: dict[str, dict]):
    _, etf_decisions = base._decision_index(decisions)
    master = base._etf_master(root)
    cache = base._etf_cache(root)
    rows = []
    boursorama_rated = 0
    if master.empty or "isin" not in master.columns:
        return rows, boursorama_rated
    for row in master.to_dict("records"):
        isin = str(row.get("isin") or "")
        if not isin:
            continue
        cache_entry = dict(cache.get(isin) or {})
        cache_fields = dict(cache_entry.get("fields") or {})
        rating = base._stars(cache_fields.get("boursorama_etf_morningstar_rating"))
        if rating is None:
            rating = base._stars(row.get("morningstar_rating"))
        has_boursorama, boursorama_url = base._boursorama_evidence(row, cache_entry)
        if rating is not None and has_boursorama:
            boursorama_rated += 1
        inv = base._investing_row(isin, investing, url_map)
        eligible = (
            rating in MORNINGSTAR_ACCEPTED
            and has_boursorama
            and inv["investing_all_buy"]
            and inv["investing_fresh"]
        )
        if not eligible:
            continue
        model = etf_decisions.get(isin, {})
        rows.append({
            "asset_class": "ETF",
            "isin": isin,
            "name": model.get("name") or row.get("name"),
            "decision_ct": model.get("decision_ct"),
            "score_ct": model.get("score_ct"),
            "decision_tct": None,
            "score_tct": None,
            "boursorama_n_analysts": None,
            "boursorama_recommendation": None,
            "boursorama_target_upside_pct": None,
            "morningstar_rating": rating,
            "investing_daily": inv["investing_daily"],
            "investing_weekly": inv["investing_weekly"],
            "investing_monthly": inv["investing_monthly"],
            "boursorama_url": boursorama_url,
            "investing_url": inv["investing_url"],
            "investing_technical_url": inv["investing_technical_url"],
            "investing_age_hours": inv["investing_age_hours"],
            "selection_rule": "BOURSORAMA_MORNINGSTAR_3_OR_4_STARS + INVESTING_DAY/WEEK/MONTH_BUY",
        })
    return rows, boursorama_rated


def run(root: Path = ROOT) -> dict:
    original = base._build_etfs
    base._build_etfs = _build_etfs
    try:
        payload = dict(base.run(root) or {})
    finally:
        base._build_etfs = original

    payload["version"] = VERSION
    payload["etf_rule"] = {
        "analyst_recommendation_required": False,
        "boursorama_morningstar_rating": "exactly 3 or 4 stars",
        "accepted_stars": [3, 4],
        "investing_daily": sorted(base.INVESTING_ACCEPTED),
        "investing_weekly": sorted(base.INVESTING_ACCEPTED),
        "investing_monthly": sorted(base.INVESTING_ACCEPTED),
    }
    payload["morningstar_5_star_selected"] = False
    payload["morningstar_3_star_selected"] = True
    payload["morningstar_4_star_selected"] = True

    audit = root / "outputs" / "audit" / "DAILY_CI_LIGHT_V21_8_2.json"
    if audit.exists():
        audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    android = root / "outputs" / "mobile" / "ANDROID_CI_CONTROL_CENTER.md"
    if android.exists():
        text = android.read_text(encoding="utf-8")
        text = text.replace("ETF : Morningstar Boursorama >3 étoiles", "ETF : Morningstar Boursorama 3★ ou 4★")
        android.write_text(text, encoding="utf-8")
    return payload
