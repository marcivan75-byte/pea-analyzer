from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import json
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama"
EVIDENCE = "B"
DEFAULT_ROOT = "inputs/boursorama_snapshots"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ascii(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).strip()


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", _ascii(value)).strip().casefold()


def _num(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).replace("\u202f", " ").replace("\xa0", " ").strip()
    if not text or text in {"-", "—"}:
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


def _date_iso(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).date().isoformat()


def _obs(universe: str, isin: str, field: str, value, *, as_of: str, source_url: str, source_file: str, provider: str = "") -> dict:
    return {
        "universe": universe,
        "isin": isin,
        "field": field,
        "value": value,
        "source": SOURCE if not provider else f"{SOURCE}/{provider}",
        "source_url": source_url,
        "source_file": source_file,
        "collected_at": _now(),
        "as_of": as_of,
        "evidence_level": EVIDENCE,
        "validation_status": "ATTRIBUTED",
    }


def _source_url_from_html(soup: BeautifulSoup, fallback: str = "") -> str:
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href"):
        return str(canonical.get("href"))
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        return str(og.get("content"))
    return fallback


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        cols = []
        for col in out.columns:
            parts = [str(x).strip() for x in col if str(x).strip() and not str(x).startswith("Unnamed")]
            cols.append(" | ".join(dict.fromkeys(parts)))
        out.columns = cols
    else:
        out.columns = [str(c).strip() for c in out.columns]
    return out


def _read_tables(html: str) -> list[pd.DataFrame]:
    """Read French Boursorama tables without turning 1,81 into 181."""
    try:
        return [
            _flatten_columns(frame)
            for frame in pd.read_html(StringIO(html), decimal=",", thousands=" ")
        ]
    except (ValueError, ImportError):
        return []


def _row_values(frame: pd.DataFrame, aliases: tuple[str, ...]) -> list[object] | None:
    if frame.empty:
        return None
    for _, row in frame.iterrows():
        first = _norm(row.iloc[0] if len(row) else "")
        if any(first.startswith(_norm(alias)) for alias in aliases):
            return list(row.iloc[1:])
    return None


def _clean_values(values: list[object] | None) -> list[float]:
    if not values:
        return []
    result = []
    for value in values:
        parsed = _num(value)
        if parsed is not None:
            result.append(parsed)
    return result


