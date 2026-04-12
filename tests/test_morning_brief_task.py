"""String-contract tests for the morning-brief scheduled task (T14).

T14 ships a new bundled scheduled task at
    secondbrain/scheduled-tasks/morning-brief/SKILL.md

It fires at 08:00 daily (cron `0 8 * * *`) and dispatches the
`secondbrain-morning-brief` subagent, which produces
`brain/morning-brief.md`. The file the test checks is a lightweight
discovery manifest — the real work lives in the subagent definition
(tested separately in test_morning_brief_subagent_def.py).

Scope:
    - File exists at the expected path
    - Frontmatter has a `name:` matching the task name
    - Frontmatter description describes what the task does
    - Body references the secondbrain-morning-brief subagent
    - The MANIFEST.md in scheduled-tasks/ includes a row for this task
      with the 08:00 cron
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"
TASK_DIR = PLUGIN_ROOT / "scheduled-tasks" / "morning-brief"
TASK_FILE = TASK_DIR / "SKILL.md"
MANIFEST = PLUGIN_ROOT / "scheduled-tasks" / "MANIFEST.md"


@pytest.fixture(scope="module")
def task_text() -> str:
    assert TASK_FILE.is_file(), (
        f"morning-brief scheduled-task SKILL.md must exist at {TASK_FILE}; "
        f"create it during T14."
    )
    return TASK_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest_text() -> str:
    assert MANIFEST.is_file()
    return MANIFEST.read_text(encoding="utf-8")


class TestFilePresence:
    def test_directory_exists(self):
        assert TASK_DIR.is_dir(), (
            f"scheduled-tasks/morning-brief directory must exist at "
            f"{TASK_DIR}"
        )

    def test_file_exists(self):
        assert TASK_FILE.is_file()

    def test_file_not_empty(self, task_text: str):
        assert len(task_text.strip()) > 0


class TestFrontmatter:
    def test_has_frontmatter(self, task_text: str):
        assert task_text.startswith("---\n")
        assert "\n---" in task_text[4:]

    def test_name_field_matches_task_name(self, task_text: str):
        assert "name: morning-brief" in task_text, (
            "scheduled-task SKILL.md must declare `name: morning-brief` "
            "so init's CronCreate registration uses the right key."
        )

    def test_description_field_present(self, task_text: str):
        assert "description:" in task_text

    def test_description_mentions_morning_brief(self, task_text: str):
        # Extract the description line (or block) and check it.
        low = task_text.lower()
        assert "morning" in low


class TestBodyDispatchesSubagent:
    def test_body_references_subagent_name(self, task_text: str):
        """The body must name the `secondbrain-morning-brief` subagent
        so the main agent session knows which agent to dispatch when the
        cron fires.
        """
        assert "secondbrain-morning-brief" in task_text, (
            "scheduled-task body must reference the secondbrain-morning-"
            "brief subagent so CronCreate's prompt can route to it."
        )


class TestManifestEntry:
    def test_manifest_lists_morning_brief_task(self, manifest_text: str):
        assert "morning-brief" in manifest_text, (
            "scheduled-tasks/MANIFEST.md must include a row for the "
            "morning-brief task so /secondbrain:init picks it up."
        )

    def test_manifest_has_8am_cron_for_morning_brief(self, manifest_text: str):
        """The cron string `0 8 * * *` means '08:00 every day'. We look
        for it in the same line as 'morning-brief' so we don't false-
        positive on the existing 10:30 morning-briefing row.
        """
        lines = manifest_text.splitlines()
        found = False
        for line in lines:
            if "morning-brief" in line and "morning-briefing" not in line:
                # This is the new task row — must have the 08:00 cron.
                if "0 8 * * *" in line:
                    found = True
                    break
        assert found, (
            "MANIFEST.md must include a row for the `morning-brief` task "
            "with cron `0 8 * * *` (08:00 daily). The existing "
            "`morning-briefing` row at 10:30am is a distinct task."
        )
