from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(path, json_text(payload) + "\n")


def json_text(payload: Any, *, pretty: bool = True, sort_keys: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=sort_keys)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=sort_keys)
