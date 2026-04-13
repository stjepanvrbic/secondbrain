"""Scheduled-task inventory should come from MANIFEST.md, not stale prose."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "secondbrain" / "scheduled-tasks" / "MANIFEST.md"
FILES_TO_KEEP_HONEST = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "ARCHITECTURE.md",
    REPO_ROOT / "secondbrain" / "skills" / "doctor" / "SKILL.md",
    REPO_ROOT / "secondbrain" / "skills" / "init" / "SKILL.md",
]


def _manifest_task_count() -> int:
    rows = 0
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ") and line.count("|") >= 4 and "Cron" not in line and "---" not in line:
            rows += 1
    return rows


def test_manifest_current_task_count():
    assert _manifest_task_count() == 7


def test_docs_do_not_hardcode_old_six_task_inventory():
    stale_fragments = (
        "6 scheduled tasks",
        "all 6 scheduled tasks",
        "the 6 bundled",
        "verify the 6 bundled",
    )
    offenders: list[str] = []
    for path in FILES_TO_KEEP_HONEST:
        text = path.read_text(encoding="utf-8")
        for fragment in stale_fragments:
            if fragment in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {fragment}")
    assert not offenders, (
        "scheduled-task inventory must come from MANIFEST.md; stale hardcoded counts found:\n"
        + "\n".join(offenders)
    )
