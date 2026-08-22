from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import math
import re
import time
from typing import Callable

import pandas as pd
from bs4 import BeautifulSoup

BOURSORAMA_BASE = "https://www.boursorama.com"
CACHE_VERSION = "BOURSORAMA_PUBLIC_V1"
_CONSENSUS_WEIGHTS = {"acheter": 5, "renforcer": 4, "conserver": 3, "alleger": 2, "vendre": 1}
_MISSING = {"", "-", "—", "N/A", "NA", "NONE", "NAN", "NULL", "<NA>"}


@dataclass(frozen=True)
class SnapshotResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: object, now: datetime) -> float:
    parsed = _parse_utc(value)
    if parsed is None:
        return math.inf
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def _ascii_key(value: object) -> str:
    text = str(value or "").strip().lower()
    table = str.maketrans("àâäáãåçéèêëíìîïñóòôöõúùûüýÿœ", "aaaaaaceeeeiiiinooooouuuuyyo")
    text = " ".join(text.translate(table).split())
    return re.sub(r"^\d+\.\s*", "", text)


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace("\u202f", " ").replace("\xa0", " ")
    if text.upper() in _MISSING:
        return None
    text = re.sub(r"[^0-9,\.\-+ ]", "", text).replace(" ", "")
    if not text or text in {"-", "+"}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        value_f = float(text)
    except ValueError:
        return None
    return value_f if math.isfinite(value_f) else None


def boursorama_code(row: object, universe: str = "ACTION") -> str | None:
    """Resolve only deterministic public Boursorama codes; never bulk-search the site."""
    getter = row.get if hasattr(row, "get") else lambda key, default=None: default
    explicit = str(getter("boursorama_code", "") or "").strip()
    if explicit and re.fullmatch(r"[A-Za-z0-9_-]{3,40}", explicit):
        return explicit
    ticker = str(getter("yahoo_ticker", "") or "").strip().upper()
    if not ticker or "." not in ticker:
        return None
    base, suffix = ticker.rsplit(".", 1)
    if not re.fullmatch(r"[A-Z0-9-]{1,20}", base):
        return None
    kind = str(universe or "ACTION").strip().upper()
    if kind == "ETF" and suffix == "PA":
        return f"1rT{base}"
    if kind == "ACTION":
        prefix = {"PA": "1rP", "AS": "1rA", "LS": "1rL"}.get(suffix)
        return f"{prefix}{base}" if prefix else None
    return None


def action_urls(code: str) -> dict[str, str]:
    safe = str(code).strip()
    return {
        "consensus": f"{BOURSORAMA_BASE}/cours/consensus/{safe}/",
        "key_figures": f"{BOURSORAMA_BASE}/cours/societe/chiffres-cles/{safe}/",
        "news": f"{BOURSORAMA_BASE}/cours/actualites/{safe}/",
    }


def etf_urls(code: str) -> dict[str, str]:
    safe = str(code).strip()
    return {
        "course": f"{BOURSORAMA_BASE}/bourse/trackers/cours/{safe}/",
        "risk": f"{BOURSORAMA_BASE}/bourse/trackers/cours/performances-risques/{safe}/",
        "composition": f"{BOURSORAMA_BASE}/bourse/trackers/cours/composition/{safe}/",
    }


def _read_tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []


def _first_column(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=object)
    return frame.iloc[:, 0].astype(str).map(_ascii_key)


def _find_consensus_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    required = set(_CONSENSUS_WEIGHTS)
    for frame in tables:
        labels = set(_first_column(frame).tolist())
        if len(required & labels) >= 4:
            return frame
    return None


def _column_position(frame: pd.DataFrame, needles: tuple[str, ...], fallback: int | None = None) -> int | None:
    labels = [_ascii_key(col) for col in frame.columns]
    for idx, label in enumerate(labels):
        if any(needle in label for needle in needles):
            return idx
    if fallback is None:
        return None
    return fallback if -len(labels) <= fallback < len(labels) else None


def _row_value(frame: pd.DataFrame, row_name: str, col_pos: int) -> float | None:
    labels = _first_column(frame)
    matches = frame.loc[labels.eq(_ascii_key(row_name))]
    if matches.empty or col_pos >= matches.shape[1]:
        return None
    return _number(matches.iloc[0, col_pos])


