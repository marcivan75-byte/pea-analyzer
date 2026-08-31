# PEA Autopilot Supervisor V1

## Objective

This package supervises selected PEA Analyzer GitHub Actions runs immediately after completion. It publishes a GitHub Job Summary plus a downloadable report artifact and can take only two automated recovery actions:

1. re-run failed jobs when logs match a whitelisted transient/infrastructure signature;
2. on explicitly approved branches, run the repository's deterministic maintenance script, require the complete pytest gate to pass, then commit/push the validated correction.

Unknown failures remain fail-closed. They are reported but never converted into an economic/model conclusion.

## Installed files

- `.github/workflows/pea_autopilot_supervisor.yml`: immediate `workflow_run` supervisor.
- `scripts/pea_autopilot.py`: classification, reporting, safe retry and deterministic remediation engine.
- `config/PEA_AUTOPILOT.json`: approved branches, retry limits and governance locks.
- `docs/PEA_AUTOPILOT.md`: this runbook.

## Governance locks

The supervisor does not authorize changes to model weights, thresholds, holdout, PIT logic or real-order authority. Current/future fundamentals cannot be injected as historical data. A CI/data failure is never labelled as an economic failure. Automated source edits are committed only after the repository's deterministic maintenance script completes and the full pytest suite is green.

## WIP=1

GitHub `concurrency` serializes the supervisor by upstream branch. The supervisor itself never starts a second methodological research workstream. A remediation push only re-enters the existing validation chain for that branch.

## Automatic output

Each supervised run produces:

- GitHub Job Summary;
- artifact `pea-autopilot-report-<upstream_run_id>`;
- `PEA_AUTOPILOT_REPORT.md`;
- `PEA_AUTOPILOT_REPORT.json`.

## Coverage

The workflow currently listens to the principal PEA workflows used by V22/V21.8/V4. To add a newly created workflow, append its exact GitHub Actions `name:` to the `workflow_run.workflows` list in `.github/workflows/pea_autopilot_supervisor.yml`.

## Important limitation

GitHub can automatically publish the result inside GitHub and can execute safe deterministic corrections. It cannot directly inject a message into an already-open ChatGPT conversation. ChatGPT can, however, read the generated report/artifact on the next interaction without having to reconstruct the run manually.
