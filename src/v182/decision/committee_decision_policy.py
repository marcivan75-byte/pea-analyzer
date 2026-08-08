from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def _num(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def classify_action(row: pd.Series) -> tuple[str, str, str]:
    """Return (decision, execution, reason).

    V20.4 GitOK enables committee recommendations only. It never authorizes
    brokerage/order execution. Existing BLOCKED rows remain blocked.
    """
    status = str(row.get("comite_status") or "").strip().upper()
    gate = str(row.get("committee_analyst_gate") or "NEUTRAL").strip().upper()
    score = _num(row.get("committee_score_with_analyst_momentum"))
    if score is None:
        score = _num(row.get("score_brut"))
    upside = _num(row.get("target_upside_pct"))
    consensus = _num(row.get("consensus_score_100"))

    if status == "BLOCKED":
        return "NONE", "BLOCKED", "BASE_COMMITTEE_BLOCK"

    if gate in {"BLOCK_NEW_BUY_REVIEW", "PENALIZE_STRONG"}:
        return "REVIEW", "RESEARCH_ONLY", f"ANALYST_GATE_{gate}"

    # A positive recommendation requires three independent pillars:
    # strong committee score, material target upside and supportive consensus.
    if (
        score is not None and score >= 74.0
        and upside is not None and upside >= 15.0
        and consensus is not None and consensus >= 65.0
        and gate not in {"PENALIZE", "BLOCK_NEW_BUY_REVIEW", "PENALIZE_STRONG"}
    ):
        return "BUY_CANDIDATE", "RECOMMENDATION_ONLY", "SCORE_UPSIDE_CONSENSUS_CONFIRMED"

    if (
        score is not None and score >= 72.0
        and ((upside is not None and upside >= 10.0) or (consensus is not None and consensus >= 65.0))
        and gate not in {"PENALIZE_STRONG", "BLOCK_NEW_BUY_REVIEW"}
    ):
        return "WATCH", "RECOMMENDATION_ONLY", "PARTIAL_COMMITTEE_CONFIRMATION"

    return "REVIEW", "RESEARCH_ONLY", "INSUFFICIENT_CONFIRMATION"


def apply_committee_policy(root: Path | None = None) -> dict:
    from v182.io.frames import load_master, save_master

    root = root or ROOT
    outputs = root / "outputs"
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    committee_path = outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv"
    decisions_path = outputs / "V20.4_GITOK_COMMITTEE_DECISIONS.csv"
    summary_path = outputs / "V20.4_GITOK_COMMITTEE_SUMMARY.md"

    actions = load_master(actions_path).astype(object)
    classified = actions.apply(classify_action, axis=1, result_type="expand")
    classified.columns = ["decision", "execution", "decision_reason"]
    for column in classified.columns:
        actions[column] = classified[column].values
    save_master(actions, actions_path)

    decision_cols = [
        c for c in [
            "isin", "name", "yahoo_ticker", "comite_status", "score_brut",
            "committee_score_with_analyst_momentum", "analyst_momentum_score",
            "committee_analyst_signal", "committee_analyst_gate",
            "target_price", "last_close", "target_upside_pct",
            "consensus_rating", "consensus_score_100", "n_analysts",
            "decision", "execution", "decision_reason",
        ] if c in actions.columns
    ]
    decisions = actions[decision_cols].copy()
    decisions.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")

    if committee_path.exists() and committee_path.stat().st_size > 0:
        committee = pd.read_csv(committee_path, sep=";", encoding="utf-8-sig", dtype=str)
        policy_cols = actions[["isin", "decision", "execution", "decision_reason"]].drop_duplicates("isin")
        committee = committee.drop(columns=["decision", "execution", "decision_reason"], errors="ignore").merge(
            policy_cols, on="isin", how="left"
        )
        committee.to_csv(committee_path, sep=";", index=False, encoding="utf-8-sig")

    counts = decisions["decision"].value_counts(dropna=False).to_dict()
    buy = decisions[decisions["decision"] == "BUY_CANDIDATE"].copy()
    if "committee_score_with_analyst_momentum" in buy.columns:
        buy["_rank"] = pd.to_numeric(buy["committee_score_with_analyst_momentum"], errors="coerce")
        buy = buy.sort_values("_rank", ascending=False).drop(columns=["_rank"])

    lines = [
        "# V20.4 GitOK — Comité d’investissement",
        "",
        "Mode: recommandations décisionnelles activées; exécution réelle d’ordres interdite.",
        "",
        f"- BUY_CANDIDATE: {counts.get('BUY_CANDIDATE', 0)}",
        f"- WATCH: {counts.get('WATCH', 0)}",
        f"- REVIEW: {counts.get('REVIEW', 0)}",
        f"- NONE/BLOCKED: {counts.get('NONE', 0)}",
        "",
        "## Priorité BUY_CANDIDATE",
    ]
    for _, row in buy.head(20).iterrows():
        lines.append(
            f"- {row.get('name', '')} — score {row.get('committee_score_with_analyst_momentum', row.get('score_brut', ''))}"
            f" — potentiel {row.get('target_upside_pct', '')}% — consensus {row.get('consensus_rating', '')}"
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "rows": len(actions),
        "buy_candidate": int(counts.get("BUY_CANDIDATE", 0)),
        "watch": int(counts.get("WATCH", 0)),
        "review": int(counts.get("REVIEW", 0)),
        "blocked": int(counts.get("NONE", 0)),
    }


def main() -> None:
    metrics = apply_committee_policy()
    print("V20_4_GITOK_COMMITTEE_POLICY", metrics)


if __name__ == "__main__":
    main()
