from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json
import pickle

import pandas as pd

from v182.features.ohlcv_features import calculate as calculate_features
from v182.reporting import waves


VERSION = "WAVE3_CPU_BUDGET_V21_15_4"
CACHE_VERSION = "WAVE3_EXACT_OBSERVATION_CACHE_V1"
_LAST_CACHE_DIAGNOSTICS: dict = {"status": "NOT_EVALUATED", "cache_hit": False}


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root_from_cache(actions_cache_dir: str) -> Path:
    path = Path(actions_cache_dir).resolve()
    return path.parents[2]


def _mapping_hash(mapping: dict[str, str]) -> str:
    payload = json.dumps(sorted((str(k), str(v)) for k, v in mapping.items()), ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _cache_contract(
    root: Path,
    actions_cache_dir: str,
    actions_ticker_isin_map: dict[str, str],
    etf_cache_dir: str,
    etf_ticker_isin_map: dict[str, str],
) -> dict:
    return {
        "cache_version": CACHE_VERSION,
        "module_version": VERSION,
        "actions_history_manifest_sha256": _sha256_file(Path(actions_cache_dir) / "history_manifest.json"),
        "etf_history_manifest_sha256": _sha256_file(Path(etf_cache_dir) / "history_manifest.json"),
        "actions_ticker_map_sha256": _mapping_hash(actions_ticker_isin_map),
        "etf_ticker_map_sha256": _mapping_hash(etf_ticker_isin_map),
        "ohlcv_formula_code_sha256": _sha256_file(root / "src" / "v182" / "features" / "ohlcv_features.py"),
        "wave_semantics_code_sha256": _sha256_file(root / "src" / "v182" / "reporting" / "waves.py"),
    }


def _cache_paths(root: Path) -> tuple[Path, Path]:
    state = root / "state" / "provenance" / "wave3_exact_cache_v1"
    return state / "observations.pkl", state / "manifest.json"


def _load_exact_cache(root: Path, contract: dict) -> tuple[list[dict], list[dict], list[dict]] | None:
    data_path, manifest_path = _cache_paths(root)
    if not data_path.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != CACHE_VERSION or manifest.get("contract") != contract:
            return None
        if manifest.get("payload_sha256") != _sha256_file(data_path):
            return None
        with data_path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict):
            return None
        obs_actions = payload.get("actions")
        obs_etf = payload.get("etf")
        obs_beta = payload.get("beta")
        if not all(isinstance(rows, list) for rows in (obs_actions, obs_etf, obs_beta)):
            return None
        if not all(all(isinstance(row, dict) for row in rows) for rows in (obs_actions, obs_etf, obs_beta)):
            return None
        return obs_actions, obs_etf, obs_beta
    except Exception:
        return None


