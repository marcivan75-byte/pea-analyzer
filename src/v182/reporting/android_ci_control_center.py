from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    for sep in (";", ",", "\t"):
        try:
            df = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    return pd.DataFrame()


def _status_token(value: str | None) -> str:
    text = str(value or "UNKNOWN").upper()
    if text in {"SUCCESS", "PASS", "SCORABLE", "ACTIVE_REFERENCE_SCORING", "ACTIVE_SHADOW_CONFIRMATION"}:
        return "[OK]"
    if text.startswith("SKIPPED") or text in {"PARTIAL_SUCCESS", "WATCH", "REVIEW", "UNKNOWN", "MISSING", "NOT_RUN"}:
        return "[WARN]"
    if text in {"FAILED", "FAIL", "BLOCKED", "BLOCK_DATA", "BLOCKED_CONFIG"}:
        return "[FAIL]"
    return "[INFO]"


def _decision_counts(decisions: pd.DataFrame, asset_class: str, horizon: str) -> str:
    if decisions.empty or not {"asset_class", "horizon", "decision"}.issubset(decisions.columns):
        return "N/A"
    sub = decisions[(decisions["asset_class"].astype(str) == asset_class) & (decisions["horizon"].astype(str) == horizon)]
    if sub.empty:
        return "N/A"
    counts = sub["decision"].astype(str).value_counts()
    preferred = ["BUY_CANDIDATE", "WATCH", "REVIEW", "REJECT", "SHORT_RISK_CANDIDATE", "WATCH_SHORT_RISK", "NO_SHORT_RISK", "FAVORABLE", "NEUTRAL", "DEFAVORABLE"]
    parts = [f"{name}={int(counts[name])}" for name in preferred if name in counts]
    if not parts:
        parts = [f"{name}={int(count)}" for name, count in counts.head(4).items()]
    return " | ".join(parts)


def _top_sectors(frame: pd.DataFrame, limit: int = 5) -> list[str]:
    if frame.empty or "sector_rotation_score" not in frame.columns:
        return []
    work = frame.copy()
    work["sector_rotation_score"] = pd.to_numeric(work["sector_rotation_score"], errors="coerce")
    work = work.dropna(subset=["sector_rotation_score"]).sort_values("sector_rotation_score", ascending=False)
    rows = []
    for _, row in work.head(limit).iterrows():
        sector = str(row.get("sector", "NON_CLASSE"))
        score = float(row["sector_rotation_score"])
        gate = row.get("recovery_gate")
        gate_text = "recovery=YES" if str(gate).lower() in {"true", "1", "yes"} else "recovery=NO"
        rows.append(f"{sector}: {score:.1f}/100 ({gate_text})")
    return rows


def _top_long_candidates(decisions: pd.DataFrame, limit: int = 5) -> list[str]:
    if decisions.empty or not {"asset_class", "horizon", "decision", "score"}.issubset(decisions.columns):
        return []
    mask = (
        decisions["asset_class"].astype(str).isin(["ACTION", "ETF"])
        & decisions["horizon"].astype(str).isin(["CT", "MT", "LT"])
        & decisions["decision"].astype(str).isin(["BUY_CANDIDATE", "WATCH"])
    )
    work = decisions.loc[mask].copy()
    if work.empty:
        return []
    work["score"] = pd.to_numeric(work["score"], errors="coerce")
    work = work.sort_values(["decision", "score"], ascending=[True, False])
    rows = []
    for _, row in work.head(limit).iterrows():
        label = str(row.get("name") or row.get("isin") or "N/A")
        rows.append(f"{label} | {row.get('asset_class')} {row.get('horizon')} | {row.get('decision')} | score={row.get('score')}")
    return rows


