#!/usr/bin/env python3
"""
archive_contradiction.py — Soft-archive contradicted content in an Obsidian vault.

Python 3.8+, zero external dependencies.

Used by dream-protocol Phase 3.12. Writes a superseded-content archive file
plus a sidecar capturing the superseded content, the new content, where the
new content came from, and why it supersedes the original. The original live
file is NEVER touched by this script — the caller edits it in place after
the archive + sidecar are in place.

Uses direct filesystem calls rather than MCP, so it bypasses the
enforce-immutability.sh hook that blocks all MCP writes to archive/*.

Usage:
    python3 archive_contradiction.py /path/to/vault \\
        --original-file brain/status.md \\
        --section-anchor "Acme Renewal" \\
        --new-content-file /tmp/new.md \\
        --source-description "2026-04-10 session log, direct from Alice" \\
        --reasoning "New info from the account owner supersedes the stale note" \\
        --subject "acme-renewal-date"

    python3 archive_contradiction.py /path/to/vault \\
        --original-file brain/stale-note.md \\
        --new-content-file /tmp/new.md \\
        --source-description "..." --reasoning "..." --subject "..." \\
        --dry-run

On success, prints a single JSON line to stdout with the final archive and
sidecar paths (relative to the vault) so callers can embed them in backlinks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

SLUG_MAX_LEN = 60


def slugify(subject: str) -> str:
    """Kebab-case, lowercase, alphanumeric + hyphens only, trimmed to SLUG_MAX_LEN."""
    s = subject.strip().lower()
    # Replace any non-alphanumeric run with a single hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Collapse repeated hyphens and strip leading/trailing hyphens
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "untitled"
    if len(s) > SLUG_MAX_LEN:
        s = s[:SLUG_MAX_LEN].rstrip("-")
    return s


def extract_section(content: str, anchor: str) -> Optional[str]:
    """
    Return the text of the section whose heading matches `anchor`, including
    the heading line itself, stopping at the next heading of the same or
    higher level. Returns None if no matching heading is found.
    """
    lines = content.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
    start_idx = None
    start_level = 0
    anchor_norm = anchor.strip().lower()

    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if not m:
            continue
        level = len(m.group(1))
        text = m.group(2).strip().lower()
        if text == anchor_norm:
            start_idx = i
            start_level = level
            break

    if start_idx is None:
        return None

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m = heading_re.match(lines[j])
        if m and len(m.group(1)) <= start_level:
            end_idx = j
            break

    # Trim trailing blank lines in the extracted section
    section_lines = lines[start_idx:end_idx]
    while section_lines and section_lines[-1].strip() == "":
        section_lines.pop()
    return "\n".join(section_lines) + "\n"


def unique_archive_stem(target_dir: Path, base_slug: str) -> str:
    """
    Return a slug such that `<target_dir>/<slug>.md` and `.sidecar.md` do not
    yet exist. Append `-<N>` for the smallest positive integer that avoids
    any collision on either filename.
    """
    def in_use(slug: str) -> bool:
        return (target_dir / f"{slug}.md").exists() or (target_dir / f"{slug}.sidecar.md").exists()

    if not in_use(base_slug):
        return base_slug
    n = 1
    while True:
        candidate = f"{base_slug}-{n}"
        # If appending causes overflow, trim the base to keep within limit.
        if len(candidate) > SLUG_MAX_LEN:
            trimmed = base_slug[: SLUG_MAX_LEN - len(f"-{n}")].rstrip("-")
            candidate = f"{trimmed}-{n}"
        if not in_use(candidate):
            return candidate
        n += 1


def build_sidecar(
    archived_ts: str,
    original_rel: str,
    subject: str,
    superseded: str,
    new_content: str,
    source: str,
    reasoning: str,
) -> str:
    return (
        "---\n"
        "type: contradiction-sidecar\n"
        f"archived: {archived_ts}\n"
        f"original-path: {original_rel}\n"
        f"subject: {subject}\n"
        "---\n"
        "\n"
        "## Superseded content\n"
        "\n"
        f"{superseded.rstrip()}\n"
        "\n"
        "## New content\n"
        "\n"
        f"{new_content.rstrip()}\n"
        "\n"
        "## Source\n"
        "\n"
        f"{source.rstrip()}\n"
        "\n"
        "## Reasoning\n"
        "\n"
        f"{reasoning.rstrip()}\n"
    )


def archive_contradiction(
    vault: Path,
    original_file: Path,
    section_anchor: Optional[str],
    new_content_file: Path,
    source_description: str,
    reasoning: str,
    subject: str,
    dry_run: bool = False,
) -> Tuple[Path, Path, str]:
    """
    Core operation. Returns (archive_path, sidecar_path, final_slug).

    Raises FileNotFoundError / ValueError on invalid inputs; those are
    converted to exit codes in main().
    """
    if not original_file.exists():
        raise FileNotFoundError(f"original file does not exist: {original_file}")
    if not new_content_file.exists():
        raise FileNotFoundError(f"new-content file does not exist: {new_content_file}")

    original_text = original_file.read_text(encoding="utf-8", errors="replace")
    new_content = new_content_file.read_text(encoding="utf-8", errors="replace")

    if section_anchor:
        superseded = extract_section(original_text, section_anchor)
        if superseded is None:
            raise ValueError(
                f"section anchor not found in {original_file}: '{section_anchor}'"
            )
    else:
        superseded = original_text

    now = datetime.now()
    month = now.strftime("%Y-%m")
    archived_ts = now.strftime("%Y-%m-%dT%H:%M")

    target_dir = vault / "archive" / "contradictions" / month
    base_slug = slugify(subject)

    # Compute unique stem — this has to consider existing files even in dry-run
    # so the output is accurate. mkdir is deferred in dry-run.
    if target_dir.exists():
        final_slug = unique_archive_stem(target_dir, base_slug)
    else:
        final_slug = base_slug

    archive_path = target_dir / f"{final_slug}.md"
    sidecar_path = target_dir / f"{final_slug}.sidecar.md"

    try:
        original_rel = str(original_file.resolve().relative_to(vault.resolve()))
    except ValueError:
        # Original file is outside the vault — fall back to the raw path
        original_rel = str(original_file)

    sidecar_body = build_sidecar(
        archived_ts=archived_ts,
        original_rel=original_rel,
        subject=subject,
        superseded=superseded,
        new_content=new_content,
        source=source_description,
        reasoning=reasoning,
    )

    if dry_run:
        print(f"  WOULD CREATE: {archive_path.relative_to(vault)}")
        print(f"  WOULD CREATE: {sidecar_path.relative_to(vault)}")
        return archive_path, sidecar_path, final_slug

    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(superseded, encoding="utf-8")
    sidecar_path.write_text(sidecar_body, encoding="utf-8")
    print(f"  CREATED: {archive_path.relative_to(vault)}")
    print(f"  CREATED: {sidecar_path.relative_to(vault)}")
    return archive_path, sidecar_path, final_slug


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Soft-archive contradicted vault content into archive/contradictions/YYYY-MM/.",
    )
    parser.add_argument("vault", type=Path, help="Path to Obsidian vault root")
    parser.add_argument(
        "--original-file",
        type=Path,
        required=True,
        help="Path to the file whose content is being superseded",
    )
    parser.add_argument(
        "--section-anchor",
        type=str,
        default=None,
        help="If provided, archive only the section with this heading text (smallest coherent unit)",
    )
    parser.add_argument(
        "--new-content-file",
        type=Path,
        required=True,
        help="Path to a file holding the new content that supersedes the original",
    )
    parser.add_argument(
        "--source-description",
        type=str,
        required=True,
        help="Where the new info came from (session log, email, entity page, etc.)",
    )
    parser.add_argument(
        "--reasoning",
        type=str,
        required=True,
        help="Why the new content supersedes the original",
    )
    parser.add_argument(
        "--subject",
        type=str,
        required=True,
        help="Short subject used to slug the archive filename",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print what would happen without writing",
    )
    args = parser.parse_args(argv)

    vault = args.vault.resolve()
    if not vault.is_dir():
        print(f"Error: vault path is not a directory: {vault}", file=sys.stderr)
        return 1

    print(
        f"Archiving contradiction in {vault}"
        + (" (dry run)" if args.dry_run else "")
    )

    try:
        archive_path, sidecar_path, final_slug = archive_contradiction(
            vault=vault,
            original_file=args.original_file,
            section_anchor=args.section_anchor,
            new_content_file=args.new_content_file,
            source_description=args.source_description,
            reasoning=args.reasoning,
            subject=args.subject,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(
            f"Error: permission denied while writing archive files: {exc}",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"Error: filesystem error: {exc}", file=sys.stderr)
        return 1

    # Emit machine-readable result so dream-protocol can thread the paths
    # into the live-file backlink.
    result = {
        "archive_path": str(archive_path.relative_to(vault)),
        "sidecar_path": str(sidecar_path.relative_to(vault)),
        "slug": final_slug,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
