---
name: cowork-debug
description: Use when you need context about Cowork local logs, Dispatch bridge state, local transcripts, regular conversation audits, or secondbrain debugging paths before investigating or changing behavior in this repo.
---

# Cowork Debug

Reference skill for debugging Cowork behavior around `secondbrain`.

This skill is descriptive. It explains where Cowork state lives, how the
artifact types differ, and which files matter for common debugging paths.
It is not a fixed procedure. Let the user's request drive what you inspect.

## Canonical Root

On macOS, Cowork local app state lives under:

`~/Library/Application Support/Claude`

That is the root for Dispatch bridge state, local session storage, and
regular conversation transcripts.

## Artifact Types

Do not collapse these into one thing. Cowork debugging usually involves
three different artifact classes:

1. **Dispatch bridge**
   - bridge/orchestration state for Dispatch or reporting
2. **Dispatched worker transcript**
   - the actual local worker session that did the work
3. **Regular conversation transcript**
   - a normal local chat outside the Dispatch bridge path

If you inspect the wrong class of file, you will often conclude the
conversation is missing when it is actually present somewhere else.

## Dispatch Bridge

The Dispatch bridge is anchored by:

- `~/Library/Application Support/Claude/bridge-state.json`

That file maps the active Cowork session to bridge state including:

- `localSessionId`
- `remoteSessionId`
- `enabled`
- `userConsented`

For Dispatch debugging, `localSessionId` is the key field. In the Cowork
bridge path it commonly resolves to a `local_ditto_*` identifier.

The detailed bridge audit usually lives at:

`~/Library/Application Support/Claude/local-agent-mode-sessions/<workspace>/<session>/agent/<localSessionId>/audit.jsonl`

Common shape:

- `agent/local_ditto_*/audit.jsonl`

This is the right place to inspect:

- `Prompt is too long`
- `invalid_request`
- `cache_read_input_tokens`
- bridge delivery/reporting behavior

If the problem is Dispatch itself, start here before assuming the worker
conversation is missing.

## Dispatched Worker Transcript

The bridge audit is not the full task transcript. The actual dispatched
work usually runs in a separate `local_<uuid>` session.

For a dispatched worker, the important files are:

- `local_<uuid>.json`
- `local_<uuid>/audit.jsonl`
- `local_<uuid>/.claude/projects/**/*.jsonl`

Important distinction:

- `local_ditto` audit = bridge layer
- `local_<uuid>/.claude/projects/**/*.jsonl` = actual worker transcript

Useful metadata fields in `local_<uuid>.json`:

- `sessionName`
- `initialMessage`
- `lastActivityAt`

If the Dispatch UI looks empty, the worker transcript may still exist in
the matching `.claude/projects` JSONL.

## Regular Conversations

Regular local conversations use the same `local_<uuid>` storage pattern
as worker sessions, but without the Dispatch bridge layer.

For a regular conversation, inspect:

- `local_<uuid>.json`
- `local_<uuid>/audit.jsonl`
- `local_<uuid>/.claude/projects/**/*.jsonl`

Use the metadata first to find the right conversation by:

- `sessionName`
- `initialMessage`
- recency / `lastActivityAt`

If the user wants the actual transcript, the `.claude/projects` JSONL is
usually the most important file. If they want low-level hook/tool/debug
events, `audit.jsonl` is usually more relevant.

## secondbrain-Specific Runs

For `secondbrain`, scheduled automation usually appears in metadata as a
scheduled-task wrapper inside `initialMessage`.

Strong markers:

- `<scheduled-task`
- task names such as `morning-briefing`, `deadline-tracker`,
  `email-triage`, `dream-protocol`
- the active vault path
- explicit `secondbrain` mentions in the prompt or metadata

Important nuance:

- scheduled runs may have `sessionName = null`
- `initialMessage` is often a better identifier than `sessionName`

Installed scheduled task definitions are often mirrored under:

- `~/Documents/Claude/Scheduled/<task-name>/SKILL.md`

That helps correlate the scheduled task definition with the actual run
under `local-agent-mode-sessions`.

## Common Failure Shapes

### `Prompt is too long`

Think bridge first.

Inspect:

- `bridge-state.json`
- active `agent/local_ditto_*/audit.jsonl`

This usually points at bridge-state growth or replay bloat, not a normal
conversation transcript problem.

### "The Dispatch conversation is empty"

Do not stop at the `local_ditto` audit. Look for the separate
`local_<uuid>` worker session and its `.claude/projects` transcript.

### "I need the regular conversation transcript"

Ignore the bridge unless the bug is specifically about Dispatch.
Start from recent `local_<uuid>.json` metadata and then open the matching
`.claude/projects` transcript.

### "Find the secondbrain scheduled run"

Search metadata for:

- `<scheduled-task`
- task name
- vault path
- `secondbrain`

That is usually more reliable than guessing from folder names alone.

## Live Lookup Heuristics

Use stable file relationships instead of stale assumptions:

- `bridge-state.json` tells you which `local_ditto` bridge is active
- `agent/local_ditto_*/audit.jsonl` is the bridge audit
- `local_<uuid>.json` is the first index into a worker or regular session
- `local_<uuid>/audit.jsonl` is the low-level event log
- `local_<uuid>/.claude/projects/**/*.jsonl` is the actual transcript
- `initialMessage` is often the strongest signal for scheduled-task runs

## Related Context

- The shipped plugin behavior lives under `secondbrain/`
- This skill is repo-local development context under `.codex/skills/`
- It should help while coding and debugging in this repo, but it is not
  part of the packaged plugin runtime
