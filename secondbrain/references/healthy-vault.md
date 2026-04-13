# Healthy Vault Contract

This file defines what "healthy" means for secondbrain.

## Command Roles

- `/secondbrain:doctor` owns runtime/bootstrap health only.
- `/secondbrain:dream-protocol` owns vault-state health.

Doctor is successful when the environment is good enough for Dream Protocol to run safely.
Dream Protocol is successful only when the vault itself is fully healthy.

## Healthy Means Zero Issues

A vault is healthy only if the final full-scan `verify_vault.py --json` result is:

- `errors = 0`
- `warnings = 0`

Dream Protocol is not complete until it reaches that state.

## What Counts Toward Health

`verify_vault.py` should report only conditions that matter for active vault health.
If a condition can exist in a healthy vault, it must be excluded from the verifier entirely.

Examples of conditions excluded from health:

- broken links inside immutable `archive/` snapshots
- expected standalone generated files such as `brain/hot-memory.md`
- legacy session-log domain markers that are preserved for history but are not active routing links

## What Dream Protocol Must Repair

Dream Protocol is responsible for leaving the vault healthy by the end of the run:

- semantic contradictions in live files
- stale status/task state
- missing entity stubs
- broken live wikilinks
- metadata normalization
- manifest rebuild
- hot-memory regeneration from current live vault state

## Two-Worker Model

Dream Protocol runs two focused workers in sequence:

1. Semantic Consolidation Worker
2. Structural Maintenance Worker

The orchestrator succeeds only when both workers succeed and final verification is clean.
