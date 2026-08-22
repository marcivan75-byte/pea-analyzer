from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
import json
import os
import re

import pandas as pd

from v182.features import tct_catalyst_context_v24_4_2 as feature
from v182.reporting import tct_next_session_catalyst_run as legacy
from v182.sources import tct_catalyst_news_grouped_shadow_v21_13_13 as grouped_shadow
from v182.sources.tct_catalyst_news_v24_4_2 import CatalystNews


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json"
VERSION = "V21.13.14_GDELT_GROUPED_AB_FROM_PIT_BASELINE"


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _first_text(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _number(value: object) -> float | None:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(parsed) else float(parsed)


def _integer(value: object) -> int:
    parsed = _number(value)
    return 0 if parsed is None else int(parsed)


def _bool(value: object) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "y"}


def _tuple_field(value: object, separator: str) -> tuple[str, ...]:
    text = _text(value)
    if not text:
        return ()
    return tuple(part.strip() for part in text.split(separator) if part.strip())


def _baseline_news(row: pd.Series) -> CatalystNews:
    return CatalystNews(
        magnitude_score=_number(row.get("news_magnitude_score")),
        direction_score=_number(row.get("news_direction_score")),
        confidence=_number(row.get("news_confidence")) or 0.0,
        article_count=_integer(row.get("news_article_count")),
        independent_sources=_integer(row.get("news_independent_sources")),
        event_types=_tuple_field(row.get("news_event_types"), "|"),
        top_headlines=_tuple_field(row.get("news_top_headlines"), " || "),
        window_start_utc=_first_text(row.get("news_window_start_utc"), row.get("snapshot_window_start_utc")),
        window_end_utc=_first_text(row.get("news_window_end_utc"), row.get("snapshot_window_end_utc")),
        source=_text(row.get("news_source")) or "GDELT_WINDOWED_V24_4_2",
        error=_text(row.get("news_error")) or None,
        match_confidence=_number(row.get("news_match_confidence")),
        cache_hit=_bool(row.get("news_cache_hit")),
    )


def _timespan_delta(value: str) -> timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([hd])\s*", str(value).lower())
    if not match:
        raise ValueError(f"Unsupported GDELT timespan for A/B guard: {value}")
    amount = int(match.group(1))
    return timedelta(hours=amount) if match.group(2) == "h" else timedelta(days=amount)


def _latest_snapshot(ledger: pd.DataFrame, phase: str) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    work = ledger.copy()
    if "phase" not in work.columns or "snapshot_generated_at_utc" not in work.columns:
        return pd.DataFrame()
    work = work[work["phase"].astype(str).str.upper() == str(phase).upper()].copy()
    if "version" in work.columns:
        work = work[work["version"].astype(str) == feature.VERSION].copy()
    if work.empty:
        return pd.DataFrame()
    work["_snapshot_ts"] = pd.to_datetime(work["snapshot_generated_at_utc"], errors="coerce", utc=True)
    work = work.dropna(subset=["_snapshot_ts"])
    if work.empty:
        return pd.DataFrame()
    latest = work["_snapshot_ts"].max()
    return work[work["_snapshot_ts"] == latest].drop(columns=["_snapshot_ts"]).copy()


def _unique_datetimes(snapshot: pd.DataFrame, column: str) -> list[datetime]:
    if column not in snapshot.columns:
        return []
    parsed = pd.to_datetime(snapshot[column], errors="coerce", utc=True).dropna()
    if parsed.empty:
        return []
    return [pd.Timestamp(value).to_pydatetime().astimezone(timezone.utc) for value in parsed.unique()]


def _window(snapshot: pd.DataFrame) -> tuple[datetime, datetime]:
    starts = _unique_datetimes(snapshot, "news_window_start_utc")
    ends = _unique_datetimes(snapshot, "news_window_end_utc")
    if len(starts) != 1 or len(ends) != 1:
        starts = _unique_datetimes(snapshot, "snapshot_window_start_utc")
        ends = _unique_datetimes(snapshot, "snapshot_window_end_utc")
    if len(starts) != 1 or len(ends) != 1:
        raise RuntimeError("PIT baseline does not contain one exact shared news window")
    start, end = starts[0], ends[0]
    if start > end:
        raise RuntimeError("PIT baseline window is inverted")
    return start, end


def run(root: Path = ROOT, *, phase: str | None = None, group_size: int = 5, now: datetime | None = None) -> dict:
    started = monotonic()
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    selected_phase = str(phase or os.environ.get("TCT_GDELT_AB_PHASE") or "PREOPEN").upper()
    if selected_phase not in {"PREOPEN", "POSTMARKET"}:
        raise ValueError(f"Unsupported A/B phase: {selected_phase}")

    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    ledger = legacy._read_csv(ledger_path)
    snapshot = _latest_snapshot(ledger, selected_phase)
    if snapshot.empty:
        raise RuntimeError(f"No V24.4.2 PIT baseline snapshot found for {selected_phase}")

    start_utc, end_utc = _window(snapshot)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timespan_text = cfg["news"]["preopen_fetch_timespan"] if selected_phase == "PREOPEN" else cfg["news"]["postmarket_fetch_timespan"]
    provider_span = _timespan_delta(timespan_text)
    if start_utc < current - provider_span:
        raise RuntimeError(
            "PIT baseline window is older than the configured GDELT timespan; "
            "run the manual A/B soon after the corresponding production snapshot"
        )

    snapshot = snapshot.drop_duplicates("isin", keep="first") if "isin" in snapshot.columns else snapshot
    candidates = [
        {"isin": _text(row.get("isin")), "name": _text(row.get("name"))}
        for _, row in snapshot.iterrows()
        if _text(row.get("isin")) and _text(row.get("name"))
    ]
    baseline = {
        _text(row.get("isin")): _baseline_news(row)
        for _, row in snapshot.iterrows()
        if _text(row.get("isin"))
    }
    if not candidates or not baseline:
        raise RuntimeError("PIT baseline has no usable candidates")

    grouped = grouped_shadow.fetch_candidate_news_grouped_shadow(
        candidates,
        start_utc=start_utc,
        end_utc=end_utc,
        phase=selected_phase,
        cfg=cfg,
        group_size=group_size,
    )
    comparison = grouped_shadow.compare_individual_vs_grouped(baseline, grouped)
    comparison.update({
        "runner_version": VERSION,
        "phase": selected_phase,
        "baseline_source": "EXISTING_V24_4_2_PIT_LEDGER_NO_NEW_INDIVIDUAL_REQUESTS",
        "baseline_snapshot_generated_at_utc": _text(snapshot.iloc[0].get("snapshot_generated_at_utc")),
        "window_start_utc": start_utc.isoformat(),
        "window_end_utc": end_utc.isoformat(),
        "group_size": int(group_size),
        "grouped_metrics": dict(grouped.metrics),
        "new_individual_requests": 0,
        "scheduled": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "promotion_authority": False,
        "production_activation": False,
        "runtime_seconds": round(monotonic() - started, 4),
    })

    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "GDELT_GROUPED_AB_V21_13_14.json"
    csv_path = outdir / "GDELT_GROUPED_AB_V21_13_14_ROWS.csv"
    json_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(comparison.get("rows", [])).to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
    return comparison


if __name__ == "__main__":
    group_size = int(os.environ.get("TCT_GDELT_AB_GROUP_SIZE", "5"))
    print(json.dumps(run(group_size=group_size), ensure_ascii=False, indent=2, default=str))
