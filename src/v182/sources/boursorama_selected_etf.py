from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import math
import re
from typing import Callable

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.boursorama_public import boursorama_code, etf_urls
from v182.sources.rate_limit import StartRateLimiter

CACHE_VERSION = "BOURSORAMA_SELECTED_ETF_V2"


@dataclass(frozen=True)
class BoursoramaSelectedETFResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def _now_utc() -> datetime: return datetime.now(timezone.utc)

def _parse_utc(value: object) -> datetime | None:
    try: dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _age_hours(value: object, now: datetime) -> float:
    dt = _parse_utc(value); return math.inf if dt is None else max(0.0, (now-dt).total_seconds()/3600)

def _text(html: str) -> str:
    try: return " ".join(BeautifulSoup(html, "lxml").stripped_strings)
    except Exception: return ""

def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    text = re.sub(r"[^0-9,+.\- ]", "", text).replace(" ", "")
    if not text or text in {"-", "+"}: return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else: text = text.replace(",", ".")
    try: n = float(text)
    except ValueError: return None
    return n if math.isfinite(n) else None

def _capture(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, flags=re.IGNORECASE); return " ".join(m.group(1).split()) if m else None

def _capture_num(text: str, pattern: str) -> float | None:
    x = _capture(text, pattern); return _num(x) if x is not None else None


def _morningstar_rating_html(html: str) -> tuple[float | None, str | None, str | None]:
    text = _text(html)
    date_match = re.search(r"Notation\s+morningstar[^\d]{0,40}(?:du\s+)?([0-3]?\d\s+\w+\.?\s+20\d{2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})", text, re.I)
    rating_date = date_match.group(1) if date_match else None
    for pattern in (
        r"(?:notation|note|rating)\s+morningstar[^0-9]{0,30}([1-5](?:[.,]\d+)?)\s*(?:/\s*5|étoiles?)",
        r"([1-5](?:[.,]\d+)?)\s*étoiles?\s+morningstar",
    ):
        n = _capture_num(text, pattern)
        if n is not None and 1 <= n <= 5: return n, "VISIBLE_NUMERIC_LABEL", rating_date
    m = re.search(r"(?:notation|rating)\s+morningstar.{0,180}?([★]{1,5})", text, re.I)
    if m: return float(len(m.group(1))), "VISIBLE_STAR_GLYPHS", rating_date
    try: soup = BeautifulSoup(html, "lxml")
    except Exception: return None, None, rating_date
    for anchor in soup.find_all(string=re.compile(r"notation\s+morningstar|morningstar\s+rating", re.I)):
        node = anchor.parent
        for container in [node, getattr(node, "parent", None), getattr(getattr(node, "parent", None), "parent", None)]:
            if container is None: continue
            candidates = container.find_all(["input", "button", "span", "i", "svg"], limit=40)
            for tag in candidates:
                attrs = {str(k).lower(): str(v) for k,v in getattr(tag, "attrs", {}).items()}
                selected = ("checked" in attrs or attrs.get("aria-checked", "").lower()=="true" or attrs.get("aria-selected", "").lower()=="true" or any(x in attrs.get("class", "").lower() for x in ("active","selected","checked","filled")))
                if not selected: continue
                blob = " ".join([attrs.get(k, "") for k in ("value","data-value","data-rating","data-score","aria-label","title")])
                nums = re.findall(r"(?<!\d)([1-5])(?!\d)", blob)
                if nums: return float(nums[-1]), "SELECTED_STAR_WIDGET_VALUE", rating_date
            filled = 0
            for tag in candidates:
                attrs = " ".join([str(tag.get("class", "")), str(tag.get("aria-label", "")), str(tag.get("title", ""))]).lower()
                if "star" in attrs and any(x in attrs for x in ("filled","active","selected","full")): filled += 1
            if 1 <= filled <= 5: return float(filled), "COUNT_FILLED_STAR_WIDGET", rating_date
    lower = html.lower()
    for m in re.finditer("morningstar", lower):
        window = html[max(0,m.start()-500):m.start()+1000]
        for pattern in (r'"(?:rating|stars|starRating)"\s*:\s*"?([1-5])', r'data-(?:rating|stars|score)=["\']([1-5])'):
            hit = re.search(pattern, window, re.I)
            if hit: return float(hit.group(1)), "EMBEDDED_MORNINGSTAR_ATTRIBUTE", rating_date
    return None, None, rating_date


