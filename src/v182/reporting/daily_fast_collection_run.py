from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable
import json
import os
import shutil

import pandas as pd

from v182.features import topdown_prefetch_v21_15_4 as topdown_prefetch
from v182.reporting import run as legacy
from v182.reporting import waves
from v182.reporting.incremental_collection_audit_v21_15_4 import IncrementalCollectionAuditor
from v182.reporting.runtime_telemetry import RuntimeTelemetry
from v182.sources import finnhub_consensus, gdelt_news, yfinance_info


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_FAST_COLLECTION_V21_15_4"
STATE_DIR = ROOT / "state" / "provenance" / "daily_fast_master_v1"
MANIFEST = STATE_DIR / "manifest.json"
ACTIONS_STATE = STATE_DIR / "actions.parquet"
ETF_STATE = STATE_DIR / "etf.parquet"
QUARANTINE_STATE = STATE_DIR / "quarantine.csv"
AUDIT = ROOT / "outputs" / "audit" / "DAILY_FAST_COLLECTION_V21_15_4.json"

_ACTION_INPUT = "V18.2_PEA_ACTIONS_MASTER.csv"
_ETF_INPUT = "V18.2_PEA_ETF_MASTER.csv"
_ACTION_OUTPUT = "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
_ETF_OUTPUT = "V18.2_PEA_ETF_MASTER_ENRICHED.csv"

_CACHE_FILES = {
    "yfinance_actions": ROOT / "state" / "provenance" / "source_cache" / "YFINANCE_INFO_V1.json",
    "finnhub_actions": ROOT / "state" / "provenance" / "source_cache" / "FINNHUB_CONSENSUS_V1.json",
    "yfinance_etf": ROOT / "state" / "provenance" / "source_cache" / "YFINANCE_ETF_INFO_V1.json",
}

_YF_CACHE_FIELDS_REQUIRED_FOR_DAILY_PRICE_RATIOS = {
    "trailing_eps_yf",
    "forward_eps_yf",
    "book_value_per_share_yf",
}


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _static_contract() -> dict[str, str | None]:
    """Inputs whose change invalidates retained enriched masters."""
    files = {
        "actions_input": ROOT / "inputs" / _ACTION_INPUT,
        "etf_input": ROOT / "inputs" / _ETF_INPUT,
        "master_config": ROOT / "config" / "V18.2_MASTER_CONFIG.json",
        "canonical_actions": ROOT / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts",
        "etf_ticker_map": ROOT / "config" / "V18.2_ETF_TICKER_MAP.csv",
        "manual_overrides": ROOT / "config" / "V18.2_MANUAL_OVERRIDES.csv",
        "morningstar_actions": ROOT / "inputs" / "V21_ACTION_MORNINGSTAR_RATINGS.csv",
        "scrape_selectors": ROOT / "config" / "V18.2_SCRAPE_SELECTORS.json",
    }
    return {name: _sha256_file(path) for name, path in files.items()}


