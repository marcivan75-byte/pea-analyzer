from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json

class Checkpoint:
    """Checkpoint scoped to one run ID. A new scheduled day never reuses DONE waves."""
    def __init__(self, path: str | Path, run_id: str):
        base = Path(path)
        self.run_id = run_id
        self.path = base.parent / "runs" / f"{run_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._data = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "waves": {}}
    def done(self, wave_id: str) -> bool:
        return self._data["waves"].get(wave_id, {}).get("status") == "DONE"
    def mark(self, wave_id: str, status: str, **details) -> None:
        self._data["waves"][wave_id] = {"status": status, "finished_at": datetime.now(timezone.utc).isoformat(), **details}
        self._save()
    def wave(self, wave_id: str) -> dict:
        return self._data["waves"].get(wave_id, {})
    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
