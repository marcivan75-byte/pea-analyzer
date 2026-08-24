from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import csv
import io
import json
import math
import os
import warnings

import pandas as pd
import requests
from urllib3.exceptions import InsecureRequestWarning

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_MARKET_ORIENTATION_V21_15_9"
CACHE_RELATIVE = Path("state/provenance/source_cache/DAILY_MARKET_ORIENTATION_V21_15_9.json")
OUTPUT_RELATIVE = Path("outputs/market_orientation/DAILY_MARKET_ORIENTATION_V21_15_9.json")
CSV_RELATIVE = Path("outputs/market_orientation/DAILY_MARKET_ORIENTATION_V21_15_9.csv")
AUDIT_RELATIVE = Path("outputs/audit/DAILY_MARKET_ORIENTATION_V21_15_9.json")
FRESHNESS_DAYS = {"VIXCLS": 10, "CNN_FEAR_GREED": 2, "VSTOXX": 10}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _pct_change(value, previous):
    value, previous = _num(value), _num(previous)
    if value is None or previous in (None, 0.0):
        return None
    return (value / previous - 1.0) * 100.0


def _direction(value, previous, *, inverse=False):
    value, previous = _num(value), _num(previous)
    if value is None or previous is None:
        return "UNKNOWN"
    if math.isclose(value, previous, rel_tol=0.0, abs_tol=1e-12):
        return "NEUTRAL"
    rising = value > previous
    if inverse:
        return "RISK_OFF" if rising else "RISK_ON"
    return "RISK_ON" if rising else "RISK_OFF"


def _parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _fresh(name: str, row: dict | None) -> bool:
    if not row or row.get("value") is None:
        return False
    observed = _parse_datetime(row.get("as_of"))
    if observed is None:
        return False
    age = (_now().date() - observed.date()).days
    return 0 <= age <= FRESHNESS_DAYS[name]


def _load_cache(root: Path) -> dict:
    path = root / CACHE_RELATIVE
    if not path.exists():
        return {"version": VERSION, "indicators": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": VERSION, "indicators": {}}
    if payload.get("version") != VERSION or not isinstance(payload.get("indicators"), dict):
        return {"version": VERSION, "indicators": {}}
    return payload


def _save_cache(root: Path, indicators: dict[str, dict]) -> None:
    path = root / CACHE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": VERSION, "updated_at_utc": _now().isoformat(), "indicators": indicators}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _get_json(url, *, params=None, headers=None, timeout=5.0):
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _fetch_vixcls() -> dict:
    source_url = "https://fred.stlouisfed.org/series/VIXCLS"
    key = str(os.environ.get("FRED_API_KEY") or "").strip()
    observations = []
    if key:
        payload = _get_json("https://api.stlouisfed.org/fred/series/observations", params={"series_id":"VIXCLS","api_key":key,"file_type":"json","sort_order":"desc","limit":10})
        for row in payload.get("observations", []):
            value = _num(row.get("value"))
            if value is not None:
                observations.append((str(row.get("date") or ""), value))
    else:
        response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS", timeout=5.0)
        response.raise_for_status()
        for row in reversed(list(csv.DictReader(io.StringIO(response.text)))[-20:]):
            value = _num(row.get("VIXCLS"))
            if value is not None:
                observations.append((str(row.get("DATE") or row.get("observation_date") or ""), value))
    if not observations:
        raise RuntimeError("VIXCLS_NO_OBSERVATION")
    as_of, value = observations[0]
    previous = observations[1][1] if len(observations) > 1 else None
    return {"indicator":"VIXCLS","market":"US","value":value,"previous":previous,"change_abs":value-previous if previous is not None else None,"change_pct":_pct_change(value,previous),"rating":"","risk_direction":_direction(value,previous,inverse=True),"as_of":as_of,"source":"FRED / CBOE","source_url":source_url,"status":"LIVE","transport_tls_verification":True}


def _fetch_cnn_fear_greed() -> dict:
    payload = _get_json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers={"User-Agent":"Mozilla/5.0 (compatible; PEA-Analyzer/21.15; daily-market-context)","Accept":"application/json","Origin":"https://edition.cnn.com","Referer":"https://edition.cnn.com/"})
    block = payload.get("fear_and_greed") or {}
    value, previous = _num(block.get("score")), _num(block.get("previous_close"))
    if value is None:
        raise RuntimeError("CNN_FEAR_GREED_SCORE_MISSING")
    return {"indicator":"CNN_FEAR_GREED","market":"US_SENTIMENT","value":value,"previous":previous,"change_abs":value-previous if previous is not None else None,"change_pct":_pct_change(value,previous),"rating":str(block.get("rating") or "").strip().upper(),"risk_direction":_direction(value,previous),"as_of":str(block.get("timestamp") or ""),"source":"CNN Fear & Greed","source_url":"https://www.cnn.com/markets/fear-and-greed","status":"LIVE","previous_1_week":_num(block.get("previous_1_week")),"previous_1_month":_num(block.get("previous_1_month")),"transport_tls_verification":True}