def parse_etf_sheet_html(html: str) -> dict[str, object]:
    text = _text(html)
    if not text: return {}
    fields: dict[str, object] = {}
    for field, pattern in {
        "boursorama_etf_theoretical_open": r"Ouverture th[eé]orique\s+([0-9\s,.]+)",
        "boursorama_etf_open": r"\bouverture\s+([0-9\s,.]+)",
        "boursorama_etf_previous_close": r"cl[oô]ture veille\s+([0-9\s,.]+)",
        "boursorama_etf_day_high": r"\+ haut\s+([0-9\s,.]+)",
        "boursorama_etf_day_low": r"\+ bas\s+([0-9\s,.]+)",
        "boursorama_etf_volume": r"\bvolume\s+([0-9\s]+)",
        "boursorama_etf_management_fee_pct": r"Frais de gestion maximum\s+([0-9\s,.]+)\s*%",
    }.items():
        n = _capture_num(text, pattern)
        if n is not None: fields[field] = n
    rating, method, rating_date = _morningstar_rating_html(html)
    if rating is not None:
        fields["boursorama_etf_morningstar_rating"] = rating
        fields["boursorama_morningstar_stars"] = rating
        fields["boursorama_morningstar_rating_proof_valid"] = True
        fields["boursorama_morningstar_rating_proof"] = method
        if rating_date: fields["boursorama_morningstar_rating_date"] = rating_date
    else:
        fields["boursorama_morningstar_rating_proof_valid"] = False
    assets = re.search(r"Actif net \(EUR\)\s+([0-9\s,.]+)([KMB])?\s*/", text, re.I)
    if assets:
        n = _num(assets.group(1)); scale={"K":0.001,"M":1.0,"B":1000.0}.get((assets.group(2) or "M").upper(),1.0)
        if n is not None: fields["boursorama_etf_aum_eur_m"] = n*scale
    for field, pattern in {
        "boursorama_etf_morningstar_category": r"cat[eé]gorie morningstar\s+(.+?)\s+(?:ouverture|cl[oô]ture veille|Date de cr[eé]ation|Forme juridique|Fonds partenaires)",
        "boursorama_etf_management_company": r"Soci[eé]t[eé] de gestion\s+(.+?)\s+(?:G[eé]rants|Cat[eé]gorie morningstar)",
        "boursorama_etf_asset_class": r"Classe d'actifs\s+(.+?)\s+Zone g[eé]ographique",
        "boursorama_etf_geographic_zone": r"Zone g[eé]ographique\s+(.+?)\s+(?:Dividende|Affectation des r[eé]sultats)",
        "boursorama_etf_distribution_policy": r"Affectation des r[eé]sultats\s+(.+?)\s+R[eé]plication",
        "boursorama_etf_replication": r"R[eé]plication\s+(.+?)\s+(?:Frais d'entr[eé]e|Frais de gestion maximum)",
    }.items():
        v = _capture(text, pattern)
        if v: fields[field] = v[:200]
    fields["boursorama_etf_pea_eligible_displayed"] = bool(re.search(r"\b[ÉE]ligibilit[eé].{0,250}\bPEA\b", text, re.I))
    return fields


