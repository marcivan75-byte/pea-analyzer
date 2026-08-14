from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import math
import pandas as pd

FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"


def _num(value) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _symbol_aliases(value: object) -> list[str]:
    raw = str(value or "").strip().upper()
    if not raw or raw in {"NAN", "NONE"}:
        return []
    aliases = [raw]
    if "." in raw:
        aliases.append(raw.split(".", 1)[0])
    return list(dict.fromkeys(aliases))


def _action_symbol_index(actions: pd.DataFrame) -> dict[str, set[str]]:
    """Index every observed symbol alias to all matching ISINs.

    An alias is intentionally not collapsed to a single first-seen ISIN. The
    PEA universe spans several European venues and bare symbols can collide.
    Resolution is allowed only when the selected alias maps to exactly one ISIN.
    """
    mapping: dict[str, set[str]] = {}
    for _, row in actions.iterrows():
        isin = str(row.get("isin", "") or "").strip()
        if not isin:
            continue
        for field in ("finnhub_ticker", "yahoo_ticker", "ticker", "symbol"):
            for alias in _symbol_aliases(row.get(field)):
                mapping.setdefault(alias, set()).add(isin)
    return mapping


def _resolve_isin(mapping: dict[str, set[str]], symbol: str) -> tuple[str | None, str | None]:
    aliases = _symbol_aliases(symbol)
    if not aliases:
        return None, None
    # Exact symbol, including exchange suffix, always has priority over a bare
    # root alias. A bare alias is accepted only when unique in the full universe.
    for alias in aliases:
        candidates = mapping.get(alias, set())
        if len(candidates) == 1:
            return next(iter(candidates)), None
        if len(candidates) > 1:
            return None, f"AMBIGUOUS_SYMBOL_ALIAS:{alias}"
    return None, None


def _history_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["isin", "event_date", "year", "quarter", "eps_estimate", "revenue_estimate", "observed_at"])
    try:
        return pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.ParserError, UnicodeError):
        return pd.DataFrame(columns=["isin", "event_date", "year", "quarter", "eps_estimate", "revenue_estimate", "observed_at"])


def _previous_estimate(history: pd.DataFrame, isin: str, year: object, quarter: object, observed_at: str) -> float | None:
    if history.empty:
        return None
    sub = history.loc[
        (history.get("isin", pd.Series(dtype=str)).astype(str) == isin)
        & (history.get("year", pd.Series(dtype=str)).astype(str) == str(year))
        & (history.get("quarter", pd.Series(dtype=str)).astype(str) == str(quarter))
        & (history.get("observed_at", pd.Series(dtype=str)).astype(str) < observed_at)
    ].copy()
    if sub.empty:
        return None
    sub = sub.sort_values("observed_at")
    for value in reversed(sub.get("eps_estimate", pd.Series(dtype=object)).tolist()):
        parsed = _num(value)
        if parsed is not None:
            return parsed
    return None


