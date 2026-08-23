from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

import pandas as pd

from v182.reporting import daily_tactical_super_runner_v21_15_4 as base
from v182.reporting import selected_source_enrichment as selected_source


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_TACTICAL_DAG_V21_15_5"
ACTION_CT_DAILY_TOP_N_DEFAULT = 20
INVESTING_DAILY_RETRY_BUDGET = 8
INVESTING_DAILY_TIMEOUT_SECONDS = 5.0


def _daily_tct_scope(actions_with_tct: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return the only rows that can produce an authorised Daily T1/T2 outcome.

    The exact detector itself already requires current baseline Top-N eligibility
    before T1/T2 can become actionable. Full-universe T1/T2 research remains a
    weekly responsibility; Daily evaluates the same exact formula only where its
    result can affect the Daily TCT decision path.
    """
    if actions_with_tct.empty:
        return actions_with_tct.copy()
    rank = pd.to_numeric(actions_with_tct.get("tct_baseline_rank"), errors="coerce")
    coverage = pd.to_numeric(actions_with_tct.get("tct_baseline_coverage"), errors="coerce")
    top_n = max(0, int(cfg.get("scope", {}).get("baseline_top_n", 20)))
    minimum = float(cfg.get("scope", {}).get("baseline_min_coverage", 0.60))
    mask = rank.notna() & rank.between(1, top_n) & coverage.notna() & coverage.ge(minimum)
    return actions_with_tct.loc[mask].copy()


def _build_core_daily(root: Path) -> dict:
    """Build the historical Daily decisions while bounding exact TCT work to Top20."""
    outputs = root / "outputs"
    outdir = outputs / "daily_tct_ct"
    outdir.mkdir(parents=True, exist_ok=True)

    actions = base._read(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etfs = base._read(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_ref = base.load_registry(root / "config" / "V21_ACTIONS_REFERENCE_V21_0.json")
    etf_ref = base.load_registry(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    tct_cfg = base.load_tct_config(root / "config" / "TCT_V24_1_7_SHADOW.json")
    v21_cfg = json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))

    parts: list[pd.DataFrame] = [
        base.decisions_from_scores(actions, action_ref, "ACTION", ["CT"]),
        base.decisions_from_scores(etfs, etf_ref, "ETF", ["CT"]),
    ]

    actions_with_tct, baseline = base.build_tct_baseline(actions, tct_cfg)
    base._write(actions_with_tct, outdir / "TCT_BASELINE_V24_1_8.csv")
    exact_scope = _daily_tct_scope(actions_with_tct, tct_cfg)
    tct_state_path = root / str(tct_cfg.get("state", {}).get("path", "state/TCT_V24_1_7_T1_STATE.json"))
    tct_shadow, exact = base.build_exact_timing_snapshot(
        exact_scope,
        root / "data" / "cache" / "actions",
        tct_state_path,
        tct_cfg,
    )
    base._write(tct_shadow, outdir / "TCT_SHADOW_V24_1_7.csv")
    if tct_shadow is not None and not tct_shadow.empty:
        parts.append(base.tct_adapter(tct_shadow))

    decisions = pd.concat([part for part in parts if part is not None and not part.empty], ignore_index=True, sort=False)
    generated_at = base.datetime.now(base.timezone.utc).isoformat()
    decisions["generated_at_utc"] = generated_at
    decisions["live_orders_enabled"] = False
    decisions["daily_tactical_scope"] = True
    base._assert_unique(decisions, "DAILY_CORE")
    base._write(decisions, outdir / "DAILY_TCT_CT_DECISIONS.csv")
    source_input = base.attach_master_identity(decisions, actions, etfs)

    auditdir = outputs / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    scope_payload = {
        "version": VERSION,
        "action_universe_rows": int(len(actions_with_tct)),
        "tct_exact_daily_rows": int(len(exact_scope)),
        "tct_exact_weekly_full_universe_preserved": True,
        "daily_exact_policy": "CURRENT_BASELINE_TOP_N_WITH_MINIMUM_COVERAGE_ONLY",
        "baseline_top_n": int(tct_cfg.get("scope", {}).get("baseline_top_n", 20)),
        "baseline_min_coverage": float(tct_cfg.get("scope", {}).get("baseline_min_coverage", 0.60)),
        "t1_t2_formula_changed": False,
        "t1_t2_thresholds_changed": False,
        "t1_t2_scope_asset_horizon_changed": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
    }
    (auditdir / "DAILY_SCOPE_V21_15_5.json").write_text(
        json.dumps(scope_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "actions": actions,
        "etfs": etfs,
        "decisions": decisions,
        "source_input": source_input,
        "generated_at": generated_at,
        "baseline": baseline,
        "exact": exact,
        "v21_cfg": v21_cfg,
    }


def _action_ct_top_n(root: Path) -> int:
    try:
        cfg = json.loads((root / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
        return max(
            1,
            int(
                cfg.get("candidate_selection", {})
                .get("preselection_scope", {})
                .get("action_ct_top_n", ACTION_CT_DAILY_TOP_N_DEFAULT)
            ),
        )
    except Exception:
        return ACTION_CT_DAILY_TOP_N_DEFAULT


def _selected_action_ct_isins(root: Path) -> set[str]:
    path = root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv"
    if not path.exists() or path.stat().st_size == 0:
        return set()
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    required = {"asset_class", "horizon", "isin", "score", "decision"}
    if not required.issubset(frame.columns):
        return set()
    work = frame[
        frame["asset_class"].astype(str).str.upper().eq("ACTION")
        & frame["horizon"].astype(str).str.upper().eq("CT")
    ].copy()
    allowed = {"BUY_CANDIDATE", "WATCH", "REVIEW"}
    work["_score"] = pd.to_numeric(work["score"], errors="coerce")
    work = work[work["decision"].astype(str).str.upper().isin(allowed) & work["_score"].notna()]
    if "status" in work.columns:
        work = work[work["status"].astype(str).eq("SCORABLE")]
    work = work.sort_values("_score", ascending=False).head(_action_ct_top_n(root))
    return set(work["isin"].astype(str).str.upper().str.strip())


def _run_tactical_scoped(original_run, root: Path, *, tct_complete_callback=None) -> dict:
    """Run shadow engines only for the Daily operational candidate scope.

    Action CT V22.0/V22.1 have zero decision/score influence and are retained in
    full-universe form in the weekly research run. Daily keeps the same models and
    formulas but applies them only to the already-selected Action CT Top-N. TCT
    V24.3.1 is automatically bounded because its input TCT shadow is Daily Top-N.
    """
    selected_isins = _selected_action_ct_isins(root)
    v220 = base.tactical.action_ct_bundle.v220
    original_read_csv = v220._read_csv

    def scoped_read_csv(path: Path):
        frame = original_read_csv(path)
        if Path(path).name != "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv":
            return frame
        if frame.empty or "isin" not in frame.columns:
            return frame
        if not selected_isins:
            return frame.iloc[0:0].copy()
        isin = frame["isin"].astype(str).str.upper().str.strip()
        return frame.loc[isin.isin(selected_isins)].copy()

    v220._read_csv = scoped_read_csv
    started = perf_counter()
    try:
        payload = original_run(root=root, tct_complete_callback=tct_complete_callback)
    finally:
        v220._read_csv = original_read_csv

    payload = dict(payload or {})
    payload["daily_scope_version"] = VERSION
    payload["action_ct_daily_selected_isins"] = int(len(selected_isins))
    payload["action_ct_full_universe_weekly_preserved"] = True
    payload["action_ct_model_changed"] = False
    payload["action_ct_scope_policy"] = "UPSTREAM_ACTION_CT_PRESELECTION_TOP_N"
    payload["daily_scoped_total_seconds"] = round(float(perf_counter() - started), 6)
    return payload


def _enrich_selected_daily(rows: pd.DataFrame, root: Path, *, profile: str = "DAILY_TCT_CT"):
    """Keep Investing, but bound failed Daily retries; weekly uses the full contract."""
    original_collect = selected_source.collect_technical_context_cached

    def bounded_collect(*args, **kwargs):
        requested = int(kwargs.get("refresh_budget", INVESTING_DAILY_RETRY_BUDGET))
        kwargs["refresh_budget"] = min(requested, INVESTING_DAILY_RETRY_BUDGET)
        kwargs["timeout_seconds"] = min(
            float(kwargs.get("timeout_seconds", INVESTING_DAILY_TIMEOUT_SECONDS)),
            INVESTING_DAILY_TIMEOUT_SECONDS,
        )
        return original_collect(*args, **kwargs)

    selected_source.collect_technical_context_cached = bounded_collect
    try:
        enriched, payload = selected_source.enrich_selected_rows(rows, root, profile=profile)
    finally:
        selected_source.collect_technical_context_cached = original_collect

    payload = dict(payload or {})
    payload["investing_daily_retry_budget"] = INVESTING_DAILY_RETRY_BUDGET
    payload["investing_daily_timeout_seconds"] = INVESTING_DAILY_TIMEOUT_SECONDS
    payload["investing_full_refresh_weekly_preserved"] = True
    payload["investing_function_removed"] = False
    return enriched, payload


def _run_postmarket_dtype_safe(original_run, root: Path) -> dict:
    """Fix the observed pandas bool-to-float lineage failure without changing PIT logic."""
    lineage = base.postmarket.lineage
    original_apply = lineage.apply_lineage

    def compatible_apply(catalyst_ledger, ohlc_ledger, **kwargs):
        ledger = catalyst_ledger.copy()
        if "pit_label_evaluable" in ledger.columns:
            ledger["pit_label_evaluable"] = ledger["pit_label_evaluable"].astype(object)
        return original_apply(ledger, ohlc_ledger, **kwargs)

    lineage.apply_lineage = compatible_apply
    try:
        return original_run(root=root)
    finally:
        lineage.apply_lineage = original_apply


def _patch_runtime_audits(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload or {})
    enriched["version"] = VERSION
    enriched["daily_scope_optimization"] = {
        "tct_exact": "BASELINE_TOP_N_ONLY",
        "action_ct_shadow": "UPSTREAM_PRESELECTION_TOP_N_ONLY",
        "tct_trader": "TCT_DAILY_TOP_N_INPUT_ONLY",
        "investing": "BOUNDED_DAILY_RETRY_FULL_WEEKLY_REFRESH",
        "postmarket_lineage_dtype_fix": True,
        "weekly_full_universe_research_preserved": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
    text = json.dumps(enriched, ensure_ascii=False, indent=2, default=str)
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_4.json").write_text(text, encoding="utf-8")
    (auditdir / "DAILY_TACTICAL_DAG_RUNTIME_V21_15_5.json").write_text(text, encoding="utf-8")

    daily_path = auditdir / "DAILY_TCT_CT_AUDIT.json"
    if daily_path.exists():
        try:
            daily = json.loads(daily_path.read_text(encoding="utf-8"))
            daily["runtime_orchestration_version"] = VERSION
            daily["daily_tct_exact_scope"] = "BASELINE_TOP_N_ONLY"
            daily["weekly_full_tct_research_preserved"] = True
            daily_path.write_text(json.dumps(daily, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass


def run(root: Path = ROOT) -> dict:
    """Final Daily tactical orchestration: bounded work, unchanged authorised models."""
    original_build_core = base._build_core
    original_tactical_run = base.tactical.run
    original_enrich = base.enrich_selected_rows
    original_postmarket_run = base.postmarket.run
    original_version = base.VERSION

    base._build_core = _build_core_daily
    base.tactical.run = lambda root=ROOT, tct_complete_callback=None: _run_tactical_scoped(
        original_tactical_run,
        root,
        tct_complete_callback=tct_complete_callback,
    )
    base.enrich_selected_rows = _enrich_selected_daily
    base.postmarket.run = lambda root=ROOT: _run_postmarket_dtype_safe(original_postmarket_run, root)
    base.VERSION = VERSION
    try:
        payload = base.run(root=root)
    finally:
        base._build_core = original_build_core
        base.tactical.run = original_tactical_run
        base.enrich_selected_rows = original_enrich
        base.postmarket.run = original_postmarket_run
        base.VERSION = original_version

    payload = dict(payload or {})
    payload["version"] = VERSION
    payload["weekly_full_universe_research_preserved"] = True
    payload["daily_operational_scope_only"] = True
    _patch_runtime_audits(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
