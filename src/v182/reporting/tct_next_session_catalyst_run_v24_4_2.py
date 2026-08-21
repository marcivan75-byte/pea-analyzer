from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.features import tct_catalyst_context_v24_4_2 as feature
from v182.reporting.tct_next_session_catalyst_engine import run_engine
from v182.sources import tct_catalyst_news_v24_4_2 as news_source


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json"
VERSION = feature.VERSION


def _fmt(value, *, signed: bool = False) -> str:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return "N/A"
    return f"{float(parsed):+.1f}" if signed else f"{float(parsed):.1f}"


def _mobile_summary(frame: pd.DataFrame, phase: str, generated_at: str, market: dict, audit: dict) -> str:
    metrics = audit.get("news_batch_metrics", {}) or {}
    lines = [
        f"# TCT V24.4.2 — {phase} — Next Session",
        "",
        f"Généré UTC : {generated_at}",
        f"Couverture news : erreurs finales {metrics.get('final_error_rate', 'N/A')} | cache {metrics.get('cache_hits', 0)} | p95 {metrics.get('latency_p95_seconds', 'N/A')} s",
        f"Runtime : {audit.get('runtime_seconds', 'N/A')} s / budget {audit.get('runtime_budget_seconds', 'N/A')} s",
        f"Contexte global : risk-on {market.get('risk_on_score', 'N/A')} | choc {market.get('shock_magnitude_score', 'N/A')}",
        "Influence production = 0 | aucun ordre réel | aucun 1m/5m.",
        "",
    ]
    if frame.empty:
        return "\n".join(lines + ["Aucun candidat exploitable."]) + "\n"

    work = frame.copy()
    work["_move"] = pd.to_numeric(work.get("movement_potential_score"), errors="coerce")
    work["_dir"] = pd.to_numeric(work.get("direction_bias_score"), errors="coerce")
    work["_exit"] = pd.to_numeric(work.get("exit_risk_score"), errors="coerce")

    lines.extend(["## Top 5 potentiel de mouvement", ""])
    for _, row in work.sort_values("_move", ascending=False, na_position="last").head(5).iterrows():
        lines.append(
            f"- **{row.get('name') or row.get('isin')}** — {row.get('catalyst_state')} — move {_fmt(row.get('_move'))} — biais {_fmt(row.get('_dir'), signed=True)} — {row.get('candidate_rank_reason', '')}"
        )
        events = str(row.get("news_event_types") or "").strip()
        if events and events.lower() != "nan":
            lines.append(f"  - News : {events}")

    up = work[work["_dir"] >= 25].sort_values("_dir", ascending=False).head(5)
    lines.extend(["", "## Top haussiers", ""])
    if up.empty:
        lines.append("- Aucun biais haussier qualifié.")
    else:
        for _, row in up.iterrows():
            lines.append(f"- {row.get('name') or row.get('isin')} — biais {_fmt(row.get('_dir'), signed=True)} — move {_fmt(row.get('_move'))}")

    conflict = work[work.get("news_technical_conflict", False).astype(bool) if "news_technical_conflict" in work.columns else pd.Series(False, index=work.index)]
    lines.extend(["", "## Conflits / vigilance", ""])
    if conflict.empty:
        lines.append("- Aucun conflit news/tech qualifié.")
    else:
        for _, row in conflict.sort_values("_move", ascending=False).head(5).iterrows():
            lines.append(f"- {row.get('name') or row.get('isin')} — NEWS_CONFLICT — move {_fmt(row.get('_move'))} — biais {_fmt(row.get('_dir'), signed=True)}")

    exits = work[work.get("exit_state", pd.Series(index=work.index, dtype=object)).astype(str) == "EXIT_RISK_HIGH_SHADOW"].sort_values("_exit", ascending=False).head(5)
    lines.extend(["", "## EXIT_RISK_HIGH du seed", ""])
    if exits.empty:
        lines.append("- Aucun.")
    else:
        for _, row in exits.iterrows():
            lines.append(f"- {row.get('name') or row.get('isin')} — risque sortie {_fmt(row.get('_exit'))}")

    degraded = int((work.get("data_quality_state", pd.Series(index=work.index, dtype=object)).astype(str) != "COMPLETE_ENOUGH").sum())
    lines.extend(["", "## Qualité", "", f"- Lignes dégradées : {degraded}/{len(work)}", f"- Circuit-breaker news : {metrics.get('circuit_breaker_triggered', False)}"])
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT, *, phase: str | None = None, now=None) -> dict:
    def fetch_news(candidates, *, start_utc, end_utc, phase, cfg, budget_seconds=None):
        return news_source.fetch_candidate_news(
            candidates,
            start_utc=start_utc,
            end_utc=end_utc,
            phase=phase,
            cfg=cfg,
            budget_seconds=budget_seconds,
            root=root,
        )

    return run_engine(
        root=root,
        config_filename=CONFIG,
        version=VERSION,
        catalyst_window_fn=feature.catalyst_window,
        infer_phase_fn=feature.infer_phase,
        select_candidates_fn=feature.select_catalyst_candidates,
        score_candidate_fn=feature.score_candidate,
        fetch_news_fn=fetch_news,
        phase=phase,
        now=now,
        output_filename="TCT_NEXT_SESSION_CATALYST_V24_4_2.csv",
        audit_filename="TCT_NEXT_SESSION_CATALYST_V24_4_2_AUDIT.json",
        android_filename="ANDROID_TCT_NEXT_SESSION_CATALYST_V24_4_2.md",
        android_summary_fn=_mobile_summary,
    )


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
