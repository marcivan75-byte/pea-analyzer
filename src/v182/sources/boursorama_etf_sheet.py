from __future__ import annotations

from io import StringIO
import re

import pandas as pd

from v182.sources.boursorama_selected_etf import (
    _capture,
    _capture_num,
    _num,
    _text,
    parse_etf_morningstar_sri_html,
)


def parse_etf_sheet_html(html: str) -> dict[str, object]:
    text = _text(html)
    if not text:
        return {}
    fields: dict[str, object] = {}
    numeric = {
        "boursorama_etf_theoretical_open": r"Ouverture th[eé]orique\s+([0-9\s,.]+)",
        "boursorama_etf_open": r"\bouverture\s+([0-9\s,.]+)",
        "boursorama_etf_previous_close": r"cl[oô]ture veille\s+([0-9\s,.]+)",
        "boursorama_etf_day_high": r"\+ haut\s+([0-9\s,.]+)",
        "boursorama_etf_day_low": r"\+ bas\s+([0-9\s,.]+)",
        "boursorama_etf_volume": r"\bvolume\s+([0-9\s]+)",
        "boursorama_etf_management_fee_pct": r"Frais de gestion maximum\s+([0-9\s,.]+)\s*%",
    }
    for field, pattern in numeric.items():
        value = _capture_num(text, pattern)
        if value is not None:
            fields[field] = value
    assets = re.search(r"Actif net \(EUR\)\s+([0-9\s,.]+)([KMB])?\s*/", text, flags=re.IGNORECASE)
    if assets:
        number = _num(assets.group(1))
        scale = {"K": 0.001, "M": 1.0, "B": 1000.0}.get((assets.group(2) or "M").upper(), 1.0)
        if number is not None:
            fields["boursorama_etf_aum_eur_m"] = number * scale
    strings = {
        "boursorama_etf_morningstar_category": r"cat[eé]gorie morningstar\s+(.+?)\s+(?:ouverture|cl[oô]ture veille|Date de cr[eé]ation|Forme juridique)",
        "boursorama_etf_management_company": r"Soci[eé]t[eé] de gestion\s+(.+?)\s+(?:G[eé]rants|Cat[eé]gorie morningstar)",
        "boursorama_etf_asset_class": r"Classe d'actifs\s+(.+?)\s+Zone g[eé]ographique",
        "boursorama_etf_geographic_zone": r"Zone g[eé]ographique\s+(.+?)\s+(?:Dividende|Affectation des r[eé]sultats)",
        "boursorama_etf_distribution_policy": r"Affectation des r[eé]sultats\s+(.+?)\s+R[eé]plication",
        "boursorama_etf_replication": r"R[eé]plication\s+(.+?)\s+(?:Frais d'entr[eé]e|Frais de gestion maximum)",
    }
    for field, pattern in strings.items():
        value = _capture(text, pattern)
        if value:
            fields[field] = value[:200]
    fields["boursorama_etf_pea_eligible_displayed"] = bool(
        re.search(r"\b[ÉE]ligibilit[eé].{0,250}\bPEA\b", text, flags=re.IGNORECASE)
    )
    fields.update(parse_etf_morningstar_sri_html(html))
    return fields


def parse_etf_risk_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    try:
        tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return fields
    for frame in tables:
        headers = [str(col).upper() for col in frame.columns]
        joined = " ".join(headers)
        if "VOLATILITE" not in joined and "VOLATILIT" not in joined:
            continue
        if "BETA" not in joined or frame.empty:
            continue
        row = frame.iloc[0]
        mapping = {
            "VOLATIL": "boursorama_etf_volatility_1y_pct",
            "ALPHA": "boursorama_etf_alpha_1y",
            "R\u00b2": "boursorama_etf_r2_1y",
            "R2": "boursorama_etf_r2_1y",
            "BETA": "boursorama_etf_beta_1y",
        }
        for idx, header in enumerate(headers):
            out = next((field for token, field in mapping.items() if token in header), None)
            if out is None or idx >= len(row):
                continue
            value = _num(row.iloc[idx])
            if value is not None:
                fields[out] = value
        if fields:
            break
    return fields
