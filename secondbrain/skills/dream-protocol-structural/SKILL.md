---
name: dream-protocol-structural
description: >
  Internal worker for /secondbrain:dream-protocol. Owns deterministic vault
  health repair: verification, canonical link repairs, entity stub creation,
  manifest rebuild, and hot-memory regeneration until the vault verifies clean.
metadata:
  version: "3.6.3"
---

# Core Rule

This worker owns structural/programmatic vault health. It is responsible for
the final clean state. The dream-protocol orchestrator is not complete until
this worker leaves `verify_vault.py` at `0 errors, 0 warnings`.

# Prerequisites

1. Read `@${CLAUDE_PLUGIN_ROOT}/references/healthy-vault.md`.
2. Read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.
3. Read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
4. Read `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol/references/execution-pipeline.md`.

# Responsibilities

- run full-scan verification
- run auto-fixable structural repairs
- create missing entity stubs from verify output
- apply deterministic canonical link/metadata repairs
- rebuild `_MANIFEST.md`
- regenerate `brain/hot-memory.md` from current live vault state
- re-run verification until the final report is clean

# Completion Rule

Completion requires all of the following:

- final `verify_vault.py --json` returns `errors = 0` and `warnings = 0`
- `_MANIFEST.md` rebuilt from current state
- `brain/hot-memory.md` regenerated successfully from live vault state

If any of those fail, hand control back to the dream-protocol orchestrator as a failure.
