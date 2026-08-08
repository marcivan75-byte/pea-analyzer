from __future__ import annotations
import io
import re
import unicodedata
from pathlib import Path
import pandas as pd
import requests

STABLE_RESOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/c2539d1c-8531-4937-9cba-3bd8e9786cc5"

ALIASES = {
    "holder": {"nom_du_detenteur", "detenteur", "holder", "holder_name"},
    "holder_lei": {"lei_du_detenteur", "lei_detenteur", "holder_lei"},
    "issuer": {"nom_de_lemetteur", "nom_de_l_emetteur", "emetteur", "issuer", "issuer_name"},
    "isin": {"isin"},
    "short_position_pct": {"position_courte_nette", "position_nette_courte", "net_short_position", "position"},
    "position_date": {"date_de_debut_de_position", "date_position", "position_date"},
    "publication_start": {"date_de_debut_de_publication_de_la_position", "date_debut_publication", "publication_start"},
    "publication_end": {"date_de_fin_de_publication_de_la_position", "date_fin_publication", "publication_end"},
}


def fetch_csv(url: str = STABLE_RESOURCE_URL, timeout: int = 30) -> pd.DataFrame:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "PEA-Analyzer-V18.3/1.0"})
    response.raise_for_status()
    return _read_csv_bytes(response.content)


def load_csv(path: str | Path) -> pd.DataFrame:
    return _read_csv_bytes(Path(path).read_bytes())


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    columns = {_norm(c): c for c in frame.columns}
    rename = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if alias in columns:
                rename[columns[alias]] = target
                break
    out = frame.rename(columns=rename).copy()
    required = {"holder", "issuer", "isin", "short_position_pct", "position_date"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"AMF short CSV schema not recognized; missing={sorted(missing)}; columns={list(frame.columns)}")
    out["isin"] = out["isin"].astype(str).str.strip().str.upper()
    out["short_position_pct"] = out["short_position_pct"].map(_pct)
    for c in ("position_date", "publication_start", "publication_end"):
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
    out["source"] = "AMF_OPEN_DATA_SHORTS"
    out["evidence_level"] = "A"
    out["validation_status"] = "VALIDATED"
    out["public_censored_below_05"] = out["short_position_pct"] < 0.5
    return out


def public_position_history(
    frame: pd.DataFrame,
    as_of: str | None = None,
    depth_per_holder: int = 4,
) -> pd.DataFrame:
    """Return point-in-time public history, preserving prior observations.

    Short scoring needs at least two published observations per holder to
    measure covering/increasing exposure. Availability is governed by the
    publication date and never by the earlier economic position date.
    """
    f = normalize(frame) if "evidence_level" not in frame.columns else frame.copy()
    availability = "publication_start" if "publication_start" in f.columns else "position_date"
    f = f[f[availability].notna()].copy()
    if as_of:
        f = f[f[availability] <= as_of[:10]].copy()
    f = f.sort_values(["isin", "holder", availability, "position_date"])
    depth = max(1, int(depth_per_holder))
    return (
        f.groupby(["isin", "holder"], as_index=False, dropna=False, group_keys=False)
        .tail(depth)
        .reset_index(drop=True)
    )


def latest_public_positions(frame: pd.DataFrame, as_of: str | None = None) -> pd.DataFrame:
    history = public_position_history(frame, as_of=as_of, depth_per_holder=1)
    return history.reset_index(drop=True)


def to_events(
    frame: pd.DataFrame,
    as_of: str | None = None,
    history_depth_per_holder: int = 4,
) -> list[dict]:
    from v183.smart_money.models import SmartMoneyEvent

    history = public_position_history(
        frame,
        as_of=as_of,
        depth_per_holder=history_depth_per_holder,
    )
    events = []
    for _, r in history.iterrows():
        pub = r.get("publication_start") or r.get("position_date")
        e = SmartMoneyEvent(
            universe="ACTION", isin=str(r["isin"]), event_type="SHORT", event_subtype="PUBLIC_NET_SHORT",
            source="AMF_OPEN_DATA_SHORTS", evidence_level="A", validation_status="VALIDATED",
            publication_date=str(pub)[:10], transaction_date=str(r.get("position_date") or pub)[:10],
            actor_name=str(r.get("holder") or ""), direction=0,
            short_position_pct=float(r["short_position_pct"]),
            source_document_id=f"AMF_SHORT:{r.get('holder','')}:{r.get('isin','')}:{r.get('position_date','')}",
            metadata={"holder_lei": r.get("holder_lei"), "issuer": r.get("issuer"),
                      "public_censored_below_05": bool(r.get("public_censored_below_05", False))},
        ).to_dict()
        e["position_date"] = str(r.get("position_date") or "")[:10]
        e["public_censored_below_05"] = bool(r.get("public_censored_below_05", False))
        events.append(e)
    return events


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    text = data.decode("utf-8-sig")
    return pd.read_csv(io.StringIO(text), sep=None, engine="python", dtype=str)


def _norm(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s


def _pct(value) -> float:
    s = str(value).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    if s.endswith("%"):
        s = s[:-1]
    return float(s)
