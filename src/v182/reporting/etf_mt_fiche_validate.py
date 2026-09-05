from __future__ import annotations

from pathlib import Path
import json
import sys

from v182.decision.etf_mt_fiche_validator import validate_fiche_dir


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = Path(args[0]) if args else Path("outputs/etf_mt_v2081/fiches_thesis_mt")
    summary = validate_fiche_dir(target)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
