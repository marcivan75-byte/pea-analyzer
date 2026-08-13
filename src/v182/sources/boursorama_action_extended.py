from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama"
EVIDENCE = "B"


def _ascii(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _norm(value: object) -> str:
    text = _ascii(value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(value))


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    if not text or text.casefold() in {"nan", "none", "nd", "n/a", "-", "—"}:
        return None
    match = re.search(r"[-+]?\d[\d .]*(?:,\d+|\.\d+)?", text)
    if not match:
        return None
    token = match.group(0).replace(" ", "")
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".")
    elif "," in token:
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def _canonical_url(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _date_iso(value: str | None, *, default_year: int | None = None) -> str | None:
    if not value:
        return None
    text = _ascii(value).strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    months = {
        "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    }
    m = re.search(r"\b(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?\b", _norm(text))
    if m and m.group(2) in months:
        year = int(m.group(3)) if m.group(3) else int(default_year or datetime.now(timezone.utc).year)
        try:
            return datetime(year, months[m.group(2)], int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    return None


def _as_of_from_text(text: str) -> str:
    matches = re.findall(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
    parsed = [d for d in (_date_iso(v) for v in matches) if d]
    return max(parsed) if parsed else datetime.now(timezone.utc).date().isoformat()


def _obs(
    isin: str,
    field: str,
    value,
    *,
    url: str,
    source_file: str,
    as_of: str,
    provider: str = "",
    evidence: str = EVIDENCE,
) -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": SOURCE if not provider else f"{SOURCE}/{provider}",
        "source_url": url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "evidence_level": evidence,
        "validation_status": "ATTRIBUTED",
    }


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        frames = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []
    out: list[pd.DataFrame] = []
    for frame in frames:
        f = frame.copy()
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = [
                " | ".join(
                    str(x).strip() for x in col
                    if str(x).strip() and not str(x).startswith("Unnamed")
                )
                for col in f.columns
            ]
        else:
            f.columns = [str(c).strip() for c in f.columns]
        out.append(f)
    return out


def _action_maps(actions: pd.DataFrame) -> tuple[set[str], dict[str, str], dict[str, str]]:
    isins = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    name_map: dict[str, str] = {}
    dupes: set[str] = set()
    ticker_map: dict[str, str] = {}
    for _, row in actions.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin:
            continue
        name = _compact(row.get("name"))
        if name:
            if name in name_map and name_map[name] != isin:
                dupes.add(name)
            else:
                name_map[name] = isin
        ticker = str(row.get("yahoo_ticker") or "").upper().strip()
        if ticker:
            ticker_map[ticker] = isin
    for key in dupes:
        name_map.pop(key, None)
    return isins, name_map, ticker_map


def _page_isin(text: str, canonical: set[str]) -> str | None:
    found = list(dict.fromkeys(i for i in re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text) if i in canonical))
    return found[0] if len(found) == 1 else None


def _line_value(lines: list[str], *labels: str) -> str | None:
    normalized = [_norm(x) for x in lines]
    targets = [_norm(x) for x in labels]
    for i, key in enumerate(normalized):
        if any(key == target or key.startswith(target) for target in targets):
            for raw in lines[i + 1:i + 5]:
                candidate = raw.strip()
                low = _norm(candidate)
                if candidate and low and not low.startswith("qu est ce") and low not in {"fermer", "chargement"}:
                    return candidate
    return None


def parse_profile_html(
    html: str,
    actions: pd.DataFrame,
    source_file: str = "",
) -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/cours/societe/profil/" not in url:
        return [], [], {"matched_rows": 0, "not_profile": True}
    text = soup.get_text("\n", strip=True)
    canonical, _, _ = _action_maps(actions)
    isin = _page_isin(text, canonical)
    if not isin:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "PROFILE_ISIN_NOT_UNIQUE"}], {"matched_rows": 0}
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    as_of = _as_of_from_text(_line_value(lines, "dernier échange", "dernier echange") or text)
    fields: dict[str, object] = {}

    sector = _line_value(lines, "secteur")
    if sector:
        fields["boursorama_sector"] = sector
        fields["sector_v21"] = sector
    index_name = _line_value(lines, "indice de référence", "indice de reference")
    if index_name:
        fields["boursorama_reference_index"] = index_name
    for labels, field in (
        (("ouverture",), "boursorama_open"),
        (("clôture veille", "cloture veille"), "boursorama_previous_close"),
        (("+ haut",), "boursorama_intraday_high"),
        (("+ bas",), "boursorama_intraday_low"),
        (("volume",), "boursorama_volume"),
        (("capital échangé", "capital echange"), "boursorama_capital_traded_pct"),
    ):
        raw = _line_value(lines, *labels)
        value = _num(raw)
        if value is not None:
            fields[field] = value

    market_cap = _num(_line_value(lines, "valorisation", "capitalisation boursière", "capitalisation boursiere"))
    if market_cap is not None:
        fields["boursorama_market_cap_eur_m"] = market_cap
        fields["market_cap"] = market_cap * 1_000_000.0

    per = _num(_line_value(lines, "per estimé", "per estime"))
    if per is not None:
        fields["boursorama_per_forward_current"] = per
        fields["per_forward_v21"] = per
    dy = _num(_line_value(lines, "rendement estimé", "rendement estime"))
    if dy is not None:
        fields["boursorama_dividend_yield_forward_current_pct"] = dy
        fields["dividend_yield_v21_pct"] = dy
    last_div = _line_value(lines, "dernier dividende", "dernier coupon")
    last_div_value = _num(last_div)
    if last_div_value is not None:
        fields["boursorama_last_dividend_amount_eur"] = last_div_value
    date_div = _date_iso(_line_value(lines, "date dernier dividende") or "")
    if not date_div and last_div:
        m = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", last_div)
        date_div = _date_iso(m.group(1)) if m else None
    if date_div:
        fields["boursorama_last_dividend_date"] = date_div

    employees = _num(_line_value(lines, "effectif"))
    if employees is not None:
        fields["boursorama_employees"] = int(round(employees))
    shares = _num(_line_value(lines, "nombre de titres"))
    if shares is not None:
        fields["boursorama_shares_outstanding"] = int(round(shares))
    market = _line_value(lines, "marché", "marche")
    if market:
        fields["boursorama_market_segment"] = market

    esg = re.search(r"Risque\s+ESG.*?([0-9]{1,2}(?:[,.][0-9]+)?)\s*/\s*100", text, flags=re.I | re.S)
    if esg:
        value = _num(esg.group(1))
        if value is not None:
            fields["morningstar_sustainalytics_esg_risk_bourso"] = value
            fields["morningstar_sustainalytics_esg_quality_bourso"] = round(100.0 - value, 4)

    normalized = _norm(text)
    if re.search(r"\beligibilite\b.{0,220}\bpea\b", normalized):
        fields["boursorama_pea_eligibility_observed"] = True

    observations = [
        _obs(
            isin, field, value, url=url, source_file=source_file, as_of=as_of,
            provider="Morningstar/Sustainalytics" if field.startswith("morningstar_") else "",
        )
        for field, value in fields.items()
    ]
    return observations, [], {"matched_rows": 1, "observations": len(observations), "isin": isin, "source_url": url}


