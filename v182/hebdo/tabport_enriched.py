"""Publication enrichie des résultats TABPORT HEBDO AT META."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd


def _safe_div(a: float, b: float) -> float | None:
    return None if b == 0 or not np.isfinite(b) else float(a / b)


def _period_trade_metrics(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return {
            "trades": 0, "gains": 0, "pertes_faux_positifs": 0, "taux_gain_pct": 0.0,
            "gain_moyen_pct": None, "perte_moyenne_pct": None, "esperance_pct": None,
            "profit_factor": None, "rr_payoff": None, "stops": 0, "stop_rate_pct": 0.0,
            "mae_moyen_pct": None, "mae_pire_pct": None, "mfe_moyen_pct": None,
            "mfe_meilleur_pct": None, "pnl_net_eur": 0.0, "frais_eur": 0.0,
        }
    ret = pd.to_numeric(ledger["return_net"], errors="coerce")
    pnl = pd.to_numeric(ledger["pnl_net"], errors="coerce")
    wins = ret > 0
    losses = ret <= 0
    gross_profit = float(pnl[wins].sum())
    gross_loss = float(-pnl[losses].sum())
    avg_win = float(ret[wins].mean()) if wins.any() else None
    avg_loss = float(ret[losses].mean()) if losses.any() else None
    rr = None
    if avg_win is not None and avg_loss is not None and avg_loss < 0:
        rr = float(avg_win / abs(avg_loss))
    pf = _safe_div(gross_profit, gross_loss)
    stop_mask = ledger.get("exit_reason", pd.Series("", index=ledger.index)).astype(str).str.startswith("STOP")
    mae = pd.to_numeric(ledger.get("mae", pd.Series(np.nan, index=ledger.index)), errors="coerce")
    mfe = pd.to_numeric(ledger.get("mfe", pd.Series(np.nan, index=ledger.index)), errors="coerce")
    fees = pd.to_numeric(ledger.get("fees_total", pd.Series(0.0, index=ledger.index)), errors="coerce")
    return {
        "trades": int(len(ledger)),
        "gains": int(wins.sum()),
        "pertes_faux_positifs": int(losses.sum()),
        "taux_gain_pct": float(wins.mean() * 100),
        "gain_moyen_pct": None if avg_win is None else avg_win * 100,
        "perte_moyenne_pct": None if avg_loss is None else avg_loss * 100,
        "esperance_pct": float(ret.mean() * 100),
        "profit_factor": pf,
        "rr_payoff": rr,
        "stops": int(stop_mask.sum()),
        "stop_rate_pct": float(stop_mask.mean() * 100),
        "mae_moyen_pct": None if mae.dropna().empty else float(mae.mean() * 100),
        "mae_pire_pct": None if mae.dropna().empty else float(mae.min() * 100),
        "mfe_moyen_pct": None if mfe.dropna().empty else float(mfe.mean() * 100),
        "mfe_meilleur_pct": None if mfe.dropna().empty else float(mfe.max() * 100),
        "pnl_net_eur": float(pnl.sum()),
        "frais_eur": float(fees.sum()),
    }


def _nav_metrics(nav: pd.DataFrame) -> dict:
    if nav.empty:
        return {"rendement_portefeuille_pct": None, "drawdown_max_pct": None, "nav_fin_eur": None}
    work = nav.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce", utc=True)
    work["equity"] = pd.to_numeric(work["equity"], errors="coerce")
    work = work.dropna(subset=["date", "equity"]).sort_values("date")
    if work.empty:
        return {"rendement_portefeuille_pct": None, "drawdown_max_pct": None, "nav_fin_eur": None}
    start = float(work["equity"].iloc[0])
    end = float(work["equity"].iloc[-1])
    dd = work["equity"] / work["equity"].cummax() - 1
    return {
        "rendement_portefeuille_pct": float((end / start - 1) * 100) if start > 0 else None,
        "drawdown_max_pct": float(dd.min() * 100),
        "nav_fin_eur": end,
    }


def period_table(ledger: pd.DataFrame, nav: pd.DataFrame, freq: str) -> pd.DataFrame:
    if freq not in {"Q", "Y"}:
        raise ValueError("freq must be Q or Y")
    ld = ledger.copy()
    nv = nav.copy()
    if not ld.empty:
        ld["exit_date"] = pd.to_datetime(ld["exit_date"], errors="coerce", utc=True)
        ld = ld.dropna(subset=["exit_date"])
        ld["period"] = ld["exit_date"].dt.tz_localize(None).dt.to_period(freq).astype(str)
    if not nv.empty:
        nv["date"] = pd.to_datetime(nv["date"], errors="coerce", utc=True)
        nv = nv.dropna(subset=["date"])
        nv["period"] = nv["date"].dt.tz_localize(None).dt.to_period(freq).astype(str)
    periods = sorted(set(ld.get("period", [])) | set(nv.get("period", [])))
    rows = []
    for period in periods:
        row = {"periode": period}
        row.update(_period_trade_metrics(ld[ld["period"] == period] if not ld.empty else ld))
        row.update(_nav_metrics(nv[nv["period"] == period] if not nv.empty else nv))
        rows.append(row)
    return pd.DataFrame(rows)


def overall_summary(ledger: pd.DataFrame, nav: pd.DataFrame, initial_cash: float = 65000.0) -> dict:
    metrics = _period_trade_metrics(ledger)
    navm = _nav_metrics(nav)
    metrics.update(navm)
    if navm["nav_fin_eur"] is not None:
        metrics["rendement_total_depuis_65000_pct"] = float((navm["nav_fin_eur"] / initial_cash - 1) * 100)
    else:
        metrics["rendement_total_depuis_65000_pct"] = None
    if not ledger.empty and "sessions_held" in ledger:
        s = pd.to_numeric(ledger["sessions_held"], errors="coerce")
        metrics["duree_moyenne_sessions"] = None if s.dropna().empty else float(s.mean())
    return metrics


def _fmt(v, digits=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    return f"{v:.{digits}f}"


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    cols = list(df.columns)
    def cell(v):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "n/a"
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in df.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines)


def markdown_report(summary: dict, quarterly: pd.DataFrame, yearly: pd.DataFrame, manifest: dict) -> str:
    lines = [
        "# TABPORT enrichi — HEBDO AT META",
        "",
        "## Synthèse portefeuille 65 k€",
        "",
        f"- Trades clôturés : **{summary.get('trades', 0)}**",
        f"- Taux de gain : **{_fmt(summary.get('taux_gain_pct'))}%**",
        f"- Résultat net cumulé : **{_fmt(summary.get('pnl_net_eur'))} €**",
        f"- Rendement total sur 65 k€ : **{_fmt(summary.get('rendement_total_depuis_65000_pct'))}%**",
        f"- Profit Factor : **{_fmt(summary.get('profit_factor'))}**",
        f"- RR / payoff : **{_fmt(summary.get('rr_payoff'))}**",
        f"- Espérance moyenne par trade : **{_fmt(summary.get('esperance_pct'))}%**",
        f"- Drawdown maximal : **{_fmt(summary.get('drawdown_max_pct'))}%**",
        f"- Stops : **{summary.get('stops', 0)}** ({_fmt(summary.get('stop_rate_pct'))}%)",
        f"- MAE moyen / pire : **{_fmt(summary.get('mae_moyen_pct'))}% / {_fmt(summary.get('mae_pire_pct'))}%**",
        f"- MFE moyen / meilleur : **{_fmt(summary.get('mfe_moyen_pct'))}% / {_fmt(summary.get('mfe_meilleur_pct'))}%**",
        "",
        "## Résultats trimestriels",
        "",
    ]
    if quarterly.empty:
        lines.append("Aucun trimestre clôturé.")
    else:
        cols = ["periode","trades","gains","pertes_faux_positifs","taux_gain_pct","gain_moyen_pct","perte_moyenne_pct","esperance_pct","profit_factor","rr_payoff","stops","mae_moyen_pct","mfe_moyen_pct","pnl_net_eur","rendement_portefeuille_pct","drawdown_max_pct"]
        q = quarterly[[c for c in cols if c in quarterly.columns]].copy()
        lines.append(_markdown_table(q))
    lines += ["", "## Résultats annuels", ""]
    if yearly.empty:
        lines.append("Aucune année clôturée.")
    else:
        cols = ["periode","trades","gains","pertes_faux_positifs","taux_gain_pct","esperance_pct","profit_factor","rr_payoff","stops","pnl_net_eur","rendement_portefeuille_pct","drawdown_max_pct"]
        y = yearly[[c for c in cols if c in yearly.columns]].copy()
        lines.append(_markdown_table(y))
    lines += [
        "", "## Gouvernance et provenance", "",
        f"- Moteur : `{manifest.get('engine','TABPORT_HEBDO_AT_META')}`",
        f"- Fenêtre : `{manifest.get('window',{})}`",
        f"- Données synthétiques : **{manifest.get('synthetic_fallback', False)}**",
        f"- Retuning : **{manifest.get('retuning', False)}**",
        f"- Validation PIT : `{manifest.get('inputs',{}).get('signals',{}).get('pit_validation','')}`",
        "",
        "## Glossaire",
        "",
        "- **PF / Profit Factor** : somme des gains nets divisée par la somme absolue des pertes nettes. >1 signifie que les gains dépassent les pertes.",
        "- **RR / payoff** : gain moyen des trades gagnants divisé par la perte moyenne absolue des trades perdants.",
        "- **Espérance** : rendement net moyen par trade, gains et pertes inclus.",
        "- **MAE** : Maximum Adverse Excursion, pire excursion défavorable subie pendant le trade.",
        "- **MFE** : Maximum Favorable Excursion, meilleure excursion favorable atteinte pendant le trade.",
        "- **Drawdown** : baisse maximale de la valeur du portefeuille depuis un plus-haut antérieur.",
        "- **Faux positif** : signal ayant conduit à un trade clôturé sans gain net ; il est compté avec les pertes.",
        "",
        "## Fichiers du package",
        "",
        "`TABPORT_LEDGER_ENRICHI.csv`, `TABPORT_DAILY_NAV.csv`, `TABPORT_TRIMESTRIEL_ENRICHI.csv`, `TABPORT_ANNUEL_ENRICHI.csv`, `TABPORT_SKIPPED.csv`, `TABPORT_MANIFEST.json`, `TABPORT_ENRICHI_SUMMARY.json`.",
    ]
    return "\n".join(lines) + "\n"


def publish_enriched(result: dict, output_dir: str | Path, initial_cash: float = 65000.0) -> dict:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    ledger = result["ledger"].copy(); nav = result["equity"].copy()
    quarterly = period_table(ledger, nav, "Q")
    yearly = period_table(ledger, nav, "Y")
    summary = overall_summary(ledger, nav, initial_cash=initial_cash)
    manifest = result.get("manifest", {})
    if not ledger.empty:
        ledger["is_gain"] = pd.to_numeric(ledger["return_net"], errors="coerce") > 0
        ledger["is_faux_positif"] = ~ledger["is_gain"]
        ledger["stop_declenche"] = ledger.get("exit_reason", "").astype(str).str.startswith("STOP")
    ledger.to_csv(out / "TABPORT_LEDGER_ENRICHI.csv", index=False)
    nav.to_csv(out / "TABPORT_DAILY_NAV.csv", index=False)
    quarterly.to_csv(out / "TABPORT_TRIMESTRIEL_ENRICHI.csv", index=False)
    yearly.to_csv(out / "TABPORT_ANNUEL_ENRICHI.csv", index=False)
    result.get("skipped", pd.DataFrame()).to_csv(out / "TABPORT_SKIPPED.csv", index=False)
    (out / "TABPORT_MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (out / "TABPORT_ENRICHI_SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out / "TABPORT_ENRICHI.md").write_text(markdown_report(summary, quarterly, yearly, manifest), encoding="utf-8")
    return {"summary": summary, "quarterly": quarterly, "yearly": yearly}
