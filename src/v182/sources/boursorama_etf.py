from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import re
import pandas as pd
from bs4 import BeautifulSoup

from v182.sources.boursorama_resolver import resolve_boursorama_url
from v182.sources.rate_limit import StartRateLimiter


def extract_category_ranks(html: str) -> dict[str, int]:
    """Extract Boursorama/Morningstar annual category ranks exactly as published.

    Boursorama's performance-risk page displays five calendar-year columns and a
    `Rang` row. The category population is not published beside that row, so the
    collector deliberately keeps raw ranks and never invents a percentile.
    """
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    section = re.search(
        r"PERFORMANCES ANNUELLES DES 5 DERNI[ÈE]RES ANN[ÉE]ES(?P<body>.{0,1800}?)(?:performance volatilit[eé]|MESURE DE RISQUE|Liste des trackers)",
        text,
        flags=re.IGNORECASE,
    )
    body = section.group("body") if section else text
    years = re.findall(r"\b(20\d{2})\b", body)
    years = list(dict.fromkeys(years))[:5]
    rank_match = re.search(r"\bRang\b\s*((?:\d{1,5}|-)\s+(?:\d{1,5}|-)\s+(?:\d{1,5}|-)\s+(?:\d{1,5}|-)\s+(?:\d{1,5}|-))", body, flags=re.IGNORECASE)
    if not rank_match or len(years) < 1:
        return {}
    raw_ranks = re.findall(r"\d{1,5}|-", rank_match.group(1))
    out: dict[str, int] = {}
    for year, raw in zip(years, raw_ranks):
        if raw == "-":
            continue
        rank = int(raw)
        if rank >= 1:
            out[year] = rank
    return out


def extract_morningstar_category(html: str) -> str | None:
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"cat[eé]gorie morningstar\s+(.{2,120}?)(?=\s+(?:ouverture|cl[oô]ture|volume|dernier [eé]change|actif net|risque du fonds))", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_rank_as_of(html: str) -> str | None:
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).replace(" ", " ")
    match = re.search(r"Calcul fin de mois au\s+(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return pd.to_datetime(match.group(1), dayfirst=True).date().isoformat()
    except (ValueError, TypeError):
        return None


def _performance_url(base: str) -> str:
    clean = str(base or "").strip()
    if "/bourse/trackers/cours/performances-risques/" in clean:
        return clean
    if "/bourse/trackers/cours/" in clean:
        return clean.replace("/bourse/trackers/cours/", "/bourse/trackers/cours/performances-risques/", 1)
    return clean


def _fetch_one(row: pd.Series, requests, limiter: StartRateLimiter) -> tuple[str, dict[str, int], str | None, str | None, str | None]:
    isin = str(row.get("isin", "") or "").strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.6.3; +data-quality)",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    }
    resolved = resolve_boursorama_url(row, requests, limiter, headers)
    url = _performance_url(resolved or "")
    if not url or "/bourse/trackers/" not in url:
        return isin, {}, None, None, "BOURSORAMA_ETF_URL_NOT_RESOLVED"
    try:
        limiter.wait()
        response = requests.get(url, timeout=20, headers=headers)
        if response.status_code >= 400:
            return isin, {}, None, None, f"HTTP_{response.status_code}"
        ranks = extract_category_ranks(response.text)
        if not ranks:
            return isin, {}, extract_morningstar_category(response.text), extract_rank_as_of(response.text), "RANK_NOT_FOUND"
        return isin, ranks, extract_morningstar_category(response.text), extract_rank_as_of(response.text), None
    except Exception as exc:
        return isin, {}, None, None, type(exc).__name__


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.ParserError, UnicodeError):
        return pd.DataFrame()


def _rank_summary(ranks: dict[str, int]) -> dict[str, float | int | str | None]:
    if not ranks:
        return {}
    ordered = sorted(ranks.items(), key=lambda item: int(item[0]))
    values = [rank for _, rank in ordered]
    earliest_year, earliest_rank = ordered[0]
    latest_year, latest_rank = ordered[-1]
    return {
        "latest_year": latest_year,
        "latest_rank": latest_rank,
        "mean_rank": round(sum(values) / len(values), 4),
        "best_rank": min(values),
        "worst_rank": max(values),
        "annual_improvement": earliest_rank - latest_rank if len(values) >= 2 else None,
        "earliest_year": earliest_year,
    }