def _score_from_counts(counts: dict[str, int]) -> float | None:
    total = sum(max(0, int(counts.get(key, 0))) for key in _CONSENSUS_WEIGHTS)
    if total <= 0:
        return None
    return sum(max(0, int(counts.get(key, 0))) * weight for key, weight in _CONSENSUS_WEIGHTS.items()) / total


def _label_from_score(score: float) -> str:
    if score >= 4.5:
        return "STRONG_BUY"
    if score >= 3.5:
        return "BUY"
    if score >= 2.5:
        return "HOLD"
    if score >= 1.5:
        return "SELL"
    return "STRONG_SELL"


def _visible_text(html: str) -> str:
    try:
        return " ".join(BeautifulSoup(html, "lxml").stripped_strings)
    except Exception:
        return ""


def parse_action_consensus_html(html: str) -> dict[str, object]:
    """Parse FactSet consensus exposed on a public Boursorama action page.

    The five recommendation buckets are converted with exactly the same 5..1
    weights used by the existing Finnhub collector. Boursorama median targets are
    kept under provider-specific names and are never relabelled as Finnhub/Yahoo
    mean targets.
    """
    tables = _read_tables(html)
    table = _find_consensus_table(tables)
    if table is None:
        return {}
    current_col = table.shape[1] - 1
    previous_col = _column_position(table, ("1 mois", "1 month"))
    if previous_col is None and table.shape[1] >= 3:
        previous_col = table.shape[1] - 3

    current_counts: dict[str, int] = {}
    previous_counts: dict[str, int] = {}
    for key in _CONSENSUS_WEIGHTS:
        current = _row_value(table, key, current_col)
        if current is not None:
            current_counts[key] = max(0, int(round(current)))
        if previous_col is not None:
            previous = _row_value(table, key, previous_col)
            if previous is not None:
                previous_counts[key] = max(0, int(round(previous)))

    score = _score_from_counts(current_counts)
    if score is None:
        return {}
    fields: dict[str, object] = {
        "boursorama_consensus": _label_from_score(score),
        "boursorama_consensus_score": round(score, 4),
        "boursorama_consensus_score_100": round(score * 20.0, 4),
        "boursorama_buy_n": current_counts.get("acheter", 0) + current_counts.get("renforcer", 0),
        "boursorama_hold_n": current_counts.get("conserver", 0),
        "boursorama_sell_n": current_counts.get("alleger", 0) + current_counts.get("vendre", 0),
        "boursorama_n_analysts": sum(current_counts.values()),
        "boursorama_consensus_provider": "FactSet via Boursorama",
    }
    previous_score = _score_from_counts(previous_counts)
    if previous_score is not None:
        delta_100 = (score - previous_score) * 20.0
        current_net = fields["boursorama_buy_n"] - fields["boursorama_sell_n"]
        previous_net = (
            previous_counts.get("acheter", 0)
            + previous_counts.get("renforcer", 0)
            - previous_counts.get("alleger", 0)
            - previous_counts.get("vendre", 0)
        )
        fields["boursorama_consensus_delta_4w"] = round(delta_100, 4)
        fields["boursorama_net_upgrades_30d"] = int(current_net - previous_net)
        fields["boursorama_broker_weighted_revision_30d"] = round(delta_100, 4)

    labels = _first_column(table)
    for idx, label in enumerate(labels):
        if "objectif" in label and ("median" in label or "cours" in label):
            value = _number(table.iloc[idx, current_col])
            if value is not None:
                fields["boursorama_target_median"] = value
        if "note median" in label or "note med" in label:
            value = _number(table.iloc[idx, current_col])
            if value is not None:
                fields["boursorama_median_note"] = value

    visible = _visible_text(html)
    potentials = re.findall(r"\bPotentiel\s*:?\s*([+-]?\d+(?:[,.]\d+)?)\s*%", visible, flags=re.IGNORECASE)
    if potentials:
        value = _number(potentials[-1])
        if value is not None:
            fields["boursorama_target_upside_pct"] = value
    return fields


