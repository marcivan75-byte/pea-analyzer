from __future__ import annotations

from pathlib import Path
import pandas as pd

REQUIRED = {"date", "isin", "aum", "nav"}
EVIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


def load_normalized_snapshots(path: str | Path, as_of: str | None = None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["date", "isin", "aum", "nav", "source", "evidence_level", "provider"])
    frame = pd.read_csv(p, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"missing ETF flow snapshot columns: {sorted(missing)}")
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce", dayfirst=False).dt.strftime("%Y-%m-%d")
    out["isin"] = out["isin"].astype(str).str.strip().str.upper()
    out["aum"] = pd.to_numeric(out["aum"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["nav"] = pd.to_numeric(out["nav"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    if "source" not in out.columns:
        out["source"] = "NORMALIZED_ISSUER_IMPORT"
    if "evidence_level" not in out.columns:
        out["evidence_level"] = "B"
    if "provider" not in out.columns:
        out["provider"] = ""
    out["source"] = out["source"].fillna("NORMALIZED_ISSUER_IMPORT").astype(str)
    out["evidence_level"] = out["evidence_level"].fillna("B").astype(str).str.upper()
    out["provider"] = out["provider"].fillna("").astype(str)
    valid_isin = out["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    out = out[
        out["date"].notna()
        & valid_isin
        & out["aum"].notna()
        & out["nav"].notna()
        & (out["aum"] > 0)
        & (out["nav"] > 0)
        & out["evidence_level"].isin(EVIDENCE_RANK)
    ].copy()
    if as_of:
        out = out[out["date"] <= as_of[:10]].copy()
    return out[["date", "isin", "aum", "nav", "source", "evidence_level", "provider"]].reset_index(drop=True)


def load_state(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["date", "isin", "aum", "nav", "source", "evidence_level", "provider"])
    frame = pd.read_parquet(p)
    return frame if not frame.empty else pd.DataFrame(columns=["date", "isin", "aum", "nav", "source", "evidence_level", "provider"])


def upsert_history(existing: pd.DataFrame | None, incoming: pd.DataFrame | None) -> pd.DataFrame:
    frames = [f for f in (existing, incoming) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["date", "isin", "aum", "nav", "source", "evidence_level", "provider"])
    all_rows = pd.concat(frames, ignore_index=True, sort=False)
    for col in ("source", "provider"):
        if col not in all_rows.columns:
            all_rows[col] = ""
    if "evidence_level" not in all_rows.columns:
        all_rows["evidence_level"] = "B"
    all_rows["_evidence_rank"] = all_rows["evidence_level"].map(EVIDENCE_RANK).fillna(0)
    all_rows = all_rows.sort_values(["isin", "date", "_evidence_rank"], ascending=[True, True, False])
    # One canonical snapshot per ISIN/date. Higher evidence wins; same-rank last input wins.
    all_rows = all_rows.drop_duplicates(["isin", "date"], keep="first").drop(columns=["_evidence_rank"])
    return all_rows.sort_values(["isin", "date"]).reset_index(drop=True)


def save_state(frame: pd.DataFrame, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p, index=False)


def history_for_isin(frame: pd.DataFrame, isin: str, as_of: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=frame.columns)
    rows = frame[frame["isin"].astype(str) == str(isin)].copy()
    if as_of:
        rows = rows[rows["date"].astype(str) <= as_of[:10]]
    return rows.sort_values("date").reset_index(drop=True)
