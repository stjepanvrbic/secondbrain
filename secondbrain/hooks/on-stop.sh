#!/usr/bin/env bash
# Stop hook stub — real implementation arrives in Phase 2 (git commit) and
# Phase 3 (ingester dispatch). Phase 1 wires the matcher in hooks.json so
# Phase 2/3 can drop in the real logic without touching the wiring.
#
# Exits 0 cleanly so Stop events don't block the session.

exit 0
