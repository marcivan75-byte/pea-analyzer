from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup

PROFILE_MARKET_CAP_FIELDS = {"market_cap", "boursorama_market_cap_eur_m"}
PROFILE_DIVIDEND_EUR_FIELD = "boursorama_last_dividend_amount_eur"
PROFILE_MONETARY_EUR_FIELDS = PROFILE_MARKET_CAP_FIELDS | {PROFILE_DIVIDEND_EUR_FIELD}


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+]+", " ", text)).strip()


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
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


def _line_value(html: str, labels: set[str]) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = [_norm(line) for line in lines]
    for idx, label in enumerate(normalized):
        if label in labels or any(label.startswith(prefix) for prefix in labels):
            for raw in lines[idx + 1:idx + 5]:
                if _num(raw) is not None:
                    return raw
    return None


def _profile_market_cap_raw(html: str) -> str | None:
    raw = _line_value(html, {"valorisation", "capitalisation boursiere"})
    if raw:
        return raw
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    match = re.search(
        r"(?:Valorisation|Capitalisation\s+boursi[èe]re)\s*[:\-]?\s*([0-9][0-9\s.,]*\s*M\s*[A-Z]{3})",
        text,
        flags=re.I,
    )
    return match.group(1).strip() if match else None


def _profile_dividend_raw(html: str) -> str | None:
    raw = _line_value(html, {"dernier dividende", "dernier coupon"})
    if raw:
        return raw
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    match = re.search(
        r"(?:Dernier\s+dividende|Dernier\s+coupon)\s*[:\-]?\s*([0-9][0-9\s.,]*\s*[A-Z]{3})",
        text,
        flags=re.I,
    )
    return match.group(1).strip() if match else None


def _market_cap_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).upper().replace("\u202f", " ").replace("\xa0", " ")
    # Boursorama displays values such as `170 064 M EUR`, `26 541 MNOK` or
    # `389 859 MSEK`. Accept both spaced and compact `M<CCY>` forms.
    match = re.search(r"\bM\s*([A-Z]{3})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\bM([A-Z]{3})\b", text)
    return match.group(1) if match else None


def _amount_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).upper().replace("\u202f", " ").replace("\xa0", " ")
    candidates = re.findall(r"\b([A-Z]{3})\b", text)
    # Prefer recognizable ISO-like currency tokens. Unknown tokens are not
    # treated as EUR; they are simply preserved as reported context.
    for token in candidates:
        if token in {"EUR", "USD", "GBP", "NOK", "SEK", "DKK", "CHF", "PLN", "CZK", "HUF"}:
            return token
    return candidates[-1] if candidates else None


def _resolve_path(root: Path, source_file: str) -> Path | None:
    if not source_file:
        return None
    candidate = Path(source_file)
    if candidate.exists():
        return candidate
    candidate = root / source_file
    return candidate if candidate.exists() else None


def _context_observation(template: dict, field: str, value) -> dict:
    row = dict(template)
    row["field"] = field
    row["value"] = value
    row["collected_at"] = datetime.now(timezone.utc).isoformat()
    row["validation_status"] = "ATTRIBUTED"
    row["evidence_level"] = "B"
    return row


def sanitize_profile_market_cap_observations(
    root: Path,
    observations: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    """Guard EUR-labelled profile monetary fields before evidence-aware merge.

    The profile parser stages numeric values. This runtime guard validates their
    displayed currency from the same saved Boursorama page. Canonical EUR market
    cap and EUR-labelled last-dividend amount are retained only when EUR is
    explicit. Local/unknown currencies remain attributed raw context; there is
    no implicit FX conversion.

    The function keeps its historical name for runtime compatibility.
    """
    by_file: dict[str, list[dict]] = {}
    for row in observations:
        if str(row.get("field")) in PROFILE_MONETARY_EUR_FIELDS:
            by_file.setdefault(str(row.get("source_file") or ""), []).append(row)

    info_by_file: dict[str, dict[str, object]] = {}
    failures: list[dict] = []
    for source_file in by_file:
        path = _resolve_path(root, source_file)
        if path is None:
            info_by_file[source_file] = {}
            failures.append({
                "source": "Boursorama",
                "source_file": source_file,
                "reason": "PROFILE_MONETARY_SOURCE_FILE_UNAVAILABLE_FOR_CURRENCY_GUARD",
            })
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        market_raw = _profile_market_cap_raw(html)
        dividend_raw = _profile_dividend_raw(html)
        info_by_file[source_file] = {
            "market_raw": market_raw,
            "market_value": _num(market_raw),
            "market_currency": _market_cap_currency(market_raw),
            "dividend_raw": dividend_raw,
            "dividend_value": _num(dividend_raw),
            "dividend_currency": _amount_currency(dividend_raw),
        }

    safe: list[dict] = []
    context_added: set[tuple[str, str, str]] = set()
    stats = {
        "profile_files_with_eur_labelled_monetary_fields": len(by_file),
        "market_cap_retained_eur_observations": 0,
        "market_cap_dropped_local_currency_observations": 0,
        "market_cap_dropped_unknown_currency_observations": 0,
        "dividend_retained_eur_observations": 0,
        "dividend_dropped_local_currency_observations": 0,
        "dividend_dropped_unknown_currency_observations": 0,
        "policy": "EUR-labelled profile monetary fields retained only when the saved Boursorama page explicitly states EUR; no implicit FX conversion.",
    }

    for row in observations:
        field = str(row.get("field") or "")
        if field not in PROFILE_MONETARY_EUR_FIELDS:
            safe.append(row)
            continue
        source_file = str(row.get("source_file") or "")
        info = info_by_file.get(source_file, {})
        isin = str(row.get("isin") or "")

        if field in PROFILE_MARKET_CAP_FIELDS:
            currency = info.get("market_currency")
            reported = info.get("market_value")
            raw = info.get("market_raw")
            if currency == "EUR":
                safe.append(row)
                stats["market_cap_retained_eur_observations"] += 1
                continue
            if currency:
                stats["market_cap_dropped_local_currency_observations"] += 1
            else:
                stats["market_cap_dropped_unknown_currency_observations"] += 1
            key = (isin, source_file, "market_cap")
            if key not in context_added:
                if reported is not None:
                    safe.append(_context_observation(row, "boursorama_market_cap_reported_m", reported))
                if currency:
                    safe.append(_context_observation(row, "boursorama_market_cap_currency", currency))
                if raw:
                    safe.append(_context_observation(row, "boursorama_market_cap_reported_raw", raw))
                context_added.add(key)
            continue

        currency = info.get("dividend_currency")
        reported = info.get("dividend_value")
        raw = info.get("dividend_raw")
        if currency == "EUR":
            safe.append(row)
            stats["dividend_retained_eur_observations"] += 1
            continue
        if currency:
            stats["dividend_dropped_local_currency_observations"] += 1
        else:
            stats["dividend_dropped_unknown_currency_observations"] += 1
        key = (isin, source_file, "last_dividend")
        if key not in context_added:
            if reported is not None:
                safe.append(_context_observation(row, "boursorama_last_dividend_amount_reported", reported))
            if currency:
                safe.append(_context_observation(row, "boursorama_last_dividend_currency", currency))
            if raw:
                safe.append(_context_observation(row, "boursorama_last_dividend_reported_raw", raw))
            context_added.add(key)

    return safe, failures, stats
