#!/usr/bin/env bash
set -euo pipefail

exec python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle_ingest.py" session-end-hook
