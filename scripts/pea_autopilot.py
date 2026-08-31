from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib import error, request

SUPERVISOR_ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = Path(os.environ.get("PEA_TARGET_ROOT", str(SUPERVISOR_ROOT))).resolve()
CONFIG = SUPERVISOR_ROOT / "config" / "PEA_AUTOPILOT.json"


def _api(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GITHUB_API_{exc.code}:{url}:{body[:500]}") from exc


def _run(cmd: list[str], *, cwd: Path = TARGET_ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def _jobs_and_logs(repo: str, run_id: int, token: str) -> tuple[list[dict], str]:
    base = f"https://api.github.com/repos/{repo}"
    jobs = _api("GET", f"{base}/actions/runs/{run_id}/jobs?per_page=100", token).get("jobs", [])
    chunks: list[str] = []
    for job in jobs:
        if job.get("conclusion") not in {"failure", "cancelled", "timed_out", "action_required"}:
            continue
        jid = int(job["id"])
        req = request.Request(f"{base}/actions/jobs/{jid}/logs")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with request.urlopen(req, timeout=60) as resp:
                chunks.append(f"\n===== JOB {jid} {job.get('name')} =====\n")
                chunks.append(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            chunks.append(f"LOG_FETCH_FAILED job={jid}: {exc}")
    return jobs, "\n".join(chunks)


def _artifacts(repo: str, run_id: int, token: str) -> list[dict]:
    data = _api("GET", f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100", token)
    return [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "size_in_bytes": a.get("size_in_bytes"),
            "expired": a.get("expired"),
            "expires_at": a.get("expires_at"),
            "digest": a.get("digest"),
        }
        for a in data.get("artifacts", [])
    ]


def _write_report(payload: dict, markdown: str) -> None:
    report_root = SUPERVISOR_ROOT / "outputs" / "autopilot"
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "PEA_AUTOPILOT_REPORT.md").write_text(markdown, encoding="utf-8")
    (report_root / "PEA_AUTOPILOT_REPORT.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def _head_sha() -> str:
    result = _run(["git", "rev-parse", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _head_message() -> str:
    result = _run(["git", "log", "-1", "--pretty=%B"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _chain_depth(message: str) -> int:
    match = re.search(r"\[autopilot-chain=(\d+)\]", message)
    return int(match.group(1)) if match else 0


def _changed_paths() -> list[str]:
    result = _run(["git", "status", "--porcelain"], check=False)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) >= 4:
            value = line[3:].strip()
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            paths.append(value)
    return paths


def _forbidden_paths(paths: list[str], prefixes: list[str]) -> list[str]:
    return [p for p in paths if any(p.startswith(prefix) for prefix in prefixes)]


def _validate(cfg: dict) -> tuple[bool, list[dict]]:
    results: list[dict] = []
    for cmd in cfg.get("validation_commands", []):
        proc = _run([str(x) for x in cmd], check=False)
        results.append(
            {
                "command": cmd,
                "returncode": proc.returncode,
                "tail": (proc.stdout + "\n" + proc.stderr)[-5000:],
            }
        )
        if proc.returncode != 0:
            return False, results
    return True, results


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    run_id = int(os.environ["UPSTREAM_RUN_ID"])
    name = os.environ.get("UPSTREAM_NAME", "")
    conclusion = os.environ.get("UPSTREAM_CONCLUSION", "")
    branch = os.environ.get("UPSTREAM_HEAD_BRANCH", "")
    sha = os.environ.get("UPSTREAM_HEAD_SHA", "")
    attempt = int(os.environ.get("UPSTREAM_RUN_ATTEMPT", "1"))

    payload = {
        "autopilot_version": cfg["version"],
        "upstream": {
            "run_id": run_id,
            "workflow": name,
            "branch": branch,
            "head_sha": sha,
            "conclusion": conclusion,
            "attempt": attempt,
        },
        "governance": cfg["governance"],
        "action": "REPORT_ONLY",
        "status": "OBSERVED",
        "details": {},
    }

    if not cfg.get("enabled", False):
        payload["status"] = "DISABLED"
        _write_report(payload, "# PEA Autopilot\n\nSupervisor disabled by configuration.\n")
        return 0

    if branch not in cfg["allowed_branches"]:
        payload["status"] = "SKIPPED_UNAPPROVED_BRANCH"
        _write_report(payload, f"# PEA Autopilot\n\nRun **#{run_id}** `{name}` skipped: branch `{branch}` is not approved.\n")
        return 0

    jobs, logs = _jobs_and_logs(repo, run_id, token)
    payload["details"]["jobs"] = [
        {
            "id": j.get("id"),
            "name": j.get("name"),
            "status": j.get("status"),
            "conclusion": j.get("conclusion"),
            "started_at": j.get("started_at"),
            "completed_at": j.get("completed_at"),
        }
        for j in jobs
    ]
    payload["details"]["artifacts"] = _artifacts(repo, run_id, token)

    if conclusion == "success":
        payload["status"] = "SUCCESS_REPORTED"
        artifacts = payload["details"]["artifacts"]
        artifact_lines = "\n".join(
            f"  - `{a['name']}` — id `{a['id']}`, {a.get('size_in_bytes') or 0} bytes, expires `{a.get('expires_at')}`"
            for a in artifacts
        ) or "  - none"
        markdown = (
            "# PEA Autopilot — success\n\n"
            f"- Run: **#{run_id}** — `{name}`\n"
            f"- Branch: `{branch}`\n"
            f"- SHA: `{sha}`\n"
            "- Status: **SUCCESS**\n"
            "- Governance: fail-closed/PIT/anti-lookahead unchanged.\n"
            "- Artifacts:\n"
            f"{artifact_lines}\n"
        )
        _write_report(payload, markdown)
        return 0

    low = logs.lower()
    transient_hits = [p for p in cfg["transient_patterns"] if p.lower() in low]
    payload["details"]["transient_hits"] = transient_hits
    if transient_hits and attempt <= cfg["max_transient_reruns"]:
        _api("POST", f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", token, {})
        payload["action"] = "RERUN_FAILED_JOBS"
        payload["status"] = "TRANSIENT_RETRY_REQUESTED"
        _write_report(
            payload,
            "# PEA Autopilot — transient retry\n\n"
            f"Run **#{run_id}** `{name}` matched transient signatures: `{', '.join(transient_hits)}`. "
            "Only failed jobs were re-requested automatically. No model/economic conclusion inferred.\n",
        )
        return 0

    if name not in cfg.get("autofix_workflows", []):
        payload["status"] = "REPORT_ONLY_NON_AUTOFIX_WORKFLOW"
        _write_report(
            payload,
            "# PEA Autopilot — failure classified\n\n"
            f"Run **#{run_id}** `{name}` failed on `{branch}`. This workflow is report-only for automatic remediation. "
            "No model/economic conclusion inferred; fail-closed preserved.\n",
        )
        return 0

    current_sha = _head_sha()
    payload["details"]["checked_out_sha"] = current_sha
    if cfg["governance"].get("stale_run_remediation_forbidden", True) and current_sha != sha:
        payload["status"] = "STALE_RUN_SUPERSEDED"
        _write_report(
            payload,
            "# PEA Autopilot — stale run ignored\n\n"
            f"Run **#{run_id}** failed at `{sha}`, but branch `{branch}` is already at `{current_sha}`. "
            "Automatic remediation was skipped to avoid modifying a newer WIP state.\n",
        )
        return 0

    head_message = _head_message()
    depth = _chain_depth(head_message)
    payload["details"]["autofix_chain_depth"] = depth
    if depth >= int(cfg.get("max_autofix_chain_depth", 0)):
        payload["status"] = "AUTOFIX_CHAIN_LIMIT_REACHED"
        _write_report(
            payload,
            "# PEA Autopilot — fail closed\n\n"
            f"Automatic correction chain limit reached for run **#{run_id}**. No further write was attempted.\n",
        )
        return 1

    maintenance = cfg.get("deterministic_maintenance", {}).get(branch)
    maintenance_path = TARGET_ROOT / maintenance if maintenance else None
    if not maintenance_path or not maintenance_path.exists():
        payload["status"] = "NO_WHITELISTED_REMEDIATION"
        _write_report(
            payload,
            "# PEA Autopilot — failure classified\n\n"
            f"Run **#{run_id}** has no whitelisted deterministic remediation for `{branch}`. "
            "No unsafe fallback was attempted.\n",
        )
        return 0

    before = _changed_paths()
    if before:
        payload["details"]["dirty_before"] = before
        payload["status"] = "FAIL_CLOSED_DIRTY_CHECKOUT"
        _write_report(payload, f"# PEA Autopilot — fail closed\n\nDirty checkout detected before remediation of run **#{run_id}**.\n")
        return 1

    fix = _run(["python", str(maintenance_path)], check=False)
    payload["details"]["maintenance_rc"] = fix.returncode
    payload["details"]["maintenance_tail"] = (fix.stdout + "\n" + fix.stderr)[-5000:]
    if fix.returncode != 0:
        payload["status"] = "AUTOFIX_SCRIPT_FAILED"
        _write_report(payload, f"# PEA Autopilot — remediation failed\n\nDeterministic maintenance failed for run **#{run_id}**. No unsafe fallback was attempted.\n")
        return 1

    changed = _changed_paths()
    forbidden = _forbidden_paths(changed, cfg.get("forbidden_autofix_paths", []))
    payload["details"]["changed_paths"] = changed
    payload["details"]["forbidden_changed_paths"] = forbidden
    if forbidden:
        _run(["git", "reset", "--hard", "HEAD"], check=False)
        payload["status"] = "AUTOFIX_FORBIDDEN_PATH_CHANGE"
        _write_report(
            payload,
            "# PEA Autopilot — fail closed\n\n"
            f"Deterministic maintenance touched protected paths: `{', '.join(forbidden)}`. Changes were discarded.\n",
        )
        return 1

    if not changed:
        payload["status"] = "NO_DETERMINISTIC_DIFF"
        _write_report(
            payload,
            "# PEA Autopilot — no deterministic correction\n\n"
            f"Run **#{run_id}** remains failed, but the whitelisted maintenance produced no code diff. "
            "No speculative edit was attempted.\n",
        )
        return 0

    valid, validation = _validate(cfg)
    payload["details"]["validation"] = validation
    if not valid:
        _run(["git", "reset", "--hard", "HEAD"], check=False)
        payload["status"] = "AUTOFIX_NOT_VALIDATED"
        _write_report(
            payload,
            "# PEA Autopilot — fail closed\n\n"
            f"Corrections for run **#{run_id}** failed the governed validation chain. Changes were discarded and nothing was committed.\n",
        )
        return 1

    _run(["git", "config", "user.name", "github-actions[bot]"])
    _run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    _run(["git", "add", "-A"])
    next_depth = depth + 1
    _run(
        [
            "git",
            "commit",
            "-m",
            f"fix(autopilot): deterministic remediation after run {run_id} [autopilot-chain={next_depth}]",
        ]
    )
    _run(["git", "push", "origin", f"HEAD:{branch}"])
    payload["action"] = "VALIDATED_FIX_COMMITTED"
    payload["status"] = "CORRECTION_PUSHED"
    payload["details"]["next_chain_depth"] = next_depth
    _write_report(
        payload,
        "# PEA Autopilot — correction pushed\n\n"
        f"Run **#{run_id}** `{name}` failed technically. A whitelisted deterministic correction passed Ruff, compileall and the complete pytest suite, "
        f"then was committed to `{branch}` at chain depth **{next_depth}**. The push triggers the governed validation chain. "
        "No economic/model conclusion inferred.\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
