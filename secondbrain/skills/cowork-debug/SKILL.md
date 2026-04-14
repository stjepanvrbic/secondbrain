---
name: cowork-debug
description: >
  This skill should be used when the user asks where Cowork stores
  Dispatch logs, bridge state, local transcripts, regular conversation
  audits, scheduled-task traces, or when they mention `Prompt is too
  long`, `bridge-state.json`, `local_ditto`, or
  `local-agent-mode-sessions`.
metadata:
  version: "3.5.21"
---

# Core Rule

This skill is a **Cowork log and transcript reference**, not a fixed procedure.
Use it to understand Cowork's on-disk storage model and then follow the user's
request. Let the user's request drive what you inspect.

# Canonical Root

On macOS, Cowork's local app state lives under:

`~/Library/Application Support/Claude`

That root is the starting point for both Dispatch debugging and regular
conversation transcript lookup.

# Mental Model

Cowork has three different artifact classes that are easy to confuse:

1. **Dispatch bridge**: the orchestration layer that connects Cowork's
   Dispatch/reporting machinery to a local bridge session
2. **Dispatched worker transcript**: the actual worker/local session that
   performed the task
3. **Regular conversation transcript**: normal non-Dispatch local chats

Do not treat these as interchangeable. The dispatch bridge audit is not a
full worker transcript, and a worker transcript is not the same thing as
a regular conversation transcript.

# Dispatch Bridge

The dispatch bridge is anchored by:

- `~/Library/Application Support/Claude/bridge-state.json`

That file maps an active Cowork session key to the current bridge state,
typically including:

- `localSessionId`
- `remoteSessionId`
- `enabled`
- `userConsented`

The important field for filesystem lookup is `localSessionId`. In Cowork
Dispatch runs this is commonly a `local_ditto_*` identifier.

Once you have that `localSessionId`, the detailed dispatch bridge log is
usually at this pattern:

`~/Library/Application Support/Claude/local-agent-mode-sessions/<workspace>/<session>/agent/<localSessionId>/audit.jsonl`

Common example shape:

`agent/local_ditto_*/audit.jsonl`

This `audit.jsonl` is the right place to inspect:

- bridge overflow symptoms such as `Prompt is too long`
- `invalid_request` errors
- bridge usage growth like `cache_read_input_tokens`
- bridge-level tool delivery / reporting events

If the problem is specifically Dispatch reporting or the bridge itself,
start here before looking for worker transcripts.

# Dispatched Worker Transcript

The dispatch bridge is only the router. The actual work usually runs in a
separate local session under `local-agent-mode-sessions`.

For a dispatched worker, the important artifact pattern is:

- `local_<uuid>.json` for metadata
- `local_<uuid>/audit.jsonl` for low-level audit events
- `local_<uuid>/.claude/projects/**/*.jsonl` for the actual transcript

Key point: a worker transcript usually lives under `.claude/projects`,
not inside the `local_ditto` bridge audit.

Useful metadata fields in `local_<uuid>.json` include:

- `sessionName`
- `initialMessage`
- `lastActivityAt`

When a Dispatch conversation looks empty in the UI, the worker
conversation may still exist on disk in the corresponding
`local_<uuid>/.claude/projects/**/*.jsonl` transcript.

# Regular Conversation Transcript

Regular conversation lookup uses the same local session pattern as
workers, but without the Dispatch bridge layer.

For a regular conversation, inspect:

- `local_<uuid>.json`
- `local_<uuid>/audit.jsonl`
- `local_<uuid>/.claude/projects/**/*.jsonl`

Use the metadata file first to find the right session by:

- `sessionName`
- `initialMessage`
- recency / `lastActivityAt`

If the user wants the actual conversation transcript, the `.claude/projects`
JSONL is usually the most important artifact. If they want low-level
tooling/hook/debug events, `audit.jsonl` is usually more relevant.

# secondbrain-Specific Runs

For `secondbrain`, scheduled automation usually appears in metadata as a
scheduled-task wrapper inside `initialMessage`.

The strongest markers are:

- `<scheduled-task`
- task names such as `morning-briefing`, `deadline-tracker`,
  `email-triage`, `dream-protocol`
- references to the user's active vault path
- explicit `secondbrain` mentions in the metadata or prompt text

Important nuance: scheduled-task runs may have `sessionName = null`, so
do not rely on `sessionName` alone. `initialMessage` is often the better
identifier for automated secondbrain runs.

When the task was installed by Cowork, the corresponding scheduled skill
definition often lives under:

`~/Documents/Claude/Scheduled/<task-name>/SKILL.md`

That path helps correlate a scheduled task definition with the transcript
and audit artifacts under `local-agent-mode-sessions`.

# Common Questions

## "Prompt is too long" in Dispatch

Think bridge first, not transcript first.

Inspect:

- `bridge-state.json`
- the active `agent/local_ditto_*/audit.jsonl`

This failure signature usually points at dispatch bridge state growth or
bridge replay bloat, not at a missing regular transcript.

## "I need the Dispatch conversation, but the UI looks empty"

Do not stop at the `local_ditto` bridge audit. Look for the dispatched
worker transcript in a separate `local_<uuid>` session and open the
`local_<uuid>/.claude/projects/**/*.jsonl` file.

## "I need the regular conversation transcript"

Ignore the dispatch bridge unless the user is specifically debugging
Dispatch. Start from recent `local_<uuid>.json` metadata and then open
the matching `.claude/projects` transcript.

## "Find the secondbrain scheduled run"

Search the `local_<uuid>.json` metadata files for:

- `<scheduled-task`
- the scheduled task name
- the active vault path
- `secondbrain`

That is usually more reliable than guessing from folder names alone.

# Useful Search Heuristics

This skill is descriptive, not prescriptive, so there is no mandatory
checklist. But these are the main relationships to keep in mind:

- `bridge-state.json` tells you which `local_ditto` bridge is active
- `agent/local_ditto_*/audit.jsonl` is the dispatch bridge audit
- `local_<uuid>.json` is the first index into a regular or worker session
- `local_<uuid>/audit.jsonl` is the low-level event log for that session
- `local_<uuid>/.claude/projects/**/*.jsonl` is the actual transcript
- `initialMessage` is often the best signal for scheduled-task runs

# Forbidden Confusions

- Do not confuse the **dispatch bridge** with the **worker transcript**.
- Do not assume the `local_ditto` audit contains the full conversation.
- Do not assume every scheduled run has a meaningful `sessionName`.
- Do not assume a regular conversation and a Dispatch conversation use the
  same first lookup path.

# Output Shape

When using this skill, answer the user's actual debugging request with:

- the right file class
- the right path or path pattern
- the key reason that file is the correct artifact

Do not force a mandatory checklist when the user only asked where the
files live.
