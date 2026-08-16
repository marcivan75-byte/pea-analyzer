from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fmt(value, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def build_markdown(root: Path = ROOT) -> str:
    audit = _read_json(root / "outputs" / "audit" / "BETA_CORRELATION_RISK_ENGINE.json")
    portfolio = _read_json(root / "outputs" / "risk" / "PORTFOLIO_RISK_SUMMARY.json")
    validation = _read_json(root / "config" / "BETA_RISK_ROBUST_VALIDATION_STATUS.json")
    benchmark = audit.get("benchmark", {}) if isinstance(audit.get("benchmark"), dict) else {}
    policy = validation.get("production_policy", {}) if isinstance(validation.get("production_policy"), dict) else {}
    lines = [
        "# RISK V1.1 — CONTROL CENTER",
        "",
        f"**Statut moteur:** {audit.get('status', 'NOT_RUN')}  ",
        f"**Benchmark:** {benchmark.get('label', 'N/A')} — {benchmark.get('status', 'N/A')}  ",
        f"**Couverture bêta 252j:** {_fmt(audit.get('coverage_pct'))}%  ",
        "",
        "## Portefeuille",
        f"- Bêta 252j: {_fmt(portfolio.get('portfolio_beta_252d'))}",
        f"- Downside bêta 252j: {_fmt(portfolio.get('portfolio_downside_beta_252d'))}",
        f"- Corrélation moyenne: {_fmt(portfolio.get('mean_pair_correlation_252d'))}",
        f"- Corrélation stress: {_fmt(portfolio.get('mean_stress_pair_correlation'))}",
        f"- Diversification: **{portfolio.get('diversification_warning', 'N/A')}**",
        f"- Moteur dominant: {portfolio.get('top_engine', 'N/A')} ({_fmt(portfolio.get('top_engine_share_pct'))}%)",
        "",
        "## Stress systématique",
    ]
    stress = portfolio.get("systematic_stress_scenarios_pct", {})
    if isinstance(stress, dict) and stress:
        for scenario, estimate in stress.items():
            lines.append(f"- Marché {scenario}% → composante systématique estimée {_fmt(estimate)}%")
    else:
        lines.append("- N/A")
    lines.extend(
        [
            "",
            "## Gouvernance",
            f"- Validation économique: {validation.get('status', 'NOT_RUN')}",
            "- Sizing bêta permanent: **REJETÉ**",
            "- Régime V1.1: **REJETÉ**",
            "- Régime V1.2: **REJETÉ**",
            f"- Influence score: {policy.get('score_influence', 0.0)}",
            f"- Influence décision: {policy.get('decision_influence', 0.0)}",
            f"- Influence sizing: {policy.get('sizing_execution_influence', 0.0)}",
            f"- Influence stop: {policy.get('stop_loss_influence', 0.0)}",
            "- Usage autorisé: observabilité, stress, concentration/diversification et warnings uniquement.",
            "- Les scénarios bêta représentent une composante systématique, pas une prévision de perte totale.",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path = ROOT) -> dict:
    outdir = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "RISK_V1_1_CONTROL_CENTER.md"
    text = build_markdown(root)
    path.write_text(text, encoding="utf-8")
    return {"status": "SUCCESS", "output": str(path.relative_to(root))}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