def _consensus_fields(tables: list[pd.DataFrame], text: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    for frame in tables:
        labels = [_norm(v) for v in frame.iloc[:, 0].astype(str).tolist()] if not frame.empty else []
        if not any("nombre d'analystes" in label for label in labels):
            continue
        rows = {
            "buy": _clean_values(_row_values(frame, ("1. Acheter", "Acheter"))),
            "reinforce": _clean_values(_row_values(frame, ("2. Renforcer", "Renforcer"))),
            "hold": _clean_values(_row_values(frame, ("3. Conserver", "Conserver"))),
            "reduce": _clean_values(_row_values(frame, ("4. Alléger", "Alleger"))),
            "sell": _clean_values(_row_values(frame, ("5. Vendre", "Vendre"))),
            "analysts": _clean_values(_row_values(frame, ("Nombre d'analystes",))),
            "median": _clean_values(_row_values(frame, ("Note médiane", "Note mediane"))),
            "target": _clean_values(_row_values(frame, ("Historique des objectifs de cours médian", "Historique des objectifs de cours median"))),
        }
        for key in ("buy", "reinforce", "hold", "reduce", "sell"):
            if rows[key]:
                fields[f"boursorama_consensus_{key}_count"] = int(round(rows[key][-1]))
        if rows["analysts"]:
            fields["boursorama_consensus_analysts"] = int(round(rows["analysts"][-1]))
        if rows["median"]:
            current = rows["median"][-1]
            fields["boursorama_consensus_note_median"] = current
            if 1.0 <= current <= 5.0:
                fields["consensus_score_100_v21"] = round((5.0 - current) / 4.0 * 100.0, 4)
            if len(rows["median"]) >= 3:
                one_month = rows["median"][-3]
                fields["boursorama_consensus_note_1m"] = one_month
                fields["consensus_delta_4w"] = round(one_month - current, 4)
        if rows["target"]:
            fields["boursorama_target_price"] = rows["target"][-1]
            if len(rows["target"]) >= 3:
                fields["boursorama_target_price_1m"] = rows["target"][-3]
        break
    potential = re.search(r"Potentiel\s*:\s*([-+]?\d+(?:[,.]\d+)?)\s*%", text, flags=re.IGNORECASE)
    if potential:
        parsed = _num(potential.group(1))
        if parsed is not None:
            fields["boursorama_target_upside_pct"] = parsed
            fields["target_upside_pct_v21"] = parsed
    return fields


def _forecast_fields(tables: list[pd.DataFrame]) -> dict[str, object]:
    """Keep FactSet realized/forward tables as raw attributed fields.

    The canonical current forward PER/yield are supplied by the summary block or
    the dated bulk-consensus page, avoiding equal-date conflicts between two
    Boursorama representations of the same concept.
    """
    fields: dict[str, object] = {}
    row_specs = {
        "Bénéfice net par action": ("boursorama_eps_reported", "boursorama_eps_forward_1y", "boursorama_eps_forward_2y"),
        "Benefice net par action": ("boursorama_eps_reported", "boursorama_eps_forward_1y", "boursorama_eps_forward_2y"),
        "PER": ("boursorama_per_reported", "boursorama_per_forward_1y", "boursorama_per_forward_2y"),
        "Dividende par action": ("boursorama_dividend_per_share_reported", "boursorama_dividend_per_share_forward_1y", "boursorama_dividend_per_share_forward_2y"),
        "Rendement": ("boursorama_dividend_yield_reported_pct", "boursorama_dividend_yield_forward_1y_pct", "boursorama_dividend_yield_forward_2y_pct"),
        "Chiffre d'affaires": ("boursorama_revenue_reported_m", "boursorama_revenue_forward_1y_m", "boursorama_revenue_forward_2y_m"),
        "EBITDA": ("boursorama_ebitda_reported", "boursorama_ebitda_forward_1y", "boursorama_ebitda_forward_2y"),
        "EBIT": ("boursorama_ebit_reported", "boursorama_ebit_forward_1y", "boursorama_ebit_forward_2y"),
        "Dette financière nette": ("boursorama_net_debt_reported", "boursorama_net_debt_forward_1y", "boursorama_net_debt_forward_2y"),
        "Dette financiere nette": ("boursorama_net_debt_reported", "boursorama_net_debt_forward_1y", "boursorama_net_debt_forward_2y"),
        "Actif net par action": ("boursorama_book_value_per_share_reported", "boursorama_book_value_per_share_forward_1y", "boursorama_book_value_per_share_forward_2y"),
        "Cash Flow par action": ("boursorama_cash_flow_per_share_reported", "boursorama_cash_flow_per_share_forward_1y", "boursorama_cash_flow_per_share_forward_2y"),
    }
    seen: set[str] = set()
    for frame in tables:
        for label, targets in row_specs.items():
            if targets[0] in seen:
                continue
            values = _clean_values(_row_values(frame, (label,)))
            if not values:
                continue
            if len(values) > 3:
                economic = values[::2][:3]
                if len(economic) == 3:
                    values = economic
            for target, value in zip(targets, values[:3]):
                fields[target] = value
            seen.add(targets[0])
    return fields


def _summary_fields(text: str, soup: BeautifulSoup) -> dict[str, object]:
    fields: dict[str, object] = {}
    market_cap = re.search(r"valorisation\s+([0-9\s.,]+)\s*M\s*EUR", text, flags=re.IGNORECASE)
    if market_cap:
        value = _num(market_cap.group(1))
        if value is not None:
            fields["boursorama_market_cap_eur_m"] = value
            fields["market_cap"] = value * 1_000_000.0
    esg = re.search(r"Risque\s+ESG.*?([0-9]{1,2}(?:[,.][0-9]+)?)\s*/\s*100", text, flags=re.IGNORECASE | re.DOTALL)
    if esg:
        value = _num(esg.group(1))
        if value is not None:
            fields["morningstar_sustainalytics_esg_risk_bourso"] = value
            fields["morningstar_sustainalytics_esg_quality_bourso"] = round(100.0 - value, 4)
    carbon = re.search(r"Tonnes\s+de\s+CO.?\s+émises.*?:\s*([0-9\s.,]+)", text, flags=re.IGNORECASE)
    if carbon:
        value = _num(carbon.group(1))
        if value is not None:
            fields["morningstar_sustainalytics_carbon_intensity_bourso"] = value
    controversy = re.search(r"Niveau\s+de\s+controverse\s*:\s*([^\n|]+)", text, flags=re.IGNORECASE)
    if controversy:
        fields["morningstar_sustainalytics_controversy_bourso"] = controversy.group(1).strip()
    sector_label = soup.find(string=lambda s: isinstance(s, str) and _norm(s) == "secteur")
    if sector_label is not None:
        parent = sector_label.parent
        candidates = list(parent.find_all_next(["a", "span", "div"], limit=8)) if parent is not None else []
        for node in candidates:
            candidate = node.get_text(" ", strip=True)
            if candidate and _norm(candidate) not in {"secteur", "indice de reference"} and len(candidate) < 80:
                fields["boursorama_sector"] = candidate
                fields["sector_v21"] = candidate
                break
    normalized = _norm(text)
    if re.search(r"\beligibilite\b.{0,220}\bpea\b", normalized):
        fields["boursorama_pea_eligibility_observed"] = True
    return fields


def parse_action_html(html: str, *, canonical_action_isins: set[str], source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    found = [isin for isin in re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text) if isin in canonical_action_isins]
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "ACTION_ISIN_NOT_UNIQUE", "matches": len(unique)}], {"rows": 0}
    isin = unique[0]
    source_url = _source_url_from_html(soup)
    if "boursorama.com" not in source_url:
        return [], [{"source": SOURCE, "isin": isin, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}], {"rows": 0}
    date_matches = re.findall(r"(?:au|le)\s+(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})", text, flags=re.IGNORECASE)
    as_of = max((_date_iso(v) for v in date_matches), default=datetime.now(timezone.utc).date().isoformat())
    tables = _read_tables(html)
    fields = {}
    fields.update(_summary_fields(text, soup))
    fields.update(_consensus_fields(tables, text))
    fields.update(_forecast_fields(tables))
    observations = []
    for field, value in fields.items():
        provider = (
            "FactSet" if field.startswith(("boursorama_consensus", "boursorama_target", "boursorama_eps", "boursorama_per", "boursorama_revenue", "boursorama_ebit", "boursorama_net_debt", "boursorama_book", "boursorama_cash_flow", "boursorama_dividend"))
            or field in {"consensus_score_100_v21", "consensus_delta_4w", "target_upside_pct_v21"}
            else "Morningstar/Sustainalytics" if field.startswith("morningstar_")
            else ""
        )
        observations.append(_obs("ACTION", isin, field, value, as_of=as_of, source_url=source_url, source_file=source_file, provider=provider))
    return observations, [], {"rows": 1, "isin": isin, "fields": len(fields), "as_of": as_of, "source_url": source_url}


