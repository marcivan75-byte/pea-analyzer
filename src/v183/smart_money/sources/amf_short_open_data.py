from __future__ import annotations
import io
import re
import unicodedata
from pathlib import Path
import pandas as pd
import requests

STABLE_RESOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/c2539d1c-8531-4937-9cba-3bd8e9786cc5"

ALIASES = {
    "holder": {"nom_du_detenteur", "detenteur", "holder", "holder_name", "detenteur_de_la_position_courte_nette"},
    "holder_lei": {"lei_du_detenteur", "lei_detenteur", "holder_lei", "legal_entity_identifier_detenteur"},
    "issuer": {"nom_de_lemetteur", "nom_de_l_emetteur", "emetteur", "issuer", "issuer_name", "emetteur_issuer", "nom_de_l_emetteur_de_la_position_courte_nette"},
    "isin": {"isin", "code_isin", "isin_de_l_emetteur"},
    "short_position_pct": {"position_courte_nette", "position_nette_courte", "net_short_position", "position", "ratio", "ratio_de_la_position_courte_nette", "taux_de_la_position_courte_nette", "pourcentage_de_la_position_courte_nette"},
    "position_date": {"date_de_debut_de_position", "date_de_debut_de_position_courte_nette", "date_position", "date_pos", "position_date", "date_de_la_position_courte_nette", "date_de_position_courte_nette"},
    "publication_start": {"date_de_debut_de_publication_de_la_position", "date_debut_publication", "publication_start", "date_de_debut_de_publication"},
    "publication_end": {"date_de_fin_de_publication_de_la_position", "date_fin_publication", "publication_end", "date_de_fin_de_publication"},
}


def fetch_csv(url: str = STABLE_RESOURCE_URL, timeout: int = 30) -> pd.DataFrame:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "PEA-Analyzer-V18.3/1.0"})
    response.raise_for_status()
    return _read_csv_bytes(response.content)


def load_csv(path: str | Path) -> pd.DataFrame:
    return _read_csv_bytes(Path(path).read_bytes())


def _semantic_target(normalized: str) -> str | None:
    n = normalized
    is_lei = "legal_entity_identifier" in n or re.search(r"(^|_)lei(_|$)", n) is not None
    has_holder = "detenteur" in n or "holder" in n
    has_issuer = "emetteur" in n or "issuer" in n
    has_short = "position_courte" in n or "position_nette_courte" in n or "net_short" in n
    has_date = "date" in n
    has_publication = "publication" in n
    if "isin" in n:
        return "isin"
    if is_lei and has_holder:
        return "holder_lei"
    if has_holder and not is_lei and not has_date:
        return "holder"
    if has_issuer and not is_lei and not has_date:
        return "issuer"
    if has_publication and has_date:
        if "fin" in n or "end" in n:
            return "publication_end"
        if "debut" in n or "start" in n:
            return "publication_start"
    if has_date and not has_publication and (has_short or "position" in n or "date_pos" in n):
        return "position_date"
    if not has_date and not has_holder and not has_issuer and not is_lei:
        if "ratio" in n or (has_short and any(token in n for token in ("taux", "pourcentage", "pct", "percent"))):
            return "short_position_pct"
        if n in {"position_courte_nette", "position_nette_courte", "net_short_position", "position"}:
            return "short_position_pct"
    return None


def _column_mapping(frame: pd.DataFrame) -> dict[str, str]:
    normalized = {_norm(c): c for c in frame.columns}
    rename: dict[str, str] = {}
    assigned: set[str] = set()
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                source = normalized[alias]
                rename[source] = target
                assigned.add(target)
                break
    for norm, source in normalized.items():
        if source in rename:
            continue
        target = _semantic_target(norm)
        if target is not None and target not in assigned:
            rename[source] = target
            assigned.add(target)
    return rename


