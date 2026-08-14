from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import math
import re
import pandas as pd
from bs4 import BeautifulSoup

from v182.sources.rate_limit import StartRateLimiter

PERIODS = {
    "1m": ("1 mois", "1m", "perf_1m_cat_rank_pctl"),
    "3m": ("3 mois", "3m", "perf_3m_cat_rank_pctl"),
    "6m": ("6 mois", "6m", "perf_6m_cat_rank_pctl"),
    "1y": ("1 an", "1 année", "1y", "perf_1y_cat_rank_pctl"),
    "3y": ("3 ans", "3 années", "3y", "perf_3y_cat_rank_pctl"),
    "5y": ("5 ans", "5 années", "5y", "perf_5y_cat_rank_pctl"),
}


def _score(rank: int, total: int) -> float | None:
    if rank < 1 or total < 1 or rank > total:
        return None
    if total == 1:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (1.0 - (rank - 1.0) / (total - 1.0))))


def extract_category_ranks(html: str) -> dict[str, dict[str, float | int]]:
    """Extract only explicit category rank fractions located close to a period label."""
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).lower().replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    out: dict[str, dict[str, float | int]] = {}
    for period, aliases in PERIODS.items():
        labels = aliases[:-1]
        found = None
        for label in labels:
            escaped = re.escape(label)
            patterns = (
                rf"{escaped}.{{0,120}}?(?:classement|rang|cat[eé]gorie).{{0,50}}?(\d{{1,5}})\s*(?:/|sur)\s*(\d{{1,5}})",
                rf"(?:classement|rang|cat[eé]gorie).{{0,50}}?(\d{{1,5}})\s*(?:/|sur)\s*(\d{{1,5}}).{{0,120}}?{escaped}",
                rf"{escaped}.{{0,80}}?(\d{{1,5}})\s*(?:/|sur)\s*(\d{{1,5}}).{{0,50}}?cat[eé]gorie",
            )
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    rank, total = int(match.group(1)), int(match.group(2))
                    percentile = _score(rank, total)
                    if percentile is not None:
                        found = {"rank": rank, "total": total, "score": round(percentile, 4)}
                        break
            if found:
                break
        if found:
            out[period] = found
    return out


def _candidate_urls(source_url: str) -> list[str]:
    base = str(source_url or "").strip()
    if "boursorama.com" not in base:
        return []
    urls = [base]
    for replacement in ("/performances/", "/caracteristiques/"):
        if "/cours/" in base:
            urls.append(base.replace("/cours/", replacement))
    return list(dict.fromkeys(urls))


def _fetch_one(row: pd.Series, requests, limiter: StartRateLimiter) -> tuple[str, dict[str, dict], str | None]:
    isin = str(row.get("isin", "") or "").strip()
    urls = _candidate_urls(str(row.get("source_url", "") or ""))
    if not urls:
        return isin, {}, "BOURSORAMA_URL_MISSING"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.6.3; +data-quality)",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    }
    last_reason = "RANK_NOT_FOUND"
    for url in urls:
        try:
            limiter.wait()
            response = requests.get(url, timeout=20, headers=headers)
            if response.status_code >= 400:
                last_reason = f"HTTP_{response.status_code}"
                continue
            ranks = extract_category_ranks(response.text)
            if ranks:
                return isin, ranks, None
        except Exception as exc:
            last_reason = type(exc).__name__
    return isin, {}, last_reason


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.ParserError, UnicodeError):
        return pd.DataFrame()


def _mean_score(row: dict[str, dict]) -> float | None:
    values = [float(v["score"]) for v in row.values() if isinstance(v, dict) and v.get("score") is not None and math.isfinite(float(v["score"]))]
    return sum(values) / len(values) if values else None


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
    candidates = etfs.loc[
        etfs.get("source_url", pd.Series("", index=etfs.index)).astype(str).str.contains("boursorama.com", case=False, na=False)
    ].copy()
    if candidates.empty:
        return [], [{"source": "Boursorama", "reason": "NO_ETF_BOURSORAMA_URLS"}]

    limiter = StartRateLimiter(delay_seconds)
    results: dict[str, dict[str, dict]] = {}
    failures: list[dict] = []
    workers = max(1, min(int(max_workers), len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_fetch_one, row, requests, limiter) for _, row in candidates.iterrows()]
        for future in as_completed(futures):
            isin, ranks, reason = future.result()
            if ranks:
                results[isin] = ranks
            else:
                failures.append({"isin": isin, "source": "Boursorama", "reason": reason or "RANK_NOT_FOUND"})

    history_path = Path(history_path)
    history = _read_history(history_path)
    history_rows: list[dict] = []
    observations: list[dict] = []
    for isin, ranks in sorted(results.items()):
        current_mean = _mean_score(ranks)
        previous_mean = None
        if not history.empty and "isin" in history.columns and "rank_score_mean" in history.columns:
            prior = history.loc[history["isin"].astype(str) == isin].copy()
            if not prior.empty:
                prior = prior.sort_values("observed_at")
                for value in reversed(prior["rank_score_mean"].tolist()):
                    try:
                        parsed = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(parsed):
                        previous_mean = parsed
                        break
        trend = None if current_mean is None or previous_mean is None else current_mean - previous_mean
        values: dict[str, object] = {
            "boursorama_category_rank_score_shadow": None if current_mean is None else round(current_mean, 4),
            "boursorama_category_rank_trend_shadow": None if trend is None else round(trend, 4),
        }
        for period, data in ranks.items():
            field = PERIODS[period][-1]
            values[field] = data["score"]
            values[f"boursorama_rank_{period}"] = data["rank"]
            values[f"boursorama_rank_total_{period}"] = data["total"]
        for field, value in values.items():
            if value is None:
                continue
            observations.append({
                "universe": "ETF",
                "isin": isin,
                "field": field,
                "value": value,
                "source": "Boursorama ETF category ranking",
                "collected_at": now.isoformat(),
                "as_of": now.date().isoformat(),
                "evidence_level": "B",
                "validation_status": "AUTO_MATCH",
            })
        history_rows.append({
            "isin": isin,
            "observed_at": now.isoformat(),
            "rank_score_mean": current_mean,
            **{f"rank_{p}": d["rank"] for p, d in ranks.items()},
            **{f"total_{p}": d["total"] for p, d in ranks.items()},
            **{f"score_{p}": d["score"] for p, d in ranks.items()},
        })

    if history_rows:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        updated = pd.concat([history, pd.DataFrame(history_rows)], ignore_index=True, sort=False)
        updated = updated.drop_duplicates(subset=["isin", "observed_at"], keep="last").sort_values(["isin", "observed_at"])
        updated.to_csv(history_path, index=False, encoding="utf-8-sig")
    failures.sort(key=lambda x: str(x.get("isin", "")))
    return observations, failures
