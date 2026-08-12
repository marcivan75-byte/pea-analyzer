from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import pandas as pd

from v182.audit.provenance import actual_sources_by_field
from v182.io.frames import is_missing

SOURCE_HINTS = [
    (("morningstar",), "Morningstar / source autorisee ou snapshot attribue"),
    (("consensus", "target_", "upside", "upgrade", "downgrade", "analyst"), "Finnhub / yfinance / Boursorama / Zonebourse"),
    (("funnel_global_macro", "macro_", "real_yield", "inflation"), "FRED / BCE / Eurostat"),
    (("news", "bnis", "sentiment"), "AMF / Emetteur / GDELT / Finnhub"),
    (("ter", "aum", "tracking", "replication", "holdings"), "Emetteur/KID / Boursorama / Morningstar-authorized source"),
    (("dividend", "payout"), "yfinance / Emetteur / Boursorama"),
    (("sector", "industry", "country"), "yfinance / Euronext / referentiel"),
    (("rsi", "macd", "perf_", "mm", "volatility", "drawdown", "atr", "stoch", "rvol", "distance_high_52w", "catchup", "rotation"), "OHLCV yfinance -> calcul interne PIT"),
    (("per", "pb", "roe", "roa", "margin", "growth", "debt", "fcf", "market_cap"), "yfinance / Alpha Vantage / Emetteur"),
]


def source_hint(field: str) -> str:
    low=str(field).lower()
    for tokens,source in SOURCE_HINTS:
        if any(token in low for token in tokens): return source
    return "Referentiel / source specifique du champ / non determinee"


def _field_status(frame: pd.DataFrame, asset_class: str, wave_id: str) -> pd.DataFrame:
    rows=[]; n=max(len(frame),1); identity={"isin","name"}
    for field in frame.columns:
        if field in identity: continue
        available=int((~frame[field].apply(is_missing)).sum()); coverage=available/n*100.0
        status="MISSING" if available==0 else "PARTIAL" if available<len(frame) else "AVAILABLE"
        rows.append({
            "collection":wave_id,"asset_class":asset_class,"field":field,"status":status,
            "available_rows":available,"missing_rows":int(len(frame)-available),"universe_rows":int(len(frame)),
            "coverage_pct":round(coverage,2),"source_theorique":source_hint(field),
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        })
    return pd.DataFrame(rows)


def _format_excel(path: Path) -> None:
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb=load_workbook(path); header_fill=PatternFill("solid",fgColor="1F4E78")
    for ws in wb.worksheets:
        ws.freeze_panes="A2"; ws.sheet_view.showGridLines=False
        for cell in ws[1]:
            cell.font=Font(bold=True,color="FFFFFF"); cell.fill=header_fill; cell.alignment=Alignment(horizontal="center",vertical="center")
        for col in ws.columns:
            letter=col[0].column_letter; width=min(55,max(10,max((len(str(c.value)) if c.value is not None else 0) for c in col)+2)); ws.column_dimensions[letter].width=width
    wb.save(path)


def write_collection_audit(
    actions: pd.DataFrame, etfs: pd.DataFrame, wave_id: str, output_root: str | Path,
    *, failures: list[dict] | None = None, source_context: str = "",
) -> str:
    """Write post-collection Excel with observed and missing data plus provenance.

    `source_reelle` comes from the append-only observation ledger. A theoretical
    fallback/source hint is kept separately so the workbook never presents an
    intended source as if it had actually supplied the current observation.
    """
    root=Path(output_root); root.mkdir(parents=True,exist_ok=True)
    inventory=pd.concat([_field_status(actions,"ACTION",wave_id),_field_status(etfs,"ETF",wave_id)],ignore_index=True)
    provenance=actual_sources_by_field()
    if not provenance.empty:
        inventory=inventory.merge(provenance,on="field",how="left")
    else:
        for col in ("sources_reelles","source_urls","evidence_levels","last_as_of"): inventory[col]=""
    inventory["source_reelle_absente"]=(inventory["sources_reelles"].fillna("").astype(str).str.strip()=="")
    missing=inventory[inventory["status"]=="MISSING"].copy(); partial=inventory[inventory["status"]=="PARTIAL"].copy(); available=inventory[inventory["status"]=="AVAILABLE"].copy()
    summary=pd.DataFrame([
        {"collection":wave_id,"asset_class":"ACTION","universe_rows":len(actions),"missing_fields":int((inventory.query("asset_class=='ACTION' and status=='MISSING'")).shape[0]),"partial_fields":int((inventory.query("asset_class=='ACTION' and status=='PARTIAL'")).shape[0]),"fields_with_actual_source":int(((inventory.asset_class=="ACTION")&(~inventory.source_reelle_absente)).sum()),"source_context":source_context},
        {"collection":wave_id,"asset_class":"ETF","universe_rows":len(etfs),"missing_fields":int((inventory.query("asset_class=='ETF' and status=='MISSING'")).shape[0]),"partial_fields":int((inventory.query("asset_class=='ETF' and status=='PARTIAL'")).shape[0]),"fields_with_actual_source":int(((inventory.asset_class=="ETF")&(~inventory.source_reelle_absente)).sum()),"source_context":source_context},
    ])
    failures_df=pd.DataFrame(failures or []); safe="".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in wave_id)[:40]; path=root/f"COLLECTION_AUDIT_{safe}.xlsx"
    with pd.ExcelWriter(path,engine="openpyxl") as writer:
        summary.to_excel(writer,sheet_name="Synthese",index=False)
        missing.to_excel(writer,sheet_name="Donnees_non_disponibles",index=False)
        partial.to_excel(writer,sheet_name="Donnees_partielles",index=False)
        available.to_excel(writer,sheet_name="Donnees_disponibles",index=False)
        inventory[["field","asset_class","sources_reelles","source_urls","evidence_levels","last_as_of","source_theorique"]].drop_duplicates().to_excel(writer,sheet_name="Sources_reelles",index=False)
        provenance.to_excel(writer,sheet_name="Provenance_agregee",index=False)
        failures_df.to_excel(writer,sheet_name="Echecs_collecte",index=False)
    _format_excel(path); latest=root/"COLLECTION_DATA_AVAILABILITY_LATEST.xlsx"; shutil.copyfile(path,latest)
    history_path=root/"COLLECTION_AUDIT_HISTORY.csv"; hist=summary.copy(); hist["generated_at_utc"]=datetime.now(timezone.utc).isoformat(); hist.to_csv(history_path,sep=";",encoding="utf-8-sig",index=False,mode="a",header=not history_path.exists())
    return str(path)
