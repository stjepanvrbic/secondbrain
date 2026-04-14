"""Tests for the automatic main-branch version bump helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from auto_release import (  # type: ignore[reportMissingImports]
    is_version_bump_commit_message,
    next_patch_version,
    should_skip_auto_release,
)


class TestNextPatchVersion:
    def test_increments_latest_version_patch(self):
        assert next_patch_version("3.5.22") == "3.5.23"


class TestVersionBumpCommitMessageHelpers:
    def test_recognizes_version_bump_commit_message(self):
        assert is_version_bump_commit_message("chore: bump version to 3.5.23")
        assert not is_version_bump_commit_message("feat: improve doctor output")

    def test_skips_bot_authored_bump_loop(self):
        assert should_skip_auto_release(
            actor="github-actions[bot]",
            head_commit_message="chore: bump version to 3.5.23",
        )

    def test_does_not_skip_normal_main_push(self):
        assert not should_skip_auto_release(
            actor="stjepanvrbic",
            head_commit_message="fix: harden cowork runtime validation",
        )