def build_calendar_observations(
    actions: pd.DataFrame,
    calendar_rows: list[dict],
    history_path: str | Path,
    *,
    observed_at: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Map Finnhub earnings events to PEA Actions and derive same-period PIT EPS revisions.

    Missing calendar rows stay missing. A first observation cannot manufacture a
    revision because there is no prior same-fiscal-period snapshot to compare.
    Ambiguous symbol aliases are quarantined rather than assigned by row order.
    """
    now = observed_at or datetime.now(timezone.utc)
    observed_iso = now.isoformat()
    as_of = now.date().isoformat()
    mapping = _action_symbol_index(actions)
    history_path = Path(history_path)
    history = _history_frame(history_path)
    matched: list[dict] = []
    history_rows: list[dict] = []
    failures: list[dict] = []

    for item in calendar_rows:
        symbol = str(item.get("symbol", "") or "").strip().upper()
        isin, resolution_error = _resolve_isin(mapping, symbol)
        if resolution_error:
            failures.append({"source": "Finnhub", "symbol": symbol, "reason": resolution_error})
            continue
        if not isin:
            continue
        event_date = str(item.get("date", "") or "")[:10]
        try:
            event_day = date.fromisoformat(event_date)
        except ValueError:
            failures.append({"source": "Finnhub", "symbol": symbol, "reason": "INVALID_EARNINGS_DATE", "value": event_date})
            continue

        eps_estimate = _num(item.get("epsEstimate"))
        revenue_estimate = _num(item.get("revenueEstimate"))
        previous = _previous_estimate(history, isin, item.get("year"), item.get("quarter"), observed_iso)
        revision_abs = None if eps_estimate is None or previous is None else eps_estimate - previous
        revision_pct = None
        if revision_abs is not None and previous not in (None, 0.0):
            revision_pct = revision_abs / abs(previous) * 100.0

        payload = {
            "isin": isin,
            "symbol": symbol,
            "event_date": event_date,
            "event_day": event_day,
            "hour": item.get("hour"),
            "year": item.get("year"),
            "quarter": item.get("quarter"),
            "eps_estimate": eps_estimate,
            "eps_actual": _num(item.get("epsActual")),
            "revenue_estimate": revenue_estimate,
            "revenue_actual": _num(item.get("revenueActual")),
            "revision_abs": revision_abs,
            "revision_pct": revision_pct,
        }
        matched.append(payload)
        history_rows.append({
            "isin": isin,
            "event_date": event_date,
            "year": item.get("year"),
            "quarter": item.get("quarter"),
            "eps_estimate": eps_estimate,
            "revenue_estimate": revenue_estimate,
            "observed_at": observed_iso,
        })

    observations: list[dict] = []
    if matched:
        by_isin: dict[str, list[dict]] = {}
        for item in matched:
            by_isin.setdefault(item["isin"], []).append(item)
        for isin, events in by_isin.items():
            future = [e for e in events if e["event_day"] >= now.date()]
            selected = min(future or events, key=lambda e: abs((e["event_day"] - now.date()).days))
            values = {
                "next_earnings_date_fh": selected["event_date"],
                "next_earnings_hour_fh": selected["hour"],
                "earnings_days_to_event_fh": (selected["event_day"] - now.date()).days,
                "eps_estimate_next_fh": selected["eps_estimate"],
                "eps_actual_fh": selected["eps_actual"],
                "revenue_estimate_next_fh": selected["revenue_estimate"],
                "revenue_actual_fh": selected["revenue_actual"],
                "eps_estimate_revision_abs_fh": selected["revision_abs"],
                "eps_estimate_revision_pct_fh": selected["revision_pct"],
            }
            for field, value in values.items():
                if value is None or value == "":
                    continue
                observations.append({
                    "universe": "ACTION",
                    "isin": isin,
                    "field": field,
                    "value": value,
                    "source": "Finnhub Earnings Calendar",
                    "collected_at": observed_iso,
                    "as_of": as_of,
                    "evidence_level": "B",
                    "validation_status": "AUTO_MATCH",
                })

    if history_rows:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        updated = pd.concat([history, pd.DataFrame(history_rows)], ignore_index=True)
        updated = updated.drop_duplicates(
            subset=["isin", "event_date", "year", "quarter", "eps_estimate", "revenue_estimate", "observed_at"],
            keep="last",
        ).sort_values(["isin", "event_date", "observed_at"])
        updated.to_csv(history_path, index=False, encoding="utf-8-sig")

    return observations, failures


def fetch_cdc_observations(
    actions: pd.DataFrame,
    api_key: str,
    history_path: str | Path,
    *,
    horizon_days: int = 90,
    requests_module=None,
    observed_at: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    import requests as requests_default

    requests = requests_module or requests_default
    now = observed_at or datetime.now(timezone.utc)
    params = {
        "from": now.date().isoformat(),
        "to": (now.date() + timedelta(days=max(1, int(horizon_days)))).isoformat(),
        "token": api_key,
    }
    try:
        response = requests.get(FINNHUB_EARNINGS_URL, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("earningsCalendar", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return [], [{"source": "Finnhub", "reason": "INVALID_EARNINGS_PAYLOAD"}]
        return build_calendar_observations(actions, rows, history_path, observed_at=now)
    except Exception as exc:
        return [], [{"source": "Finnhub", "reason": type(exc).__name__, "detail": str(exc)[:240]}]
