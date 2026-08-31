# PEA Autopilot Supervisor V2

## Objective

PEA Autopilot V2 reacts to the completion of selected PEA Analyzer GitHub Actions workflows. It publishes a GitHub Job Summary plus a downloadable audit artifact, retries clearly transient infrastructure failures, and performs deterministic remediation only for explicitly whitelisted workflows/branches.

Unknown failures remain fail-closed and are never converted into an economic/model conclusion.

## Installed files

- `.github/workflows/pea_autopilot_supervisor.yml`: immediate `workflow_run` supervisor.
- `scripts/pea_autopilot.py`: classification, artifact inventory, safe retry and deterministic remediation engine.
- `config/PEA_AUTOPILOT.json`: branches, workflow scope, retry/chain limits and governance locks.
- `tests/test_pea_autopilot_governance.py`: regression tests for the safety/cost contract.
- `docs/PEA_AUTOPILOT.md`: this runbook.

## V2 optimizations

V2 reduces GitHub runtime and prevents remediation races. Successful and report-only failed runs do not checkout the target branch or install the full target Python environment. The expensive target checkout/install occurs only when the failed workflow is eligible for deterministic remediation.

Before any automatic write, the supervisor verifies that the checked-out branch SHA still equals the failed upstream SHA. A superseded/stale run is never allowed to patch a newer WIP state. Remediation chains are bounded by `max_autofix_chain_depth`, and transient re-runs are bounded separately.

Successful runs now inventory available workflow artifacts in the structured JSON report. Report artifacts are unique per upstream run and attempt.

## Governance locks

The supervisor does not authorize changes to model weights, thresholds, holdout, PIT logic or real-order authority. Current/future fundamentals cannot be injected as historical data. A CI/data failure is never labelled as an economic failure.

Automatic code edits are limited to the explicitly whitelisted deterministic maintenance script and workflow. Protected runtime/data/config paths are rejected. Any rejected or unvalidated patch is discarded with `git reset --hard HEAD`.

A correction can be committed only after the configured validation chain succeeds. V2 currently requires Ruff, `compileall`, and the complete pytest suite.

## WIP=1 and race protection

GitHub `concurrency` serializes supervision by upstream branch and does not cancel an active remediation. The stale-run guard prevents an older queued supervisor execution from altering a branch that has already advanced. The supervisor never starts a second methodological research workstream.

## Automatic actions

PEA Autopilot may perform only these automatic actions:

1. Re-run failed jobs for a whitelisted transient/infrastructure signature, within the retry limit.
2. Run deterministic maintenance for an explicitly approved workflow/branch, validate it completely, commit it with an `[autopilot-chain=N]` marker, then let the resulting push re-enter the governed validation chain.
3. Otherwise publish a fail-closed classification report and make no source change.

## Automatic output

Each supervised execution produces:

- a GitHub Job Summary;
- artifact `pea-autopilot-report-<upstream_run_id>-attempt-<attempt>`;
- `PEA_AUTOPILOT_REPORT.md`;
- `PEA_AUTOPILOT_REPORT.json`, including upstream metadata, job outcomes, artifact metadata, classification, governance state and automatic action.

## Current automatic-remediation scope

Only `V22.1 CI corrections + canonical 2010-2019 data` is currently authorized for deterministic source remediation, on branch `v22/pit-mae-mfe-preopen`, through `scripts/maintenance_fix_v22_1_ci.py`.

Other monitored PEA workflows remain automatically reported and may receive transient infrastructure retries, but they cannot trigger speculative source modifications.

## Coverage

The supervisor currently listens to the principal V22/V21.8/V4 workflows. A newly created workflow is not monitored automatically merely because it exists: its exact Actions `name:` must be added to `workflow_run.workflows` in `.github/workflows/pea_autopilot_supervisor.yml` after confirming its governance category.

## ChatGPT limitation

GitHub can automatically publish results and execute the safe recovery chain inside GitHub. It cannot directly inject a message into an already-open ChatGPT conversation. On the next ChatGPT interaction, the generated report can be read directly without reconstructing the run manually.
