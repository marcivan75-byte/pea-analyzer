from __future__ import annotations

from pathlib import Path

import pandas as pd

OPPORTUNITY_ACTIONS = {
    "revenue_growth": ("FINANCIALS", "Vérifier croissance du CA historique, qualité et récurrence de la croissance"),
    "gross_margin_quality": ("FINANCIALS", "Mesurer marge brute, stabilité et comparaison aux pairs"),
    "operating_leverage": ("FINANCIALS", "Analyser évolution marge opérationnelle, coûts fixes et point mort"),
    "market_growth": ("SECTOR", "Documenter TAM/SAM, croissance sectorielle et phase du cycle"),
    "competitive_moat": ("BUSINESS_MODEL", "Évaluer moat, différenciation, coûts de changement et barrières à l'entrée"),
    "balance_sheet_post_ipo": ("BALANCE_SHEET", "Reconstituer bilan pro forma après IPO et liquidité disponible"),
    "use_of_proceeds_quality": ("PROSPECTUS", "Qualifier précisément l'utilisation des fonds levés"),
    "valuation_vs_peers": ("VALUATION", "Calculer valorisation IPO vs pairs sur EV/Sales, EV/EBITDA, P/E pertinents"),
    "bookbuilding_demand": ("OFFER_TERMS", "Contrôler demande bookbuilding, révisions de fourchette et couverture du livre"),
    "insider_alignment": ("GOVERNANCE", "Mesurer rétention des dirigeants, ventes secondaires et lock-up"),
    "underwriter_quality": ("OFFER_TERMS", "Identifier chefs de file, réputation et historique d'exécution IPO"),
    "float_liquidity": ("OFFER_TERMS", "Calculer free float, montant offert et liquidité attendue"),
}

RISK_ACTIONS = {
    "loss_cash_burn": ("FINANCIALS", "Mesurer pertes, cash burn et runway après opération"),
    "valuation": ("VALUATION", "Mesurer risque de survalorisation vs pairs et scénarios de compression de multiples"),
    "dilution_secondary": ("OFFER_TERMS", "Quantifier dilution primaire, ventes secondaires et overhang"),
    "governance_dual_class": ("GOVERNANCE", "Vérifier dual-class, droits de vote et contrôle des fondateurs"),
    "lockup_overhang": ("OFFER_TERMS", "Mesurer calendrier d'expiration lock-up et volume potentiel libérable"),
    "customer_concentration": ("BUSINESS_MODEL", "Quantifier concentration clients et dépendances commerciales"),
    "regulatory_legal": ("LEGAL", "Auditer litiges, enquêtes, licences et risques réglementaires"),
    "execution_model": ("BUSINESS_MODEL", "Tester scalabilité, dépendances opérationnelles et risque d'exécution"),
    "cyclicality_macro": ("SECTOR", "Évaluer cyclicité, sensibilité taux/FX/matières premières et régime macro"),
    "accounting_controls": ("ACCOUNTING", "Contrôler audit, faiblesses de contrôle interne et qualité comptable"),
    "small_float_liquidity": ("OFFER_TERMS", "Tester risque de faible float, volatilité et capacité d'exécution"),
    "deal_instability": ("OFFER_TERMS", "Contrôler reports, retraits, révisions de prix et stabilité du calendrier"),
}

CATEGORY_PRIORITY = {
    "VALUATION": 1,
    "FINANCIALS": 2,
    "PROSPECTUS": 3,
    "OFFER_TERMS": 4,
    "GOVERNANCE": 5,
    "LEGAL": 6,
    "BUSINESS_MODEL": 7,
    "SECTOR": 8,
    "BALANCE_SHEET": 9,
    "ACCOUNTING": 10,
}


def _missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    text = str(value).strip().lower()
    return text in {"", "nan", "none", "na", "n/a"}


def _candidate_tasks(row: dict, opportunity_weights: dict[str, float], risk_weights: dict[str, float]) -> list[dict]:
    tasks: list[dict] = []
    for criterion, weight in opportunity_weights.items():
        field = f"opportunity_{criterion}"
        if _missing(row.get(field)):
            category, action = OPPORTUNITY_ACTIONS[criterion]
            tasks.append({
                "dimension": "OPPORTUNITY",
                "criterion": criterion,
                "field": field,
                "weight_pct": float(weight),
                "category": category,
                "action": action,
            })
    for criterion, weight in risk_weights.items():
        field = f"risk_{criterion}"
        if _missing(row.get(field)):
            category, action = RISK_ACTIONS[criterion]
            tasks.append({
                "dimension": "RISK",
                "criterion": criterion,
                "field": field,
                "weight_pct": float(weight),
                "category": category,
                "action": action,
            })
    tasks.sort(key=lambda item: (-item["weight_pct"], CATEGORY_PRIORITY.get(item["category"], 99), item["criterion"]))
    return tasks