def _row_values(frame: pd.DataFrame, *aliases: str) -> list[float]:
    if frame.empty:
        return []
    for _, row in frame.iterrows():
        label = _norm(row.iloc[0] if len(row) else "")
        if any(label == _norm(alias) or label.startswith(_norm(alias)) for alias in aliases):
            values: list[float] = []
            for value in row.iloc[1:]:
                parsed = _num(value)
                if parsed is not None:
                    values.append(parsed)
            return values
    return []


def _put_latest(fields: dict[str, object], prefix: str, values: list[float], *, growth: bool = False) -> None:
    if not values:
        return
    fields[prefix] = values[-1]
    if len(values) >= 2:
        fields[f"{prefix}_previous"] = values[-2]
        if growth and values[-2] != 0:
            fields[f"{prefix}_yoy_pct"] = round((values[-1] - values[-2]) / abs(values[-2]) * 100.0, 4)


def parse_key_figures_html(
    html: str,
    actions: pd.DataFrame,
    source_file: str = "",
) -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/cours/societe/chiffres-cles/" not in url:
        return [], [], {"matched_rows": 0, "not_key_figures": True}
    text = soup.get_text("\n", strip=True)
    canonical, _, _ = _action_maps(actions)
    isin = _page_isin(text, canonical)
    if not isin:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "KEY_FIGURES_ISIN_NOT_UNIQUE"}], {"matched_rows": 0}
    as_of = _as_of_from_text(text)
    frames = _tables(html)
    fields: dict[str, object] = {}

    for frame in frames:
        labels = " | ".join(_norm(v) for v in frame.iloc[:, 0].astype(str).tolist()) if not frame.empty else ""
        if "resultat net" in labels and "chiffre d affaires" in labels:
            _put_latest(fields, "boursorama_actual_revenue_k_eur", _row_values(frame, "Chiffre d'affaires"), growth=True)
            _put_latest(fields, "boursorama_actual_operating_income_k_eur", _row_values(frame, "Résultat opérationnel", "Resultat operationnel"), growth=True)
            _put_latest(fields, "boursorama_actual_net_income_k_eur", _row_values(frame, "Résultat net", "Resultat net"), growth=True)
            group = _row_values(frame, "Résultat net (part du groupe)", "Resultat net part du groupe")
            if group:
                _put_latest(fields, "boursorama_actual_group_net_income_k_eur", group, growth=True)
        if "dettes financieres" in labels and "total passif" in labels:
            current_debt = _row_values(frame, "Dettes financières courantes", "Dettes financieres courantes")
            noncurrent_debt = _row_values(frame, "Dettes financières non courantes", "Dettes financieres non courantes")
            _put_latest(fields, "boursorama_current_financial_debt_k_eur", current_debt)
            _put_latest(fields, "boursorama_noncurrent_financial_debt_k_eur", noncurrent_debt)
            if current_debt and noncurrent_debt:
                fields["boursorama_total_financial_debt_k_eur"] = current_debt[-1] + noncurrent_debt[-1]
            _put_latest(fields, "boursorama_total_assets_k_eur", _row_values(frame, "Total actif"))
            _put_latest(fields, "boursorama_total_liabilities_k_eur", _row_values(frame, "Total passif"))
            cash = _row_values(frame, "Trésorerie et équivalents", "Tresorerie et equivalents", "Disponibilités", "Disponibilites")
            if cash:
                _put_latest(fields, "boursorama_cash_k_eur", cash)
        if "marge operationnelle" in labels and "rentabilite financiere" in labels:
            _put_latest(fields, "boursorama_eps_actual_eur", _row_values(frame, "Résultat net part du groupe par action", "Resultat net part du groupe par action"))
            _put_latest(fields, "boursorama_eps_diluted_actual_eur", _row_values(frame, "Résultat net part du groupe dilué par action", "Resultat net part du groupe dilue par action"))
            _put_latest(fields, "boursorama_operating_margin_pct", _row_values(frame, "Marge opérationnelle", "Marge operationnelle"))
            _put_latest(fields, "boursorama_return_on_equity_pct", _row_values(frame, "Rentabilité financière", "Rentabilite financiere"))
            _put_latest(fields, "boursorama_debt_ratio_pct", _row_values(frame, "Ratio d'endettement", "Ratio d endettement"))
            employees = _row_values(frame, "Effectif en fin d'année", "Effectif en fin d annee")
            if employees:
                fields["boursorama_employees_latest"] = int(round(employees[-1]))
        if "chiffre d affaires 1er trimestre" in labels:
            for aliases, suffix in (
                (("Chiffre d'affaires 1er trimestre",), "q1"),
                (("Chiffre d'affaires 2eme trimestre", "Chiffre d'affaires 2ème trimestre"), "q2"),
                (("Chiffre d'affaires 3eme trimestre", "Chiffre d'affaires 3ème trimestre"), "q3"),
                (("Chiffre d'affaires 4eme trimestre", "Chiffre d'affaires 4ème trimestre"), "q4"),
                (("Chiffre d'affaires du 1er semestre",), "h1"),
                (("Chiffre d'affaires 2ème semestre", "Chiffre d'affaires 2eme semestre"), "h2"),
                (("Chiffre d'affaires de l'année", "Chiffre d'affaires de l annee"), "fy"),
            ):
                values = _row_values(frame, *aliases)
                if values and values[-1] != 0:
                    fields[f"boursorama_revenue_{suffix}_current_k_eur"] = values[-1]
                    if len(values) >= 2 and values[-2] != 0:
                        fields[f"boursorama_revenue_{suffix}_yoy_pct"] = round((values[-1] - values[-2]) / abs(values[-2]) * 100.0, 4)

    observations = [
        _obs(isin, field, value, url=url, source_file=source_file, as_of=as_of, provider="Cofisem")
        for field, value in fields.items()
    ]
    return observations, [], {"matched_rows": 1, "observations": len(observations), "isin": isin, "source_url": url}