def _persist_exact_cache(
    root: Path,
    contract: dict,
    obs_actions: list[dict],
    obs_etf: list[dict],
    obs_beta: list[dict],
) -> None:
    data_path, manifest_path = _cache_paths(root)
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_tmp = data_path.with_suffix(".pkl.tmp")
        with data_tmp.open("wb") as handle:
            pickle.dump(
                {"actions": obs_actions, "etf": obs_etf, "beta": obs_beta},
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        data_tmp.replace(data_path)
        manifest = {
            "version": CACHE_VERSION,
            "contract": contract,
            "payload_sha256": _sha256_file(data_path),
            "actions_observations": int(len(obs_actions)),
            "etf_observations": int(len(obs_etf)),
            "beta_observations": int(len(obs_beta)),
            "exact_replay": True,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
        }
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_tmp.replace(manifest_path)
    except Exception:
        return


def _write_runtime_audit(root: Path, payload: dict) -> None:
    try:
        path = root / "outputs" / "audit" / "WAVE3_CPU_BUDGET_V21_15_4.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        return


def _action_jobs(frames: list[pd.DataFrame], ticker_isin_map: dict[str, str]) -> list[tuple[str, str, pd.DataFrame]]:
    jobs: list[tuple[str, str, pd.DataFrame]] = []
    for frame in frames:
        if not hasattr(frame.columns, "levels"):
            continue
        for ticker in frame.columns.get_level_values(0).unique():
            isin = ticker_isin_map.get(ticker)
            if isin is None:
                continue
            jobs.append((str(ticker), str(isin), frame[ticker]))
    return jobs


def _compute(job: tuple[str, str, pd.DataFrame]) -> tuple[str, str, dict]:
    ticker, isin, frame = job
    return ticker, isin, calculate_features(frame)


def _action_derived_parallel(
    frames: list[pd.DataFrame],
    ticker_isin_map: dict[str, str],
    *,
    workers: int = 2,
) -> list[dict]:
    """Exact wave3_derived_features semantics with ordered two-worker calculation."""
    jobs = _action_jobs(frames, ticker_isin_map)
    if not jobs:
        return []
    workers = max(1, min(2, int(workers), len(jobs)))
    if workers == 1:
        calculated = [_compute(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wave3-action") as pool:
            calculated = list(pool.map(_compute, jobs))

    per_ticker_perf_1y: dict[str, float] = {}
    per_ticker_perf_10d: dict[str, float] = {}
    per_ticker_indicators: dict[str, dict] = {}
    ordered_isins: list[str] = []
    for _ticker, isin, indicators in calculated:
        if not indicators:
            continue
        if isin not in per_ticker_indicators:
            ordered_isins.append(isin)
        per_ticker_indicators[isin] = indicators
        if indicators.get("perf_1y_pct") is not None:
            per_ticker_perf_1y[isin] = indicators["perf_1y_pct"]
        if indicators.get("perf_10d_pct") is not None:
            per_ticker_perf_10d[isin] = indicators["perf_10d_pct"]

    median_1y = pd.Series(per_ticker_perf_1y).median() if per_ticker_perf_1y else 0.0
    median_10d = pd.Series(per_ticker_perf_10d).median() if per_ticker_perf_10d else 0.0
    observations: list[dict] = []
    for isin in ordered_isins:
        indicators = per_ticker_indicators[isin]
        for field, value in indicators.items():
            if value is not None:
                observations.append(waves._obs("ACTION", isin, field, value, "INTERNAL_FROM_OHLCV", "C"))
        if indicators.get("perf_1y_pct") is not None:
            observations.append(
                waves._obs(
                    "ACTION",
                    isin,
                    "relative_strength",
                    round(indicators["perf_1y_pct"] - median_1y, 4),
                    "INTERNAL_FROM_OHLCV",
                    "C",
                )
            )
        if indicators.get("perf_10d_pct") is not None:
            observations.append(
                waves._obs(
                    "ACTION",
                    isin,
                    "relative_strength_10d",
                    round(indicators["perf_10d_pct"] - median_10d, 4),
                    "INTERNAL_FROM_OHLCV",
                    "C",
                )
            )
    return observations


def wave3_local_features(
    actions_cache_dir: str,
    actions_ticker_isin_map: dict[str, str],
    etf_cache_dir: str,
    etf_ticker_isin_map: dict[str, str],
    *,
    max_workers: int = 2,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Replay exact prior observations if OHLCV, mappings and formula code are identical."""
    global _LAST_CACHE_DIAGNOSTICS
    started = perf_counter()
    root = _root_from_cache(actions_cache_dir)
    contract = _cache_contract(root, actions_cache_dir, actions_ticker_isin_map, etf_cache_dir, etf_ticker_isin_map)
    manifests_available = bool(
        contract.get("actions_history_manifest_sha256")
        and contract.get("etf_history_manifest_sha256")
        and contract.get("ohlcv_formula_code_sha256")
        and contract.get("wave_semantics_code_sha256")
    )
    if manifests_available:
        cached = _load_exact_cache(root, contract)
        if cached is not None:
            obs_actions, obs_etf, obs_beta = cached
            _LAST_CACHE_DIAGNOSTICS = {
                "status": "EXACT_CACHE_REPLAY",
                "cache_hit": True,
                "elapsed_seconds": round(perf_counter() - started, 6),
                "actions_observations": len(obs_actions),
                "etf_observations": len(obs_etf),
                "beta_observations": len(obs_beta),
                "history_manifest_identity_required": True,
                "formula_code_identity_required": True,
                "ticker_map_identity_required": True,
                "decision_logic_changed": False,
            }
            _write_runtime_audit(root, _LAST_CACHE_DIAGNOSTICS)
            return obs_actions, obs_etf, obs_beta

    etf_frames = waves._history_frames(etf_cache_dir)
    obs_etf = waves.wave3_derived_features(
        etf_cache_dir,
        etf_ticker_isin_map,
        "ETF",
        history_frames=etf_frames,
    )
    obs_beta = waves.wave3_etf_beta3y(
        etf_cache_dir,
        etf_ticker_isin_map,
        history_frames=etf_frames,
    )

    action_frames = waves._history_frames(actions_cache_dir)
    obs_actions = _action_derived_parallel(
        action_frames,
        actions_ticker_isin_map,
        workers=max_workers,
    )
    if manifests_available:
        _persist_exact_cache(root, contract, obs_actions, obs_etf, obs_beta)
    _LAST_CACHE_DIAGNOSTICS = {
        "status": "FULL_EXACT_RECOMPUTE",
        "cache_hit": False,
        "elapsed_seconds": round(perf_counter() - started, 6),
        "actions_observations": len(obs_actions),
        "etf_observations": len(obs_etf),
        "beta_observations": len(obs_beta),
        "cache_persisted": manifests_available,
        "history_manifest_identity_required": True,
        "formula_code_identity_required": True,
        "ticker_map_identity_required": True,
        "decision_logic_changed": False,
    }
    _write_runtime_audit(root, _LAST_CACHE_DIAGNOSTICS)
    return obs_actions, obs_etf, obs_beta


def audit_contract() -> dict:
    return {
        "version": VERSION,
        "cache_version": CACHE_VERSION,
        "action_compute_workers_max": 2,
        "etf_rows_expected": 102,
        "action_rows_expected": 1829,
        "executor_map_order_preserved": True,
        "exact_observation_replay_when_contract_unchanged": True,
        "history_manifest_identity_required": True,
        "ticker_map_identity_required": True,
        "formula_code_identity_required": True,
        "runtime_cache_diagnostics": dict(_LAST_CACHE_DIAGNOSTICS),
        "feature_formula_changed": False,
        "relative_strength_formula_changed": False,
        "parquet_read_count_increased": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
