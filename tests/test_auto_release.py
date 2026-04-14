"""Tests for the automatic main-branch release helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from auto_release import (  # type: ignore[reportMissingImports]
    is_release_commit_message,
    latest_semver_tag,
    next_patch_version,
    release_asset_name,
    should_skip_auto_release,
)


class TestLatestSemverTag:
    def test_picks_highest_semver_tag(self):
        assert latest_semver_tag(["foo", "v3.5.21", "v3.5.22", "v3.4.99"]) == "v3.5.22"

    def test_ignores_non_semver_tags(self):
        assert latest_semver_tag(["latest", "release-3.5.22"]) is None


class TestNextPatchVersion:
    def test_increments_latest_tag_patch(self):
        assert next_patch_version("v3.5.22") == "3.5.23"


class TestReleaseCommitMessageHelpers:
    def test_recognizes_release_commit_message(self):
        assert is_release_commit_message("release: v3.5.23")
        assert not is_release_commit_message("feat: improve doctor output")

    def test_release_asset_name_matches_semver(self):
        assert release_asset_name("3.5.23") == "secondbrain-v3.5.23.zip"

    def test_skips_bot_authored_release_loop(self):
        assert should_skip_auto_release(
            actor="github-actions[bot]",
            head_commit_message="release: v3.5.23",
        )

    def test_does_not_skip_normal_main_push(self):
        assert not should_skip_auto_release(
            actor="stjepanvrbic",
            head_commit_message="fix: harden cowork runtime validation",
        )