def _fetch_vstoxx() -> dict:
    url = "https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt"
    headers = {"User-Agent":"Mozilla/5.0 (compatible; PEA-Analyzer/21.15; daily-market-context)"}
    tls_verified = True
    try:
        response = requests.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
    except requests.exceptions.SSLError:
        tls_verified = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = requests.get(url, headers=headers, timeout=5.0, verify=False)
        response.raise_for_status()
    valid = []
    for raw in response.text.splitlines():
        line = raw.strip()
        if not line or not line[0].isdigit():
            continue
        parts = [part.strip() for part in line.split(";")]
        if len(parts) < 3 or parts[1].upper() != "V2TX":
            continue
        observed, value = _parse_datetime(parts[0]), _num(parts[2])
        if observed is not None and value is not None:
            valid.append((observed, value))
    if not valid:
        raise RuntimeError("VSTOXX_OFFICIAL_HISTORY_MISSING")
    valid.sort(key=lambda item: item[0])
    observed, value = valid[-1]
    previous = valid[-2][1] if len(valid) > 1 else None
    age = (_now().date() - observed.date()).days
    if age < 0 or age > FRESHNESS_DAYS["VSTOXX"]:
        raise RuntimeError(f"VSTOXX_OFFICIAL_HISTORY_STALE:{observed.date().isoformat()}:age_days={age}")
    return {"indicator":"VSTOXX","market":"EUROPE","value":value,"previous":previous,"change_abs":value-previous if previous is not None else None,"change_pct":_pct_change(value,previous),"rating":"","risk_direction":_direction(value,previous,inverse=True),"as_of":observed.date().isoformat(),"source":"STOXX official V2TX historical data","source_url":url,"status":"LIVE","symbol":"V2TX","freshness_age_days":age,"transport_tls_verification":tls_verified}


def _fallback(name: str, cached: dict | None, exc: Exception) -> dict:
    if _fresh(name, cached):
        row = dict(cached)
        row.update({"status":"CACHE_FALLBACK","live_error_type":type(exc).__name__,"live_error":str(exc)[:240]})
        return row
    return {"indicator":name,"market":"","value":None,"previous":None,"change_abs":None,"change_pct":None,"rating":"","risk_direction":"UNKNOWN","as_of":"","source":"","source_url":"","status":"UNAVAILABLE","live_error_type":type(exc).__name__,"live_error":str(exc)[:240],"stale_cache_rejected":bool(cached)}


def _orientation(indicators: list[dict]) -> tuple[str, str]:
    directions = [str(row.get("risk_direction") or "UNKNOWN") for row in indicators if row.get("value") is not None]
    risk_on, risk_off = directions.count("RISK_ON"), directions.count("RISK_OFF")
    if risk_on >= 2 and risk_on > risk_off:
        label = "SUPPORTIVE"
    elif risk_off >= 2 and risk_off > risk_on:
        label = "CAUTION"
    else:
        label = "MIXED_NEUTRAL"
    detail = ", ".join(f"{r.get('indicator')}={r.get('risk_direction')}" + (f" ({r.get('rating')})" if r.get("rating") else "") for r in indicators)
    return label, detail


def _write_outputs(root: Path, payload: dict) -> None:
    json_path, csv_path, audit_path = root/OUTPUT_RELATIVE, root/CSV_RELATIVE, root/AUDIT_RELATIVE
    json_path.parent.mkdir(parents=True, exist_ok=True); audit_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    json_path.write_text(text, encoding="utf-8"); audit_path.write_text(text, encoding="utf-8")
    pd.DataFrame(payload.get("indicators") or []).to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")


