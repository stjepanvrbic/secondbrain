"""Tests for release workflow state planning.

pre-push must make one deterministic decision based on repo state:
  - noop: current version tag already points at HEAD
  - tag_only: user manually bumped version but forgot the tag
  - bump_and_release: HEAD moved past the latest release without a new version
  - block: inconsistent or ambiguous release state
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from release_workflow import decide_release_action, is_semver_tag  # type: ignore[reportMissingImports]


class TestIsSemverTag:
    def test_accepts_v_prefixed_semver(self):
        assert is_semver_tag("v3.5.11")

    def test_rejects_non_semver_tags(self):
        assert not is_semver_tag("latest")
        assert not is_semver_tag("release-3.5.11")
        assert not is_semver_tag("v3.5")


class TestDecideReleaseAction:
    def test_noop_when_current_tag_points_at_head(self):
        plan = decide_release_action(
            current_version="3.5.11",
            latest_release_tag="v3.5.11",
            expected_tag_exists=True,
            expected_tag_points_at_head=True,
        )
        assert plan.action == "noop"

    def test_tag_only_when_user_already_bumped_version(self):
        plan = decide_release_action(
            current_version="3.5.12",
            latest_release_tag="v3.5.11",
            expected_tag_exists=False,
            expected_tag_points_at_head=False,
        )
        assert plan.action == "tag_only"

    def test_bump_and_release_when_head_moved_past_latest_release(self):
        plan = decide_release_action(
            current_version="3.5.11",
            latest_release_tag="v3.5.11",
            expected_tag_exists=False,
            expected_tag_points_at_head=False,
        )
        assert plan.action == "bump_and_release"

    def test_bump_and_release_when_latest_tag_exists_on_prior_commit(self):
        plan = decide_release_action(
            current_version="3.5.11",
            latest_release_tag="v3.5.11",
            expected_tag_exists=True,
            expected_tag_points_at_head=False,
        )
        assert plan.action == "bump_and_release"

    def test_block_when_expected_tag_exists_but_not_at_head(self):
        plan = decide_release_action(
            current_version="3.5.12",
            latest_release_tag="v3.5.11",
            expected_tag_exists=True,
            expected_tag_points_at_head=False,
        )
        assert plan.action == "block"
