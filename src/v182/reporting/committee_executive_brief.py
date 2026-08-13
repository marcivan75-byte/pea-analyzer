from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import json

import pandas as pd

VERSION = "COMMITTEE_EXECUTIVE_BRIEF_V1"
ROLE = "REPORTING_ONLY"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _fmt(value, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(number):
        return "-"
    return f"{number:.{digits}f}"


def _cell(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    text = str(value).strip()
    return escape(text) if text else "-"


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>" for row in rows)
    if not body:
        body = f'<tr><td colspan="{len(headers)}" class="muted">Aucune donnée disponible.</td></tr>'
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _action_sections(actions: pd.DataFrame) -> str:
    if actions.empty or "horizon" not in actions.columns:
        return '<p class="muted">Vue Actions indisponible.</p>'
    blocks = []
    preferred = ["CT", "MT", "LT", "TOP_DOWN", "SHORT"]
    for horizon in preferred:
        group = actions[actions["horizon"].astype(str).eq(horizon)].head(5)
        if group.empty:
            continue
        rows = []
        for _, row in group.iterrows():
            rows.append([
                row.get("committee_rank"), row.get("name"), row.get("sector"), _fmt(row.get("score"), 1),
                _fmt(row.get("coverage_pct"), 0), row.get("decision"),
            ])
        blocks.append(f"<h3>Actions {escape(horizon)}</h3>" + _table(
            ["Rang", "Titre", "Secteur", "Score", "Couv.%", "Décision"], rows
        ))
    return "".join(blocks) or '<p class="muted">Aucune priorité Action disponible.</p>'


def _etf_section(etfs: pd.DataFrame) -> str:
    if etfs.empty:
        return '<p class="muted">Vue ETF indisponible.</p>'
    rows = []
    for _, row in etfs.head(10).iterrows():
        rows.append([
            row.get("rank_mt"), row.get("name"), _fmt(row.get("score_mt"), 1), row.get("decision_mt"),
            _fmt(row.get("score_ct"), 1), row.get("decision_ct"), row.get("selected_horizon_count"),
        ])
    return _table(["Rang MT", "ETF", "Score MT", "Décision MT", "Score CT", "Décision CT", "Horizons +"], rows)


def _tct_section(tct: pd.DataFrame) -> str:
    if tct.empty:
        return '<p class="muted">Vue TCT indisponible.</p>'
    rows = []
    for _, row in tct.head(10).iterrows():
        earnings = row.get("earnings_bucket") or "-"
        rows.append([
            row.get("tct_baseline_rank"), row.get("name"), row.get("sector"), _fmt(row.get("tct_baseline_score"), 1),
            row.get("timing_decision"), earnings, row.get("event_risk_level"),
        ])
    return _table(["Rang", "Titre", "Secteur", "Baseline", "Timing shadow", "Earnings", "Risque event"], rows)


def _sector_section(sectors: pd.DataFrame) -> str:
    if sectors.empty:
        return '<p class="muted">Dashboard sectoriel indisponible.</p>'
    work = sectors.copy()
    if "baseline_top20_count" in work.columns:
        work["_top"] = pd.to_numeric(work["baseline_top20_count"], errors="coerce")
    else:
        work["_top"] = 0
    if "mean_baseline_score" in work.columns:
        work["_score"] = pd.to_numeric(work["mean_baseline_score"], errors="coerce")
    else:
        work["_score"] = 0
    work = work.sort_values(["_top", "_score"], ascending=[False, False], na_position="last").head(8)
    rows = []
    for _, row in work.iterrows():
        rows.append([
            row.get("sector"), row.get("action_count"), row.get("baseline_top20_count"),
            row.get("t1_shadow_count"), row.get("t2_shadow_count"), row.get("earnings_d0_1_count"),
            row.get("earnings_d2_5_count"), row.get("top_3_baseline"),
        ])
    return _table(["Secteur", "Nb", "Top20", "T1 sh.", "T2 sh.", "Earn D0-1", "Earn D2-5", "Top 3 baseline"], rows)


def _quality_section(quality: pd.DataFrame) -> str:
    if quality.empty:
        return '<p class="muted">Qualité des données indisponible.</p>'
    rows = []
    for _, row in quality.iterrows():
        rows.append([
            row.get("scope"), row.get("rows"), _fmt(row.get("mean_coverage_pct"), 1),
            _fmt(row.get("min_coverage_pct"), 1), row.get("blocked_rows"), row.get("data_gap_rows"),
        ])
    return _table(["Périmètre", "Lignes", "Couv. moy.%", "Couv. min.%", "Bloquées", "Gaps"], rows)


def build_html(outdir: Path) -> str:
    actions = _read_csv(outdir / "ACTION_COMMITTEE_PRIORITY_BY_HORIZON.csv")
    etfs = _read_csv(outdir / "ETF_COMMITTEE_TOP30.csv")
    tct = _read_csv(outdir / "TCT_COMMITTEE_TOP50.csv")
    sectors = _read_csv(outdir / "TCT_SECTOR_DASHBOARD.csv")
    quality = _read_csv(outdir / "COMMITTEE_DATA_QUALITY.csv")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Comité d'investissement - Brief exécutif</title>
<style>
:root{{--ink:#152238;--muted:#667085;--line:#d8dee9;--panel:#f7f9fc;--accent:#17365d;--warn:#fff4e5}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,Helvetica,sans-serif;color:var(--ink);background:#fff}}
main{{max-width:1180px;margin:auto;padding:18px}}h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:18px;margin:24px 0 8px;border-bottom:2px solid var(--accent);padding-bottom:5px}}
h3{{font-size:15px;margin:15px 0 6px}}.meta,.muted{{color:var(--muted);font-size:12px}}.governance{{background:var(--warn);border:1px solid #f0c36d;border-radius:8px;padding:10px 12px;margin:12px 0;font-size:13px;line-height:1.4}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:7px;margin-bottom:10px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th{{background:var(--accent);color:white;text-align:left;padding:7px;white-space:nowrap}}td{{padding:6px 7px;border-top:1px solid var(--line);vertical-align:top}}tr:nth-child(even) td{{background:var(--panel)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}@media(max-width:820px){{main{{padding:10px}}h1{{font-size:20px}}.grid{{grid-template-columns:1fr}}table{{font-size:11px}}th,td{{padding:5px}}}}
@media print{{main{{max-width:none;padding:8mm}}.table-wrap{{overflow:visible}}h2{{break-after:avoid}}}}
</style></head><body><main>
<h1>Comité d’investissement - Brief exécutif</h1><div class="meta">Généré {generated} - {VERSION} - {ROLE}</div>
<div class="governance"><strong>Gouvernance :</strong> ce document est une restitution. Il ne modifie aucun score, poids, seuil ou décision. T1/T2 restent des signaux de timing ACTION TCT en shadow avec influence score = 0. Aucun ordre live, aucune probabilité/espérance fixe et aucune instruction d’achat urgente n’est généré.</div>
<h2>Priorités Actions par horizon</h2>{_action_sections(actions)}
<div class="grid"><section><h2>ETF - Top 10 de la vue compacte</h2>{_etf_section(etfs)}</section><section><h2>TCT - Top 10 baseline</h2>{_tct_section(tct)}</section></div>
<h2>Lecture sectorielle TCT</h2>{_sector_section(sectors)}
<h2>Qualité / couverture des données</h2>{_quality_section(quality)}
</main></body></html>"""


def run(root: Path) -> dict:
    outdir = root / "outputs" / "committee_master"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "COMMITTEE_EXECUTIVE_BRIEF.html"
    html = build_html(outdir)
    path.write_text(html, encoding="utf-8")
    summary = {
        "status":"SUCCESS", "version":VERSION, "role":ROLE, "html":str(path),
        "score_changes":False, "new_composite_opportunity_score":False,
        "fixed_probability_or_expectancy_added":False, "t1_t2_score_influence":0.0,
        "live_orders_enabled":False,
    }
    (outdir / "COMMITTEE_EXECUTIVE_BRIEF_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
