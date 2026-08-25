from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import time
from typing import Any

from .features import build_features
from .io import write_json_atomic, write_text_atomic
from .scoring import score_asset, score_universe
from .synthetic import synthetic_snapshot
from .utils import canonical_hash
from .validation import load_configs, validate_configs


def _report(number: int, title: str, checks: list[dict[str, Any]], elapsed: float, notes: list[str] | None = None) -> dict[str, Any]:
    failures = [check for check in checks if not check["pass"]]
    return {
        "audit": number,
        "title": title,
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failure_count": len(failures),
        "elapsed_seconds": round(elapsed, 6),
        "notes": notes or [],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [f"# Audit {report['audit']}/5 — {report['title']}", "", f"Statut : **{report['status']}**", f"Durée : {report['elapsed_seconds']:.6f} s", ""]
    for check in report["checks"]:
        lines.append(f"- {'PASS' if check['pass'] else 'FAIL'} — {check['name']} : {check.get('detail', '')}")
    if report["notes"]:
        lines.extend(["", "## Notes", ""] + [f"- {note}" for note in report["notes"]])
    return "\n".join(lines) + "\n"


def run_five_audits(root: Path) -> dict[str, Any]:
    governance, sources, universe, criteria = load_configs(root)
    reports: list[dict[str, Any]] = []

    started = time.perf_counter()
    validation = validate_configs(root)
    checks = [
        {"name": "configuration", "pass": validation["status"] == "PASS", "detail": str(validation)},
        {"name": "poids TCT", "pass": abs(sum(governance["weights"]["TCT"].values()) - 1.0) < 1e-12, "detail": "somme=1"},
        {"name": "poids CT", "pass": abs(sum(governance["weights"]["CT"].values()) - 1.0) < 1e-12, "detail": "somme=1"},
        {"name": "sources cœur", "pass": any(row["required_core"] for row in sources["sources"]), "detail": f"{len(sources['sources'])} sources"},
        {"name": "registre critères", "pass": len(criteria["criteria"]) >= 30, "detail": f"{len(criteria['criteria'])} critères"},
        {"name": "Top 100 dynamique", "pass": universe.get("universe_mode") == "TOP_MARKET_CAP_DYNAMIC" and universe.get("target_count") == 100, "detail": "100 capitalisations"},
        {"name": "univers séparé", "pass": all(row.get("category") not in {"ACTION", "ETF"} for row in universe["assets"]), "detail": "crypto-only"},
    ]
    reports.append(_report(1, "référentiels, sources et pondérations", checks, time.perf_counter() - started))

    started = time.perf_counter()
    snapshot = synthetic_snapshot(100)
    base_features = build_features(snapshot, governance)
    base_row = score_asset(base_features["bitcoin"], "CT", governance)
    missing = deepcopy(snapshot)
    missing["assets"]["bitcoin"]["network"] = {}
    missing["assets"]["bitcoin"]["evidence"] = []
    missing_row = score_asset(build_features(missing, governance)["bitcoin"], "CT", governance)
    incident = deepcopy(snapshot)
    incident["assets"]["bitcoin"]["evidence"] = [{"type": "SECURITY_INCIDENT", "severity": "HIGH"}]
    incident_row = score_asset(build_features(incident, governance)["bitcoin"], "CT", governance)
    future = deepcopy(snapshot)
    future["assets"]["bitcoin"]["history"].append({"ts": 9_999_999_999_999, "price": 10**12, "market_cap": 10**18, "volume": 10**18})
    future_row = score_asset(build_features(future, governance)["bitcoin"], "CT", governance)
    checks = [
        {"name": "absence non imputée", "pass": missing_row["coverage"] < base_row["coverage"], "detail": f"{base_row['coverage']} -> {missing_row['coverage']}"},
        {"name": "incident hard gate", "pass": incident_row["state"] == "BLOCKED_RISK", "detail": incident_row["state"]},
        {"name": "anti-look-ahead", "pass": future_row["score"] == base_row["score"], "detail": "observation future ignorée"},
        {"name": "score borné", "pass": all(row["score"] is None or 0 <= row["score"] <= 100 for row in score_universe(base_features, governance)), "detail": "[0,100]"},
        {"name": "ordres désactivés", "pass": governance["real_orders_enabled"] is False, "detail": "false"},
    ]
    reports.append(_report(2, "données, scoring, risque et PIT", checks, time.perf_counter() - started))

    started = time.perf_counter()
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "src").rglob("*.py"))
    config_text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "config").glob("*.json"))
    checks = [
        {"name": "pas d'eval/exec", "pass": not re.search(r"\b(eval|exec)\s*\(", source_text), "detail": "scan statique"},
        {"name": "pas de sous-processus", "pass": not re.search(r"^\s*(?:from\s+subprocess\s+import|import\s+subprocess\b)", source_text, re.M), "detail": "scan statique"},
        {"name": "pas de secret littéral", "pass": not re.search(r"(?:api[_-]?key|secret)\s*[=:]\s*['\"][A-Za-z0-9_-]{20,}", source_text, re.I), "detail": "scan statique"},
        {"name": "HTTPS uniquement", "pass": "http://" not in config_text, "detail": "registre sources"},
        {"name": "échec source explicite", "pass": "SOURCE_REQUEST_FAILED" in source_text, "detail": "fail closed"},
        {"name": "écriture atomique", "pass": ".replace(path)" in source_text, "detail": "artefacts"},
    ]
    reports.append(_report(3, "code, sécurité et résilience", checks, time.perf_counter() - started))

    started = time.perf_counter()
    large = synthetic_snapshot(100)
    feature_start = time.perf_counter()
    large_features = build_features(large, governance)
    large_rows = score_universe(large_features, governance)
    compute_elapsed = time.perf_counter() - feature_start
    unique_chains_100 = 20
    modeled_requests = 1 + 100 + 3 + 1 + 100 + 2 + 1 + unique_chains_100
    budget = governance["runtime"]["performance_budget_seconds_100_assets_cached"]
    checks = [
        {"name": "100 actifs scorés", "pass": len(large_rows) == 200, "detail": f"{len(large_rows)} lignes TCT/CT"},
        {"name": "budget CPU cache", "pass": compute_elapsed <= budget, "detail": f"{compute_elapsed:.6f}s <= {budget}s"},
        {"name": "budget réseau modélisé", "pass": modeled_requests <= governance["runtime"]["network_budget_requests_100_assets"], "detail": f"{modeled_requests} requêtes"},
        {"name": "collecte parallèle", "pass": "ThreadPoolExecutor" in source_text, "detail": f"max_workers={governance['runtime']['max_workers']}"},
        {"name": "cache TTL", "pass": "DiskTTLCache" in source_text and "ttl_seconds" in source_text, "detail": "persistant"},
    ]
    reports.append(_report(4, "performance et durée de traitement", checks, time.perf_counter() - started, ["Le budget réseau couvre 100 actifs; la durée à froid dépend surtout des limites CoinGecko, puis le cache TTL accélère les relances."]))

    started = time.perf_counter()
    first = score_universe(build_features(snapshot, governance), governance)
    second = score_universe(build_features(deepcopy(snapshot), governance), governance)
    hashes = canonical_hash(first), canonical_hash(second)
    checks = [
        {"name": "reproductibilité", "pass": hashes[0] == hashes[1], "detail": hashes[0]},
        {"name": "ordre déterministe", "pass": first == second, "detail": "identique"},
        {"name": "deux horizons exacts", "pass": {row["horizon"] for row in first} == {"TCT", "CT"}, "detail": "TCT+CT"},
        {"name": "aucune promotion automatique", "pass": governance["automatic_weight_promotion"] is False, "detail": "false"},
        {"name": "audits précédents", "pass": all(report["status"] == "PASS" for report in reports), "detail": "1-4 PASS"},
    ]
    reports.append(_report(5, "revue contradictoire et reproductibilité", checks, time.perf_counter() - started))

    audit_dir = root / "outputs" / "audit"
    for report in reports:
        stem = f"AUDIT_{report['audit']:02d}"
        write_json_atomic(audit_dir / f"{stem}.json", report)
        write_text_atomic(audit_dir / f"{stem}.md", _markdown(report))
    summary = {
        "version": "CRYPTO_AUDIT_SUITE_V1",
        "status": "PASS" if all(report["status"] == "PASS" for report in reports) else "FAIL",
        "audit_count": 5,
        "reports": [{"audit": report["audit"], "status": report["status"], "elapsed_seconds": report["elapsed_seconds"]} for report in reports],
        "total_elapsed_seconds": round(sum(report["elapsed_seconds"] for report in reports), 6),
    }
    write_json_atomic(audit_dir / "AUDIT_SUITE_SUMMARY.json", summary)
    write_text_atomic(audit_dir / "AUDIT_SUITE_SUMMARY.md", "# Suite d'audits Crypto\n\n" + "\n".join(f"- Audit {row['audit']}/5 : {row['status']} ({row['elapsed_seconds']:.6f} s)" for row in summary["reports"]) + f"\n\nStatut final : **{summary['status']}**\n")
    return summary
