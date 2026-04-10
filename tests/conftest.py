"""Shared test fixtures for secondbrain vault scripts."""

import os
import textwrap
import time
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Minimal valid vault — all required dirs and critical files with minimal content."""
    dirs = ["brain", "entities", "me", "inbox", "archive", "archive/inbox", "scratch"]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    (tmp_path / "brain" / "status.md").write_text(textwrap.dedent("""\
        ---
        updated: 2026-04-10
        ---
        # Status

        ## Today's Plan — 2026-04-10

        - [ ] Review PR [[entities/alice|Alice]] [due:: 2026-04-10] [energy:: medium] [est:: 30min]
    """))

    (tmp_path / "brain" / "deadlines.md").write_text("# Deadlines\n")
    (tmp_path / "brain" / "goals.md").write_text("# Goals\n")
    (tmp_path / "brain" / "decisions.md").write_text("# Decisions\n")
    (tmp_path / "brain" / "session-log.md").write_text("# Session Log\n")
    (tmp_path / "me" / "profile.md").write_text("# Profile\n\nName: Test User\n")
    (tmp_path / "glossary.md").write_text("# Glossary\n")
    (tmp_path / "log.md").write_text(textwrap.dedent("""\
        # Log

        ## [2026-04-10 10:00] session-start | Morning session
        Loaded context, built day plan.
    """))

    (tmp_path / "_MANIFEST.md").write_text(textwrap.dedent("""\
        # Vault Manifest

        **Files:** 10
        **Last updated:** 2026-04-10
    """))

    (tmp_path / "entities" / "alice.md").write_text(textwrap.dedent("""\
        ---
        type: person
        domains: [work]
        created: 2026-04-01
        updated: 2026-04-10
        ---
        # Alice

        Colleague on the platform team.
    """))

    return tmp_path


@pytest.fixture
def populated_vault(tmp_vault: Path) -> Path:
    """Richer vault with multiple entities, tasks, inbox items, and cross-links."""
    (tmp_vault / "entities" / "bob.md").write_text(textwrap.dedent("""\
        ---
        type: person
        domains: [work]
        created: 2026-04-01
        updated: 2026-04-05
        ---
        # Bob

        Engineering manager. See [[entities/alice|Alice]] for shared projects.
    """))

    (tmp_vault / "entities" / "acme-corp.md").write_text(textwrap.dedent("""\
        ---
        type: company
        domains: [work]
        created: 2026-03-15
        updated: 2026-04-08
        ---
        # Acme Corp

        Employer. Main contact: [[entities/bob|Bob]].
    """))

    (tmp_vault / "brain" / "status.md").write_text(textwrap.dedent("""\
        ---
        updated: 2026-04-10
        ---
        # Status

        ## Today's Plan — 2026-04-10

        - [ ] Review PR [[entities/alice|Alice]] [due:: 2026-04-10] [energy:: medium] [est:: 30min]
        - [ ] Call [[entities/bob|Bob]] about [[entities/acme-corp|Acme]] deal [due:: 2026-04-11] [energy:: high] [est:: 1hr]
        - [x] Send invoice to [[entities/acme-corp|Acme]] [due:: 2026-04-09] [done:: 2026-04-09]

        ## Ongoing

        - [ ] Prepare quarterly report [[entities/acme-corp|Acme]] [energy:: high] [est:: 2hr]
    """))

    (tmp_vault / "inbox" / "note-2026-04-08.md").write_text(textwrap.dedent("""\
        ---
        created: 2026-04-08
        ---
        # Quick note

        [processed:: true]

        Remember to check the deployment status.
    """))

    (tmp_vault / "inbox" / "note-2026-04-10.md").write_text(textwrap.dedent("""\
        ---
        created: 2026-04-10
        ---
        # Fresh note

        Need to schedule meeting with [[entities/alice|Alice]].
    """))

    return tmp_vault


@pytest.fixture
def broken_vault(tmp_vault: Path) -> Path:
    """Vault with intentional issues for testing detection."""
    # Broken wikilink — references entity that doesn't exist
    (tmp_vault / "brain" / "status.md").write_text(textwrap.dedent("""\
        ---
        updated: 2026-04-10
        ---
        # Status

        ## Today's Plan — 2026-04-10

        - [ ] Review PR [[entities/alice|Alice]] [due:: 2026-04-10] [energy:: medium] [est:: 30min]
        - [ ] Call [[entities/charlie|Charlie]] [due:: bad-date] [energy:: invalid] [est:: 99hr]

        ## Today's Plan — 2026-04-10

        - [ ] Duplicate section content
    """))

    # Missing entity referenced above: charlie doesn't exist

    # Stale inbox item — backdate mtime to 30 days ago
    old_note = tmp_vault / "inbox" / "old-note.md"
    old_note.write_text(textwrap.dedent("""\
        ---
        created: 2026-03-01
        ---
        # Old unprocessed note

        This has been sitting here for over a month.
    """))
    old_time = time.time() - (30 * 86400)
    os.utime(old_note, (old_time, old_time))

    # Wrong manifest count
    (tmp_vault / "_MANIFEST.md").write_text(textwrap.dedent("""\
        # Vault Manifest

        **Files:** 999
        **Last updated:** 2026-04-10
    """))

    return tmp_vault
