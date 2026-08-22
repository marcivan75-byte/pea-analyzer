from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import logging
import os
import time

import pandas as pd

logger = logging.getLogger(__name__)
PRICE_FIELDS = {"open", "high", "low", "close", "adj close"}
CACHE_FORMAT_VERSION = 5
MIGRATABLE_CACHE_FORMAT_VERSIONS = {4, 5}
DEFAULT_INCREMENTAL_PERIOD = "1mo"
DEFAULT_BOOTSTRAP_START = "2023-01-01"
DEFAULT_ROLLING_MONTHS = 60
DEFAULT_NEGATIVE_CACHE_TTL_DAYS = 7
REBASE_RTOL = 1e-5
REBASE_ATOL = 1e-8


@dataclass(frozen=True)
class DownloadResult:
    requested: int
    successful: list[str]
    failed: list[str]
    cache_file: str | None


def _has_observed_price(frame) -> bool:
    if frame is None or frame.empty:
        return False
    columns = list(frame.columns)
    price_columns = [c for c in columns if str(c).strip().lower() in PRICE_FIELDS]
    if not price_columns:
        return False
    return bool(frame[price_columns].notna().to_numpy().any())


def _contains_ticker(frame, ticker: str) -> bool:
    """A ticker is usable only when at least one real OHLC price is present."""
    if frame is None or frame.empty:
        return False
    if isinstance(frame.columns, pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            if ticker in frame.columns.get_level_values(level):
                try:
                    sub = frame.xs(ticker, axis=1, level=level, drop_level=True)
                except (KeyError, ValueError):
                    return False
                return _has_observed_price(sub)
        return False
    return _has_observed_price(frame)


def _clear_history_cache(cache: Path) -> None:
    """Explicit full-rebuild helper; normal runs preserve valid rolling history."""
    targets = list(cache.glob("history_*.parquet"))
    manifest = cache / "history_manifest.json"
    if manifest.exists():
        targets.append(manifest)
    for path in targets:
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"YFINANCE_CACHE_CLEANUP_FAILED:{path.name}:{type(exc).__name__}"
            ) from exc


def _resolve_actions_requested(cache_dir: str, include_actions: bool | None) -> bool:
    if include_actions is not None:
        return bool(include_actions)
    return Path(cache_dir).name.lower() == "actions"


