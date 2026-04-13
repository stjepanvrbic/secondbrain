---
name: ingest
description: >
  Dispatches the secondbrain-ingester subagent to process a brain dump.
  Used when the user sends unstructured multi-fact input, copy-pasted
  text, screenshots, or voice transcripts. The subagent runs in isolation
  (its own context, its own transcript) and returns a one-line summary;
  the main agent does NOT read the brain dump into its own context to do
  routing or extraction.
metadata:
  version: "3.5.16"
---

# Core Rule

When the user sends a brain dump, **dispatch the secondbrain-ingester
subagent via the Task tool**. Wait for the subagent to return. Report its
one-line summary to the user verbatim. Do NOT process the brain dump
content in your own context — that is the whole point of the T13
delegation refactor.

This skill exists so the main agent stays thin on ingest-heavy
conversations. Routing logic, extraction rules, hot-memory update
reasoning, and write operations all live inside the
`secondbrain-ingester` subagent. Duplicating any of that here re-
introduces the token cost that T13 was written to fix.

# Auto-Triggers

**MUST invoke when ANY of:**
- User sends unstructured text with multiple pieces of info
- User says "brain dump", "dump", "let me get this out", "random thoughts", "just throwing this at you"
- User pastes text from email, Slack, meeting notes, conversation, articles
- User sends screenshot (extract text from image) or voice transcript
- Session-start detects unprocessed files in the vault `inbox/` folder
- Stream-of-consciousness text that is NOT a direct question or task request

**FORBIDDEN: Responding to a brain dump without dispatching the ingest
subagent first.**

# Steps (Foreground Dispatch)

This is the **foreground** dispatch path — the user is waiting for a
one-line summary, so you call the subagent synchronously via the Task
tool and wait. The Stop hook has a separate **background** dispatch
path that uses `nohup ... & disown`; you do not touch that path from
here. Do not background the Task tool invocation — that hides errors
from the user and breaks the "one-line summary" discipline.

## 1. Gather environment details

- Active vault path: read from `~/.config/secondbrain/vaults.json`
  (the `SECONDBRAIN_VAULTS_CONFIG` override applies if set).
- Active vault id: same source.
- Your current working directory: use `pwd`.
- The raw brain-dump content: the user's message text, in full.

## 2. Build a context envelope JSON

The envelope has the same shape as the Stop hook envelope so the
subagent can read both formats uniformly (see
`agents/secondbrain-ingester.md` for the shape specification). For an
explicit brain-dump dispatch, populate the fields as follows:

- `session_id`: a synthetic id of the form `explicit-<ISO timestamp>`
- `vault_path`: absolute path to the active vault
- `vault_id`: value from the vaults.json entry
- `cwd`: your current working directory
- `cursor_path`: `null` (explicit dispatch is one-shot, not cursor-driven)
- `last_assistant_message`: `null`
- `new_turns`: a single synthetic turn whose `content` is the full
  brain-dump text:

  ```json
  [
    {
      "uuid": "explicit-1",
      "index": 0,
      "role": "user",
      "content": "<full brain dump>",
      "timestamp": "<ISO timestamp>"
    }
  ]
  ```

- `cursor_state_before`: `null`

## 3. Write the envelope to a temp file

Use `mktemp` (or an equivalent Python one-liner) to create a path like
`/tmp/secondbrain-explicit-ingest-<timestamp>.json` and write the
envelope JSON to it.

## 4. Dispatch the secondbrain-ingester subagent via the Task tool

Invoke the Task tool with:

- `description`: `"Ingest brain dump"` (short, user-facing)
- `subagent_type`: `"secondbrain-ingester"`
- `prompt`: `"Process the explicit brain-dump envelope at <path>. Session: explicit-<timestamp>."`

Note: `subagent_type` is the Task tool's parameter name. It MUST match
the frontmatter `name` in `agents/secondbrain-ingester.md` exactly.

## 5. Wait for the subagent's response (foreground)

The Task tool call is synchronous. Do not attempt to background it, do
not poll, do not spawn side channels. When it returns, you have the
subagent's final message.

## 6. Report the one-line summary verbatim

Take the subagent's return string and print it to the user as-is. No
prose around it. No "Here is the summary:" preamble. No "Let me know
if you need anything else" postamble.

**Required**:
```
Got it — added 2 tasks, 1 decision, updated [[entities/xavier-laurens]].
```

**FORBIDDEN**:
```
Let me process this for you...
Here's what I added:
- 2 tasks
...
```

# Prerequisites

For general context on the vault, routing rules, and shared write rules,
the subagent reads these itself:

1. `_MANIFEST.md` for current vault state.
2. `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md` for vault layout.
3. `@${CLAUDE_PLUGIN_ROOT}/references/templates.md` for content templates.
4. `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md` for shared write rules.
5. `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md` for script invocations.

You (the main agent) do **not** need to load any of these when running
this skill — the subagent owns the ingest workflow. They are listed
here for informational completeness only.

# Forbidden Actions

- **Processing the brain dump content in your own context.** You dispatch
  and report. The subagent does the actual routing and writes. If you
  start extracting entities, tagging tasks, or calling MCP tools
  yourself, you are running the pre-T13 code path and wasting the user's
  tokens.
- **Backgrounding the Task tool invocation.** The explicit dispatch is
  foreground-only. Background dispatch happens exclusively from the
  Stop hook via `nohup ... & disown`, never from a skill.
- **Adding your own commentary around the subagent's summary.** The
  one-line summary is the whole user-facing surface of this skill. Do
  not expand it, do not summarize it, do not editorialize.
- **Processing the brain dump when the subagent errors out.** If the
  Task tool returns an error, report the error verbatim and stop — do
  NOT fall back to inline processing. Silent fallback re-introduces
  the pre-T13 token cost and hides subagent bugs.
- **Creating new task files.** The subagent writes to `brain/status.md`;
  you do not create `TASKS.md` or anything similar.
- **Asking clarifying questions during a dump.** The subagent handles
  ambiguity via its own `[verify:: true]` discipline. Let it work.

# Implementation Notes

- Timestamp format for the synthetic session id: ISO 8601 local time.
- Envelope path convention:
  `/tmp/secondbrain-explicit-ingest-<ISO timestamp>.json`.
- If the envelope write fails (disk full, /tmp unwritable), abort the
  skill with a visible error. Do not fall back to inline processing.
- If the Task tool is not available (e.g., the main agent's
  environment is missing it), print a clear "Task tool unavailable;
  cannot ingest — please try again in a new session." message. Do not
  fall back to inline processing.