def parse_action_key_figures_html(html: str) -> dict[str, object]:
    """Extract explicit annual facts without semantic substitution into scoring fields."""
    fields: dict[str, object] = {}
    tables = _read_tables(html)
    for frame in tables:
        labels = _first_column(frame)
        if frame.shape[1] < 2:
            continue
        candidates = []
        for col in range(1, frame.shape[1]):
            values = [_number(x) for x in frame.iloc[:, col].tolist()]
            if any(v is not None and v != 0 for v in values):
                candidates.append(col)
        if not candidates:
            continue
        latest = candidates[-1]
        previous = candidates[-2] if len(candidates) >= 2 else None
        lookup = {label: idx for idx, label in enumerate(labels)}

        def exact(name: str) -> float | None:
            idx = lookup.get(_ascii_key(name))
            return _number(frame.iloc[idx, latest]) if idx is not None else None

        op_margin = exact("Marge opérationnelle (en %)")
        roe = exact("Rentabilité financière (en %)")
        debt_nc = exact("Dettes financières non courantes")
        debt_c = exact("Dettes financières courantes")
        revenue = exact("Chiffre d'affaires") or exact("Chiffre d'affaires de l'année")
        net_income = exact("Résultat net (part du groupe)")
        if op_margin is not None:
            fields["boursorama_operating_margin_pct"] = op_margin
        if roe is not None:
            fields["boursorama_roe_pct"] = roe
        if debt_nc is not None or debt_c is not None:
            fields["boursorama_total_financial_debt_thousands"] = (debt_nc or 0.0) + (debt_c or 0.0)
        if revenue is not None:
            fields["boursorama_revenue_thousands"] = revenue
        if net_income is not None:
            fields["boursorama_net_income_group_thousands"] = net_income

        if previous is not None:
            for label_name, out_name in (
                ("Chiffre d'affaires", "boursorama_revenue_growth_yoy_pct"),
                ("Résultat net (part du groupe)", "boursorama_net_income_growth_yoy_pct"),
            ):
                idx = lookup.get(_ascii_key(label_name))
                if idx is None:
                    continue
                cur = _number(frame.iloc[idx, latest])
                prev = _number(frame.iloc[idx, previous])
                if cur is not None and prev not in (None, 0):
                    fields[out_name] = round((cur / prev - 1.0) * 100.0, 6)
    return fields


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "entries": {}}
    if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return payload


def _save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _observation(isin: str, field: str, value: object, fetched_at: str, source_url: str) -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": "Boursorama public / FactSet-Cofisem",
        "source_url": source_url,
        "collected_at": fetched_at,
        "as_of": fetched_at[:10],
        "evidence_level": "B",
        "validation_status": "SHADOW_ATTRIBUTED",
    }