def fetch_boursorama_etf_rankings(
    etfs: pd.DataFrame,
    history_path: str | Path,
    *,
    requests_module=None,
    observed_at: datetime | None = None,
    max_workers: int = 6,
    delay_seconds: float = 0.25,
) -> tuple[list[dict], list[dict]]:
    import requests as requests_default

    requests = requests_module or requests_default
    now = observed_at or datetime.now(timezone.utc)
    candidates = etfs.drop_duplicates("isin").copy() if "isin" in etfs.columns else pd.DataFrame()
    if candidates.empty:
        return [], [{"source": "Boursorama", "reason": "NO_ETF_CANDIDATES"}]

    limiter = StartRateLimiter(delay_seconds)
    results: dict[str, tuple[dict[str, int], str | None, str | None]] = {}
    failures: list[dict] = []
    workers = max(1, min(int(max_workers), len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_fetch_one, row, requests, limiter) for _, row in candidates.iterrows()]
        for future in as_completed(futures):
            isin, ranks, category, rank_as_of, reason = future.result()
            if ranks:
                results[isin] = (ranks, category, rank_as_of)
            else:
                failures.append({"isin": isin, "source": "Boursorama", "reason": reason or "RANK_NOT_FOUND"})

    history_path = Path(history_path)
    history = _read_history(history_path)
    history_rows: list[dict] = []
    observations: list[dict] = []
    for isin, (ranks, category, rank_as_of) in sorted(results.items()):
        summary = _rank_summary(ranks)
        current_latest = int(summary["latest_rank"])
        previous_latest = None
        if not history.empty and "isin" in history.columns and "latest_rank" in history.columns:
            prior = history.loc[history["isin"].astype(str) == isin].copy()
            if not prior.empty:
                prior = prior.sort_values("observed_at")
                for value in reversed(prior["latest_rank"].tolist()):
                    try:
                        parsed = int(float(value))
                    except (TypeError, ValueError):
                        continue
                    if parsed >= 1:
                        previous_latest = parsed
                        break
        run_improvement = None if previous_latest is None else previous_latest - current_latest
        values: dict[str, object] = {
            "boursorama_category_name": category,
            "boursorama_category_rank_latest": current_latest,
            "boursorama_category_rank_latest_year": summary.get("latest_year"),
            "boursorama_category_rank_mean_5y": summary.get("mean_rank"),
            "boursorama_category_rank_best_5y": summary.get("best_rank"),
            "boursorama_category_rank_worst_5y": summary.get("worst_rank"),
            "boursorama_category_rank_annual_improvement": summary.get("annual_improvement"),
            "boursorama_category_rank_run_improvement": run_improvement,
            "boursorama_category_rank_as_of": rank_as_of or now.date().isoformat(),
        }
        for year, rank in ranks.items():
            values[f"boursorama_category_rank_{year}"] = rank
        for field, value in values.items():
            if value is None or value == "":
                continue
            observations.append({
                "universe": "ETF",
                "isin": isin,
                "field": field,
                "value": value,
                "source": "Boursorama / Morningstar ETF category rank",
                "collected_at": now.isoformat(),
                "as_of": rank_as_of or now.date().isoformat(),
                "evidence_level": "B",
                "validation_status": "AUTO_MATCH",
            })
        history_rows.append({
            "isin": isin,
            "observed_at": now.isoformat(),
            "rank_as_of": rank_as_of,
            "category": category,
            "latest_year": summary.get("latest_year"),
            "latest_rank": current_latest,
            "mean_rank": summary.get("mean_rank"),
            "annual_improvement": summary.get("annual_improvement"),
            "run_improvement": run_improvement,
            **{f"rank_{year}": rank for year, rank in ranks.items()},
        })

    if history_rows:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        updated = pd.concat([history, pd.DataFrame(history_rows)], ignore_index=True, sort=False)
        updated = updated.drop_duplicates(subset=["isin", "observed_at"], keep="last").sort_values(["isin", "observed_at"])
        updated.to_csv(history_path, index=False, encoding="utf-8-sig")
    failures.sort(key=lambda x: str(x.get("isin", "")))
    return observations, failures
