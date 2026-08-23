from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import time

from v182.reporting.runtime_telemetry import _budget_result

ROOT = Path(__file__).resolve().parents[3]
VERSION = "GITHUB_JOB_RUNTIME_V21_16_3"
START_FILE = ".runtime_job_start_epoch"


def run(root: Path = ROOT, *, profile: str) -> dict:
    start_path = root / START_FILE
    if not start_path.exists():
        raise RuntimeError("GITHUB_JOB_RUNTIME_START_MARKER_MISSING")
    try:
        start_epoch = float(start_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError("GITHUB_JOB_RUNTIME_START_MARKER_INVALID") from exc
    wall_seconds = max(0.0, time.time() - start_epoch)
    contract = _budget_result(profile, wall_seconds)
    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile": str(profile),
        "measurement_scope": "POST_CHECKOUT_THROUGH_ARTIFACT_UPLOAD_AND_CACHE_SAVE",
        "excluded_from_measurement": ["INITIAL_CHECKOUT_STEP", "RUNNER_QUEUE_TIME"],
        "wall_seconds": round(wall_seconds, 3),
        "wall_minutes": round(wall_seconds / 60.0, 4),
        "duration_contract": contract,
        "duration_contract_present": bool(contract),
        "decision_logic_changed": False,
    }
    out = root / "outputs" / "audit" / "GITHUB_JOB_RUNTIME_V21_16.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("\n## Runtime V21.16.3\n\n")
            handle.write(f"- Profil : `{profile}`\n")
            handle.write(f"- Temps job mesuré après checkout : **{payload['wall_minutes']:.2f} min**\n")
            status = contract.get("measurement_comparison_status", "CONTRACT_MISSING")
            handle.write(f"- Contrat : **{status}**\n")
            if contract.get("expected_wall_range_minutes"):
                handle.write(f"- Plage statique cible : `{contract['expected_wall_range_minutes']}` min\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--profile", required=True, choices=["DAILY_TACTICAL", "WEEKLY_FULL_COMMITTEE"])
    args = parser.parse_args()
    print(json.dumps(run(Path(args.root), profile=args.profile), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
