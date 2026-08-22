from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile


API = "https://api.github.com"
REQUIRED = {
    "outputs/unified/UNIFIED_SUMMARY_LATEST.json",
    "outputs/committee_master/COMMITTEE_DECISIONS.csv",
}
OPTIONAL = {"outputs/audit/CI_EXPLAINABILITY_AUDIT.json"}
DECISION_PACKAGE_V3 = {
    "outputs/decision_brief/DECISION_BRIEF.json",
    "outputs/decision_brief/DECISION_BRIEF.md",
    "outputs/decision_brief/CI_DECISION_BRIEF_V3.docx",
    "outputs/decision_brief/CI_DECISION_MATRIX_V3.csv",
}
FALLBACK_CONTEXT = {
    "outputs/committee_master/CI_REFERENTIEL_PONDERE.xlsx",
    "outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv",
    "outputs/committee_master/COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv",
    "outputs/risk/BETA_CORRELATION_RISK_ROWS.csv",
    "outputs/daily_tct_ct/TCT_NEXT_SESSION_CATALYST_V24_4_2.csv",
    "state/provenance/CI_DECISION_SNAPSHOT.csv",
}
ARTIFACT_PREFIXES = ("committee-weekly-v21-8-1-", "committee-master-v21-8-1-")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def _request(url: str, token: str, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "User-Agent": "pea-analyzer-decision-fast-v3",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.build_opener(_SafeRedirectHandler()).open(request, timeout=60)


def _json(url: str, token: str) -> dict:
    with _request(url, token) as response:
        value = json.load(response)
    return value if isinstance(value, dict) else {}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _select_run(payload: dict, current_run_id: int, max_age_hours: float) -> dict:
    now = datetime.now(timezone.utc)
    for run in payload.get("workflow_runs", []):
        if int(run.get("id", 0)) == current_run_id:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        updated = _parse_time(str(run["updated_at"]))
        age_hours = (now - updated).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            raise RuntimeError(f"Latest successful global artifact is stale ({age_hours:.1f}h > {max_age_hours:.1f}h).")
        return {**run, "artifact_age_hours": round(age_hours, 2)}
    raise RuntimeError("No successful global run is available for decision inputs.")


def _select_artifact(payload: dict) -> dict:
    candidates = [
        artifact
        for artifact in payload.get("artifacts", [])
        if str(artifact.get("name", "")).startswith(ARTIFACT_PREFIXES)
        and not artifact.get("expired", False)
    ]
    if not candidates:
        raise RuntimeError("The source global run has no valid Committee artifact.")
    return max(candidates, key=lambda item: int(item.get("id", 0)))


def _extract_targets(names: set[str]) -> tuple[set[str], bool]:
    missing = sorted(REQUIRED - names)
    if missing:
        raise RuntimeError("Source artifact is incomplete: " + ", ".join(missing))
    package_ready = DECISION_PACKAGE_V3 <= names
    targets = set(REQUIRED) | (OPTIONAL & names)
    if package_ready:
        targets |= DECISION_PACKAGE_V3
    else:
        targets |= FALLBACK_CONTEXT & names
    return targets, package_ready


def _extract_required(archive: Path, root: Path) -> list[str]:
    extracted: list[str] = []
    root = root.resolve()
    with zipfile.ZipFile(archive) as bundle:
        names = {name.replace("\\", "/").lstrip("./") for name in bundle.namelist()}
        targets, _package_ready = _extract_targets(names)
        for required in sorted(targets):
            target = (root / required).resolve()
            if root not in target.parents:
                raise RuntimeError(f"Unsafe artifact path: {required}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(required) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted.append(required)
    return extracted


def run(repo: str, token: str, current_run_id: int, root: Path, max_age_hours: float) -> dict:
    workflow = urllib.parse.quote("committee_master_daily.yml", safe="")
    runs = _json(
        f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?branch=main&status=success&per_page=10",
        token,
    )
    source_run = _select_run(runs, current_run_id, max_age_hours)
    artifacts = _json(f"{API}/repos/{repo}/actions/runs/{source_run['id']}/artifacts?per_page=100", token)
    artifact = _select_artifact(artifacts)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        archive = Path(handle.name)
        with _request(str(artifact["archive_download_url"]), token) as response:
            shutil.copyfileobj(response, handle)
    try:
        extracted = _extract_required(archive, root)
    finally:
        archive.unlink(missing_ok=True)
    restored = set(extracted)
    metadata = {
        "version": "DECISION_FAST_INPUTS_V3",
        "source_run_id": int(source_run["id"]),
        "source_head_sha": source_run.get("head_sha"),
        "source_updated_at": source_run.get("updated_at"),
        "artifact_id": int(artifact["id"]),
        "artifact_name": artifact.get("name"),
        "artifact_age_hours": source_run["artifact_age_hours"],
        "max_age_hours": max_age_hours,
        "decision_package_v3_restored": DECISION_PACKAGE_V3 <= restored,
        "fallback_context_restored": sorted(FALLBACK_CONTEXT & restored),
        "extracted": extracted,
    }
    out = root / "outputs" / "decision_brief" / "SOURCE_RUN.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--current-run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID", "0")))
    parser.add_argument("--root", default=".")
    parser.add_argument("--max-age-hours", type=float, default=192.0)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not args.repo or not token:
        raise SystemExit("GITHUB_REPOSITORY and GH_TOKEN/GITHUB_TOKEN are required.")
    print(json.dumps(run(args.repo, token, args.current_run_id, Path(args.root), args.max_age_hours), indent=2))


if __name__ == "__main__":
    main()