def _column(frame: pd.DataFrame, *names: str) -> str | None:
    normalized = {_norm(c): str(c) for c in frame.columns}
    for name in names:
        target = _norm(name)
        for norm_name, original in normalized.items():
            if target == norm_name or target in norm_name:
                return original
    return None


def _name_map(etfs: pd.DataFrame) -> dict[str, str]:
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    if "name" not in etfs.columns or "isin" not in etfs.columns:
        return result
    for _, row in etfs.iterrows():
        key = re.sub(r"[^a-z0-9]+", "", _norm(row.get("name")))
        isin = str(row.get("isin") or "").strip()
        if not key or not isin:
            continue
        if key in result and result[key] != isin:
            duplicates.add(key)
        else:
            result[key] = isin
    for key in duplicates:
        result.pop(key, None)
    return result


def parse_etf_html(html: str, *, etfs: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    source_url = _source_url_from_html(soup)
    if "boursorama.com" not in source_url:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}], {"rows": 0}
    canonical = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    names = _name_map(etfs)
    observations: list[dict] = []
    failures: list[dict] = []
    matched: set[str] = set()
    as_of = datetime.now(timezone.utc).date().isoformat()
    for frame in _read_tables(html):
        isin_col = _column(frame, "Isin", "ISIN")
        name_col = _column(frame, "Libellé", "Libelle", "Nom")
        rating_col = _column(frame, "Notation", "Morningstar")
        category_col = _column(frame, "Catégorie Morningstar", "Categorie Morningstar")
        risk_col = _column(frame, "Risque", "Indicateur de risque")
        perf1y_col = _column(frame, "Perf. 1 an", "Perf 1 an")
        last_col = _column(frame, "Dernier")
        currency_col = _column(frame, "Devise")
        if not any((isin_col, name_col)):
            continue
        for _, row in frame.iterrows():
            isin = str(row.get(isin_col) or "").strip() if isin_col else ""
            if isin not in canonical and name_col:
                name_key = re.sub(r"[^a-z0-9]+", "", _norm(row.get(name_col)))
                isin = names.get(name_key, "")
            if not isin or isin not in canonical:
                continue
            matched.add(isin)
            fields: dict[str, object] = {}
            if rating_col:
                rating = _num(row.get(rating_col))
                if rating is not None and 0 <= rating <= 5:
                    fields["morningstar_rating"] = rating
                    fields["boursorama_morningstar_rating"] = rating
            if category_col:
                category = str(row.get(category_col) or "").strip()
                if category and category.lower() != "nan":
                    fields["morningstar_category"] = category
                    fields["boursorama_morningstar_category"] = category
            if risk_col:
                risk = _num(row.get(risk_col))
                if risk is not None and 0 <= risk <= 7:
                    fields["risk_indicator"] = risk
                    fields["boursorama_risk_indicator"] = risk
            if perf1y_col:
                value = _num(row.get(perf1y_col))
                if value is not None:
                    fields["boursorama_perf_1y_pct"] = value
            if last_col:
                value = _num(row.get(last_col))
                if value is not None:
                    fields["boursorama_last_price"] = value
            if currency_col:
                currency = str(row.get(currency_col) or "").strip()
                if currency and currency.lower() != "nan":
                    fields["boursorama_currency"] = currency
            for field, value in fields.items():
                provider = "Morningstar" if field.startswith("morningstar") or "risk_indicator" in field else ""
                observations.append(_obs("ETF", isin, field, value, as_of=as_of, source_url=source_url, source_file=source_file, provider=provider))
    if not matched:
        failures.append({"source": SOURCE, "source_file": source_file, "reason": "NO_CANONICAL_ETF_MATCH"})
    return observations, failures, {"rows": len(matched), "fields": len(observations), "source_url": source_url}