def build_gap_worklist(ranking: pd.DataFrame, config: dict) -> pd.DataFrame:
    columns = [
        "candidate_id", "identity_key", "name", "symbol", "exchange", "expected_date", "decision",
        "net_ipo_score", "opportunity_score", "risk_score", "opportunity_coverage_pct", "risk_coverage_pct",
        "missing_criteria_count", "missing_weight_total_pct", "priority_1_category", "priority_1_criterion",
        "priority_1_weight_pct", "priority_1_action", "priority_2_category", "priority_2_criterion",
        "priority_2_weight_pct", "priority_2_action", "priority_3_category", "priority_3_criterion",
        "priority_3_weight_pct", "priority_3_action", "all_missing_criteria", "all_required_actions",
        "dd_status", "live_order_allowed",
    ]
    if ranking.empty:
        return pd.DataFrame(columns=columns)
    records: list[dict] = []
    opportunity_weights = config["opportunity_weights"]
    risk_weights = config["risk_weights"]
    for _, series in ranking.iterrows():
        row = series.to_dict()
        tasks = _candidate_tasks(row, opportunity_weights, risk_weights)
        missing_weight = sum(task["weight_pct"] for task in tasks)
        record = {
            "candidate_id": row.get("candidate_id"),
            "identity_key": row.get("identity_key"),
            "name": row.get("name"),
            "symbol": row.get("symbol"),
            "exchange": row.get("exchange"),
            "expected_date": row.get("expected_date"),
            "decision": row.get("decision"),
            "net_ipo_score": row.get("net_ipo_score"),
            "opportunity_score": row.get("opportunity_score"),
            "risk_score": row.get("risk_score"),
            "opportunity_coverage_pct": row.get("opportunity_coverage_pct"),
            "risk_coverage_pct": row.get("risk_coverage_pct"),
            "missing_criteria_count": len(tasks),
            "missing_weight_total_pct": round(missing_weight, 2),
            "all_missing_criteria": "|".join(f"{task['dimension']}:{task['criterion']}" for task in tasks),
            "all_required_actions": "|".join(dict.fromkeys(task["action"] for task in tasks)),
            "dd_status": "COMPLETE" if not tasks else "ACTION_REQUIRED",
            "live_order_allowed": False,
        }
        for index in range(3):
            task = tasks[index] if index < len(tasks) else None
            prefix = f"priority_{index + 1}"
            record[f"{prefix}_category"] = task["category"] if task else ""
            record[f"{prefix}_criterion"] = task["criterion"] if task else ""
            record[f"{prefix}_weight_pct"] = task["weight_pct"] if task else None
            record[f"{prefix}_action"] = task["action"] if task else ""
        records.append(record)
    frame = pd.DataFrame(records).reindex(columns=columns)
    decision_order = {"PRIORITY_DD": 0, "DEEP_DD": 1, "WATCH": 2, "WATCH_EARLY_FILING": 3, "WATCH_DATA_GAP": 4}
    frame["_decision_rank"] = frame["decision"].map(decision_order).fillna(9)
    frame = frame.sort_values(
        ["_decision_rank", "missing_weight_total_pct", "expected_date"],
        ascending=[True, False, True],
        na_position="last",
    ).drop(columns=["_decision_rank"])
    return frame.reset_index(drop=True)


def write_gap_worklist(root: Path, config: dict) -> dict:
    ranking_path = root / "outputs" / "ipo_radar" / "IPO_RANKING.csv"
    output_path = root / "outputs" / "ipo_radar" / "IPO_DD_GAPS.csv"
    ranking = pd.read_csv(ranking_path, low_memory=False) if ranking_path.exists() else pd.DataFrame()
    frame = build_gap_worklist(ranking, config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    actionable = frame[frame["dd_status"] == "ACTION_REQUIRED"] if not frame.empty else frame
    return {
        "output": "outputs/ipo_radar/IPO_DD_GAPS.csv",
        "candidate_count": int(len(frame)),
        "action_required_count": int(len(actionable)),
        "complete_count": int((frame["dd_status"] == "COMPLETE").sum()) if not frame.empty else 0,
    }