def _date_series(values: pd.Series) -> pd.Series:
    s = values.astype("string").str.strip()
    iso_mask = s.str.match(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$", na=False).fillna(False).astype(bool)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if bool(iso_mask.any()):
        out.loc[iso_mask] = pd.to_datetime(s.loc[iso_mask], errors="coerce").values
    other = (~iso_mask) & s.notna().astype(bool) & s.fillna("").ne("").astype(bool)
    if bool(other.any()):
        out.loc[other] = pd.to_datetime(s.loc[other], errors="coerce", dayfirst=True).values
    return out.dt.strftime("%Y-%m-%d")


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    rename = _column_mapping(frame)
    out = frame.rename(columns=rename).copy()
    required = {"holder", "issuer", "isin", "short_position_pct", "position_date"}
    missing = required - set(out.columns)
    if missing:
        diagnostics = {str(c): _norm(c) for c in frame.columns}
        raise ValueError(f"AMF short CSV schema not recognized; missing={sorted(missing)}; normalized={diagnostics}")
    out["isin"] = out["isin"].astype(str).str.strip().str.upper()
    out["short_position_pct"] = out["short_position_pct"].map(_pct)
    for c in ("position_date", "publication_start", "publication_end"):
        if c in out.columns:
            out[c] = _date_series(out[c])
    out["source"] = "AMF_OPEN_DATA_SHORTS"
    out["evidence_level"] = "A"
    out["validation_status"] = "VALIDATED"
    out["public_censored_below_05"] = out["short_position_pct"] < 0.5
    return out


def public_position_history(frame: pd.DataFrame, as_of: str | None = None, depth_per_holder: int = 4) -> pd.DataFrame:
    f = normalize(frame) if "evidence_level" not in frame.columns else frame.copy()
    availability = "publication_start" if "publication_start" in f.columns else "position_date"
    f = f[f[availability].notna()].copy()
    if as_of:
        f = f[f[availability] <= as_of[:10]].copy()
    f = f.sort_values(["isin", "holder", availability, "position_date"])
    return f.groupby(["isin", "holder"], as_index=False, dropna=False, group_keys=False).tail(max(1, int(depth_per_holder))).reset_index(drop=True)


def latest_public_positions(frame: pd.DataFrame, as_of: str | None = None) -> pd.DataFrame:
    return public_position_history(frame, as_of=as_of, depth_per_holder=1).reset_index(drop=True)


def _safe_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _first_text(*values) -> str:
    for value in values:
        s = _safe_text(value)
        if s:
            return s
    return ""


def _safe_bool(value) -> bool:
    if value is None or pd.isna(value):
        return False
    return bool(value)


def to_events(frame: pd.DataFrame, as_of: str | None = None, history_depth_per_holder: int = 4) -> list[dict]:
    from v183.smart_money.models import SmartMoneyEvent
    history = public_position_history(frame, as_of=as_of, depth_per_holder=history_depth_per_holder)
    events = []
    for _, r in history.iterrows():
        position_date = _safe_text(r.get("position_date"))
        pub = _first_text(r.get("publication_start"), position_date)
        holder = _safe_text(r.get("holder"))
        isin = _safe_text(r.get("isin")).upper()
        issuer = _safe_text(r.get("issuer"))
        holder_lei = _safe_text(r.get("holder_lei"))
        censored = _safe_bool(r.get("public_censored_below_05"))
        if not pub or not position_date or not isin:
            continue
        e = SmartMoneyEvent(
            universe="ACTION", isin=isin, event_type="SHORT", event_subtype="PUBLIC_NET_SHORT",
            source="AMF_OPEN_DATA_SHORTS", evidence_level="A", validation_status="VALIDATED",
            publication_date=pub[:10], transaction_date=position_date[:10],
            actor_name=holder, direction=0, short_position_pct=float(r["short_position_pct"]),
            source_document_id=f"AMF_SHORT:{holder}:{isin}:{position_date}",
            metadata={"holder_lei": holder_lei, "issuer": issuer, "public_censored_below_05": censored},
        ).to_dict()
        e["position_date"] = position_date[:10]
        e["public_censored_below_05"] = censored
        events.append(e)
    return events


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    text = data.decode("utf-8-sig")
    frame = pd.read_csv(io.StringIO(text), sep=";", dtype=str, engine="c")
    if len(frame.columns) >= 5:
        return frame
    alt = pd.read_csv(io.StringIO(text), sep=",", dtype=str, engine="c")
    return alt if len(alt.columns) > len(frame.columns) else frame


def _norm(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()


def _pct(value) -> float:
    s = str(value).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    if s.endswith("%"):
        s = s[:-1]
    return float(s)