def _cache_contract() -> dict[str, str | None]:
    return {name: _sha256_file(path) for name, path in _CACHE_FILES.items()}


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_fast_frame(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def _valid_fast_frame(frame: pd.DataFrame, *, expected_rows: int) -> bool:
    return bool(
        expected_rows > 0
        and not frame.empty
        and "isin" in frame.columns
        and len(frame) == int(expected_rows)
        and frame["isin"].astype(str).nunique() == int(expected_rows)
    )


def _load_fast_state() -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    """Return retained masters and DISABLED, DELTA_ONLY or RECONCILE_CACHE."""
    if os.environ.get("PEA_RUN_PROFILE", "").strip().upper() != "DAILY_TACTICAL":
        return pd.DataFrame(), pd.DataFrame(), {}, "DISABLED"
    manifest = _load_manifest()
    if manifest.get("version") != VERSION or manifest.get("validated") is not True:
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"
    if manifest.get("static_contract") != _static_contract():
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"

    recorded_revision = str(manifest.get("github_sha") or "").strip()
    current_revision = str(os.environ.get("GITHUB_SHA") or "").strip()
    if recorded_revision and current_revision and recorded_revision != current_revision:
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"

    if manifest.get("actions_sha256") != _sha256_file(ACTIONS_STATE):
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"
    if manifest.get("etf_sha256") != _sha256_file(ETF_STATE):
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"

    actions = _read_fast_frame(ACTIONS_STATE)
    etf = _read_fast_frame(ETF_STATE)
    if not _valid_fast_frame(actions, expected_rows=int(manifest.get("actions_rows", 0) or 0)):
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"
    if not _valid_fast_frame(etf, expected_rows=int(manifest.get("etf_rows", 0) or 0)):
        return pd.DataFrame(), pd.DataFrame(), manifest, "DISABLED"

    mode = "DELTA_ONLY" if manifest.get("cache_contract") == _cache_contract() else "RECONCILE_CACHE"
    return actions, etf, manifest, mode


def _dedupe_dicts(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    output: list[dict] = []
    for row in rows:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _load_quarantine_state() -> list[dict]:
    if not QUARANTINE_STATE.exists() or QUARANTINE_STATE.stat().st_size == 0:
        return []
    try:
        frame = pd.read_csv(QUARANTINE_STATE, sep=";", encoding="utf-8-sig", low_memory=False)
    except Exception:
        return []
    return frame.to_dict("records") if not frame.empty else []


def _topdown_to_observations(result) -> tuple[list[dict], list[dict], dict]:
    obs_actions: list[dict] = []
    obs_etf: list[dict] = []
    for isin, fields in result.action_scores.items():
        for field, value in fields.items():
            source = result.provenance.get(field, "TOPDOWN_INTERNAL")
            evidence = "B" if source.startswith("FRED") or source.startswith("GDELT") else "C"
            obs_actions.append(waves._obs("ACTION", isin, field, value, source, evidence))
        if "funnel_market_sentiment_score" in fields:
            obs_actions.append(
                waves._obs(
                    "ACTION",
                    isin,
                    "sentiment_regime_score",
                    fields["funnel_market_sentiment_score"],
                    "INTERNAL_PIT_BREADTH_MOMENTUM",
                    "C",
                )
            )
    for isin, fields in result.etf_scores.items():
        for field, value in fields.items():
            source = result.provenance.get(field, "TOPDOWN_INTERNAL")
            evidence = "B" if source.startswith("FRED") or source.startswith("GDELT") else "C"
            obs_etf.append(waves._obs("ETF", isin, field, value, source, evidence))
    diagnostics = {
        "global_scores": result.global_scores,
        "provenance": result.provenance,
        "details": result.diagnostics,
    }
    return obs_actions, obs_etf, diagnostics


def _fixed_window_fetcher(anchor: datetime, original_fetch: Callable) -> Callable:
    """Freeze Top-Down's 2d GDELT window at run start for safe early prefetch."""
    start = anchor.astimezone(timezone.utc) - timedelta(days=2)
    end = anchor.astimezone(timezone.utc)
    start_text = start.strftime("%Y%m%d%H%M%S")
    end_text = end.strftime("%Y%m%d%H%M%S")

    def fetch_articles(
        query: str,
        *,
        timespan: str = "2d",
        max_records: int = 50,
        timeout: int = 20,
        limiter=None,
    ) -> tuple[list[dict], str | None]:
        if str(timespan).strip().lower() not in {"2d", "2days"}:
            return original_fetch(
                query,
                timespan=timespan,
                max_records=max_records,
                timeout=timeout,
                limiter=limiter,
            )
        import requests
        import time

        last_error: str | None = None
        for attempt in range(len(gdelt_news.GDELT_RETRY_BACKOFF_SECONDS) + 1):
            try:
                if limiter is not None:
                    limiter.wait()
                gdelt_news._GDELT_GLOBAL_LIMITER.wait()
                response = requests.get(
                    gdelt_news.GDELT_DOC,
                    params={
                        "query": query,
                        "mode": "ArtList",
                        "format": "json",
                        "maxrecords": int(max_records),
                        "startdatetime": start_text,
                        "enddatetime": end_text,
                        "sort": "HybridRel",
                    },
                    timeout=int(timeout),
                )
                response.raise_for_status()
                payload = response.json()
                articles = payload.get("articles", []) if isinstance(payload, dict) else []
                return articles if isinstance(articles, list) else [], None
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                if attempt >= len(gdelt_news.GDELT_RETRY_BACKOFF_SECONDS) or not gdelt_news._retryable_gdelt_error(exc):
                    return [], last_error
                time.sleep(gdelt_news.GDELT_RETRY_BACKOFF_SECONDS[attempt])
        return [], last_error or "GDELT_UNKNOWN_ERROR"

    return fetch_articles


class DailyFastRuntime:
    def __init__(self, actions: pd.DataFrame, etf: pd.DataFrame, manifest: dict, mode: str, anchor: datetime) -> None:
        self.actions = actions
        self.etf = etf
        self.manifest = manifest
        self.mode = mode
        self.anchor = anchor
        self.captured: dict[str, pd.DataFrame] = {}
        self.previous_quarantine = _load_quarantine_state() if mode == "DELTA_ONLY" else []
        self.quarantine_injected = False
        self.prefetch_pool: ThreadPoolExecutor | None = None
        self.prefetch_future: Future | None = None
        self.prepared_topdown = None
        self.prefetch_reused = False
        self.prefetch_fallback = False
        self.shadow_skipped = False
        self.incremental_auditor = IncrementalCollectionAuditor(legacy.DATA_AUDIT)

        self.original_load_master = legacy.load_master
        self.original_save_master = legacy.save_master
        self.original_apply_and_track = legacy.apply_and_track
        self.original_audit = legacy._audit
        self.original_wave4_shadow = legacy._collect_wave4_boursorama_shadow_parallel
        self.original_wave9 = waves.wave9_topdown
        self.original_yf_entry_rows = yfinance_info._entry_rows
        self.original_finnhub_entry_observations = finnhub_consensus._entry_observations
        self.original_gdelt_fetch = gdelt_news.fetch_articles

    @property
    def enabled(self) -> bool:
        return self.mode in {"DELTA_ONLY", "RECONCILE_CACHE"}

    def install(self) -> None:
        if not self.enabled:
            return

        def fast_load_master(path):
            name = Path(path).name
            if name == _ACTION_INPUT:
                return self.actions.copy(deep=True)
            if name == _ETF_INPUT:
                return self.etf.copy(deep=True)
            return self.original_load_master(path)

        def capture_save_master(frame, path):
            self.original_save_master(frame, path)
            name = Path(path).name
            if name == _ACTION_OUTPUT:
                self.captured["ACTION"] = frame.copy(deep=True)
            elif name == _ETF_OUTPUT:
                self.captured["ETF"] = frame.copy(deep=True)

        def fast_apply(frame, observations):
            self.incremental_auditor.note(observations)
            output, quarantined = self.original_apply_and_track(frame, observations)
            if self.mode == "DELTA_ONLY" and not self.quarantine_injected:
                quarantined = _dedupe_dicts([*self.previous_quarantine, *quarantined])
                self.quarantine_injected = True
            return output, quarantined

        def fast_audit(actions, etfs, wave_id, *, failures=None, source_context=""):
            self.incremental_auditor.audit(
                actions,
                etfs,
                wave_id,
                failures=failures,
                source_context=source_context,
                original_audit=self.original_audit,
            )

        def fast_yf_entry_rows(entry: dict, ticker: str, cache_state: str, tier: str) -> list[dict]:
            rows = self.original_yf_entry_rows(entry, ticker, cache_state, tier)
            if self.mode != "DELTA_ONLY" or cache_state != "CACHE_HIT":
                return rows
            # Cached EPS/book remain available because WAVE04 recomputes today's
            # price-sensitive PER/PB ratios with the fresh WAVE03 last_close.
            return [row for row in rows if str(row.get("field")) in _YF_CACHE_FIELDS_REQUIRED_FOR_DAILY_PRICE_RATIOS]

        def fast_finnhub_entry(entry: dict, ticker: str, *, recommendation_live: bool, target_live: bool) -> list[dict]:
            rows = self.original_finnhub_entry_observations(
                entry,
                ticker,
                recommendation_live=recommendation_live,
                target_live=target_live,
            )
            if self.mode != "DELTA_ONLY":
                return rows
            return [row for row in rows if str(row.get("cache_state")) == "LIVE_REFRESH"]

        def fast_wave4(actions_df: pd.DataFrame, cfg: dict, run_profile: str):
            wave4_result = waves.wave4_info_actions(actions_df, cfg)
            self.shadow_skipped = True
            payload = {
                "status": "DAILY_FAST_CACHE_REUSED_NO_LIVE_REFRESH",
                "profile": run_profile,
                "live_refresh_requested": 0,
                "live_refresh_success": 0,
                "observations": 0,
                "decision_influence": False,
                "existing_provider_suppression": False,
                "full_shadow_equivalence_deferred_to_weekly": True,
            }
            path = ROOT / "outputs" / "audit" / "BOURSORAMA_PUBLIC_SHADOW_METRICS.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return wave4_result, payload

        def fast_wave9(actions_df: pd.DataFrame, etf_df: pd.DataFrame, cfg: dict, fred_api_key: str | None):
            if self.prefetch_future is None or self.prepared_topdown is None:
                self.prefetch_fallback = True
                return self.original_wave9(actions_df, etf_df, cfg, fred_api_key)
            external = self.prefetch_future.result()
            try:
                result = topdown_prefetch.finalize(
                    actions_df,
                    etf_df,
                    self.prepared_topdown,
                    external,
                )
            except RuntimeError:
                self.prefetch_fallback = True
                return self.original_wave9(actions_df, etf_df, cfg, fred_api_key)
            self.prefetch_reused = True
            return _topdown_to_observations(result)

        legacy.load_master = fast_load_master
        legacy.save_master = capture_save_master
        legacy.apply_and_track = fast_apply
        legacy._audit = fast_audit
        legacy._collect_wave4_boursorama_shadow_parallel = fast_wave4
        yfinance_info._entry_rows = fast_yf_entry_rows
        finnhub_consensus._entry_observations = fast_finnhub_entry
        waves.wave9_topdown = fast_wave9

        if self.mode == "DELTA_ONLY":
            # Exact STARTDATETIME/ENDDATETIME makes the query window independent
            # of whether the same requests start now or later at WAVE09.
            gdelt_news.fetch_articles = _fixed_window_fetcher(self.anchor, self.original_gdelt_fetch)
            cfg = legacy._load_cfg()
            top_n = int(cfg.get("topdown", {}).get("instrument_news_top_n", 80))
            self.prepared_topdown = topdown_prefetch.prepare(
                self.actions,
                self.etf,
                instrument_news_top_n=top_n,
            )
            self.prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-fast-topdown")
            self.prefetch_future = self.prefetch_pool.submit(
                topdown_prefetch.fetch_external,
                self.prepared_topdown,
                fred_api_key=os.environ.get("FRED_API_KEY"),
            )

    def restore(self) -> None:
        if not self.enabled:
            return
        legacy.load_master = self.original_load_master
        legacy.save_master = self.original_save_master
        legacy.apply_and_track = self.original_apply_and_track
        legacy._audit = self.original_audit
        legacy._collect_wave4_boursorama_shadow_parallel = self.original_wave4_shadow
        yfinance_info._entry_rows = self.original_yf_entry_rows
        finnhub_consensus._entry_observations = self.original_finnhub_entry_observations
        gdelt_news.fetch_articles = self.original_gdelt_fetch
        waves.wave9_topdown = self.original_wave9
        if self.prefetch_pool is not None:
            self.prefetch_pool.shutdown(wait=True, cancel_futures=False)

    def promote(self) -> dict:
        """Persist fast state only after the legacy pipeline quality gates pass."""
        actions = self.captured.get("ACTION")
        etf = self.captured.get("ETF")
        if actions is None or etf is None:
            return {"promoted": False, "reason": "ENRICHED_MASTER_NOT_CAPTURED"}

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        actions_tmp = STATE_DIR / ".actions.parquet.tmp"
        etf_tmp = STATE_DIR / ".etf.parquet.tmp"
        actions.to_parquet(actions_tmp, index=False)
        etf.to_parquet(etf_tmp, index=False)
        actions_tmp.replace(ACTIONS_STATE)
        etf_tmp.replace(ETF_STATE)

        quarantine = ROOT / "outputs" / "gaps" / "V18.2_QUARANTINE.csv"
        if quarantine.exists() and quarantine.stat().st_size > 0:
            shutil.copyfile(quarantine, QUARANTINE_STATE)
        elif QUARANTINE_STATE.exists():
            QUARANTINE_STATE.unlink()

        payload = {
            "version": VERSION,
            "validated": True,
            "validated_at_utc": datetime.now(timezone.utc).isoformat(),
            "github_sha": str(os.environ.get("GITHUB_SHA") or ""),
            "static_contract": _static_contract(),
            "cache_contract": _cache_contract(),
            "actions_rows": int(len(actions)),
            "etf_rows": int(len(etf)),
            "actions_columns": int(len(actions.columns)),
            "etf_columns": int(len(etf.columns)),
            "actions_sha256": _sha256_file(ACTIONS_STATE),
            "etf_sha256": _sha256_file(ETF_STATE),
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
        }
        temp = MANIFEST.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(MANIFEST)
        return {"promoted": True, "manifest": str(MANIFEST.relative_to(ROOT))}

    def audit(self, promotion: dict, status: str) -> None:
        self.incremental_auditor.write_audit()
        payload = {
            "version": VERSION,
            "status": status,
            "mode": self.mode,
            "fast_state_used": self.enabled,
            "cache_contract_unchanged": self.mode == "DELTA_ONLY",
            "cache_reconciliation_required": self.mode == "RECONCILE_CACHE",
            "prior_quarantine_replayed": bool(self.previous_quarantine and self.mode == "DELTA_ONLY"),
            "boursorama_shadow_cache_only_work_skipped": self.shadow_skipped,
            "topdown_external_prefetch_started_at_pipeline_start": self.prefetch_future is not None,
            "topdown_prefetch_reused": self.prefetch_reused,
            "topdown_prefetch_fail_closed_fallback": self.prefetch_fallback,
            "gdelt_exact_window_used_for_prefetch": self.mode == "DELTA_ONLY",
            "incremental_collection_audit": self.incremental_auditor.payload(),
            "promotion": promotion,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "full_pipeline_fallback_when_state_invalid": True,
        }
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run() -> dict:
    anchor = datetime.now(timezone.utc)
    run_id = os.environ.get("V182_RUN_ID") or anchor.strftime("%Y-%m-%d")
    run_profile = os.environ.get("PEA_RUN_PROFILE", "FULL").strip().upper() or "FULL"
    actions, etf, manifest, mode = _load_fast_state()
    fast = DailyFastRuntime(actions, etf, manifest, mode, anchor)
    runtime = RuntimeTelemetry(legacy.OUTPUTS / "audit", run_id=run_id, profile=f"{run_profile}_FAST")
    promotion: dict = {"promoted": False, "reason": "PIPELINE_NOT_COMPLETED"}
    fast.install()
    try:
        result = legacy._run_pipeline(run_id, run_profile, runtime)
        promotion = fast.promote()
        result["daily_fast_collection"] = {"version": VERSION, "mode": mode, **promotion}
        result["runtime_telemetry"] = runtime.finalize(
            "SUCCESS",
            excel_exports_enabled=result["excel_exports_enabled"],
            intermediate_collection_audit_format="CSV" if run_profile == "DAILY_TACTICAL" else "XLSX",
            daily_fast_mode=mode,
        )
        fast.audit(promotion, "SUCCESS")
        return result
    except Exception as exc:
        runtime.finalize(
            "FAILED",
            error_type=type(exc).__name__,
            error_detail=str(exc)[:500],
            daily_fast_mode=mode,
        )
        fast.audit(promotion, "FAILED")
        raise
    finally:
        fast.restore()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