def build_markdown(root: Path = ROOT) -> str:
    unified = _read_json(root / "outputs" / "unified" / "UNIFIED_SUMMARY_LATEST.json")
    committee = _read_json(root / "outputs" / "committee_master" / "SUMMARY.json")
    decisions = _read_csv(root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv")
    sectors = _read_csv(root / "outputs" / "V21_3_SECTOR_ROTATION.csv")
    governance = _read_json(root / "outputs" / "audit" / "CRITERIA_STUDY_GOVERNANCE.json")
    static_audit = _read_json(root / "outputs" / "audit" / "PYTHON_STATIC_AUDIT.json")
    etf_mt = _read_json(root / "outputs" / "etf_mt_v2081" / "V20.8.1_ETF_MT_SUMMARY.json")
    gold = _read_json(root / "outputs" / "gold_v1_1" / "GOLD_V1_1_DECISION.json")

    generated = unified.get("generated_at_utc") or datetime.now(timezone.utc).isoformat()
    run_status = unified.get("status", "UNKNOWN")
    steps = unified.get("steps", {}) if isinstance(unified.get("steps"), dict) else {}

    lines = [
        "# PEA ANALYZER — CONTROL CENTER ANDROID",
        "",
        f"**Run:** {_status_token(run_status)} {run_status}  ",
        f"**Généré:** {generated}  ",
        "**Mode:** SHADOW / AUCUN ORDRE RÉEL",
        "",
        "## COMITÉ D’INVESTISSEMENT",
        f"Actions CT — {_decision_counts(decisions, 'ACTION', 'CT')}",
        f"Actions MT — {_decision_counts(decisions, 'ACTION', 'MT')}",
        f"Actions LT — {_decision_counts(decisions, 'ACTION', 'LT')}",
        f"ETF MT — {_decision_counts(decisions, 'ETF', 'MT')}",
        "",
    ]

    candidates = _top_long_candidates(decisions)
    lines.append("### Priorités BUY / WATCH")
    lines.extend([f"- {item}" for item in candidates] or ["- Aucune donnée disponible"])

    lines.extend(["", "## PLUS HAUT 52 SEMAINES — BONUS / MALUS"])
    overlay = committee.get("action_52w_rotation_overlay", {}) if isinstance(committee.get("action_52w_rotation_overlay"), dict) else {}
    lines.append(f"- Bonus positifs observés: {overlay.get('positive_52w_bonus_rows', 'N/A')}")
    lines.append(f"- Malus proximité du plus haut: {overlay.get('near_high_malus_rows', 'N/A')}")
    lines.append("- Règle: ≤2% du plus haut = -4 pts; >2% à 5% = -2 pts; reprise confirmée et distance ≥8% = +1; ≥15% = +2,5; ≥25% = +4.")
    lines.append("- Gouvernance: bonus positif = challenger uniquement, ne peut pas créer un BUY; malus peut dégrader le challenger.")

    lines.extend(["", "## ROTATION SECTORIELLE"])
    top_sectors = _top_sectors(sectors)
    lines.extend([f"- {item}" for item in top_sectors] or ["- Aucune donnée sectorielle disponible"])
    lines.append("- Le score combine écart au plus haut 52 semaines, momentum 1 mois, accélération, breadth MM50/MM200 et inflexion relative.")
    lines.append("- Gouvernance V21.7: signal conservé en overlay/shadow à poids nul avant validation PIT/OOS.")

    lines.extend(["", "## RUN COMPLET / MODULES"])
    ordered_steps = ["criteria_governance", "refresh", "cdc", "etf_structure", "etf_mt", "gold", "committee", "performance"]
    for name in ordered_steps:
        spec = steps.get(name, {}) if isinstance(steps.get(name), dict) else {}
        status = spec.get("status", "NOT_RUN")
        lines.append(f"- {name}: {_status_token(status)} {status}")

    lines.extend(["", "## ETF MT"])
    if etf_mt:
        lines.append(f"- Version: {etf_mt.get('version', 'V20.8.1')}")
        lines.append(f"- Scorables: {etf_mt.get('scorable_etfs', 'N/A')}")
        selected = etf_mt.get("selected", [])
        lines.append(f"- Sélectionnés: {len(selected) if isinstance(selected, list) else selected}")
        lines.append("- Attribution 90,91%: sous-bloc dynamique PIT historique de 38 critères uniquement; référentiel ETF complet = 268.")
    else:
        lines.append("- Résumé ETF MT indisponible")

    lines.extend(["", "## TCT"])
    tct = committee.get("tct_exact_timing", {}) if isinstance(committee.get("tct_exact_timing"), dict) else {}
    lines.append(f"- Statut: {_status_token(tct.get('status'))} {tct.get('status', 'N/A')}")
    lines.append(f"- T1 détectés: {tct.get('t1_detected_raw', 'N/A')} | T2 confirmés: {tct.get('t2_confirmed', 'N/A')}")
    lines.append("- T1/T2: ACTION TCT uniquement.")

    lines.extend(["", "## OR"])
    if gold:
        lines.append(f"- Décision: {gold.get('decision', gold.get('status', 'N/A'))}")
        lines.append(f"- Score: {gold.get('score', gold.get('score_100', 'N/A'))}")
    else:
        lines.append("- Décision Or indisponible")

    lines.extend(["", "## DATA QUALITY"])
    lines.append(f"- Gouvernance critères: {_status_token(governance.get('status'))} {governance.get('status', 'N/A')}")
    lines.append(f"- Audit Python HIGH: {static_audit.get('high', 'N/A')} | MEDIUM: {static_audit.get('medium', 'N/A')}")
    canonical = committee.get("canonical_actions", {}) if isinstance(committee.get("canonical_actions"), dict) else {}
    lines.append(f"- Univers Actions canonique: {canonical.get('canonical_rows', 'N/A')} / 1829")

    lines.extend(["", "## BACKTEST / VALIDATION"])
    lines.append("- Actions V21.7: challenger, aucune attribution de performance avant PIT/OOS dédié.")
    lines.append("- ETF composite MT 43 = 38 dynamiques + 5 structurels: recherche uniquement jusqu’au backtest complet.")
    lines.append("- Holdouts et règles anti-look-ahead restent inchangés.")

    lines.extend(["", "## GITHUB STATUS / INCIDENTS"])
    failed = []
    for name, spec in steps.items():
        if isinstance(spec, dict) and str(spec.get("status", "")).upper() not in {"SUCCESS"}:
            failed.append(f"{name}: {spec.get('status')} {spec.get('error', '')} {spec.get('detail', '')}".strip())
    lines.extend([f"- {item}" for item in failed] or ["- Aucun incident de pipeline déclaré dans le résumé du run"])
    lines.append("- Le statut GitHub Actions lui-même reste visible en tête du run; ce panneau résume le contenu décisionnel du Comité pour lecture mobile.")

    lines.extend([
        "",
        "## FICHIERS CLÉS",
        "- Comité: `outputs/committee_master/COMMITTEE_DECISIONS.csv`",
        "- Rotation sectorielle: `outputs/V21_3_SECTOR_ROTATION.csv`",
        "- ETF MT: `outputs/etf_mt_v2081/`",
        "- Qualité: `outputs/audit/` et `outputs/data_audit/`",
        "- Control Center Android: `outputs/mobile/ANDROID_CI_CONTROL_CENTER.md`",
        "",
    ])
    return "\n".join(lines)


def run(root: Path = ROOT) -> dict:
    outdir = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "ANDROID_CI_CONTROL_CENTER.md"
    text = build_markdown(root)
    path.write_text(text, encoding="utf-8")
    return {"status": "SUCCESS", "output": str(path.relative_to(root)), "bytes": len(text.encode('utf-8'))}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False))
