# Temporary recovery harness — Final package V21.13.16

This branch exists only to recover the immutable final package artifact through a pull-request-triggered workflow.

The recovery workflow checks out the exact release commit `694e23821b57ba9027125f0f87bc1a9da19118d3`, rebuilds the archive with `git archive`, verifies required contents, writes SHA-256 and exact commit sidecars, and uploads the result as `PEA_ANALYZER_FINAL_V21_13_16_RECOVERED`.

This branch is not intended to be merged into production.