def collect_action_snapshots_cached(
    rows: pd.DataFrame,
    cache_path: str | Path,
    *,
    refresh_budget: int = 120,
    ttl_hours: float = 48.0,
    request_start_interval_seconds: float = 1.0,
    timeout_seconds: float = 15.0,
    refresh_due: bool = True,
    bootstrap_missing: bool = True,
    include_key_figures: bool = False,
    fetcher: Callable[..., object] | None = None,
    now: datetime | None = None,
) -> SnapshotResult:
    """Cache-first public snapshot collector; raw HTML is never persisted.

    Only deterministic Boursorama codes are requested. A response contributes a
    normalized field dictionary, source URL, fetch timestamp and SHA-256 page hash.
    The HTML itself is deliberately discarded.
    """
    current = (now or _now_utc()).astimezone(timezone.utc)
    path = Path(cache_path)
    payload = _load_cache(path)
    entries: dict[str, dict] = payload["entries"]
    supported: list[tuple[str, str]] = []
    failures: list[dict] = []
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin:
            continue
        code = boursorama_code(row, "ACTION")
        if code:
            supported.append((isin, code))
        else:
            failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DETERMINISTIC_CODE"})

    due: list[tuple[float, str, str]] = []
    for isin, code in supported:
        entry = entries.get(isin)
        age = _age_hours((entry or {}).get("fetched_at_utc"), current)
        missing = entry is None
        if (missing and bootstrap_missing) or (not missing and refresh_due and age >= float(ttl_hours)):
            due.append((-age if math.isfinite(age) else -1e12, isin, code))
    due.sort()
    selected = due[: max(0, int(refresh_budget))]

    if fetcher is None:
        import requests
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.14; public-data-cache)"})
        fetcher = session.get

    last_start = 0.0
    live_success = 0
    for _, isin, code in selected:
        wait = float(request_start_interval_seconds) - (time.monotonic() - last_start)
        if wait > 0:
            time.sleep(wait)
        urls = action_urls(code)
        try:
            last_start = time.monotonic()
            response = fetcher(urls["consensus"], timeout=timeout_seconds)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            html = str(getattr(response, "text", "") or "")
            fields = parse_action_consensus_html(html)
            hashes = {"consensus": sha256(html.encode("utf-8", errors="replace")).hexdigest()}
            source_urls = {"consensus": urls["consensus"]}
            if include_key_figures:
                wait = float(request_start_interval_seconds) - (time.monotonic() - last_start)
                if wait > 0:
                    time.sleep(wait)
                last_start = time.monotonic()
                response_k = fetcher(urls["key_figures"], timeout=timeout_seconds)
                if hasattr(response_k, "raise_for_status"):
                    response_k.raise_for_status()
                html_k = str(getattr(response_k, "text", "") or "")
                fields.update(parse_action_key_figures_html(html_k))
                hashes["key_figures"] = sha256(html_k.encode("utf-8", errors="replace")).hexdigest()
                source_urls["key_figures"] = urls["key_figures"]
            if not fields:
                failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_PARSEABLE_FIELDS", "url": urls["consensus"]})
                continue
            fetched = current.isoformat()
            entries[isin] = {
                "status": "OK",
                "boursorama_code": code,
                "fetched_at_utc": fetched,
                "fields": fields,
                "source_urls": source_urls,
                "page_sha256": hashes,
            }
            live_success += 1
        except Exception as exc:
            failures.append({
                "isin": isin,
                "source": "Boursorama",
                "reason": type(exc).__name__,
                "detail": str(exc)[:160],
                "url": urls["consensus"],
            })

    payload["updated_at_utc"] = current.isoformat()
    payload["policy"] = {
        "refresh_budget": int(refresh_budget),
        "ttl_hours": float(ttl_hours),
        "request_start_interval_seconds": float(request_start_interval_seconds),
        "raw_html_persisted": False,
        "deterministic_codes_only": True,
        "mode": "SHADOW_ATTRIBUTED",
    }
    _save_cache(path, payload)

    observations: list[dict] = []
    cache_hits = 0
    usable = 0
    selected_isins = {selected_isin for _, selected_isin, _ in selected}
    for isin, _code in supported:
        entry = entries.get(isin)
        if not entry or str(entry.get("status")) != "OK":
            continue
        age = _age_hours(entry.get("fetched_at_utc"), current)
        if age > max(float(ttl_hours) * 3.0, 168.0):
            continue
        usable += 1
        if isin not in selected_isins:
            cache_hits += 1
        fetched = str(entry.get("fetched_at_utc") or current.isoformat())
        urls = entry.get("source_urls") if isinstance(entry.get("source_urls"), dict) else {}
        consensus_url = str(urls.get("consensus") or action_urls(str(entry.get("boursorama_code") or ""))["consensus"])
        key_url = str(urls.get("key_figures") or consensus_url)
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        for field, value in fields.items():
            if value is None:
                continue
            source_url = key_url if field.startswith("boursorama_") and any(x in field for x in ("margin", "roe", "debt", "revenue", "net_income")) else consensus_url
            observations.append(_observation(isin, str(field), value, fetched, source_url))

    metrics = {
        "cache_version": CACHE_VERSION,
        "requested_rows": int(len(rows)),
        "deterministic_code_rows": len(supported),
        "unsupported_rows": int(len(rows) - len(supported)),
        "live_refresh_requested": len(selected),
        "live_refresh_success": live_success,
        "cache_hit_tickers": cache_hits,
        "usable_cached_tickers": usable,
        "observations": len(observations),
        "raw_html_persisted": False,
        "decision_influence": False,
    }
    return SnapshotResult(observations, failures, metrics)
