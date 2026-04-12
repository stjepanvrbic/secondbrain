#!/usr/bin/env python3
"""
update_hot_memory.py — THE ONLY WRITER of `brain/hot-memory.md`.

Hot memory is a pre-computed "always loaded" context file the SessionStart
hook emits as a `systemMessage`. The Claude tool-level PreToolUse hook
(`enforce-immutability.sh`) blocks MCP writes to `brain/hot-memory.md`, so
the agent can never touch the file directly. This script bypasses that
block by talking to the Connect MCP server via `connect_mcp_client.py`
(pure Python, not a Claude tool), which is the sanctioned write path.

Two modes:

    --regenerate --vault <path>
        Assemble a fresh hot-memory from current vault state
        (me/profile.md, brain/status.md, brain/deadlines.md, tail of log.md)
        and write it via `client.vault_update()`. Used by dream-protocol
        nightly.

    --apply <draft.json> --vault <path>
        Apply section updates from a JSON draft produced by the ingest
        subagent (T13). Keeps all sections not mentioned in the draft
        unchanged and replaces the ones that are. Used after each
        session's ingest run.

Both modes MUST:
  - Validate the final document via `hot_memory_schema.validate()` before
    writing. If validation fails, exit non-zero and do NOT write — the
    existing file stays exactly as it was.
  - Handle `ConnectMCPUnreachable` gracefully (log to stderr, exit 1).
  - Stamp the `generated_by` and `generated_at` frontmatter fields.

Stdlib-only, Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Ensure sibling modules (connect_mcp_client, hot_memory_schema) are
# importable from anywhere the script is invoked from.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from connect_mcp_client import (  # type: ignore[reportMissingImports]
    ConnectMCPClient,
    ConnectMCPError,
    ConnectMCPNotFound,
    ConnectMCPUnreachable,
)
from hot_memory_schema import (  # type: ignore[reportMissingImports]
    REQUIRED_SECTIONS,
    assemble_document,
    current_iso_timestamp,
    initial_template,
    parse_sections,
    validate,
)


HOT_MEMORY_PATH = "brain/hot-memory.md"

# Files the minimal regenerator reads as source material. Each is optional —
# missing files fall back to a placeholder body so a fresh vault still
# produces a valid hot-memory.
SOURCE_PROFILE = "me/profile.md"
SOURCE_STATUS = "brain/status.md"
SOURCE_DEADLINES = "brain/deadlines.md"
SOURCE_LOG = "log.md"


# ---------------------------------------------------------------------------
# Client factory (dependency injection for tests)
# ---------------------------------------------------------------------------

ClientFactory = Callable[[], object]


def _default_client_factory() -> ConnectMCPClient:
    """Default factory — constructs a real ConnectMCPClient.

    Tests pass their own factory into `main(..., client_factory=...)`.
    """
    return ConnectMCPClient()


# ---------------------------------------------------------------------------
# Safe vault reads
# ---------------------------------------------------------------------------

def _read_optional(client, path: str) -> Optional[str]:
    """Read a vault file, returning None on NotFound.

    Used by the regenerator so missing sources turn into placeholders
    instead of hard-failing the whole regeneration.
    """
    try:
        return client.vault_read(path)
    except ConnectMCPNotFound:
        return None


# ---------------------------------------------------------------------------
# Minimal regenerator — assembles sections from raw source files
# ---------------------------------------------------------------------------

def _snippet_from_profile(raw: Optional[str]) -> str:
    """Identity & User Snapshot from me/profile.md.

    Strips frontmatter (naively — drops lines between the first pair of
    '---' delimiters) and returns the first few non-empty content lines.
    This is deliberately a minimal summary; the ingest subagent is the
    smart one and can overwrite it later. All we need is a valid body.
    """
    if not raw:
        return "_No profile yet. Add me/profile.md so the agent knows who you are._"

    cleaned: List[str] = []
    in_frontmatter = raw.lstrip().startswith("---")
    frontmatter_open_seen = False
    for line in raw.splitlines():
        stripped = line.rstrip()
        if in_frontmatter:
            if stripped == "---":
                if not frontmatter_open_seen:
                    frontmatter_open_seen = True
                    continue
                in_frontmatter = False
                continue
            continue
        if stripped:
            cleaned.append(stripped)

    if not cleaned:
        return "_profile present but empty_"
    # Take up to the first 3 non-empty content lines as a snippet.
    return "\n".join(cleaned[:3])


def _identity_section() -> str:
    return (
        "You are the user's personal memory agent. Keep their vault coherent,\n"
        "surface what matters now, and route brain dumps without asking\n"
        "permission for obvious moves."
    )


def _user_snapshot_section(profile: Optional[str]) -> str:
    snippet = _snippet_from_profile(profile)
    return snippet


def _extract_deadlines(raw: Optional[str], limit: int = 5) -> str:
    """Top deadlines: take the first `limit` bullet-like lines."""
    if not raw:
        return "_No deadlines yet._"
    out: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("-") or stripped.startswith("*"):
            out.append(stripped)
            if len(out) >= limit:
                break
    if not out:
        return "_No deadlines yet._"
    return "\n".join(out)


def _extract_urgent_tasks(raw: Optional[str], limit: int = 5) -> str:
    """Urgent this week: take the first `limit` uncompleted task lines."""
    if not raw:
        return "_No urgent tasks._"
    out: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("* [ ]"):
            out.append(stripped)
            if len(out) >= limit:
                break
    if not out:
        return "_No urgent tasks._"
    return "\n".join(out)


def _extract_recent_activity(raw: Optional[str], limit: int = 3) -> str:
    """Recent activity: take the last `limit` session entries from log.md.

    The log uses '## [timestamp]' headings — we collect them and keep the
    last N, then emit just the heading lines.
    """
    if not raw:
        return "_No recent activity._"
    headings: List[str] = []
    for line in raw.splitlines():
        if line.startswith("## ["):
            headings.append(line[3:].strip())
    if not headings:
        return "_No recent activity._"
    tail = headings[-limit:]
    return "\n".join("- " + h for h in tail)


def _vault_layout_section() -> str:
    return (
        "- `brain/` — agent-managed state (status, deadlines, goals, decisions)\n"
        "- `entities/` — one file per person/company/project\n"
        "- `inbox/` — user-drop zone, write-once, immutable via MCP\n"
        "- `archive/` — processed inbox, immutable via MCP\n"
        "- `me/profile.md` — user identity\n"
        "- `log.md` — append-only session + dream log"
    )


def _routing_section() -> str:
    return (
        "- Brain dump with mixed topics -> ingest skill\n"
        "- Task with a date -> `brain/status.md`\n"
        "- Hard deadline -> `brain/deadlines.md`\n"
        "- New person/company/project -> `entities/<slug>.md`\n"
        "- Big decision with rationale -> `brain/decisions.md`"
    )


def _file_pointers_section() -> str:
    return "None yet."


def _build_system_alerts(vault_path: Optional[Path]) -> Optional[str]:
    """Run deterministic health checks and return alerts as a bullet list.

    Returns None if no issues found (section is omitted from hot-memory).
    All checks are pure file-existence or version-comparison — zero agent
    reasoning needed.
    """
    alerts: List[str] = []

    if vault_path:
        vault = Path(vault_path)
        if (vault / "CLAUDE.md").exists():
            alerts.append(
                "Legacy `CLAUDE.md` at vault root — deprecated since v3.3.3, "
                "may pollute agent context. Safe to delete or archive."
            )

    vaults_config = Path.home() / ".config" / "secondbrain" / "vaults.json"
    if not vaults_config.exists():
        alerts.append(
            "`vaults.json` missing — session hooks disabled (no session logging, "
            "no per-turn commits, no immutability enforcement). "
            "Run `/secondbrain:init` or `/secondbrain:doctor` to fix."
        )

    if not alerts:
        return None
    return "\n".join(f"- {a}" for a in alerts)


def _build_regenerated_sections(client, vault_path: Optional[Path] = None) -> Dict[str, str]:
    """Query the vault and build a sections dict.

    Missing source files are tolerated — the corresponding section gets a
    placeholder body instead. This keeps the regenerator functional on
    fresh vaults where dream-protocol has not yet run.
    """
    profile = _read_optional(client, SOURCE_PROFILE)
    status = _read_optional(client, SOURCE_STATUS)
    deadlines = _read_optional(client, SOURCE_DEADLINES)
    log = _read_optional(client, SOURCE_LOG)

    sections = {
        "Identity & Directive": _identity_section(),
        "User Snapshot": _user_snapshot_section(profile),
        "Top Deadlines": _extract_deadlines(deadlines),
        "Urgent This Week": _extract_urgent_tasks(status),
        "Recent Activity": _extract_recent_activity(log),
        "Vault Layout": _vault_layout_section(),
        "Routing — When You Detect This, Do This": _routing_section(),
        "File Pointers": _file_pointers_section(),
    }

    # System Alerts: populated when critical health issues exist, omitted when clean.
    alerts_body = _build_system_alerts(vault_path)
    if alerts_body:
        sections["System Alerts"] = alerts_body

    return sections


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------

def _run_regenerate(client, vault_path: Optional[Path] = None) -> int:
    """Build + validate + write a fresh hot-memory.md.

    Returns 0 on success, 1 on validation failure. Network errors
    propagate to `main` as ConnectMCPError subclasses and are handled there.
    """
    sections = _build_regenerated_sections(client, vault_path=vault_path)
    doc = assemble_document(
        sections,
        generated_by="update_hot_memory.py --regenerate",
        generated_at=current_iso_timestamp(),
    )

    result = validate(doc)
    if not result.ok:
        sys.stderr.write(
            "error: regenerated hot-memory failed validation; refusing to write\n"
        )
        for err in result.errors:
            sys.stderr.write("  - " + err + "\n")
        return 1

    client.vault_update(HOT_MEMORY_PATH, doc)
    sys.stdout.write(
        "regenerated brain/hot-memory.md (tokens: "
        + str(result.token_estimate)
        + ")\n"
    )
    for warning in result.warnings:
        sys.stdout.write("  warning: " + warning + "\n")
    return 0


def _load_draft(draft_path: Path) -> Optional[dict]:
    """Load a JSON draft from disk, returning None on any parse/io error.

    Errors are written to stderr. The caller decides the exit code.
    """
    if not draft_path.exists():
        sys.stderr.write("error: draft file not found: " + str(draft_path) + "\n")
        return None
    try:
        raw = draft_path.read_text()
    except OSError as exc:
        sys.stderr.write(
            "error: cannot read draft " + str(draft_path) + ": " + str(exc) + "\n"
        )
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(
            "error: draft " + str(draft_path) + " is not valid JSON: " + str(exc) + "\n"
        )
        return None


def _run_apply(client, draft_path: Path) -> int:
    """Load a draft, patch hot-memory sections, validate, and write.

    Draft shape:
        {
          "section_updates": [
            {"section": "Top Deadlines", "content": "..."},
            ...
          ],
          "reason": "why this update is happening"
        }

    A section update may use either `content` (a string) or `items` (a
    list of strings, which we join with newlines — this matches the shape
    the T13 ingester subagent emits). Both are accepted so callers don't
    have to serialize their bullets themselves.
    """
    draft = _load_draft(draft_path)
    if draft is None:
        return 1
    if not isinstance(draft, dict):
        sys.stderr.write("error: draft root must be a JSON object\n")
        return 1

    updates = draft.get("section_updates")
    if not isinstance(updates, list):
        sys.stderr.write("error: draft.section_updates must be a list\n")
        return 1

    # Read the current hot-memory. If missing, bootstrap from INITIAL_TEMPLATE
    # so --apply works on a fresh vault.
    existing = _read_optional(client, HOT_MEMORY_PATH)
    if existing is None:
        existing = initial_template(
            generated_by="update_hot_memory.py --apply (bootstrap)",
            generated_at=current_iso_timestamp(),
        )
        bootstrap = True
    else:
        bootstrap = False

    sections = parse_sections(existing)
    # If parse_sections returned nothing (e.g. malformed existing file), fall
    # back to the canonical layout so we can still deposit the draft.
    if not sections or not set(REQUIRED_SECTIONS).issubset(sections.keys()):
        template = initial_template(
            generated_by="update_hot_memory.py --apply (repaired)",
            generated_at=current_iso_timestamp(),
        )
        sections = parse_sections(template)

    updated_count = 0
    for update in updates:
        if not isinstance(update, dict):
            sys.stderr.write("error: section_updates entries must be objects\n")
            return 1
        name = update.get("section")
        if not isinstance(name, str) or not name:
            sys.stderr.write("error: section_update missing 'section' key\n")
            return 1
        # Accept both `content` (string) and `items` (list of strings).
        # The T13 ingester emits items-style drafts; tests + manual
        # drafts tend to use content.
        content = update.get("content")
        items = update.get("items")
        if isinstance(items, list):
            body = "\n".join(str(item) for item in items)
        elif isinstance(content, list):
            body = "\n".join(str(item) for item in content)
        elif isinstance(content, str):
            body = content
        else:
            sys.stderr.write(
                "error: section_update must have 'content' (str/list) or 'items' (list) "
                "for section "
                + repr(name)
                + "\n"
            )
            return 1
        sections[name] = body
        updated_count += 1

    doc = assemble_document(
        sections,
        generated_by="update_hot_memory.py --apply",
        generated_at=current_iso_timestamp(),
    )

    result = validate(doc)
    if not result.ok:
        sys.stderr.write(
            "error: applied hot-memory failed validation; refusing to write\n"
        )
        for err in result.errors:
            sys.stderr.write("  - " + err + "\n")
        return 1

    if bootstrap:
        # No existing file — create it.
        client.vault_create(HOT_MEMORY_PATH, doc)
    else:
        client.vault_update(HOT_MEMORY_PATH, doc)
    sys.stdout.write(
        "updated "
        + str(updated_count)
        + " section(s) in brain/hot-memory.md (tokens: "
        + str(result.token_estimate)
        + ")\n"
    )
    for warning in result.warnings:
        sys.stdout.write("  warning: " + warning + "\n")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_hot_memory.py",
        description=(
            "Regenerate or patch brain/hot-memory.md via Connect MCP. "
            "Called by dream-protocol (--regenerate) and the ingest "
            "subagent (--apply <draft>)."
        ),
    )
    # Note: NOT using add_mutually_exclusive_group — argparse would call
    # sys.exit(2) internally instead of letting us return 1, which makes
    # test-level mode checks messier. We enforce mutual exclusion manually
    # in `main`.
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Build a fresh hot-memory from current vault state.",
    )
    parser.add_argument(
        "--apply",
        metavar="DRAFT",
        help="Apply section updates from the given JSON draft file.",
    )
    parser.add_argument(
        "--vault",
        required=False,
        help=(
            "Path to the vault directory (informational — the writer talks "
            "to MCP, which already knows the vault)."
        ),
    )
    return parser


def main(
    argv: Optional[List[str]] = None,
    client_factory: Optional[Callable[[], object]] = None,
) -> int:
    """Entry point usable from both the CLI and tests.

    Tests inject a fake `client_factory`; production code uses the default.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.regenerate and not args.apply:
        sys.stderr.write("error: one of --regenerate or --apply is required\n")
        return 1
    if args.regenerate and args.apply:
        sys.stderr.write("error: --regenerate and --apply are mutually exclusive\n")
        return 1
    if not args.vault:
        sys.stderr.write("error: --vault is required\n")
        return 1

    if client_factory is None:
        client_factory = _default_client_factory

    try:
        client = client_factory()
    except ConnectMCPUnreachable as exc:
        sys.stderr.write("error: Connect MCP unreachable: " + str(exc) + "\n")
        return 1
    except ConnectMCPError as exc:
        sys.stderr.write("error: Connect MCP error: " + str(exc) + "\n")
        return 1

    try:
        if args.regenerate:
            return _run_regenerate(client, vault_path=Path(args.vault) if args.vault else None)
        return _run_apply(client, Path(args.apply))
    except ConnectMCPUnreachable as exc:
        sys.stderr.write("error: Connect MCP unreachable: " + str(exc) + "\n")
        return 1
    except ConnectMCPError as exc:
        sys.stderr.write("error: Connect MCP error: " + str(exc) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
