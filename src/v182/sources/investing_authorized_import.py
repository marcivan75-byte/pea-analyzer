from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Investing.com"
DEFAULT_ROOT = "inputs/investing_snapshots"
ALLOWED_TECH = {
    "STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL",
}


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%+./-]+", " ", text)).strip()


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    if not text or text.casefold() in {"nan", "none", "n/a", "-", "—"}:
        return None
    m = re.search(r"[-+]?\d[\d .]*(?:,\d+|\.\d+)?", text)
    if not m:
        return None
    token = m.group(0).replace(" ", "")
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


def _tech_enum(raw: object) -> str | None:
    value = _norm(raw)
    mapping = {
        "achat fort": "STRONG_BUY",
        "strong buy": "STRONG_BUY",
        "achat": "BUY",
        "buy": "BUY",
        "neutre": "NEUTRAL",
        "neutral": "NEUTRAL",
        "vente forte": "STRONG_SELL",
        "strong sell": "STRONG_SELL",
        "vente": "SELL",
        "sell": "SELL",
    }
    return mapping.get(value)


def _extract_period_signal(text: str, labels: tuple[str, ...]) -> str | None:
    # Longest labels first so "Achat Fort" cannot be truncated to "Achat".
    verdicts = r"(Vente\s+Forte|Achat\s+Fort|Neutre|Vente|Achat|Strong\s+Sell|Strong\s+Buy|Neutral|Sell|Buy)"
    for label in labels:
        m = re.search(rf"\b{re.escape(label)}\b\s*{verdicts}", text, flags=re.I)
        if m:
            return _tech_enum(m.group(1))
    return None


def _wm_confirmation(weekly: str | None, monthly: str | None) -> str:
    bullish = {"BUY", "STRONG_BUY"}
    bearish = {"SELL", "STRONG_SELL"}
    if weekly == "STRONG_BUY" and monthly == "STRONG_BUY":
        return "DOUBLE_STRONG_BUY"
    if weekly in bullish and monthly in bullish:
        return "BULLISH_CONFIRMED"
    if weekly == "STRONG_SELL" and monthly == "STRONG_SELL":
        return "DOUBLE_STRONG_SELL"
    if weekly in bearish and monthly in bearish:
        return "BEARISH_CONFIRMED"
    if (weekly in bullish and monthly in bearish) or (weekly in bearish and monthly in bullish):
        return "DIVERGENT"
    if weekly is None or monthly is None:
        return "INCOMPLETE"
    return "MIXED_NEUTRAL"


def _obs(isin: str, field: str, value, *, url: str, source_file: str, as_of: str, evidence: str = "C") -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": SOURCE,
        "source_url": url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "evidence_level": evidence,
        "validation_status": "ATTRIBUTED",
    }


