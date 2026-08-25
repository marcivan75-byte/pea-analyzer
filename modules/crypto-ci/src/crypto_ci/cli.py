from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import run_five_audits
from .performance_audit import run_three_performance_audits
from .pipeline import run_pipeline
from .validation import validate_configs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crypto-ci")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "audit", "performance-audit"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd())
    run = sub.add_parser("run")
    run.add_argument("--root", type=Path, default=Path.cwd())
    run.add_argument("--snapshot", type=Path)
    run.add_argument("--as-of")
    run.add_argument("--full-output", action="store_true", help="Print all 200 rows instead of the compact run summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    if args.command == "validate":
        payload = validate_configs(root)
    elif args.command == "audit":
        payload = run_five_audits(root)
    elif args.command == "performance-audit":
        payload = run_three_performance_audits(root)
    else:
        payload = run_pipeline(root, snapshot_path=args.snapshot, as_of=args.as_of)
    displayed = payload
    if args.command == "run" and not args.full_output:
        displayed = {
            "version": payload.get("version"),
            "as_of": payload.get("as_of"),
            "status": payload.get("status"),
            "data_mode": payload.get("data_mode"),
            "asset_count": payload.get("asset_count"),
            "row_count": payload.get("row_count"),
            "state_counts": payload.get("state_counts"),
            "t1_t2": payload.get("t1_t2"),
            "runtime": payload.get("runtime"),
            "source_status": payload.get("source_status"),
            "snapshot_fingerprint": payload.get("snapshot_fingerprint"),
        }
    print(json.dumps(displayed, ensure_ascii=True, indent=2, default=str))
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
