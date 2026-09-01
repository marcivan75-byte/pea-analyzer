"""Audit 73 sidecar for lossless Boursorama/FactSet consensus history.

The public collector historically normalized the page to the current state.  This
module preserves every FactSet column exposed by Boursorama without fabricating
calendar dates for relative labels (3 months, 2 months, 1 month, 7 days).

`available_at` is the real page-capture timestamp.  A relative observation is never
promoted to an exact historical `as_of_date`; it can support revision diagnostics
known at capture time, but cannot be used as if it had been observed earlier.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from io import StringIO

import pandas as pd

_WEIGHTS = {"acheter": 5, "renforcer": 4, "conserver": 3, "alleger": 2, "vendre": 1}
_RELATIVE = {
    "3 mois": "P3M",
    "3 months": "P3M",
    "2 mois": "P2M",
    "2 months": "P2M",
    "1 mois": "P1M",
    "1 month": "P1M",
    "7 jours": "P7D",
    "7 days": "P7D",
}


def _key(value: object) -> str:
    text = str(value or "").strip().lower()
    table = str.maketrans("àâäáãåçéèêëíìîïñóòôöõúùûüýÿœ", "aaaaaaceeeeiiiinooooouuuuyyo")
    return " ".join(text.translate(table).split())


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace("\u202f", " ").replace("\xa0", " ")
    if not text or text.upper() in {"-", "—", "N/A", "NA", "NONE", "NAN", "NULL", "<NA>"}:
        return None
    text = re.sub(r"[^0-9,.+\- ]", "", text).replace(" ", "")
    if not text or text in {"-", "+"}:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _capture_iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _exact_date(label: str) -> str | None:
    text = str(label).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _find_table(html: str) -> pd.DataFrame | None:
    try:
        tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return None
    for frame in tables:
        if frame.empty or frame.shape[1] < 2:
            continue
        labels = {_key(v) for v in frame.iloc[:, 0].tolist()}
        if len(labels & set(_WEIGHTS)) >= 4:
            return frame
    return None


def _row_map(frame: pd.DataFrame) -> dict[str, int]:
    return {_key(value): idx for idx, value in enumerate(frame.iloc[:, 0].tolist())}


def _value(frame: pd.DataFrame, rows: dict[str, int], row: str, col: int) -> float | None:
    idx = rows.get(_key(row))
    return None if idx is None else _number(frame.iloc[idx, col])


def _score(counts: dict[str, int]) -> float | None:
    total = sum(counts.values())
    if total <= 0:
        return None
    return sum(counts[k] * _WEIGHTS[k] for k in _WEIGHTS) / total


def _label(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 4.5:
        return "STRONG_BUY"
    if score >= 3.5:
        return "BUY"
    if score >= 2.5:
        return "HOLD"
    if score >= 1.5:
        return "SELL"
    return "STRONG_SELL"


def parse_factset_consensus_history(html: str, *, captured_at: datetime | str) -> list[dict]:
    """Return lossless column observations in page order.

    Relative periods deliberately have ``as_of_date=None``.  All rows carry the
    real ``available_at`` capture timestamp, which is the only admissible knowledge
    timestamp for strict PIT simulation.
    """
    frame = _find_table(html)
    if frame is None:
        return []
    rows = _row_map(frame)
    available_at = _capture_iso(captured_at)
    out: list[dict] = []
    for col in range(1, frame.shape[1]):
        raw_label = str(frame.columns[col]).strip()
        normalized = _key(raw_label)
        relative_period = _RELATIVE.get(normalized)
        exact = _exact_date(raw_label)
        period_kind = "RELATIVE" if relative_period else "CURRENT" if exact else "UNRESOLVED"

        counts: dict[str, int] = {}
        for bucket in _WEIGHTS:
            val = _value(frame, rows, bucket, col)
            counts[bucket] = max(0, int(round(val))) if val is not None else 0
        score = _score(counts)
        buy_n = counts["acheter"] + counts["renforcer"]
        hold_n = counts["conserver"]
        sell_n = counts["alleger"] + counts["vendre"]
        target = _value(frame, rows, "objectif de cours median", col)
        if target is None:
            target = _value(frame, rows, "objectif de cours médian", col)
        potential = _value(frame, rows, "potentiel", col)
        median_note = _value(frame, rows, "note mediane", col)
        if median_note is None:
            median_note = _value(frame, rows, "note médiane", col)

        out.append({
            "period_label": raw_label,
            "period_kind": period_kind,
            "relative_period": relative_period,
            "as_of_date": exact,
            "available_at": available_at,
            "consensus": _label(score),
            "consensus_score": None if score is None else round(score, 4),
            "target_median": target,
            "published_upside_pct": potential,
            "median_note": median_note,
            "n_analysts": sum(counts.values()),
            "buy_n": buy_n,
            "hold_n": hold_n,
            "sell_n": sell_n,
            "acheter_n": counts["acheter"],
            "renforcer_n": counts["renforcer"],
            "conserver_n": counts["conserver"],
            "alleger_n": counts["alleger"],
            "vendre_n": counts["vendre"],
            "provider": "FactSet via Boursorama",
            "artificial_date_assigned": False,
        })
    return out


def current_and_revision(history: list[dict]) -> dict[str, object]:
    """Derive current state and 4-week revision only from one captured page."""
    current = next((r for r in reversed(history) if r.get("period_kind") == "CURRENT"), None)
    one_month = next((r for r in history if r.get("relative_period") == "P1M"), None)
    if current is None:
        return {}
    result = {
        "consensus": current.get("consensus"),
        "target_median": current.get("target_median"),
        "published_upside_pct": current.get("published_upside_pct"),
        "n_analysts": current.get("n_analysts"),
        "buy_n": current.get("buy_n"),
        "hold_n": current.get("hold_n"),
        "sell_n": current.get("sell_n"),
        "available_at": current.get("available_at"),
    }
    if one_month and current.get("consensus_score") is not None and one_month.get("consensus_score") is not None:
        result["consensus_delta_4w"] = round((float(current["consensus_score"]) - float(one_month["consensus_score"])) * 20.0, 4)
        result["net_upgrades_30d"] = int((current["buy_n"] - current["sell_n"]) - (one_month["buy_n"] - one_month["sell_n"]))
    return result