def _find_col(frame: pd.DataFrame, *needles: str) -> str | None:
    for col in frame.columns:
        norm = _norm(col)
        if any(_norm(needle) in norm for needle in needles):
            return str(col)
    return None


def _map_row_isin(row: pd.Series, name_col: str | None, name_map: dict[str, str]) -> str:
    if not name_col:
        return ""
    return name_map.get(_compact(row.get(name_col)), "")


def parse_per_palmares_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/actions/palmares/per/" not in url:
        return [], [], {"matched_rows": 0, "not_per_palmares": True}
    _, name_map, _ = _action_maps(actions)
    as_of = datetime.now(timezone.utc).date().isoformat()
    current_year = datetime.now(timezone.utc).year
    observations: list[dict] = []
    failures: list[dict] = []
    matched: set[str] = set()
    for frame in _tables(html):
        name_col = _find_col(frame, "Libellé", "Libelle")
        if not name_col or not _find_col(frame, "PER"):
            continue
        for _, row in frame.iterrows():
            isin = _map_row_isin(row, name_col, name_map)
            if not isin:
                continue
            matched.add(isin)
            for year in range(current_year - 1, current_year + 2):
                per_col = _find_col(frame, f"PER {year}")
                bna_col = _find_col(frame, f"BNA {year}")
                if per_col:
                    value = _num(row.get(per_col))
                    if value is not None:
                        observations.append(_obs(isin, f"boursorama_per_{year}", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
                        if year == current_year:
                            observations.append(_obs(isin, "per_forward_v21", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
                if bna_col:
                    value = _num(row.get(bna_col))
                    if value is not None:
                        observations.append(_obs(isin, f"boursorama_bna_{year}_eur", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
    return observations, failures, {"matched_rows": len(matched), "observations": len(observations), "source_url": url}


def parse_dividend_palmares_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/actions/palmares/dividendes/" not in url:
        return [], [], {"matched_rows": 0, "not_dividend_palmares": True}
    _, name_map, _ = _action_maps(actions)
    as_of = datetime.now(timezone.utc).date().isoformat()
    current_year = datetime.now(timezone.utc).year
    observations: list[dict] = []
    matched: set[str] = set()
    for frame in _tables(html):
        name_col = _find_col(frame, "Libellé", "Libelle")
        if not name_col or not _find_col(frame, "Rend"):
            continue
        for _, row in frame.iterrows():
            isin = _map_row_isin(row, name_col, name_map)
            if not isin:
                continue
            matched.add(isin)
            for year in range(current_year - 1, current_year + 2):
                div_col = _find_col(frame, f"Div. {year}", f"Div {year}")
                yield_col = _find_col(frame, f"Rend. {year}", f"Rend {year}")
                if div_col:
                    value = _num(row.get(div_col))
                    if value is not None:
                        observations.append(_obs(isin, f"boursorama_dividend_{year}_eur", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
                if yield_col:
                    value = _num(row.get(yield_col))
                    if value is not None:
                        observations.append(_obs(isin, f"boursorama_dividend_yield_{year}_pct", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
                        if year == current_year:
                            observations.append(_obs(isin, "dividend_yield_v21_pct", value, url=url, source_file=source_file, as_of=as_of, provider="FactSet"))
    return observations, [], {"matched_rows": len(matched), "observations": len(observations), "source_url": url}


def parse_extremes_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/actions/palmares/extremes-annuels/" not in url:
        return [], [], {"matched_rows": 0, "not_extremes": True}
    _, name_map, _ = _action_maps(actions)
    as_of = datetime.now(timezone.utc).date().isoformat()
    observations: list[dict] = []
    matched: set[str] = set()
    for table in soup.find_all("table"):
        heading = table.find_previous(["h1", "h2", "h3"])
        heading_text = _norm(heading.get_text(" ", strip=True) if heading else "")
        if "plus haut" not in heading_text and "plus bas" not in heading_text:
            continue
        try:
            frame = _tables(str(table))[0]
        except (IndexError, ValueError):
            continue
        name_col = _find_col(frame, "Libellé", "Libelle")
        if not name_col:
            continue
        flag = "boursorama_touched_52w_high_flag" if "plus haut" in heading_text else "boursorama_touched_52w_low_flag"
        for _, row in frame.iterrows():
            isin = _map_row_isin(row, name_col, name_map)
            if not isin:
                continue
            matched.add(isin)
            observations.append(_obs(isin, flag, 1.0, url=url, source_file=source_file, as_of=as_of, provider=""))
            for needles, field in (
                (("Dernier",), "boursorama_extreme_last_price"),
                (("Var",), "boursorama_extreme_day_change_pct"),
                (("Vol",), "boursorama_extreme_volume"),
                (("+Haut", "+ Haut"), "boursorama_extreme_intraday_high"),
                (("+Bas", "+ Bas"), "boursorama_extreme_intraday_low"),
                (("Ouv",), "boursorama_extreme_open"),
            ):
                col = _find_col(frame, *needles)
                value = _num(row.get(col)) if col else None
                if value is not None:
                    observations.append(_obs(isin, field, value, url=url, source_file=source_file, as_of=as_of, provider=""))
    return observations, [], {"matched_rows": len(matched), "observations": len(observations), "source_url": url}


def parse_dividend_calendar_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/bourse/actualites/calendriers/dividendes" not in url:
        return [], [], {"matched_rows": 0, "not_dividend_calendar": True}
    _, name_map, _ = _action_maps(actions)
    today = datetime.now(timezone.utc).date()
    as_of = today.isoformat()
    candidates: dict[str, tuple[str, float | None, float | None, str]] = {}
    for frame in _tables(html):
        date_col = _find_col(frame, "Date")
        name_col = _find_col(frame, "Société", "Societe")
        event_col = _find_col(frame, "évènement", "evenement")
        amount_col = _find_col(frame, "Montant")
        yield_col = _find_col(frame, "Rendement")
        if not date_col or not name_col:
            continue
        for _, row in frame.iterrows():
            isin = _map_row_isin(row, name_col, name_map)
            if not isin:
                continue
            event_date = _date_iso(str(row.get(date_col) or ""), default_year=today.year)
            if not event_date:
                continue
            parsed_date = datetime.fromisoformat(event_date).date()
            if parsed_date < today and (today - parsed_date).days > 60:
                # A year-less January/December edge may belong to next year.
                rollover = _date_iso(str(row.get(date_col) or ""), default_year=today.year + 1)
                if rollover:
                    event_date = rollover
                    parsed_date = datetime.fromisoformat(rollover).date()
            event = str(row.get(event_col) or "").strip() if event_col else ""
            amount = _num(row.get(amount_col)) if amount_col else None
            yield_value = _num(row.get(yield_col)) if yield_col else None
            previous = candidates.get(isin)
            if previous is None or event_date < previous[0]:
                candidates[isin] = (event_date, amount, yield_value, event)
    observations: list[dict] = []
    for isin, (event_date, amount, yield_value, event) in candidates.items():
        days = (datetime.fromisoformat(event_date).date() - today).days
        observations.append(_obs(isin, "boursorama_next_dividend_event_date", event_date, url=url, source_file=source_file, as_of=as_of, provider="Cofisem/CercleFinance"))
        observations.append(_obs(isin, "boursorama_days_to_dividend_event", days, url=url, source_file=source_file, as_of=as_of, provider="Cofisem/CercleFinance"))
        if event:
            observations.append(_obs(isin, "boursorama_next_dividend_event_type", event, url=url, source_file=source_file, as_of=as_of, provider="Cofisem/CercleFinance"))
        if amount is not None:
            observations.append(_obs(isin, "boursorama_next_dividend_amount_eur", amount, url=url, source_file=source_file, as_of=as_of, provider="Cofisem/CercleFinance"))
        if yield_value is not None:
            observations.append(_obs(isin, "boursorama_next_dividend_event_yield_pct", yield_value, url=url, source_file=source_file, as_of=as_of, provider="Cofisem/CercleFinance"))
    return observations, [], {"matched_rows": len(candidates), "observations": len(observations), "source_url": url}


def parse_technical_html(html: str, actions: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "/cours/analyses/" not in url:
        return [], [], {"matched_rows": 0, "not_technical": True}
    text = soup.get_text("\n", strip=True)
    canonical, _, _ = _action_maps(actions)
    isin = _page_isin(text, canonical)
    if not isin:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "TECHNICAL_ISIN_NOT_UNIQUE"}], {"matched_rows": 0}
    match = re.search(r"SYNTHESE\s+(.*?)(?:information\s+fournie\s+par\s+TEC|\n\s*TEC\b)", text, flags=re.I | re.S)
    if not match:
        return [], [], {"matched_rows": 1, "observations": 0, "no_tec_summary": True}
    summary = re.sub(r"\s+", " ", match.group(1)).strip()[:900]
    low = _norm(summary)
    date_match = re.search(r"TEC[^0-9]{0,20}(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text, flags=re.I)
    as_of = _date_iso(date_match.group(1)) if date_match else _as_of_from_text(text)
    fields: dict[str, object] = {"boursorama_tec_summary": summary}
    if "macd est positif" in low:
        fields["boursorama_tec_macd_positive_flag"] = 1.0
    if "macd est negatif" in low:
        fields["boursorama_tec_macd_negative_flag"] = 1.0
    if "superieur a sa ligne de signal" in low:
        fields["boursorama_tec_macd_above_signal_flag"] = 1.0
    if "inferieur a sa ligne de signal" in low:
        fields["boursorama_tec_macd_below_signal_flag"] = 1.0
    if "rsi" in low and "surachat" in low:
        fields["boursorama_tec_rsi_overbought_flag"] = 1.0
    if "rsi" in low and "survente" in low:
        fields["boursorama_tec_rsi_oversold_flag"] = 1.0
    if "stochast" in low and "surachat" in low:
        fields["boursorama_tec_stoch_overbought_flag"] = 1.0
    if "stochast" in low and "survente" in low:
        fields["boursorama_tec_stoch_oversold_flag"] = 1.0
    observations = [
        _obs(isin, field, value, url=url, source_file=source_file, as_of=as_of or datetime.now(timezone.utc).date().isoformat(), provider="TEC", evidence="C")
        for field, value in fields.items()
    ]
    return observations, [], {"matched_rows": 1, "observations": len(observations), "isin": isin, "source_url": url}


def load_action_extended_pages(
    root: Path,
    actions: pd.DataFrame,
    relative_root: str = "inputs/boursorama_snapshots",
) -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    if not directory.exists():
        return [], [], {"files": 0, "observations": 0, "matched_rows": 0}
    parsers = (
        parse_profile_html,
        parse_key_figures_html,
        parse_per_palmares_html,
        parse_dividend_palmares_html,
        parse_extremes_html,
        parse_dividend_calendar_html,
        parse_technical_html,
    )
    observations: list[dict] = []
    failures: list[dict] = []
    stats = {"files": 0, "recognized_files": 0, "matched_rows": 0, "observations": 0, "by_parser": {}}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        stats["files"] += 1
        html = path.read_text(encoding="utf-8", errors="replace")
        recognized = False
        for parser in parsers:
            obs, failed, detail = parser(html, actions, str(path))
            if any(key.startswith("not_") and value for key, value in detail.items()):
                continue
            if detail.get("matched_rows", 0) or obs or failed:
                recognized = True
                name = parser.__name__
                item = stats["by_parser"].setdefault(name, {"files": 0, "matched_rows": 0, "observations": 0})
                item["files"] += 1
                item["matched_rows"] += int(detail.get("matched_rows", 0))
                item["observations"] += len(obs)
                stats["matched_rows"] += int(detail.get("matched_rows", 0))
                observations.extend(obs)
                failures.extend(failed)
                break
        if recognized:
            stats["recognized_files"] += 1
    stats["observations"] = len(observations)
    stats["failures"] = len(failures)
    return observations, failures, stats
