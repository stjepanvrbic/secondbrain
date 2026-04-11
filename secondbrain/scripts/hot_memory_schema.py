#!/usr/bin/env python3
"""
hot_memory_schema.py — schema + validator + assembler for `brain/hot-memory.md`.

Hot memory is the agent's pre-computed "always-loaded" context file: a small
(~1200 tokens), structured markdown document the SessionStart hook emits as a
`systemMessage` on every session. It replaces the old pattern where the agent
had to burn tool calls loading context at the start of every conversation.

Two writers maintain it:
  - `dream-protocol` nightly: full regenerate from current vault state.
  - The ingest subagent (T13): incremental updates for critical content
    (new high-urgency tasks, deadlines, etc.) after a session.

This module is a *schema-as-code* layer. It defines what a valid hot-memory
document looks like and exposes the pure functions the two writers share:
`validate`, `parse_sections`, `assemble_document`, plus the canonical
`INITIAL_TEMPLATE`/`initial_template()` helpers used by `setup_steps` when
bootstrapping a fresh vault.

Contract invariants
-------------------
* Importable with zero side effects (no I/O, no logging, no `print`).
* Python 3.8+, stdlib only.
* `validate` never raises for malformed input — every failure mode funnels
  into `ValidationResult.errors` / `warnings` so callers can render a single
  structured report.
* The module does *not* know about MCP or the filesystem. Readers/writers
  live in `validate_hot_memory.py` and `update_hot_memory.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
"""Integer schema version. Incremented any time the required-section list
or frontmatter contract changes. Older files are accepted with a warning;
future (unknown) versions are rejected with an error.
"""

REQUIRED_SECTIONS: List[str] = [
    "Identity & Directive",
    "User Snapshot",
    "Top Deadlines",
    "Urgent This Week",
    "Recent Activity",
    "Vault Layout",
    "Routing — When You Detect This, Do This",
    "File Pointers",
]
"""The 8 H2 sections every hot-memory document MUST contain, in canonical
order. `assemble_document` emits them in this order so the file reads
consistently across regenerations.
"""

OPTIONAL_SECTIONS: List[str] = [
    "Active Project Context",   # appended by session-start hook at runtime
    "Morning Brief Status",
]
"""H2 sections that are allowed but not required. Listed here so that
`validate` does not flag them as unknown-extra sections (which would
otherwise produce a warning).
"""

TOKEN_SOFT_LIMIT = 1200
"""Token budget at which we start warning. Hot memory is loaded on every
session — bloat is expensive. 1200 tokens is ~4800 chars, the sweet spot
from early prototyping.
"""

TOKEN_HARD_LIMIT = 1500
"""Absolute ceiling. Writers that produce a document over this limit must
fail; the existing file stays in place. 1500 leaves some headroom over
the soft limit so that a single high-priority item doesn't automatically
reject the whole draft.
"""


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Conservative Claude token estimate: `len(text) // 4`.

    Real tokenization is model-specific and we don't ship the BPE tables
    here — len/4 is Anthropic's documented rule of thumb for English text
    and matches what the rest of the plugin uses for budget math.
    """
    if not text:
        return 0
    return len(text) // 4


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Structured output of `validate`.

    `ok` is True iff `errors` is empty — warnings alone do not block. The
    other fields expose detail the CLI or doctor check can render in a
    single pass without re-parsing.
    """
    ok: bool
    schema_version: Optional[int]
    token_estimate: int
    sections_found: List[str]
    missing_sections: List[str]
    extra_sections: List[str]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _split_frontmatter(content: str) -> Tuple[Optional[Dict[str, str]], str, Optional[str]]:
    """Extract a YAML-ish frontmatter block from the start of `content`.

    Returns `(fields, body, error_message)`. On success, `error_message`
    is None. On failure, `fields` is None and `error_message` describes
    what went wrong.

    This is a *deliberately minimal* frontmatter parser. Hot-memory
    frontmatter is always writer-controlled (dream-protocol + the
    ingester), so we expect plain `key: value` lines. A real YAML parser
    is overkill and would add a dependency.
    """
    if not content.startswith("---"):
        return None, content, "missing frontmatter (document must start with '---')"

    lines = content.splitlines(keepends=True)
    # First line is "---". Find the closing "---".
    closing_index: Optional[int] = None
    for i in range(1, len(lines)):
        stripped = lines[i].rstrip("\r\n")
        if stripped == "---":
            closing_index = i
            break

    if closing_index is None:
        return None, content, "unterminated frontmatter (no closing '---')"

    fields: Dict[str, str] = {}
    for i in range(1, closing_index):
        line = lines[i].rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    body = "".join(lines[closing_index + 1 :])
    return fields, body, None


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def parse_sections(content: str) -> Dict[str, str]:
    """Parse the H2 sections of a hot-memory markdown document.

    Returns a dict mapping section name → section body (without the heading).
    Strips the frontmatter before parsing. Dict insertion order matches
    document order, which `update_hot_memory.py` relies on so replacing a
    single section preserves the surrounding layout.

    Unknown or malformed documents return as much as can be extracted; this
    function never raises. It is intentionally forgiving so writers can
    round-trip through parse → edit → assemble without the parser being
    the thing that breaks.
    """
    _fields, body, _error = _split_frontmatter(content)
    # Fall through either way — if frontmatter is missing we still parse the
    # body. `validate` is the gatekeeper that decides whether the document
    # is actually *valid*.

    sections: Dict[str, str] = {}
    current_name: Optional[str] = None
    current_body: List[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            # Flush the previous section.
            if current_name is not None:
                sections[current_name] = "\n".join(current_body).strip("\n")
            current_name = line[3:].strip()
            current_body = []
        else:
            if current_name is not None:
                current_body.append(line)
            # Lines before the first H2 are ignored (they'd be stray prose).

    if current_name is not None:
        sections[current_name] = "\n".join(current_body).strip("\n")

    return sections


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def assemble_document(
    sections: Dict[str, str],
    generated_by: str,
    generated_at: str,
) -> str:
    """Rebuild a hot-memory markdown document from section bodies.

    `sections` must be a dict mapping section name → body content (no
    heading). Missing required sections are emitted as placeholder-only
    bodies so the result still validates. Extra sections are preserved
    in the order they appear in `sections`.

    `generated_by` and `generated_at` are stamped into the frontmatter.
    The caller is expected to pass a current ISO-8601 timestamp; we don't
    generate one here to keep the function side-effect free.
    """
    parts: List[str] = []
    parts.append("---")
    parts.append(f"schema_version: {SCHEMA_VERSION}")
    parts.append(f"generated_by: {generated_by}")
    parts.append(f"generated_at: {generated_at}")
    parts.append("---")
    parts.append("")

    seen: Set[str] = set()
    # Emit required sections first, in canonical order.
    for name in REQUIRED_SECTIONS:
        body = sections.get(name, "").strip("\n")
        parts.append(f"## {name}")
        parts.append("")
        parts.append(body if body else "_pending_")
        parts.append("")
        seen.add(name)

    # Emit any extras (optional or unknown) in the caller's order.
    for name, body in sections.items():
        if name in seen:
            continue
        parts.append(f"## {name}")
        parts.append("")
        parts.append(body.strip("\n") if body.strip("\n") else "_pending_")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# INITIAL_TEMPLATE and initial_template()
# ---------------------------------------------------------------------------

def _initial_sections() -> Dict[str, str]:
    """Canonical section bodies for a fresh vault with no content yet.

    Deliberately short so the template fits well under the soft limit.
    Writers overwrite these on first real regenerate; the template just
    needs to exist and validate so `create_hot_memory_initial()` in
    setup_steps can land a file without calling the MCP server.
    """
    return {
        "Identity & Directive": (
            "You are the user's personal memory agent. Your job is to keep\n"
            "their vault coherent, surface what matters now, and route brain\n"
            "dumps to the right place without asking permission for obvious moves."
        ),
        "User Snapshot": (
            "_Pending first dream-protocol run._ The user snapshot is\n"
            "regenerated nightly from `me/profile.md`."
        ),
        "Top Deadlines": (
            "_None yet._ Deadlines surface here once `brain/deadlines.md` has\n"
            "entries — sorted by due-date ascending."
        ),
        "Urgent This Week": (
            "_None yet._ Populated from incomplete tasks in `brain/status.md`\n"
            "with `[due:: within 7 days]` or `[priority:: high]`."
        ),
        "Recent Activity": (
            "_None yet._ The last 3-5 non-trivial entries from `log.md` appear\n"
            "here after the first session."
        ),
        "Vault Layout": (
            "- `brain/` — agent-managed state (status, deadlines, goals, decisions)\n"
            "- `entities/` — one file per person/company/project\n"
            "- `inbox/` — user-drop zone, write-once, immutable via MCP\n"
            "- `archive/` — processed inbox, immutable via MCP\n"
            "- `me/profile.md` — user identity (name, roles, preferences)\n"
            "- `log.md` — append-only session + dream log"
        ),
        "Routing — When You Detect This, Do This": (
            "- Brain dump with mixed topics → ingest skill\n"
            "- Task with a date → `brain/status.md`\n"
            "- Hard deadline → `brain/deadlines.md`\n"
            "- New person/company/project → `entities/<slug>.md`\n"
            "- Big decision with rationale → `brain/decisions.md`"
        ),
        "File Pointers": (
            "None yet."
        ),
    }


def initial_template(generated_by: str, generated_at: str) -> str:
    """Return a fresh hot-memory markdown body stamped with the given
    provenance fields.

    Used by setup_steps.create_hot_memory_initial() so a brand-new vault
    gets a valid hot-memory.md without having to call the MCP server or
    the update script.
    """
    return assemble_document(
        _initial_sections(),
        generated_by=generated_by,
        generated_at=generated_at,
    )


# Module-level INITIAL_TEMPLATE uses a static timestamp so imports remain
# deterministic and side-effect-free. Writers that actually persist the
# template should call `initial_template()` with a current ISO timestamp.
INITIAL_TEMPLATE: str = initial_template(
    generated_by="hot-memory-schema-initial-template",
    generated_at="1970-01-01T00:00:00Z",
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(content: str) -> ValidationResult:
    """Check that `content` is a well-formed hot-memory.md document.

    Performs, in order:
      1. Non-empty content check
      2. Frontmatter presence + schema_version extraction
      3. Required-section presence
      4. Extra-section detection → warning
      5. Token-budget evaluation (soft → warning, hard → error)

    Returns a `ValidationResult`. `ok` is True iff there are zero errors;
    warnings alone never block.
    """
    errors: List[str] = []
    warnings: List[str] = []
    schema_version: Optional[int] = None

    if not content or not content.strip():
        errors.append("hot-memory content is empty")
        return ValidationResult(
            ok=False,
            schema_version=None,
            token_estimate=0,
            sections_found=[],
            missing_sections=list(REQUIRED_SECTIONS),
            extra_sections=[],
            errors=errors,
            warnings=warnings,
        )

    # Frontmatter
    fields, _body, fm_error = _split_frontmatter(content)
    if fm_error is not None:
        errors.append(fm_error)
    if fields is None:
        fields = {}

    raw_version = fields.get("schema_version")
    if raw_version is None:
        errors.append("frontmatter missing schema_version")
    else:
        try:
            schema_version = int(raw_version)
        except ValueError:
            errors.append(
                "frontmatter schema_version is not an integer: " + repr(raw_version)
            )

        if schema_version is not None:
            if schema_version > SCHEMA_VERSION:
                errors.append(
                    "unknown schema version "
                    + str(schema_version)
                    + " (expected "
                    + str(SCHEMA_VERSION)
                    + " or older)"
                )
            elif schema_version < SCHEMA_VERSION:
                warnings.append(
                    "schema version "
                    + str(schema_version)
                    + " is older than current "
                    + str(SCHEMA_VERSION)
                    + "; consider re-running dream-protocol"
                )

    if "generated_by" not in fields or not fields.get("generated_by"):
        warnings.append("frontmatter missing generated_by (who wrote this file?)")
    if "generated_at" not in fields or not fields.get("generated_at"):
        warnings.append("frontmatter missing generated_at (when was this written?)")

    # Sections
    sections = parse_sections(content)
    sections_found = list(sections.keys())
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    known = set(REQUIRED_SECTIONS) | set(OPTIONAL_SECTIONS)
    extras = [name for name in sections_found if name not in known]

    if missing:
        errors.append(
            "missing required sections: " + ", ".join(missing)
        )
    for name in extras:
        warnings.append("unknown extra section: " + name)

    # Token budget
    token_estimate = estimate_tokens(content)
    if token_estimate > TOKEN_HARD_LIMIT:
        errors.append(
            "token estimate "
            + str(token_estimate)
            + " exceeds hard limit "
            + str(TOKEN_HARD_LIMIT)
        )
    elif token_estimate > TOKEN_SOFT_LIMIT:
        warnings.append(
            "token estimate "
            + str(token_estimate)
            + " exceeds soft limit "
            + str(TOKEN_SOFT_LIMIT)
        )

    return ValidationResult(
        ok=not errors,
        schema_version=schema_version,
        token_estimate=token_estimate,
        sections_found=sections_found,
        missing_sections=missing,
        extra_sections=extras,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Convenience: current ISO timestamp (never called at import)
# ---------------------------------------------------------------------------

def current_iso_timestamp() -> str:
    """Return an ISO-8601 timestamp in UTC with a trailing 'Z'.

    Exposed so `update_hot_memory.py` and callers don't have to reach into
    `datetime` themselves. Not used at import time.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
