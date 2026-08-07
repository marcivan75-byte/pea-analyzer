from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {"missing": True, "path": str(path)}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"invalid": True, "path": str(path), "error": type(exc).__name__}
    return value if isinstance(value, dict) else {"invalid": True, "path": str(path)}


def _ratio_ok(wave: dict, threshold: float = 0.90) -> bool:
    try:
        requested = int(wave.get("requested", 0) or 0)
        successful = int(wave.get("successful", 0) or 0)
    except (TypeError, ValueError):
        return False
    return requested > 0 and successful / requested >= threshold


def _float_at_least(payload: dict, key: str, threshold: float) -> bool:
    try:
        return float(payload.get(key, 0) or 0) >= threshold
    except (TypeError, ValueError):
        return False


def _current_sha(root: Path) -> str:
    env_sha = os.environ.get("GITHUB_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _tracked_files(root: Path) -> list[Path]:
    try:
        raw = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=root, stderr=subprocess.DEVNULL
        )
        names = [item.decode("utf-8") for item in raw.split(b"\0") if item]
        return [root / name for name in names if (root / name).is_file()]
    except (OSError, subprocess.CalledProcessError):
        excluded_roots = {".git", "outputs", ".pytest_cache", "__pycache__"}
        files: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in excluded_roots for part in path.relative_to(root).parts):
                continue
            files.append(path)
        return sorted(files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_evidence(root: Path | None = None) -> dict:
    root = root or ROOT
    outputs = root / "outputs"
    audit = outputs / "audit"
    context = outputs / "context"
    release_dir = outputs / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = str(pyproject.get("project", {}).get("version", ""))
    master_cfg = _read_json(root / "config" / "V18.2_MASTER_CONFIG.json")
    config_version = str(master_cfg.get("version", ""))

    quality = _read_json(audit / "V18.2_QUALITY_GATES.json")
    source = _read_json(audit / "V18.2_SOURCE_FALLBACK_METRICS.json")
    gaps = _read_json(audit / "V18.2_OHLCV_GAP_METRICS.json")
    scenarios = _read_json(audit / "V18.2_SCENARIO_METRICS.json")
    analyst = _read_json(audit / "V18.2_ANALYST_MOMENTUM_METRICS.json")
    macro = _read_json(context / "V18.2_MACRO_CONTEXT.json")
    energy = _read_json(context / "V18.2_ENERGY_CONTEXT.json")

    marketbeat = analyst.get("marketbeat") if isinstance(analyst.get("marketbeat"), dict) else {}
    overlay = analyst.get("marketbeat_overlay") if isinstance(analyst.get("marketbeat_overlay"), dict) else {}
    wave01 = source.get("wave01_actions") if isinstance(source.get("wave01_actions"), dict) else {}
    wave02 = source.get("wave02_etf") if isinstance(source.get("wave02_etf"), dict) else {}
    fundamentals = source.get("wave04_yfinance") if isinstance(source.get("wave04_yfinance"), dict) else {}
    consensus = source.get("wave05_finnhub") if isinstance(source.get("wave05_finnhub"), dict) else {}
    openfigi = source.get("openfigi") if isinstance(source.get("openfigi"), dict) else {}
    fred = source.get("macro_fred") if isinstance(source.get("macro_fred"), dict) else {}
    eia = source.get("energy_eia") if isinstance(source.get("energy_eia"), dict) else {}

    checks = {
        "version_18_2_1_consistent": package_version == "18.2.1" and config_version == "18.2.1",
        "quality_gates_passed": quality.get("passed") is True,
        "actions_ohlcv_success_ge_90pct": _ratio_ok(wave01),
        "etf_ohlcv_success_ge_90pct": _ratio_ok(wave02),
        "actions_last_close_coverage_ge_90pct": _float_at_least(gaps, "actions_last_close_coverage_pct", 90.0),
        "etf_last_close_coverage_ge_90pct": _float_at_least(gaps, "etf_last_close_coverage_pct", 90.0),
        "openfigi_mapping_ge_70pct": _float_at_least(openfigi, "coverage_pct", 70.0),
        "fundamentals_availability_ge_90pct": _float_at_least(fundamentals, "available_pct", 90.0),
        "consensus_availability_ge_90pct": _float_at_least(consensus, "available_pct", 90.0),
        "fred_runtime_success": fred.get("success") is True and macro.get("source") == "FRED",
        "eia_runtime_success": eia.get("success") is True and energy.get("source") == "EIA",
        "scenarios_present": int(scenarios.get("scenario_isins", 0) or 0) > 0,
        "execution_gate_shadow_blocked": analyst.get("execution_gate") == "SHADOW_BLOCKED",
        "marketbeat_runtime_success": marketbeat.get("success") is True,
        "marketbeat_useful_data": int(marketbeat.get("successful", 0) or 0) >= 1
        and int(marketbeat.get("observations", 0) or 0) > 0,
        "marketbeat_no_quarantine": int(marketbeat.get("quarantined", 0) or 0) == 0,
        "marketbeat_overlay_applied": int(overlay.get("rows", 0) or 0) >= 1,
    }
    ready = all(checks.values())
    commit_sha = _current_sha(root)

    tracked = _tracked_files(root)
    checksum_lines = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}" for path in tracked
    ]
    checksum_path = release_dir / "V18.2_TRACKED_FILES_SHA256.txt"
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    evidence = {
        "version": "18.2.1",
        "package_version": package_version,
        "config_version": config_version,
        "status": "READY_FOR_GITHUB_INTEGRATION" if ready else "BLOCKED",
        "ready_for_integration": ready,
        "tested_commit_sha": commit_sha,
        "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_gate": analyst.get("execution_gate"),
        "main_merge_performed": False,
        "checks": checks,
        "key_metrics": {
            "actions_rows": gaps.get("actions_total"),
            "etf_rows": gaps.get("etf_total"),
            "actions_last_close_coverage_pct": gaps.get("actions_last_close_coverage_pct"),
            "etf_last_close_coverage_pct": gaps.get("etf_last_close_coverage_pct"),
            "actions_ohlcv_successful": wave01.get("successful"),
            "actions_ohlcv_requested": wave01.get("requested"),
            "etf_ohlcv_successful": wave02.get("successful"),
            "etf_ohlcv_requested": wave02.get("requested"),
            "fundamentals_available_pct": fundamentals.get("available_pct"),
            "consensus_available_pct": consensus.get("available_pct"),
            "openfigi_coverage_pct": openfigi.get("coverage_pct"),
            "scenario_isins": scenarios.get("scenario_isins"),
            "marketbeat_selected": marketbeat.get("selected"),
            "marketbeat_successful": marketbeat.get("successful"),
            "marketbeat_observations": marketbeat.get("observations"),
            "marketbeat_overlay_rows": overlay.get("rows"),
        },
        "evidence_files": {
            "tracked_files_sha256": checksum_path.relative_to(root).as_posix(),
            "quality_gates": "outputs/audit/V18.2_QUALITY_GATES.json",
            "source_metrics": "outputs/audit/V18.2_SOURCE_FALLBACK_METRICS.json",
            "ohlcv_gap_metrics": "outputs/audit/V18.2_OHLCV_GAP_METRICS.json",
            "scenario_metrics": "outputs/audit/V18.2_SCENARIO_METRICS.json",
            "analyst_metrics": "outputs/audit/V18.2_ANALYST_MOMENTUM_METRICS.json",
        },
        "note": "Exact-SHA runtime evidence. API smoke jobs are validated separately on the same commit before integration.",
    }
    evidence_path = release_dir / "V18.2_RELEASE_EVIDENCE.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    human = [
        f"V18.2.1 RELEASE EVIDENCE — {evidence['status']}",
        f"TESTED COMMIT: {commit_sha}",
        f"GITHUB RUN: {evidence['github_run_id']}",
        f"EXECUTION GATE: {evidence['execution_gate']}",
        "MAIN MERGE PERFORMED: NO",
        "",
    ]
    human.extend(f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in checks.items())
    (release_dir / "V18.2_RELEASE_EVIDENCE.txt").write_text("\n".join(human) + "\n", encoding="utf-8")
    return evidence


def main() -> None:
    evidence = build_release_evidence()
    print(
        "RELEASE_EVIDENCE — "
        f"status={evidence['status']} | sha={evidence['tested_commit_sha']} | "
        f"checks={sum(evidence['checks'].values())}/{len(evidence['checks'])}"
    )
    if os.environ.get("V182_REQUIRE_RELEASE_READY", "0") == "1" and not evidence["ready_for_integration"]:
        failed = [name for name, passed in evidence["checks"].items() if not passed]
        raise RuntimeError(f"RELEASE_EVIDENCE_BLOCK: {failed}")


if __name__ == "__main__":
    main()
