from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import base64
import hashlib
import zlib
import pandas as pd

EXPECTED_ACTIONS=1829
EXPECTED_SHA256="1e95d51d5a8fa3e616e97ec3fec0a033b29e841d0971ff5a644efa4f5049c085"
IDENTITY_ONLY_STATUS="WHITELIST_ONLY_MISSING_METADATA"


@dataclass(frozen=True)
class CanonicalUniverseResult:
    included: pd.DataFrame
    excluded: pd.DataFrame
    whitelist_count: int
    whitelist_sha256: str
    materialized_missing_count: int = 0


def _read_encoded(path: Path) -> str:
    if path.is_dir():
        parts=sorted(path.glob("*.part"))
        if not parts: raise RuntimeError("V21_3_ACTION_UNIVERSE_PARTS_EMPTY")
        return "".join(p.read_text(encoding="utf-8").strip() for p in parts)
    return path.read_text(encoding="utf-8").strip()


def load_compressed_isins(path: str | Path) -> list[str]:
    encoded=_read_encoded(Path(path))
    if len(encoded)%4: raise RuntimeError(f"V21_3_ACTION_UNIVERSE_BASE64_LENGTH:{len(encoded)}")
    try: raw=zlib.decompress(base64.b64decode(encoded,validate=True)).decode("utf-8")
    except Exception as exc: raise RuntimeError(f"V21_3_ACTION_UNIVERSE_DECODE_ERROR:{type(exc).__name__}:{exc}") from exc
    digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest!=EXPECTED_SHA256: raise RuntimeError(f"V21_3_ACTION_UNIVERSE_DIGEST_MISMATCH:{digest}")
    isins=[line.strip() for line in raw.splitlines() if line.strip()]
    if len(isins)!=EXPECTED_ACTIONS or len(set(isins))!=EXPECTED_ACTIONS: raise RuntimeError(f"V21_3_ACTION_UNIVERSE_COUNT_MISMATCH:{len(isins)}:{len(set(isins))}")
    return isins


def filter_actions(frame: pd.DataFrame, path: str | Path, *, materialize_missing: bool = True) -> CanonicalUniverseResult:
    """Return the exact canonical Action universe, never by row count alone.

    Legacy masters may contain only the historical subset. Missing canonical ISINs
    are materialized as explicit skeleton rows when ``materialize_missing`` is
    enabled; all non-identity fields stay missing and therefore remain subject to
    normal data-coverage gates. No name, ticker, score or market datum is invented.
    """
    if "isin" not in frame.columns: raise RuntimeError("V21_3_ACTION_UNIVERSE_MISSING_ISIN_COLUMN")
    isins=load_compressed_isins(path); allowed=set(isins)
    normalized=frame.copy(); normalized["isin"]=normalized["isin"].astype(str).str.strip()
    duplicated=normalized[normalized["isin"].duplicated(keep=False)]
    if not duplicated.empty: raise RuntimeError(f"V21_3_ACTION_UNIVERSE_DUPLICATE_INPUT_ISIN:{len(duplicated)}")
    included=normalized[normalized["isin"].isin(allowed)].copy(); excluded=normalized[~normalized["isin"].isin(allowed)].copy()
    present=set(included["isin"]); missing=[isin for isin in isins if isin not in present]
    if missing and not materialize_missing:
        raise RuntimeError(f"V21_3_ACTION_UNIVERSE_MISSING_CANONICAL_ISINS:{len(missing)}")
    if missing:
        if "canonical_seed_status" not in included.columns:
            included["canonical_seed_status"]="LEGACY_ROW"
        else:
            included["canonical_seed_status"]=included["canonical_seed_status"].fillna("LEGACY_ROW")
        skeleton=pd.DataFrame(pd.NA,index=range(len(missing)),columns=included.columns)
        skeleton["isin"]=missing
        if "asset_class" in skeleton.columns: skeleton["asset_class"]="ACTION"
        skeleton["canonical_seed_status"]=IDENTITY_ONLY_STATUS
        included=pd.concat([included,skeleton],ignore_index=True)
    if len(included)!=EXPECTED_ACTIONS or included["isin"].nunique()!=EXPECTED_ACTIONS:
        raise RuntimeError(f"V21_3_ACTION_UNIVERSE_FILTER_COUNT:{len(included)}:{included['isin'].nunique()}")
    order={isin:i for i,isin in enumerate(isins)}; included["_canonical_order"]=included["isin"].map(order)
    included=included.sort_values("_canonical_order").drop(columns=["_canonical_order"]).reset_index(drop=True)
    raw='\n'.join(isins)+'\n'
    return CanonicalUniverseResult(included,excluded,len(isins),hashlib.sha256(raw.encode()).hexdigest(),len(missing))