def run(root: Path = ROOT) -> dict:
    started = perf_counter(); cached = (_load_cache(root).get("indicators") or {})
    tasks = {"VIXCLS":_fetch_vixcls,"CNN_FEAR_GREED":_fetch_cnn_fear_greed,"VSTOXX":_fetch_vstoxx}; results = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="daily-market-orientation") as pool:
        future_map = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                row = future.result()
                if not _fresh(name, row):
                    raise RuntimeError(f"{name}_LIVE_VALUE_STALE:{row.get('as_of')}")
                results[name] = row
            except Exception as exc:
                results[name] = _fallback(name, cached.get(name), exc)
    ordered = [results[name] for name in ("VIXCLS","CNN_FEAR_GREED","VSTOXX")]
    orientation, detail = _orientation(ordered); live_count = sum(r.get("status") == "LIVE" for r in ordered); usable_count = sum(r.get("value") is not None for r in ordered)
    payload = {"status":"SUCCESS" if usable_count == 3 else ("SUCCESS_WITH_CACHE_OR_PARTIAL" if usable_count else "UNAVAILABLE_NON_BLOCKING"),"version":VERSION,"generated_at_utc":_now().isoformat(),"scope":"DAILY_UPSTREAM_LIGHT_MARKET_ORIENTATION","orientation":orientation,"orientation_detail":detail,"indicators":ordered,"live_indicators":live_count,"usable_indicators":usable_count,"decision_influence":False,"score_influence":0.0,"weights_changed":False,"thresholds_changed":False,"criteria_changed":False,"can_create_buy":False,"can_block_buy":False,"real_orders_enabled":False,"elapsed_seconds":round(perf_counter()-started,6)}
    next_cache = {name: row for name,row in results.items() if row.get("status") == "LIVE" and _fresh(name,row)}
    for name,row in cached.items():
        if name not in next_cache and name in FRESHNESS_DAYS and _fresh(name,row): next_cache[name] = row
    _save_cache(root, next_cache); _write_outputs(root, payload); return payload


def _fmt(value, digits=2):
    number = _num(value); return "n/a" if number is None else f"{number:.{digits}f}"


def _market_lines(payload: dict) -> list[str]:
    lines = ["## Orientation marché légère — contexte amont","",f"Orientation synthétique : **{payload.get('orientation','n/a')}** — contexte uniquement, influence score/décision = 0.","","| Indicateur | Valeur | Variation | Lecture | As of | Statut |","|---|---:|---:|---|---|---|"]
    for row in payload.get("indicators") or []:
        change = _fmt(row.get("change_pct")); change = change + "%" if change != "n/a" else change; reading = str(row.get("rating") or row.get("risk_direction") or "n/a")
        lines.append(f"| {row.get('indicator')} | {_fmt(row.get('value'))} | {change} | {reading} | {row.get('as_of') or 'n/a'} | {row.get('status') or 'n/a'} |")
    return lines + ["",f"Détail : {payload.get('orientation_detail','')}",""]


def publish_ci_context(root: Path, payload: dict) -> dict:
    committee, mobile = root/"outputs"/"committee_master", root/"outputs"/"mobile"; committee.mkdir(parents=True, exist_ok=True); mobile.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(payload.get("indicators") or []).to_csv(committee/"CI_MARKET_ORIENTATION.csv", sep=";", index=False, encoding="utf-8-sig"); result = {"csv":"outputs/committee_master/CI_MARKET_ORIENTATION.csv"}
    android = mobile/"ANDROID_CI_CONTROL_CENTER.md"
    if android.exists(): android.write_text("\n".join(_market_lines(payload))+"\n"+android.read_text(encoding="utf-8"), encoding="utf-8"); result["android"] = str(android.relative_to(root))
    word = committee/"CI_COMITE_INVESTISSEMENT.docx"
    if word.exists():
        try:
            from docx import Document
            doc = Document(word); doc.add_heading("Orientation marché légère — contexte amont", level=1); doc.add_paragraph(f"Orientation synthétique : {payload.get('orientation','n/a')}. Bloc contextuel uniquement : influence score/décision = 0.")
            table = doc.add_table(rows=1, cols=6)
            for i,label in enumerate(["Indicateur","Valeur","Variation %","Lecture","As of","Statut"]): table.rows[0].cells[i].text=label
            for row in payload.get("indicators") or []:
                cells=table.add_row().cells; vals=[row.get("indicator"),_fmt(row.get("value")),_fmt(row.get("change_pct")),row.get("rating") or row.get("risk_direction"),row.get("as_of"),row.get("status")]
                for i,value in enumerate(vals): cells[i].text=str(value or "")
            doc.save(word); result["word"]=str(word.relative_to(root))
        except Exception as exc: result["word_error"]=f"{type(exc).__name__}:{str(exc)[:160]}"
    excel = committee/"CI_REFERENTIEL_PONDERE.xlsx"
    if excel.exists():
        try:
            from openpyxl import load_workbook
            wb=load_workbook(excel)
            if "MARKET_ORIENTATION" in wb.sheetnames: del wb["MARKET_ORIENTATION"]
            ws=wb.create_sheet("MARKET_ORIENTATION",0); ws.append(["Orientation",payload.get("orientation"),"Influence décision",0,"Influence score",0]); ws.append([])
            columns=["indicator","market","value","previous","change_abs","change_pct","rating","risk_direction","as_of","source","source_url","status","transport_tls_verification"]; ws.append(columns)
            for row in payload.get("indicators") or []: ws.append([row.get(c) for c in columns])
            wb.save(excel); result["excel"]=str(excel.relative_to(root))
        except Exception as exc: result["excel_error"]=f"{type(exc).__name__}:{str(exc)[:160]}"
    return result