def parse_etf_risk_html(html: str) -> dict[str, object]:
    fields = {}
    try: tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except Exception: return fields
    for frame in tables:
        headers = [str(c).upper() for c in frame.columns]; joined=" ".join(headers)
        if "VOLATIL" not in joined or "BETA" not in joined or frame.empty: continue
        row=frame.iloc[0]
        for i,h in enumerate(headers):
            out = next((f for token,f in {"VOLATIL":"boursorama_etf_volatility_1y_pct","ALPHA":"boursorama_etf_alpha_1y","R²":"boursorama_etf_r2_1y","R2":"boursorama_etf_r2_1y","BETA":"boursorama_etf_beta_1y"}.items() if token in h), None)
            if out and i < len(row):
                n=_num(row.iloc[i])
                if n is not None: fields[out]=n
        if fields: break
    return fields


def _load(path: Path) -> dict:
    if not path.exists(): return {"version": CACHE_VERSION, "entries": {}}
    try: p=json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {"version": CACHE_VERSION, "entries": {}}
    return p if p.get("version")==CACHE_VERSION and isinstance(p.get("entries"),dict) else {"version": CACHE_VERSION,"entries":{}}

def _save(path: Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); tmp.replace(path)
def _default_fetcher(url:str,*,timeout:float):
    import requests
    return requests.get(url,headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36","Accept-Language":"fr-FR,fr;q=0.9,en;q=0.7"},timeout=timeout,allow_redirects=True)


def collect_selected_etf_context_cached(rows: pd.DataFrame, cache_path: str|Path, *, dynamic_ttl_hours:float=8.0, deep_ttl_hours:float=168.0, refresh_budget:int=40, request_start_interval_seconds:float=1.0, timeout_seconds:float=15.0, max_workers:int=4, fetcher:Callable[...,object]|None=None, now:datetime|None=None) -> BoursoramaSelectedETFResult:
    current=(now or _now_utc()).astimezone(timezone.utc); cache_file=Path(cache_path); payload=_load(cache_file); entries=payload["entries"]; fetch=fetcher or _default_fetcher; limiter=StartRateLimiter(request_start_interval_seconds); failures=[]
    unique=rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame(); work=[]
    for _,row in unique.iterrows():
        isin=str(row.get("isin") or "").strip(); code=boursorama_code(row,"ETF") if isin else None
        if not isin or not code:
            if isin: failures.append({"isin":isin,"source":"Boursorama ETF","reason":"NO_DETERMINISTIC_CODE"})
            continue
        entry=entries.get(isin,{}); dd=_age_hours(entry.get("dynamic_fetched_at_utc"),current)>=dynamic_ttl_hours; dp=_age_hours(entry.get("deep_fetched_at_utc"),current)>=deep_ttl_hours
        fields=dict(entry.get("fields") or {}); proof_missing=not bool(fields.get("boursorama_morningstar_rating_proof_valid"))
        if dd or dp or proof_missing: work.append((isin,code,dd or proof_missing,dp))
    work=work[:max(0,int(refresh_budget))]
    def worker(item):
        isin,code,dynamic_due,deep_due=item; entry=dict(entries.get(isin,{})); urls=etf_urls(code); local=[]; fields=dict(entry.get("fields") or {})
        if dynamic_due:
            try:
                limiter.wait(); r=fetch(urls["course"],timeout=timeout_seconds); r.raise_for_status() if hasattr(r,"raise_for_status") else None; html=str(getattr(r,"text","") or "")
                if isin.upper() not in html.upper(): raise ValueError("ISIN_MISMATCH_COURSE_PAGE")
                dynamic=parse_etf_sheet_html(html)
                if dynamic:
                    for name in set(entry.get("dynamic_fields") or []): fields.pop(name,None)
                    fields.update(dynamic); entry["dynamic_fields"]=sorted(dynamic); entry["dynamic_fetched_at_utc"]=current.isoformat(); entry["course_url"]=urls["course"]; entry["course_sha256"]=sha256(html.encode("utf-8",errors="replace")).hexdigest()
                    if dynamic.get("boursorama_morningstar_rating_proof_valid"):
                        fields["boursorama_morningstar_rating_source_url"]=urls["course"]
                        fields["boursorama_morningstar_rating_observed_at"]=current.isoformat()
                else: local.append({"isin":isin,"source":"Boursorama ETF","reason":"NO_DYNAMIC_FIELDS","url":urls["course"]})
            except Exception as exc: local.append({"isin":isin,"source":"Boursorama ETF","reason":type(exc).__name__,"detail":str(exc)[:160],"url":urls["course"]})
        if deep_due:
            try:
                limiter.wait(); r=fetch(urls["risk"],timeout=timeout_seconds); r.raise_for_status() if hasattr(r,"raise_for_status") else None; html=str(getattr(r,"text","") or "")
                if isin.upper() not in html.upper(): raise ValueError("ISIN_MISMATCH_RISK_PAGE")
                deep=parse_etf_sheet_html(html); deep.update(parse_etf_risk_html(html))
                if deep:
                    for name in set(entry.get("deep_fields") or []): fields.pop(name,None)
                    fields.update(deep); entry["deep_fields"]=sorted(deep); entry["deep_fetched_at_utc"]=current.isoformat(); entry["risk_url"]=urls["risk"]; entry["risk_sha256"]=sha256(html.encode("utf-8",errors="replace")).hexdigest()
                else: local.append({"isin":isin,"source":"Boursorama ETF","reason":"NO_DEEP_FIELDS","url":urls["risk"]})
            except Exception as exc: local.append({"isin":isin,"source":"Boursorama ETF","reason":type(exc).__name__,"detail":str(exc)[:160],"url":urls["risk"]})
        entry["status"]="OK" if fields else "EMPTY"; entry["boursorama_code"]=code; entry["fields"]=fields; return isin,entry,local
    refreshed=0; workers=max(1,min(int(max_workers),len(work))) if work else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="boursorama-selected-etf") as pool:
            for f in as_completed([pool.submit(worker,x) for x in work]):
                isin,entry,local=f.result(); entries[isin]=entry; failures.extend(local); refreshed += int(entry.get("status")=="OK")
    payload["updated_at_utc"]=current.isoformat(); payload["policy"]={"selected_only":True,"dynamic_ttl_hours":float(dynamic_ttl_hours),"deep_ttl_hours":float(deep_ttl_hours),"refresh_budget":int(refresh_budget),"raw_html_persisted":False,"priority_source":True,"morningstar_proof_required_for_ci_light":True}; _save(cache_file,payload)
    observations=[]; usable=0; morningstar_proven=0
    for _,row in rows.iterrows():
        isin=str(row.get("isin") or "").strip(); entry=entries.get(isin)
        if not entry or entry.get("status")!="OK": continue
        usable+=1; fields=dict(entry.get("fields") or {}); morningstar_proven += int(bool(fields.get("boursorama_morningstar_rating_proof_valid"))); collected=entry.get("dynamic_fetched_at_utc") or entry.get("deep_fetched_at_utc")
        for field,value in fields.items():
            if value is None: continue
            observations.append({"isin":isin,"asset_class":"ETF","horizon":str(row.get("horizon") or ""),"field":field,"value":value,"source":"Boursorama public priority ETF fiche","source_url":fields.get("boursorama_morningstar_rating_source_url") or entry.get("course_url") or entry.get("risk_url"),"collected_at":collected,"validation_status":"POST_SELECTION_PRIORITY_CONTEXT"})
    return BoursoramaSelectedETFResult(observations,failures,{"requested_rows":int(len(rows)),"unique_instruments":int(len(unique)),"refresh_requested":int(len(work)),"refresh_success":int(refreshed),"usable_rows":int(usable),"observations":int(len(observations)),"morningstar_proof_valid_rows":int(morningstar_proven),"selected_only":True,"priority_source":True,"raw_html_persisted":False,"decision_influence":False,"score_influence":0.0,"cache_version":CACHE_VERSION})
