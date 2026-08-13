from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _words(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    match = re.search(r"[-+]?\d[\d ]*(?:[,.]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _explicit_risk(value: object) -> float | None:
    """Accept only an actually observed SRI numerator.

    Boursorama can expose only `/7` when the graphical numerator is absent from
    text. That remains missing and is never interpreted as risk 7/7.
    """
    text = str(value or "").strip()
    ratio = re.search(r"(?<!\d)([1-7])\s*/\s*7(?!\d)", text)
    if ratio:
        return float(ratio.group(1))
    if re.fullmatch(r"[1-7](?:[,.]0+)?", text):
        return float(text.replace(",", "."))
    return None


def _canonical_url(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        frames = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []
    out = []
    for frame in frames:
        f = frame.copy()
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = [" | ".join(str(x).strip() for x in col if str(x).strip() and not str(x).startswith("Unnamed")) for col in f.columns]
        else:
            f.columns = [str(c).strip() for c in f.columns]
        out.append(f)
    return out


def _col(frame: pd.DataFrame, *needles: str) -> str | None:
    for col in frame.columns:
        normalized = _norm(col)
        if any(_norm(needle) in normalized for needle in needles):
            return str(col)
    return None


def _name_map(etfs: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: set[str] = set()
    if "isin" not in etfs.columns or "name" not in etfs.columns:
        return mapping
    for _, row in etfs.iterrows():
        key = _norm(row.get("name"))
        isin = str(row.get("isin") or "").strip()
        if not key or not isin:
            continue
        if key in mapping and mapping[key] != isin:
            duplicates.add(key)
        else:
            mapping[key] = isin
    for key in duplicates:
        mapping.pop(key, None)
    return mapping


def _obs(
    isin: str,
    field: str,
    value,
    url: str,
    source_file: str,
    provider: str = "Morningstar",
    *,
    as_of: str | None = None,
) -> dict:
    return {
        "universe": "ETF",
        "isin": isin,
        "field": field,
        "value": value,
        "source": f"{SOURCE}/{provider}" if provider else SOURCE,
        "source_url": url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of or datetime.now(timezone.utc).date().isoformat(),
        "evidence_level": "B",
        "validation_status": "ATTRIBUTED",
    }


def _date_iso(value: str) -> str | None:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _as_of(text: str) -> str:
    matches = re.findall(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
    dates = [d for d in (_date_iso(v) for v in matches) if d]
    return max(dates) if dates else datetime.now(timezone.utc).date().isoformat()


def parse_etf_html_safe(html: str, etfs: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    """Parse Boursorama ETF search/palmares tables conservatively."""
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "boursorama.com" not in url:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}], {"matched_rows": 0}
    canonical = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    names = _name_map(etfs)
    observations: list[dict] = []
    failures: list[dict] = []
    matched: set[str] = set()
    explicit_risk = 0
    risk_unobserved = 0
    as_of = _as_of(soup.get_text(" ", strip=True))

    for frame in _tables(html):
        name_col = _col(frame, "libelle", "nom")
        isin_col = _col(frame, "isin")
        rating_col = _col(frame, "morningstar", "notation")
        risk_col = _col(frame, "risque")
        category_col = _col(frame, "categorie morningstar")
        perf_col = _col(frame, "perf 1 an")
        price_col = _col(frame, "dernier")
        if not name_col and not isin_col:
            continue
        if not rating_col and not risk_col and not category_col:
            continue
        for _, row in frame.iterrows():
            isin = str(row.get(isin_col) or "").strip() if isin_col else ""
            if isin not in canonical and name_col:
                isin = names.get(_norm(row.get(name_col)), "")
            if not isin or isin not in canonical:
                continue
            matched.add(isin)
            if rating_col:
                rating = _num(row.get(rating_col))
                if rating is not None and 0 <= rating <= 5:
                    observations.append(_obs(isin, "morningstar_rating", rating, url, source_file, as_of=as_of))
                    observations.append(_obs(isin, "boursorama_morningstar_rating", rating, url, source_file, as_of=as_of))
            if risk_col:
                risk = _explicit_risk(row.get(risk_col))
                if risk is not None:
                    explicit_risk += 1
                    observations.append(_obs(isin, "risk_indicator", risk, url, source_file, as_of=as_of))
                    observations.append(_obs(isin, "boursorama_risk_indicator", risk, url, source_file, as_of=as_of))
                elif str(row.get(risk_col) or "").strip():
                    risk_unobserved += 1
            if category_col:
                category = str(row.get(category_col) or "").strip()
                if category and category.casefold() != "nan":
                    observations.append(_obs(isin, "morningstar_category", category, url, source_file, as_of=as_of))
                    observations.append(_obs(isin, "boursorama_morningstar_category", category, url, source_file, as_of=as_of))
            if perf_col:
                perf = _num(row.get(perf_col))
                if perf is not None:
                    observations.append(_obs(isin, "boursorama_perf_1y_pct", perf, url, source_file, provider="", as_of=as_of))
            if price_col:
                price = _num(row.get(price_col))
                if price is not None:
                    observations.append(_obs(isin, "boursorama_last_price", price, url, source_file, provider="", as_of=as_of))

    if risk_unobserved:
        failures.append({
            "source": SOURCE,
            "source_file": source_file,
            "reason": "ETF_RISK_GRAPHIC_NUMERATOR_NOT_OBSERVED",
            "rows": risk_unobserved,
            "governance": "Risk remains N/A; a rendered '/7' is never converted to 7/7.",
        })
    return observations, failures, {
        "matched_rows": len(matched),
        "observations": len(observations),
        "explicit_risk_rows": explicit_risk,
        "risk_unobserved_rows": risk_unobserved,
        "source_url": url,
    }


def _line_value(lines: list[str], *labels: str) -> str | None:
    normalized = [_words(x) for x in lines]
    targets = [_words(x) for x in labels]
    for i, key in enumerate(normalized):
        if any(key == target or key.startswith(target) for target in targets):
            for raw in lines[i + 1:i + 5]:
                candidate = raw.strip()
                low = _words(candidate)
                if candidate and low and low not in {"fermer", "chargement"} and not low.startswith("qu est ce"):
                    return candidate
    return None


def _page_isin(text: str, canonical: set[str]) -> str | None:
    found = list(dict.fromkeys(i for i in re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text) if i in canonical))
    return found[0] if len(found) == 1 else None


def _performance_fields(frame: pd.DataFrame) -> dict[str, object]:
    fields: dict[str, object] = {}
    if frame.empty:
        return fields
    first = str(frame.columns[0])
    label_values = frame[first].astype(str).map(_words).tolist()
    if not any(label in {"etf", "tracker"} for label in label_values):
        return fields
    horizon_map = {
        "1erjanv": "ytd", "1mois": "1m", "3mois": "3m", "6mois": "6m",
        "1an": "1y", "3ans": "3y", "5ans": "5y", "10ans": "10y",
    }
    for _, row in frame.iterrows():
        label = _words(row.get(first))
        prefix = "boursorama_perf" if label in {"etf", "tracker"} else "boursorama_morningstar_category_perf" if label.startswith("cat") else "boursorama_morningstar_rank" if label.startswith("rang") else ""
        if not prefix:
            continue
        for col in frame.columns[1:]:
            key = _norm(col)
            suffix = None
            for needle, mapped in horizon_map.items():
                if needle in key:
                    suffix = mapped
                    break
            if not suffix:
                continue
            value = _num(row.get(col))
            if value is not None:
                fields[f"{prefix}_{suffix}" + ("_pct" if "rank" not in prefix else "")] = value
    return fields


def _annual_performance_fields(frame: pd.DataFrame) -> dict[str, object]:
    fields: dict[str, object] = {}
    if frame.empty:
        return fields
    first = str(frame.columns[0])
    labels = frame[first].astype(str).map(_words).tolist()
    if not any(label in {"tracker", "etf"} for label in labels):
        return fields
    year_cols = [col for col in frame.columns[1:] if re.fullmatch(r"20\d{2}", str(col).strip())]
    if not year_cols:
        return fields
    for _, row in frame.iterrows():
        label = _words(row.get(first))
        prefix = "boursorama_perf_calendar" if label in {"tracker", "etf"} else "boursorama_morningstar_category_calendar" if label.startswith("categorie") or label.startswith("cat") else "boursorama_morningstar_rank_calendar" if label.startswith("rang") else ""
        if not prefix:
            continue
        for col in year_cols:
            value = _num(row.get(col))
            if value is not None:
                fields[f"{prefix}_{str(col).strip()}" + ("_pct" if "rank" not in prefix else "")] = value
    return fields


def parse_etf_detail_html(html: str, etfs: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    """Parse saved individual ETF course/performance/composition pages.

    Structural values are retained as Boursorama/Morningstar context. A
    management-fee maximum is deliberately not relabelled as TER, and a visible
    `/7` without numerator remains missing.
    """
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/trackers/cours/" not in url or "/recherche/" in url:
        return [], [], {"matched_rows": 0, "not_etf_detail": True}
    text = soup.get_text("\n", strip=True)
    canonical = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    isin = _page_isin(text, canonical)
    if not isin:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "ETF_DETAIL_ISIN_NOT_UNIQUE"}], {"matched_rows": 0}
    as_of = _as_of(text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    fields: dict[str, object] = {}

    for labels, field in (
        (("indice de référence", "indice de reference"), "boursorama_reference_index"),
        (("catégorie morningstar", "categorie morningstar"), "morningstar_category"),
        (("date de création", "date de creation"), "boursorama_creation_date"),
        (("société de gestion", "societe de gestion"), "boursorama_management_company"),
        (("gérants", "gerants"), "boursorama_managers"),
        (("forme juridique",), "boursorama_legal_form"),
        (("classe d'actifs", "classe d actifs"), "boursorama_asset_class"),
        (("zone géographique", "zone geographique"), "boursorama_geographic_zone"),
        (("affectation des résultats", "affectation des resultats"), "boursorama_distribution_policy"),
        (("réplication", "replication"), "boursorama_replication"),
    ):
        raw = _line_value(lines, *labels)
        if raw:
            fields[field] = raw
            if field == "morningstar_category":
                fields["boursorama_morningstar_category"] = raw

    fee = _num(_line_value(lines, "frais de gestion maximum"))
    if fee is not None:
        fields["boursorama_management_fee_max_pct"] = fee

    net_asset = re.search(r"Actif\s+net\s*\(EUR\).*?([0-9][0-9\s,.]*)\s*M\s*/\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text, flags=re.I | re.S)
    if net_asset:
        value = _num(net_asset.group(1))
        if value is not None:
            fields["boursorama_net_assets_eur_m"] = value
        date = _date_iso(net_asset.group(2))
        if date:
            fields["boursorama_net_assets_date"] = date

    risk_block = re.search(r"Risque\s+du\s+fonds\s*\(SRI\)(.*?)(?:Portefeuille|Liste|Caractéristiques|Caracteristiques|Composition)", text, flags=re.I | re.S)
    if risk_block:
        risk = _explicit_risk(risk_block.group(1))
        if risk is not None:
            fields["risk_indicator"] = risk
            fields["boursorama_risk_indicator"] = risk

    normalized = _words(text)
    if re.search(r"eligibilite.{0,180}\bpea\b", normalized):
        fields["boursorama_pea_button_observed"] = True

    comp = re.search(r"Composition\s*\(les\s+10\s+premi[eè]res\s+lignes\)(.*?)Date\s+du\s+portefeuille\s*:\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text, flags=re.I | re.S)
    if comp:
        holdings = [re.sub(r"\s+", " ", x).strip(" |-\t") for x in comp.group(1).splitlines()]
        holdings = [x for x in holdings if x and _words(x) not in {"chargement"}][:10]
        fields["boursorama_top10_holdings_count"] = len(holdings)
        for idx, holding in enumerate(holdings, 1):
            fields[f"boursorama_top_holding_{idx}"] = holding
        portfolio_date = _date_iso(comp.group(2))
        if portfolio_date:
            fields["boursorama_portfolio_date"] = portfolio_date

    morningstar_date = re.search(r"Donn[ée]es\s+calcul[ée]es\s+par\s+Morningstar\s+au\s*:\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text, flags=re.I)
    if morningstar_date:
        parsed = _date_iso(morningstar_date.group(1))
        if parsed:
            fields["boursorama_morningstar_data_date"] = parsed

    for frame in _tables(html):
        fields.update(_performance_fields(frame))
        fields.update(_annual_performance_fields(frame))

    observations: list[dict] = []
    for field, value in fields.items():
        provider = "Morningstar" if field.startswith("morningstar_") or field.startswith("boursorama_morningstar") or field.startswith("boursorama_perf") or field.startswith("boursorama_top") or field in {"risk_indicator", "boursorama_risk_indicator", "boursorama_portfolio_date"} else ""
        observations.append(_obs(isin, field, value, url, source_file, provider=provider, as_of=as_of))
    return observations, [], {"matched_rows": 1, "observations": len(observations), "explicit_risk_rows": 1 if "risk_indicator" in fields else 0, "isin": isin, "source_url": url}


def load_etf_pages(root: Path, etfs: pd.DataFrame, relative_root: str = "inputs/boursorama_snapshots") -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    if not directory.exists():
        return [], [], {"files": 0, "matched_rows": 0, "observations": 0}
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    matched = 0
    explicit_risk = 0
    detail_files = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        lower = html.casefold()
        if "tracker" not in lower and "etf" not in lower:
            continue
        file_obs: list[dict] = []
        file_fail: list[dict] = []
        file_matched = 0
        file_risk = 0
        if "morningstar" in lower:
            obs, failed, stats = parse_etf_html_safe(html, etfs, str(path))
            file_obs.extend(obs)
            file_fail.extend(failed)
            file_matched += int(stats.get("matched_rows", 0))
            file_risk += int(stats.get("explicit_risk_rows", 0))
        detail_obs, detail_fail, detail_stats = parse_etf_detail_html(html, etfs, str(path))
        if not detail_stats.get("not_etf_detail"):
            detail_files += 1
            file_obs.extend(detail_obs)
            file_fail.extend(detail_fail)
            file_matched += int(detail_stats.get("matched_rows", 0))
            file_risk += int(detail_stats.get("explicit_risk_rows", 0))
        if not file_obs and not file_fail:
            continue
        files += 1
        matched += file_matched
        explicit_risk += file_risk
        observations.extend(file_obs)
        failures.extend(file_fail)

    # Avoid duplicate equal-source/equal-date observations generated when an
    # individual page also contains a compatible search-style table fragment.
    dedup: dict[tuple[str, str, str, str], dict] = {}
    for row in observations:
        key = (str(row.get("isin")), str(row.get("field")), str(row.get("source")), str(row.get("as_of")))
        dedup[key] = row
    observations = list(dedup.values())
    return observations, failures, {
        "files": files,
        "detail_files": detail_files,
        "matched_rows": matched,
        "observations": len(observations),
        "explicit_risk_rows": explicit_risk,
        "failures": len(failures),
    }
