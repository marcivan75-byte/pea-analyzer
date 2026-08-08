from __future__ import annotations

from datetime import datetime
from pathlib import Path
import base64
import hashlib
import io
import json
import lzma
import os

import pandas as pd

from .actions_universe_3609_policy import (
    ROOT,
    OUT,
    AUDIT,
    _identity_overlay,
    _calculate_scores,
    _horizon_and_timing,
)

DEFAULT_SOURCE = ROOT / "data" / "external" / "ACTIONS_PEA_3609_LATEST.columnar.xz.b64"
EXPECTED_B64_SHA256 = "2f8b876f06ea270ba4bfc366d22f44e0d91c0cd85547ca81bbcf4f533668e5f0"
EXPECTED_XZ_SHA256 = "829f6e5e526cb7dd5e9d307a1f1ab57271778395b1c34b51e91d9e4c1280447d"
EXPECTED_PACKED_SHA256 = "baf36cc54fc2f3ed1c7ab37dc29b3271f8f6055833d8d878c16b75353b9f6565"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_columnar_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing canonical 3609 source: {path}")

    payload_text = "".join(path.read_text(encoding="ascii").split())
    if _sha(payload_text.encode("ascii")) != EXPECTED_B64_SHA256:
        raise RuntimeError("3609 source transport checksum mismatch (base64 text)")

    try:
        compressed = base64.b64decode(payload_text, validate=True)
    except Exception as exc:
        raise RuntimeError("3609 source base64 transport is invalid") from exc
    if _sha(compressed) != EXPECTED_XZ_SHA256:
        raise RuntimeError("3609 source compressed checksum mismatch")

    try:
        packed = lzma.decompress(compressed)
    except Exception as exc:
        raise RuntimeError("3609 source XZ payload cannot be decompressed") from exc
    if _sha(packed) != EXPECTED_PACKED_SHA256:
        raise RuntimeError("3609 source decompressed checksum mismatch")

    obj = json.loads(packed.decode("utf-8"))
    if obj.get("rows") != 3609:
        raise RuntimeError(f"Expected 3609 encoded rows, got {obj.get('rows')}")

    data: dict[str, list[str]] = {}
    for item in obj.get("columns", []):
        if not isinstance(item, list) or len(item) != 4 and len(item) != 3:
            raise RuntimeError("Malformed 3609 columnar payload")
        name = item[0]
        mode = item[1]
        if mode == "dict":
            uniques, codes = item[2], item[3]
            values = [uniques[int(code)] for code in codes]
        elif mode == "values":
            values = item[2]
        else:
            raise RuntimeError(f"Unknown 3609 column encoding: {mode}")
        if len(values) != 3609:
            raise RuntimeError(f"Column {name!r} has {len(values)} rows, expected 3609")
        data[name] = values

    df = pd.DataFrame(data)
    if len(df) != 3609:
        raise RuntimeError(f"Expected exactly 3609 action rows, got {len(df)}")
    if len(df.columns) != 146:
        raise RuntimeError(f"Expected 146 source criteria, got {len(df.columns)}")
    required = {"Nom société", "ISIN", "Cours €", "Score V10 /100", "Verdict V10"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing mandatory 3609 source columns: {sorted(missing)}")
    if df["Nom société"].astype(str).str.strip().duplicated().any():
        raise RuntimeError("Duplicate company names in 3609 source")
    return df


def apply_3609_csv_policy(root: Path | None = None, source_path: Path | None = None) -> dict:
    root = root or ROOT
    source = source_path or Path(os.getenv("V204_ACTIONS_3609_SOURCE", str(DEFAULT_SOURCE)))
    df = _load_columnar_source(source)
    original_columns = list(df.columns)
    df = _identity_overlay(root, df)
    df = _calculate_scores(df)
    df = _horizon_and_timing(df)
    derived = [c for c in df.columns if c not in original_columns]
    df = df[original_columns + derived]
    out = root / "outputs" / OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("committee_score_3609", ascending=False).to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    audit = {
        "rows": int(len(df)),
        "source_columns": int(len(original_columns)),
        "source_isin_valid": int(df["source_isin_valid"].sum()),
        "identity_methods": {str(k): int(v) for k, v in df["identity_method"].value_counts().to_dict().items()},
        "decision_counts": {str(k): int(v) for k, v in df["decision"].value_counts().to_dict().items()},
        "execution_live_enabled": False,
        "source_path": str(source),
        "source_format": "checksummed-columnar-xz-base64-text",
        "source_transport_sha256": EXPECTED_B64_SHA256,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    audit_path = root / "outputs" / "audit" / AUDIT.name
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    print("V20_4_GITOK_ACTIONS_3609_CSV_POLICY", apply_3609_csv_policy())


if __name__ == "__main__":
    main()
