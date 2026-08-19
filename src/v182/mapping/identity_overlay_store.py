from __future__ import annotations

import base64
import hashlib
from io import StringIO
from pathlib import Path
import zlib

import pandas as pd

EXPECTED_ROWS = 399
EXPECTED_VALIDATED_ROWS = 360
EXPECTED_RAW_SHA256 = "0dfecb6a4014c4c77b1b5e17379ebf202e427bc50f190b86a0d46967dceebd20"
PARTS_RELATIVE_PATH = Path("config/V21_9_ACTION_IDENTITY_MAP.parts")
MATERIALIZED_RELATIVE_PATH = Path("state/generated/V21_9_ACTION_IDENTITY_MAP.csv")


def decode_identity_overlay(root: Path) -> pd.DataFrame:
    parts_dir = root / PARTS_RELATIVE_PATH
    parts = sorted(parts_dir.glob("*.part"))
    if not parts:
        return pd.DataFrame()
    encoded = "".join(part.read_text(encoding="utf-8").strip() for part in parts)
    try:
        raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    except Exception as exc:
        raise RuntimeError(f"ACTION_IDENTITY_OVERLAY_DECODE_ERROR:{type(exc).__name__}:{exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_RAW_SHA256:
        raise RuntimeError(f"ACTION_IDENTITY_OVERLAY_DIGEST_MISMATCH:{digest}")
    frame = pd.read_csv(StringIO(raw.decode("utf-8")), sep=";", dtype=str, keep_default_na=True)
    if len(frame) != EXPECTED_ROWS or frame["isin"].nunique() != EXPECTED_ROWS:
        raise RuntimeError(f"ACTION_IDENTITY_OVERLAY_COUNT_MISMATCH:{len(frame)}:{frame['isin'].nunique()}")
    validated = int(frame["identity_resolution_status"].astype(str).str.upper().eq("VALIDATED").sum())
    if validated != EXPECTED_VALIDATED_ROWS:
        raise RuntimeError(f"ACTION_IDENTITY_OVERLAY_VALIDATED_COUNT_MISMATCH:{validated}")
    if frame["isin"].duplicated().any():
        raise RuntimeError("ACTION_IDENTITY_OVERLAY_DUPLICATE_ISIN")
    return frame


def materialize_identity_overlay(root: Path, output_path: Path | None = None) -> Path | None:
    frame = decode_identity_overlay(root)
    if frame.empty:
        return None
    target = output_path or (root / MATERIALIZED_RELATIVE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, sep=";", encoding="utf-8-sig", index=False)
    return target
