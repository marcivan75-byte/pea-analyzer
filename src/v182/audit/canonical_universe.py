from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import base64
import hashlib
import zlib
import pandas as pd

EXPECTED_ACTIONS=1429
EXPECTED_SHA256="03797bb98b5276482d66c6bf884a151edd5bf75d8a7b0e0858f0e7f6d76c33a0"


@dataclass(frozen=True)
class CanonicalUniverseResult:
    included: pd.DataFrame
    excluded: pd.DataFrame
    whitelist_count: int
    whitelist_sha256: str


def _read_encoded(path: Path) -> str:
    if path.is_dir():
        parts=sorted(path.glob("*.part"))
        if not parts:
            raise RuntimeError("V21_ACTION_UNIVERSE_PARTS_EMPTY")
        return "".join(p.read_text(encoding="utf-8").strip() for p in parts)
    return path.read_text(encoding="utf-8").strip()


def load_compressed_isins(path: str | Path) -> list[str]:
    encoded=_read_encoded(Path(path))
    if len(encoded) % 4:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_BASE64_LENGTH:{len(encoded)}")
    try:
        raw=zlib.decompress(base64.b64decode(encoded,validate=True)).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_DECODE_ERROR:{type(exc).__name__}:{exc}") from exc
    digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_DIGEST_MISMATCH:{digest}")
    isins=[line.strip() for line in raw.splitlines() if line.strip()]
    if len(isins) != EXPECTED_ACTIONS or len(set(isins)) != EXPECTED_ACTIONS:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_COUNT_MISMATCH:{len(isins)}:{len(set(isins))}")
    return isins


def filter_actions(frame: pd.DataFrame, path: str | Path) -> CanonicalUniverseResult:
    """Filter by the exact V21.0 ISIN whitelist, never by row count alone."""
    if "isin" not in frame.columns:
        raise RuntimeError("V21_ACTION_UNIVERSE_MISSING_ISIN_COLUMN")
    isins=load_compressed_isins(path)
    allowed=set(isins)
    duplicated=frame[frame["isin"].astype(str).duplicated(keep=False)]
    if not duplicated.empty:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_DUPLICATE_INPUT_ISIN:{len(duplicated)}")
    included=frame[frame["isin"].astype(str).isin(allowed)].copy()
    excluded=frame[~frame["isin"].astype(str).isin(allowed)].copy()
    missing=allowed-set(included["isin"].astype(str))
    if missing:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_MISSING_CANONICAL_ISINS:{len(missing)}")
    if len(included) != EXPECTED_ACTIONS:
        raise RuntimeError(f"V21_ACTION_UNIVERSE_FILTER_COUNT:{len(included)}")
    raw='\n'.join(isins)+'\n'
    return CanonicalUniverseResult(included,excluded,len(isins),hashlib.sha256(raw.encode()).hexdigest())
