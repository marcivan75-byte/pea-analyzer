from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "PEA_AUTOPILOT.json"


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


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=check)


def _jobs_and_logs(repo: str, run_id: int, token: str) -> tuple[list[dict], str]:
    base = f"https://api.github.com/repos/{repo}"
    jobs = _api("GET", f"{base}/actions/runs/{run_id}/jobs?per_page=100", token).get("jobs", [])
    chunks: list[str] = []
    for job in jobs:
        if job.get("conclusion") not in {"failure", "cancelled", "timed_out"}:
            continue
        jid = int(job["id"])
        req = request.Request(f"{base}/actions/jobs/{jid}/logs")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with request.urlopen(req, timeout=60) as resp:
                chunks.append(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            chunks.append(f"LOG_FETCH_FAILED job={jid}: {exc}")
    return jobs, "\n".join(chunks)


def _write_report(payload: dict, markdown: str, cfg: dict) -> None:
    md = ROOT / cfg["report_path"]
    js = ROOT / cfg["json_path"]
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown, encoding="utf-8")
    js.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


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
        "upstream": {"run_id": run_id, "workflow": name, "branch": branch, "head_sha": sha, "conclusion": conclusion, "attempt": attempt},
        "governance": cfg["governance"],
        "action": "REPORT_ONLY",
        "status": "OBSERVED",
        "details": {},
    }

    if branch not in cfg["allowed_branches"]:
        payload["status"] = "SKIPPED_UNAPPROVED_BRANCH"
        _write_report(payload, f"# PEA Autopilot\n\nRun **#{run_id}** `{name}` skipped: branch `{branch}` is not approved.\n", cfg)
        return 0

    jobs, logs = _jobs_and_logs(repo, run_id, token)
    payload["details"]["jobs"] = [{"id": j.get("id"), "name": j.get("name"), "conclusion": j.get("conclusion")} for j in jobs]

    if conclusion == "success":
        payload["status"] = "SUCCESS_REPORTED"
        markdown = f"# PEA Autopilot — success\n\n- Run: **#{run_id}** — `{name}`\n- Branch: `{branch}`\n- SHA: `{sha}`\n- Status: **SUCCESS**\n- Governance: fail-closed/PIT/anti-lookahead unchanged.\n"
        _write_report(payload, markdown, cfg)
        return 0

    low = logs.lower()
    transient = any(p.lower() in low for p in cfg["transient_patterns"])
    if transient and attempt <= cfg["max_transient_reruns"]:
        _api("POST", f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", token, {})
        payload["action"] = "RERUN_FAILED_JOBS"
        payload["status"] = "TRANSIENT_RETRY_REQUESTED"
        markdown = f"# PEA Autopilot — transient retry\n\nRun **#{run_id}** `{name}` failed on a transient/infrastructure signature. Failed jobs were re-requested automatically. No model/economic conclusion inferred.\n"
        _write_report(payload, markdown, cfg)
        return 0

    maintenance = cfg.get("deterministic_maintenance", {}).get(branch)
    if maintenance and (ROOT / maintenance).exists():
        before = _run(["git", "status", "--porcelain"], check=True).stdout.strip()
        if before:
            payload["status"] = "FAIL_CLOSED_DIRTY_CHECKOUT"
            _write_report(payload, f"# PEA Autopilot — fail closed\n\nDirty checkout detected before remediation of run **#{run_id}**. No automated write performed.\n", cfg)
            return 1

        fix = _run(["python", maintenance], check=False)
        payload["details"]["maintenance_rc"] = fix.returncode
        payload["details"]["maintenance_tail"] = (fix.stdout + "\n" + fix.stderr)[-4000:]
        if fix.returncode != 0:
            payload["status"] = "AUTOFIX_SCRIPT_FAILED"
            _write_report(payload, f"# PEA Autopilot — remediation failed\n\nDeterministic maintenance failed for run **#{run_id}**. No unsafe fallback was attempted.\n", cfg)
            return 1

        tests = _run(["python", "-m", "pytest", "-q"], check=False)
        payload["details"]["pytest_rc"] = tests.returncode
        payload["details"]["pytest_tail"] = (tests.stdout + "\n" + tests.stderr)[-5000:]
        if tests.returncode != 0:
            payload["status"] = "AUTOFIX_NOT_VALIDATED"
            _write_report(payload, f"# PEA Autopilot — fail closed\n\nDeterministic corrections were attempted for run **#{run_id}**, but the complete pytest gate remains red. Nothing was committed.\n", cfg)
            return 1

        changed = _run(["git", "status", "--porcelain"], check=True).stdout.strip()
        if changed:
            _run(["git", "config", "user.name", "github-actions[bot]"])
            _run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
            _run(["git", "add", "-A"])
            _run(["git", "commit", "-m", f"fix(autopilot): deterministic remediation after run {run_id}"])
            _run(["git", "push", "origin", f"HEAD:{branch}"])
            payload["action"] = "VALIDATED_FIX_COMMITTED"
            payload["status"] = "CORRECTION_PUSHED"
            markdown = f"# PEA Autopilot — correction pushed\n\nRun **#{run_id}** `{name}` failed technically. A whitelisted deterministic correction passed the complete pytest suite and was committed to `{branch}`. The resulting push will trigger the governed validation chain. No economic/model conclusion inferred.\n"
            _write_report(payload, markdown, cfg)
            return 0

        payload["status"] = "NO_DETERMINISTIC_DIFF"

    payload["status"] = payload.get("status") or "UNKNOWN_FAILURE_FAIL_CLOSED"
    markdown = f"# PEA Autopilot — failure classified\n\n- Run: **#{run_id}** — `{name}`\n- Branch: `{branch}`\n- SHA: `{sha}`\n- Status: **{conclusion.upper()}**\n- Automatic action: none beyond safe deterministic rules.\n- Interpretation: CI/data failure only; **not** a model/economic result.\n- Governance: fail-closed preserved.\n"
    _write_report(payload, markdown, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