def _as_of(text: str, fallback: str) -> str:
    dates = re.findall(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b", text)
    parsed: list[str] = []
    for day, month, year in dates:
        y = int(year)
        if y < 100:
            y += 2000
        try:
            parsed.append(datetime(y, int(month), int(day)).date().isoformat())
        except ValueError:
            pass
    return max(parsed) if parsed else fallback


def parse_technical_html(html: str, *, isin: str, source_file: str, source_date: str) -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "investing.com" not in url or "technical" not in url:
        return [], [{"source": SOURCE, "isin": isin, "source_file": source_file, "reason": "INVESTING_TECHNICAL_CANONICAL_URL_REQUIRED"}], {"matched": 0}
    text = soup.get_text(" ", strip=True)
    weekly = _extract_period_signal(text, ("Hebdomadaire", "Weekly"))
    monthly = _extract_period_signal(text, ("Mensuel", "Monthly"))
    daily = _extract_period_signal(text, ("Journalier", "Daily"))
    fields: dict[str, object] = {
        "investing_technical_daily": daily,
        "investing_technical_weekly": weekly,
        "investing_technical_monthly": monthly,
        "investing_weekly_monthly_confirmation": _wm_confirmation(weekly, monthly),
    }
    # Keep ordinal versions for future PIT/OOS research only. They are not active scoring fields.
    ordinal = {"STRONG_SELL": -2, "SELL": -1, "NEUTRAL": 0, "BUY": 1, "STRONG_BUY": 2}
    if weekly in ordinal:
        fields["investing_technical_weekly_ordinal_shadow"] = ordinal[weekly]
    if monthly in ordinal:
        fields["investing_technical_monthly_ordinal_shadow"] = ordinal[monthly]

    # Extract common indicator values visible in the currently rendered technical table.
    # They remain page-default-timeframe context and never replace internally computed PIT indicators.
    for label, field in (
        (r"RSI\s*\(14\)", "investing_rsi14_page_context"),
        (r"MACD\s*\(12\s*,\s*26\)", "investing_macd_12_26_page_context"),
        (r"ADX\s*\(14\)", "investing_adx14_page_context"),
        (r"STOCH\s*\(9\s*,\s*6\)", "investing_stoch_9_6_page_context"),
        (r"STOCHRSI\s*\(14\)", "investing_stochrsi14_page_context"),
        (r"CCI\s*\(14\)", "investing_cci14_page_context"),
    ):
        m = re.search(label + r"\s*([-+]?\d+(?:[,.]\d+)?)", text, flags=re.I)
        if m:
            value = _num(m.group(1))
            if value is not None:
                fields[field] = value

    as_of = _as_of(text, source_date)
    observations = [_obs(isin, field, value, url=url, source_file=source_file, as_of=as_of) for field, value in fields.items() if value is not None]
    return observations, [], {"matched": 1, "observations": len(observations), "weekly": weekly, "monthly": monthly, "daily": daily}


def parse_overview_html(html: str, *, isin: str, source_file: str, source_date: str) -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "investing.com" not in url or "/equities/" not in url:
        return [], [{"source": SOURCE, "isin": isin, "source_file": source_file, "reason": "INVESTING_EQUITY_CANONICAL_URL_REQUIRED"}], {"matched": 0}
    text = soup.get_text("\n", strip=True)
    # Prevent wrong-listing joins: Investing overview pages normally expose ISIN explicitly.
    observed_isins = re.findall(r"\b[A-Z]{2}[A-Z0-9]{10}\b", text)
    if observed_isins and isin not in observed_isins:
        return [], [{"source": SOURCE, "isin": isin, "source_file": source_file, "reason": "INVESTING_ISIN_MISMATCH"}], {"matched": 0}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = [_norm(line) for line in lines]

    def next_value(*labels: str) -> str | None:
        targets = {_norm(label) for label in labels}
        for idx, key in enumerate(normalized):
            if key in targets:
                for raw in lines[idx + 1:idx + 5]:
                    if raw and _norm(raw) not in targets:
                        return raw
        return None

    fields: dict[str, object] = {}
    for labels, field in (
        (("Capitalisation",), "investing_market_cap_reported"),
        (("PER",), "investing_per"),
        (("Ratio P/B",), "investing_price_to_book"),
        (("Rendement des Actifs",), "investing_roa_pct"),
        (("Rendement des fonds propres",), "investing_roe_pct"),
        (("Marge Bénéficiaire Brute",), "investing_gross_margin_pct"),
        (("EBITDA",), "investing_ebitda_reported"),
        (("EV/EBITDA",), "investing_ev_to_ebitda"),
        (("Bêta", "Beta"), "investing_beta"),
        (("Volume moyen (3m)",), "investing_avg_volume_3m_reported"),
        (("Variation sur 1 an",), "investing_perf_1y_pct"),
    ):
        raw = next_value(*labels)
        if raw is None:
            continue
        value = _num(raw)
        if value is not None:
            fields[field] = value
        fields[field + "_raw"] = raw

    sector = next_value("Secteur")
    industry = next_value("Industrie")
    market = next_value("Marché", "Marche")
    if sector:
        fields["investing_sector"] = sector
    if industry:
        fields["investing_industry"] = industry
    if market:
        fields["investing_market"] = market

    analyst = re.search(r"Sentiment\s+des\s+analystes\s+(Achat\s+Fort|Achat|Neutre|Vente\s+Forte|Vente|Strong\s+Buy|Buy|Neutral|Strong\s+Sell|Sell)", text, flags=re.I)
    if analyst:
        fields["investing_analyst_sentiment"] = _tech_enum(analyst.group(1))
    target = re.search(r"Objectif\s+moyen\s+([-+]?\d+(?:[,.]\d+)?)", text, flags=re.I)
    if target:
        fields["investing_analyst_target_mean"] = _num(target.group(1))
    div = re.search(r"Dividende\s+([-+]?\d+(?:[,.]\d+)?)\s*\(([-+]?\d+(?:[,.]\d+)?)%\)", text, flags=re.I)
    if div:
        fields["investing_dividend_per_share"] = _num(div.group(1))
        fields["investing_dividend_yield_pct"] = _num(div.group(2))
    range52 = re.search(r"Ecart\s+52\s+sem\.?\s+([-+]?\d+(?:[,.]\d+)?)\s+([-+]?\d+(?:[,.]\d+)?)", text, flags=re.I)
    if range52:
        fields["investing_52w_low"] = _num(range52.group(1))
        fields["investing_52w_high"] = _num(range52.group(2))

    as_of = _as_of(text, source_date)
    observations = [_obs(isin, field, value, url=url, source_file=source_file, as_of=as_of) for field, value in fields.items() if value is not None]
    return observations, [], {"matched": 1, "observations": len(observations)}


def load_authorized_snapshots(root: Path, actions: pd.DataFrame, relative_root: str = DEFAULT_ROOT) -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    manifest_path = directory / "INVESTING_MANIFEST.csv"
    if not manifest_path.exists():
        return [], [], {"status": "NO_MANIFEST", "files": 0, "observations": 0}
    manifest = pd.read_csv(manifest_path, sep=";", dtype=str).fillna("")
    required = {"filename", "isin", "source_url", "source_date", "authorization_confirmed"}
    if not required.issubset(manifest.columns):
        return [], [{"source": SOURCE, "reason": "MANIFEST_COLUMNS_MISSING", "required": "|".join(sorted(required))}], {"status": "BLOCKED_MANIFEST", "files": 0, "observations": 0}
    canonical = set(actions["isin"].astype(str).str.strip()) if "isin" in actions.columns else set()
    observations: list[dict] = []
    failures: list[dict] = []
    processed = 0
    blocked_authorization = 0
    for _, row in manifest.iterrows():
        filename = str(row.get("filename") or "").strip()
        isin = str(row.get("isin") or "").strip()
        authorized = str(row.get("authorization_confirmed") or "").strip().casefold() in {"true", "1", "yes", "oui", "licensed", "authorized"}
        if not authorized:
            blocked_authorization += 1
            failures.append({"source": SOURCE, "isin": isin, "source_file": filename, "reason": "INVESTING_AUTHORIZATION_REQUIRED"})
            continue
        if isin not in canonical:
            failures.append({"source": SOURCE, "isin": isin, "source_file": filename, "reason": "ISIN_OUTSIDE_CANONICAL_ACTION_UNIVERSE"})
            continue
        path = directory / filename
        if not path.exists():
            failures.append({"source": SOURCE, "isin": isin, "source_file": filename, "reason": "SNAPSHOT_FILE_MISSING"})
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        declared_url = str(row.get("source_url") or "")
        source_date = str(row.get("source_date") or datetime.now(timezone.utc).date().isoformat())
        canonical_url = _canonical_url(BeautifulSoup(html, "lxml"))
        if "investing.com" not in declared_url or (canonical_url and "investing.com" not in canonical_url):
            failures.append({"source": SOURCE, "isin": isin, "source_file": filename, "reason": "INVESTING_SOURCE_URL_REQUIRED"})
            continue
        if "technical" in (canonical_url or declared_url):
            obs, failed, _ = parse_technical_html(html, isin=isin, source_file=str(path), source_date=source_date)
        else:
            obs, failed, _ = parse_overview_html(html, isin=isin, source_file=str(path), source_date=source_date)
        observations.extend(obs)
        failures.extend(failed)
        processed += 1
    return observations, failures, {
        "status": "SUCCESS" if processed else "NO_AUTHORIZED_SNAPSHOT_PROCESSED",
        "files": processed,
        "observations": len(observations),
        "blocked_authorization_rows": blocked_authorization,
        "failures": len(failures),
        "direct_automated_fetch": False,
        "authorization_required": True,
    }