def _read_manual_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str).fillna("")
    return pd.DataFrame()


def parse_manual_table(path: Path, actions: pd.DataFrame, etfs: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    frame = _read_manual_table(path)
    if frame.empty:
        return [], [{"source": SOURCE, "source_file": str(path), "reason": "EMPTY_OR_UNSUPPORTED_TABLE"}], {"rows": 0}
    required = {"isin", "source_url", "source_date"}
    if not required.issubset(frame.columns):
        return [], [{"source": SOURCE, "source_file": str(path), "reason": "TABLE_REQUIRES_ISIN_SOURCE_URL_SOURCE_DATE"}], {"rows": 0}
    action_isins = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    etf_isins = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    meta = {"isin", "source_url", "source_date", "name", "notes"}
    observations = []
    failures = []
    rows = 0
    for _, row in frame.iterrows():
        isin = str(row.get("isin") or "").strip()
        url = str(row.get("source_url") or "").strip()
        as_of = _date_iso(str(row.get("source_date") or ""))
        if "boursorama.com" not in url:
            failures.append({"source": SOURCE, "isin": isin, "reason": "NON_BOURSORAMA_URL"})
            continue
        universe = "ACTION" if isin in action_isins else "ETF" if isin in etf_isins else ""
        if not universe:
            failures.append({"source": SOURCE, "isin": isin, "reason": "ISIN_OUTSIDE_CANONICAL_UNIVERSE"})
            continue
        rows += 1
        for field in frame.columns:
            if field in meta:
                continue
            value = row.get(field)
            if value is None or str(value).strip() == "":
                continue
            observations.append(_obs(universe, isin, field, value, as_of=as_of, source_url=url, source_file=str(path)))
    return observations, failures, {"rows": rows, "fields": len(observations)}


def load_boursorama_imports(root: Path, actions: pd.DataFrame, etfs: pd.DataFrame, *, relative_root: str = DEFAULT_ROOT) -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    directory.mkdir(parents=True, exist_ok=True)
    action_isins = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    observations: list[dict] = []
    failures: list[dict] = []
    stats = {"directory": relative_root, "files": 0, "action_pages": 0, "etf_pages": 0, "bulk_pages_deferred": 0, "manual_tables": 0, "observations": 0}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix not in {".html", ".htm", ".csv", ".xlsx", ".xlsm"}:
            continue
        stats["files"] += 1
        if suffix in {".csv", ".xlsx", ".xlsm"}:
            obs, failed, _detail = parse_manual_table(path, actions, etfs)
            stats["manual_tables"] += 1
        else:
            html = path.read_text(encoding="utf-8", errors="replace")
            text = _norm(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
            if ("nb analyst" in text or "nombre d analystes" in text) and "obj cours" in text and "libelle" in text:
                obs, failed = [], []
                stats["bulk_pages_deferred"] += 1
            elif "trackers" in text or ("morningstar" in text and "isin" in text and "categorie" in text):
                obs, failed, _detail = parse_etf_html(html, etfs=etfs, source_file=str(path))
                stats["etf_pages"] += 1
            else:
                obs, failed, _detail = parse_action_html(html, canonical_action_isins=action_isins, source_file=str(path))
                stats["action_pages"] += 1
        observations.extend(obs)
        failures.extend(failed)
    stats["observations"] = len(observations)
    stats["failures"] = len(failures)
    return observations, failures, stats


def write_capture_worklist(root: Path, actions: pd.DataFrame, etfs: pd.DataFrame, output_path: Path) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for _, row in actions.iterrows():
        isin = str(row.get("isin") or "").strip()
        name = str(row.get("name") or "").strip()
        ticker = str(row.get("yahoo_ticker") or "").strip()
        comite = str(row.get("comite_status") or "").upper()
        missing = []
        for field in ("consensus_score_100_v21", "target_upside_pct_v21", "per_forward_v21"):
            value = row.get(field)
            if value is None or str(value).strip().lower() in {"", "nan", "none", "non_observe"}:
                missing.append(field)
        priority = "P0" if comite in {"COMMITTEE", "WATCH"} else "P1" if missing else "P2"
        suggested = ""
        if ticker.endswith(".PA"):
            symbol = ticker[:-3]
            if symbol:
                suggested = f"https://www.boursorama.com/cours/consensus/1rP{symbol}/"
        rows.append({
            "priority": priority,
            "universe": "ACTION",
            "isin": isin,
            "name": name,
            "yahoo_ticker": ticker,
            "missing_high_value_fields": "|".join(missing),
            "suggested_manual_url_when_unambiguous": suggested,
            "capture_instruction": "Open manually in browser and save the Boursorama consensus page as HTML, then place it under inputs/boursorama_snapshots/actions/.",
        })
    for _, row in etfs.iterrows():
        rows.append({
            "priority": "P0",
            "universe": "ETF",
            "isin": str(row.get("isin") or "").strip(),
            "name": str(row.get("name") or "").strip(),
            "yahoo_ticker": str(row.get("yahoo_ticker") or "").strip(),
            "missing_high_value_fields": "morningstar_rating|risk_indicator|morningstar_category",
            "suggested_manual_url_when_unambiguous": "https://www.boursorama.com/bourse/trackers/recherche/",
            "capture_instruction": "Save the Boursorama ETF characteristics/risk search results as HTML; the importer matches by ISIN first.",
        })
    work = pd.DataFrame(rows)
    order = pd.Categorical(work["priority"], categories=["P0", "P1", "P2"], ordered=True)
    work = work.assign(_priority=order).sort_values(["_priority", "universe", "name", "isin"]).drop(columns=["_priority"])
    work.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")
    return {"rows": len(work), "p0": int((work["priority"] == "P0").sum()), "path": str(output_path)}


def write_import_audit(root: Path, observations: list[dict], failures: list[dict], stats: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    obs_path = output_dir / "BOURSORAMA_IMPORT_OBSERVATIONS.csv"
    failures_path = output_dir / "BOURSORAMA_IMPORT_FAILURES.csv"
    summary_path = output_dir / "BOURSORAMA_IMPORT_SUMMARY.json"
    if observations:
        pd.DataFrame(observations).to_csv(obs_path, sep=";", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["universe", "isin", "field", "value", "source", "source_url", "as_of"]).to_csv(obs_path, sep=";", index=False, encoding="utf-8-sig")
    if failures:
        pd.DataFrame(failures).to_csv(failures_path, sep=";", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["source", "reason"]).to_csv(failures_path, sep=";", index=False, encoding="utf-8-sig")
    summary = {
        "status": "SUCCESS" if observations else "NO_ATTRIBUTED_BOURSORAMA_INPUT",
        "mode": "USER_OR_AUTHORIZED_SNAPSHOT_IMPORT_ONLY",
        "direct_automated_retrieval": False,
        "reason": "Boursorama CGU prohibit automated recovery; only user/authorized attributed exports are ingested.",
        "high_value_action_fields": [
            "consensus_score_100_v21", "consensus_delta_4w", "target_upside_pct_v21", "per_forward_v21",
            "dividend_yield_v21_pct", "market_cap", "BPA/EPS forecasts", "revenue/EBITDA/EBIT/net debt/book value/cash-flow forecasts",
            "Morningstar/Sustainalytics ESG context",
        ],
        "high_value_etf_fields": ["morningstar_rating", "morningstar_category", "risk_indicator", "performance/context"],
        "score_weight_changes": False,
        "t1_t2_score_influence": 0.0,
        "live_orders_enabled": False,
        **stats,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"observations_csv": str(obs_path), "failures_csv": str(failures_path), "summary_json": str(summary_path), **summary}
