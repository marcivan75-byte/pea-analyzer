from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup

PROFILE_MARKET_CAP_FIELDS = {"market_cap", "boursorama_market_cap_eur_m"}


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


def _profile_market_cap_raw(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = [_norm(line) for line in lines]
    labels = {"valorisation", "capitalisation boursiere"}
    for idx, label in enumerate(normalized):
        if label in labels or any(label.startswith(prefix) for prefix in labels):
            for raw in lines[idx + 1:idx + 5]:
                if _num(raw) is not None:
                    return raw
    # Some templates keep label and value in the same text node.
    match = re.search(
        r"(?:Valorisation|Capitalisation\s+boursi[èe]re)\s*[:\-]?\s*([0-9][0-9\s.,]*\s*M\s*[A-Z]{3})",
        text,
        flags=re.I,
    )
    return match.group(1).strip() if match else None


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).upper().replace("\u202f", " ").replace("\xa0", " ")
    # Boursorama displays values such as `170 064 M EUR`, `26 541 MNOK` or
    # `389 859 MSEK`. Accept either spaced or compact `M<CCY>` forms.
    match = re.search(r"\bM\s*([A-Z]{3})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\bM([A-Z]{3})\b", text)
    return match.group(1) if match else None


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
    """Prevent local-currency Boursorama market caps from entering EUR canonical data.

    The legacy profile parser stages the observed numeric market cap. This guard
    runs before evidence-aware merge. Canonical `market_cap` and the explicit
    `_eur_m` field are retained only when the saved Boursorama profile itself
    states EUR. Local/unknown currencies are preserved as attributed context and
    never converted implicitly.
    """
    by_file: dict[str, list[dict]] = {}
    for row in observations:
        if str(row.get("field")) in PROFILE_MARKET_CAP_FIELDS:
            by_file.setdefault(str(row.get("source_file") or ""), []).append(row)

    currency_by_file: dict[str, tuple[str | None, float | None, str | None]] = {}
    failures: list[dict] = []
    for source_file in by_file:
        path = _resolve_path(root, source_file)
        if path is None:
            currency_by_file[source_file] = (None, None, None)
            failures.append({
                "source": "Boursorama",
                "source_file": source_file,
                "reason": "PROFILE_MARKET_CAP_SOURCE_FILE_UNAVAILABLE_FOR_CURRENCY_GUARD",
            })
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        raw = _profile_market_cap_raw(html)
        currency_by_file[source_file] = (_currency(raw), _num(raw), raw)

    safe: list[dict] = []
    context_added: set[tuple[str, str]] = set()
    dropped_local = 0
    dropped_unknown = 0
    retained_eur = 0
    for row in observations:
        field = str(row.get("field") or "")
        if field not in PROFILE_MARKET_CAP_FIELDS:
            safe.append(row)
            continue
        source_file = str(row.get("source_file") or "")
        currency, reported_m, raw = currency_by_file.get(source_file, (None, None, None))
        if currency == "EUR":
            safe.append(row)
            retained_eur += 1
            continue

        if currency:
            dropped_local += 1
        else:
            dropped_unknown += 1
        key = (str(row.get("isin") or ""), source_file)
        if key not in context_added:
            template = row
            if reported_m is not None:
                safe.append(_context_observation(template, "boursorama_market_cap_reported_m", reported_m))
            if currency:
                safe.append(_context_observation(template, "boursorama_market_cap_currency", currency))
            if raw:
                safe.append(_context_observation(template, "boursorama_market_cap_reported_raw", raw))
            context_added.add(key)

    return safe, failures, {
        "profile_files_with_market_cap": len(by_file),
        "retained_eur_observations": retained_eur,
        "dropped_local_currency_observations": dropped_local,
        "dropped_unknown_currency_observations": dropped_unknown,
        "policy": "Canonical market_cap retained only when Boursorama profile explicitly states EUR; no implicit FX conversion.",
    }