def _normalize_frame(frame: pd.DataFrame | None, tickers: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        ticker_level = None
        for level in range(out.columns.nlevels):
            values = {str(v) for v in out.columns.get_level_values(level)}
            if any(t in values for t in tickers):
                ticker_level = level
                break
        if ticker_level not in (None, 0):
            order = [ticker_level] + [
                i for i in range(out.columns.nlevels) if i != ticker_level
            ]
            out.columns = out.columns.reorder_levels(order)
    elif len(tickers) == 1:
        out.columns = pd.MultiIndex.from_tuples(
            [(tickers[0], str(c)) for c in out.columns]
        )
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def _select_tickers(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Select ticker columns without inventing observations."""
    if frame is None or frame.empty or not tickers:
        return pd.DataFrame()
    wanted = set(tickers)
    if isinstance(frame.columns, pd.MultiIndex):
        columns = [c for c in frame.columns if str(c[0]) in wanted]
    elif len(tickers) == 1:
        columns = list(frame.columns)
    else:
        columns = []
    if not columns:
        return pd.DataFrame()
    return frame.loc[:, columns].copy()


def _drop_tickers(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty or not tickers:
        return frame
    unwanted = set(tickers)
    if isinstance(frame.columns, pd.MultiIndex):
        columns = [c for c in frame.columns if str(c[0]) not in unwanted]
        return frame.loc[:, columns].copy() if columns else pd.DataFrame(index=frame.index)
    return pd.DataFrame(index=frame.index)


def _merge_history_frames(
    existing: pd.DataFrame | None, update: pd.DataFrame | None
) -> pd.DataFrame:
    """Merge by cell: new observations replace overlap without erasing valid peers."""
    if existing is None or existing.empty:
        return pd.DataFrame() if update is None else update.sort_index()
    if update is None or update.empty:
        return existing.sort_index()
    base = existing[~existing.index.duplicated(keep="last")].sort_index()
    fresh = update[~update.index.duplicated(keep="last")].sort_index()
    index = base.index.union(fresh.index)
    columns = base.columns.union(fresh.columns, sort=False)
    base = base.reindex(index=index, columns=columns)
    fresh = fresh.reindex(index=index, columns=columns)
    return fresh.combine_first(base).sort_index()


def _rolling_window_start(
    anchor_start: str | None,
    rolling_months: int,
    *,
    now: pd.Timestamp | None = None,
) -> pd.Timestamp | None:
    """Return max(anchor_start, current_date - rolling_months)."""
    if not anchor_start:
        return None
    if int(rolling_months) <= 0:
        raise ValueError(f"INVALID_YFINANCE_ROLLING_MONTHS:{rolling_months}")
    anchor = pd.Timestamp(anchor_start).normalize()
    reference = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if reference.tzinfo is not None:
        reference = reference.tz_localize(None)
    rolling_start = (
        reference.normalize() - pd.DateOffset(months=int(rolling_months))
    ).normalize()
    return max(anchor, rolling_start)


def _trim_to_rolling_window(
    frame: pd.DataFrame,
    window_start: pd.Timestamp | None,
) -> pd.DataFrame:
    """Drop observations older than the active rolling-history floor."""
    if frame is None or frame.empty or window_start is None:
        return frame
    if not isinstance(frame.index, pd.DatetimeIndex):
        return frame
    cutoff = pd.Timestamp(window_start)
    if frame.index.tz is not None and cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(frame.index.tz)
    elif frame.index.tz is None and cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    return frame.loc[frame.index >= cutoff].sort_index()


def _price_columns(frame: pd.DataFrame) -> list:
    if frame.empty:
        return []
    if isinstance(frame.columns, pd.MultiIndex):
        return [c for c in frame.columns if str(c[-1]).strip().lower() in PRICE_FIELDS]
    return [c for c in frame.columns if str(c).strip().lower() in PRICE_FIELDS]


def _overlap_rebase_detected(
    existing: pd.DataFrame,
    fresh: pd.DataFrame,
    *,
    rtol: float = REBASE_RTOL,
    atol: float = REBASE_ATOL,
) -> bool:
    """Detect material revisions of already closed adjusted-price observations."""
    if existing.empty or fresh.empty:
        return False
    common_index = existing.index.intersection(fresh.index).sort_values()
    if len(common_index) <= 1:
        return False
    common_index = common_index[:-1]
    common_columns = existing.columns.intersection(fresh.columns, sort=False)
    price_columns = [c for c in common_columns if c in _price_columns(existing)]
    if not price_columns:
        return False
    old = existing.loc[common_index, price_columns].apply(pd.to_numeric, errors="coerce")
    new = fresh.loc[common_index, price_columns].apply(pd.to_numeric, errors="coerce")
    both = old.notna() & new.notna()
    if not bool(both.to_numpy().any()):
        return False
    delta = (new - old).abs()
    tolerance = atol + rtol * old.abs()
    return bool(((delta > tolerance) & both).to_numpy().any())


def _read_manifest(cache: Path) -> dict | None:
    path = cache / "history_manifest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_structure_is_compatible(
    manifest: dict | None,
    interval: str,
    batch_size: int,
    auto_adjust: bool,
    actions_requested: bool,
    bootstrap_start: str | None,
    rolling_months: int,
) -> bool:
    """Check cache semantics independently from the current ticker universe."""
    if not manifest:
        return False
    if int(manifest.get("cache_format_version", -1)) not in MIGRATABLE_CACHE_FORMAT_VERSIONS:
        return False
    if manifest.get("interval") != interval:
        return False
    if int(manifest.get("batch_size", -1)) != int(batch_size):
        return False
    if bool(manifest.get("auto_adjust")) != bool(auto_adjust):
        return False
    if bool(manifest.get("actions_requested")) != bool(actions_requested):
        return False
    if (manifest.get("bootstrap_start") or None) != (bootstrap_start or None):
        return False
    if int(manifest.get("rolling_months", -1)) != int(rolling_months):
        return False
    return True


def _cache_is_usable(
    cache: Path,
    tickers: list[str],
    interval: str,
    batch_size: int,
    auto_adjust: bool,
    actions_requested: bool,
    bootstrap_start: str | None = None,
    rolling_months: int = DEFAULT_ROLLING_MONTHS,
) -> bool:
    manifest = _read_manifest(cache)
    if not manifest or manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
        return False
    if manifest.get("requested_tickers") != tickers:
        return False
    return _cache_structure_is_compatible(
        manifest,
        interval,
        batch_size,
        auto_adjust,
        actions_requested,
        bootstrap_start,
        rolling_months,
    )


def _cache_files_complete(cache: Path, tickers: list[str], batch_size: int) -> bool:
    return all(
        (cache / f"history_{start:05d}.parquet").exists()
        for start in range(0, len(tickers), batch_size)
    )


def _updated_today_utc(manifest: dict | None) -> bool:
    raw = (manifest or {}).get("updated_at_utc")
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def _read_cached_frame(path: Path, tickers: list[str]) -> pd.DataFrame:
    try:
        return _normalize_frame(pd.read_parquet(path), tickers)
    except Exception as exc:
        logger.warning(
            "OHLCV cache read failed for %s: %s: %s",
            path,
            type(exc).__name__,
            exc,
        )
        return pd.DataFrame()


def _history_batch_files(cache: Path) -> list[Path]:
    return sorted(cache.glob("history_[0-9][0-9][0-9][0-9][0-9].parquet"))


def _load_all_cached_history(cache: Path, prior_tickers: list[str]) -> pd.DataFrame:
    combined = pd.DataFrame()
    for path in _history_batch_files(cache):
        frame = _read_cached_frame(path, prior_tickers)
        combined = _merge_history_frames(combined, frame)
    return combined


def _negative_cache_seed(manifest: dict | None) -> dict[str, dict]:
    """Load V5 negative cache and safely seed it from recent V4 failures."""
    result: dict[str, dict] = {}
    if not manifest:
        return result
    raw = manifest.get("negative_cache")
    if isinstance(raw, dict):
        for ticker, entry in raw.items():
            if isinstance(entry, dict) and entry.get("last_failed_at_utc"):
                result[str(ticker)] = dict(entry)
    stamp = manifest.get("updated_at_utc")
    if stamp:
        for ticker in manifest.get("failed", []) or []:
            result.setdefault(
                str(ticker),
                {
                    "last_failed_at_utc": str(stamp),
                    "reason": "LEGACY_NO_USABLE_HISTORY",
                    "consecutive_failures": 1,
                },
            )
    return result


def _negative_cache_active(
    entry: dict | None,
    ttl_days: int,
    *,
    now: datetime | None = None,
) -> bool:
    if not entry or int(ttl_days) <= 0:
        return False
    raw = entry.get("last_failed_at_utc")
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(timezone.utc) - stamp.astimezone(timezone.utc) < timedelta(
        days=int(ttl_days)
    )


def _migrate_cache_ticker_universe(
    cache: Path,
    tickers: list[str],
    interval: str,
    batch_size: int,
    auto_adjust: bool,
    actions_requested: bool,
    bootstrap_start: str | None,
    rolling_months: int,
) -> bool:
    """Re-shard compatible cached history when the ticker universe changes.

    This is a local operation. Existing overlapping history is preserved; no
    market request is made here. New tickers remain absent and are bootstrapped
    later by ``download_history`` only for those tickers.
    """
    manifest = _read_manifest(cache)
    if not _cache_structure_is_compatible(
        manifest,
        interval,
        batch_size,
        auto_adjust,
        actions_requested,
        bootstrap_start,
        rolling_months,
    ):
        return False
    prior_tickers = [str(t) for t in (manifest or {}).get("requested_tickers", [])]
    if prior_tickers == tickers and manifest.get("cache_format_version") == CACHE_FORMAT_VERSION:
        return True
    if not prior_tickers:
        return False
    all_history = _load_all_cached_history(cache, prior_tickers)
    if all_history.empty:
        return False

    temp_files: list[tuple[Path, Path]] = []
    try:
        for batch_start in range(0, len(tickers), batch_size):
            batch = tickers[batch_start : batch_start + batch_size]
            selected = _select_tickers(all_history, batch)
            temp = cache / f"history_migrate_{batch_start:05d}.parquet"
            selected.to_parquet(temp)
            target = cache / f"history_{batch_start:05d}.parquet"
            temp_files.append((temp, target))
        for old in _history_batch_files(cache):
            old.unlink()
        for temp, target in temp_files:
            temp.replace(target)
    except Exception as exc:
        for temp, _ in temp_files:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError as cleanup_exc:
                logger.debug(
                    "OHLCV migration temp cleanup failed for %s: %s",
                    temp,
                    cleanup_exc,
                )
        logger.warning("OHLCV universe migration failed: %s: %s", type(exc).__name__, exc)
        return False

    cached_tickers = sorted(t for t in tickers if _contains_ticker(all_history, t))
    prior_set = set(prior_tickers)
    current_set = set(tickers)
    migrated = dict(manifest or {})
    migrated.update(
        {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "requested": len(tickers),
            "requested_tickers": tickers,
            "cached_tickers": cached_tickers,
            "successful": sorted(set(migrated.get("successful", [])) & current_set),
            "failed": sorted(set(migrated.get("failed", [])) & current_set),
            "negative_cache": {
                ticker: entry
                for ticker, entry in _negative_cache_seed(manifest).items()
                if ticker in current_set
            },
            "universe_migration": {
                "migrated_at_utc": datetime.now(timezone.utc).isoformat(),
                "prior_requested": len(prior_tickers),
                "current_requested": len(tickers),
                "added": sorted(current_set - prior_set),
                "removed": sorted(prior_set - current_set),
                "preserved_cached": len(cached_tickers),
                "network_calls": 0,
            },
        }
    )
    (cache / "history_manifest.json").write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "OHLCV cache universe migrated locally: %s preserved, %s added, %s removed",
        len(cached_tickers),
        len(current_set - prior_set),
        len(prior_set - current_set),
    )
    return True


def _download(
    yf,
    tickers: list[str],
    period: str,
    interval: str,
    auto_adjust: bool,
    actions_requested: bool,
    *,
    threads: bool,
    start: str | None = None,
) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    window = {"start": start} if start else {"period": period}
    raw = yf.download(
        tickers=tickers,
        interval=interval,
        group_by="ticker",
        auto_adjust=auto_adjust,
        actions=actions_requested,
        threads=threads,
        progress=False,
        timeout=30,
        **window,
    )
    return _normalize_frame(raw, tickers)


def _download_one(
    yf,
    ticker: str,
    period: str,
    interval: str,
    auto_adjust: bool,
    actions_requested: bool,
    *,
    start: str | None = None,
) -> pd.DataFrame:
    frame = _download(
        yf,
        [ticker],
        period,
        interval,
        auto_adjust,
        actions_requested,
        threads=False,
        start=start,
    )
    return frame if _contains_ticker(frame, ticker) else pd.DataFrame()


def download_history(
    tickers: list[str],
    cache_dir: str,
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 100,
    auto_adjust: bool = True,
    include_actions: bool | None = None,
    start: str | None = DEFAULT_BOOTSTRAP_START,
    rolling_months: int = DEFAULT_ROLLING_MONTHS,
) -> DownloadResult:
    """Maintain rolling OHLCV without rebuilding valid history on universe changes.

    A compatible existing cache is re-sharded locally when the ticker list
    changes. Only newly introduced tickers are bootstrapped from the active
    history floor; cached tickers receive the normal incremental overlap refresh.

    Tickers with no usable history are kept as N/D and enter a bounded negative
    cache (default 7 days) so known invalid/delisted symbols are not retried on
    every run. TTL expiry or PEA_YF_FORCE_REFRESH=1 rechecks them.
    """
    import yfinance as yf

    clean = sorted({t.strip() for t in tickers if t and t.strip()})
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    actions_requested = _resolve_actions_requested(cache_dir, include_actions)
    force_full = os.environ.get("PEA_YF_FORCE_FULL_HISTORY", "").strip() == "1"
    force_refresh = os.environ.get("PEA_YF_FORCE_REFRESH", "").strip() == "1"
    incremental_period = (
        os.environ.get("PEA_YF_INCREMENTAL_PERIOD", DEFAULT_INCREMENTAL_PERIOD).strip()
        or DEFAULT_INCREMENTAL_PERIOD
    )
    rolling_override = os.environ.get("PEA_YF_ROLLING_MONTHS", "").strip()
    negative_ttl_override = os.environ.get("PEA_YF_NEGATIVE_CACHE_TTL_DAYS", "").strip()
    if rolling_override:
        try:
            rolling_months = int(rolling_override)
        except ValueError as exc:
            raise ValueError(f"INVALID_YFINANCE_ROLLING_MONTHS:{rolling_override}") from exc
    try:
        negative_ttl_days = (
            int(negative_ttl_override)
            if negative_ttl_override
            else DEFAULT_NEGATIVE_CACHE_TTL_DAYS
        )
    except ValueError as exc:
        raise ValueError(
            f"INVALID_YFINANCE_NEGATIVE_CACHE_TTL_DAYS:{negative_ttl_override}"
        ) from exc
    rolling_months = int(rolling_months)
    if rolling_months <= 0:
        raise ValueError(f"INVALID_YFINANCE_ROLLING_MONTHS:{rolling_months}")
    if negative_ttl_days < 0:
        raise ValueError(f"INVALID_YFINANCE_NEGATIVE_CACHE_TTL_DAYS:{negative_ttl_days}")

    bootstrap_start = str(start).strip() if start else None
    if bootstrap_start:
        try:
            pd.Timestamp(bootstrap_start)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"INVALID_YFINANCE_HISTORY_START:{bootstrap_start}") from exc

    window_start = _rolling_window_start(bootstrap_start, rolling_months)
    effective_start = window_start.strftime("%Y-%m-%d") if window_start is not None else None

    prior_manifest = _read_manifest(cache)
    migration_performed = False
    usable = (not force_full) and _cache_is_usable(
        cache,
        clean,
        interval,
        batch_size,
        auto_adjust,
        actions_requested,
        bootstrap_start,
        rolling_months,
    )
    if not force_full and not usable and _cache_structure_is_compatible(
        prior_manifest,
        interval,
        batch_size,
        auto_adjust,
        actions_requested,
        bootstrap_start,
        rolling_months,
    ):
        migration_performed = _migrate_cache_ticker_universe(
            cache,
            clean,
            interval,
            batch_size,
            auto_adjust,
            actions_requested,
            bootstrap_start,
            rolling_months,
        )
        usable = migration_performed and _cache_is_usable(
            cache,
            clean,
            interval,
            batch_size,
            auto_adjust,
            actions_requested,
            bootstrap_start,
            rolling_months,
        )
    prior_manifest = _read_manifest(cache) if usable else None

    if (
        usable
        and not migration_performed
        and prior_manifest is not None
        and _cache_files_complete(cache, clean, batch_size)
        and not force_refresh
        and _updated_today_utc(prior_manifest)
    ):
        cached_successful = sorted(set(prior_manifest.get("cached_tickers", [])))
        cached_failed = sorted(set(clean) - set(cached_successful))
        logger.info("OHLCV cache hit: already refreshed today (%s tickers)", len(clean))
        return DownloadResult(
            len(clean), cached_successful, cached_failed, str(cache / "history_manifest.json")
        )

    mode = "INCREMENTAL" if usable else "FULL_BOOTSTRAP"
    if not usable:
        _clear_history_cache(cache)
        prior_manifest = None

    refreshed_successful: set[str] = set()
    cached_tickers: set[str] = set()
    failure_details: list[dict] = []
    rebased_batches: list[int] = []
    negative_cache = _negative_cache_seed(prior_manifest)
    negative_cache_hits: list[str] = []
    now_utc = datetime.now(timezone.utc)

    for batch_start in range(0, len(clean), batch_size):
        batch = clean[batch_start : batch_start + batch_size]
        output = cache / f"history_{batch_start:05d}.parquet"
        existing = _read_cached_frame(output, batch) if mode == "INCREMENTAL" else pd.DataFrame()
        existing = _trim_to_rolling_window(existing, window_start)
        cached_batch = [ticker for ticker in batch if _contains_ticker(existing, ticker)]
        skipped_negative = [
            ticker
            for ticker in batch
            if ticker not in cached_batch
            and not force_refresh
            and _negative_cache_active(negative_cache.get(ticker), negative_ttl_days, now=now_utc)
        ]
        negative_cache_hits.extend(skipped_negative)
        new_batch = [
            ticker for ticker in batch if ticker not in cached_batch and ticker not in skipped_negative
        ]
        fresh = pd.DataFrame()

        if cached_batch:
            try:
                incremental = _download(
                    yf,
                    cached_batch,
                    incremental_period,
                    interval,
                    auto_adjust,
                    actions_requested,
                    threads=True,
                )
                if _overlap_rebase_detected(_select_tickers(existing, cached_batch), incremental):
                    logger.warning(
                        "OHLCV adjusted-history revision detected in batch %s; rebuilding cached tickers",
                        batch_start,
                    )
                    incremental = _download(
                        yf,
                        cached_batch,
                        period,
                        interval,
                        auto_adjust,
                        actions_requested,
                        threads=True,
                        start=effective_start,
                    )
                    existing = _drop_tickers(existing, cached_batch)
                    rebased_batches.append(batch_start)
                fresh = _merge_history_frames(fresh, incremental)
            except Exception as exc:
                failure_details.append(
                    {
                        "scope": "cached_batch_refresh",
                        "batch_start": batch_start,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:180],
                    }
                )

        if new_batch:
            try:
                bootstrapped = _download(
                    yf,
                    new_batch,
                    period,
                    interval,
                    auto_adjust,
                    actions_requested,
                    threads=True,
                    start=effective_start,
                )
                fresh = _merge_history_frames(fresh, bootstrapped)
            except Exception as exc:
                failure_details.append(
                    {
                        "scope": "new_ticker_bootstrap_batch",
                        "batch_start": batch_start,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:180],
                    }
                )

        for ticker in batch:
            if _contains_ticker(fresh, ticker):
                refreshed_successful.add(ticker)
                negative_cache.pop(ticker, None)

        retry_candidates: list[tuple[str, str, str | None]] = []
        for ticker in cached_batch:
            if not _contains_ticker(fresh, ticker):
                retry_candidates.append((ticker, incremental_period, None))
        for ticker in new_batch:
            if not _contains_ticker(fresh, ticker):
                retry_candidates.append((ticker, period, effective_start))

        for ticker, retry_period, retry_start in retry_candidates:
            try:
                retry = _download_one(
                    yf,
                    ticker,
                    retry_period,
                    interval,
                    auto_adjust,
                    actions_requested,
                    start=retry_start,
                )
                if retry.empty:
                    if ticker not in cached_batch:
                        old = negative_cache.get(ticker, {})
                        negative_cache[ticker] = {
                            "last_failed_at_utc": now_utc.isoformat(),
                            "reason": "EMPTY_OR_NO_PRICE_DATA",
                            "consecutive_failures": int(old.get("consecutive_failures", 0)) + 1,
                        }
                    failure_details.append(
                        {
                            "scope": "ticker_refresh" if ticker in cached_batch else "ticker_bootstrap",
                            "ticker": ticker,
                            "error": "EMPTY_OR_NO_PRICE_DATA",
                            "cached_history_preserved": ticker in cached_batch,
                        }
                    )
                else:
                    refreshed_successful.add(ticker)
                    negative_cache.pop(ticker, None)
                    fresh = _merge_history_frames(fresh, retry)
            except Exception as exc:
                if ticker not in cached_batch:
                    old = negative_cache.get(ticker, {})
                    negative_cache[ticker] = {
                        "last_failed_at_utc": now_utc.isoformat(),
                        "reason": type(exc).__name__,
                        "consecutive_failures": int(old.get("consecutive_failures", 0)) + 1,
                    }
                failure_details.append(
                    {
                        "scope": "ticker_refresh" if ticker in cached_batch else "ticker_bootstrap",
                        "ticker": ticker,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:180],
                        "cached_history_preserved": ticker in cached_batch,
                    }
                )
            time.sleep(0.05)

        for ticker in skipped_negative:
            failure_details.append(
                {
                    "scope": "negative_cache",
                    "ticker": ticker,
                    "error": "NEGATIVE_CACHE_HIT",
                    "ttl_days": negative_ttl_days,
                }
            )

        combined = _merge_history_frames(existing, fresh)
        combined = _trim_to_rolling_window(combined, window_start)
        try:
            combined.to_parquet(output)
        except Exception as exc:
            logger.warning(
                "OHLCV cache write failed for %s: %s: %s",
                output,
                type(exc).__name__,
                exc,
            )
            failure_details.append(
                {
                    "scope": "cache_write",
                    "batch_start": batch_start,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:180],
                }
            )
        for ticker in batch:
            if _contains_ticker(combined, ticker):
                cached_tickers.add(ticker)
                negative_cache.pop(ticker, None)
        time.sleep(0.05)

    successful = sorted(cached_tickers)
    failed = sorted(set(clean) - cached_tickers)
    negative_cache = {
        ticker: entry
        for ticker, entry in negative_cache.items()
        if ticker in set(failed)
    }
    manifest = cache / "history_manifest.json"
    payload = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "mode": mode,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested": len(clean),
        "requested_tickers": clean,
        "cached_tickers": successful,
        "successful": successful,
        "refreshed_successful": sorted(refreshed_successful),
        "failed": failed,
        "failure_details": failure_details,
        "negative_cache": negative_cache,
        "negative_cache_ttl_days": negative_ttl_days,
        "negative_cache_hits": sorted(set(negative_cache_hits)),
        "rebased_batches": rebased_batches,
        "universe_migrated_this_run": migration_performed,
        "actions_requested": actions_requested,
        "interval": interval,
        "batch_size": int(batch_size),
        "auto_adjust": bool(auto_adjust),
        "bootstrap_start": bootstrap_start,
        "rolling_months": rolling_months,
        "effective_window_start": effective_start,
        "bootstrap_period_fallback": period,
        "incremental_period": incremental_period,
    }
    try:
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error(
            "OHLCV manifest write failed for %s: %s: %s",
            manifest,
            type(exc).__name__,
            exc,
        )
        return DownloadResult(len(clean), successful, failed, None)

    logger.info(
        "OHLCV %s: %s/%s usable, %s refreshed, %s negative-cache hits, %s rebased batches, migrated=%s, window_start=%s",
        mode,
        len(successful),
        len(clean),
        len(refreshed_successful),
        len(set(negative_cache_hits)),
        len(rebased_batches),
        migration_performed,
        effective_start,
    )
    return DownloadResult(len(clean), successful, failed, str(manifest))


def _retry_individual(
    yf,
    tickers,
    cache: Path,
    batch_start: int,
    period: str,
    interval: str,
    auto_adjust: bool,
    actions_requested: bool,
    successful: list[str],
    failed: list[str],
    failure_details: list[dict],
) -> None:
    """Backward-compatible helper retained for callers/tests."""
    output = cache / f"history_{batch_start:05d}.parquet"
    existing = _read_cached_frame(output, list(tickers))
    fresh = pd.DataFrame()
    for ticker in tickers:
        try:
            retry = _download_one(
                yf, ticker, period, interval, auto_adjust, actions_requested
            )
            if retry.empty:
                failed.append(ticker)
                failure_details.append(
                    {
                        "scope": "ticker",
                        "ticker": ticker,
                        "error": "EMPTY_OR_NO_PRICE_DATA",
                    }
                )
            else:
                successful.append(ticker)
                fresh = _merge_history_frames(fresh, retry)
        except Exception as exc:
            failed.append(ticker)
            failure_details.append(
                {
                    "scope": "ticker",
                    "ticker": ticker,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:180],
                }
            )
        time.sleep(0.12)
    combined = _merge_history_frames(existing, fresh)
    if not combined.empty:
        combined.to_parquet(output)
