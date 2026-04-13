#!/usr/bin/env python3
"""Pure helpers for pre-push release planning."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from typing import Optional


SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


@dataclasses.dataclass(frozen=True)
class ReleasePlan:
    action: str
    reason: str


def is_semver_tag(tag: str) -> bool:
    return bool(SEMVER_TAG_RE.match(tag))


def _parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def decide_release_action(
    *,
    current_version: str,
    latest_release_tag: Optional[str],
    expected_tag_exists: bool,
    expected_tag_points_at_head: bool,
) -> ReleasePlan:
    expected_tag = f"v{current_version}"

    if expected_tag_exists and expected_tag_points_at_head:
        return ReleasePlan("noop", f"{expected_tag} already points at HEAD")

    if expected_tag_exists and not expected_tag_points_at_head:
        if latest_release_tag == expected_tag:
            return ReleasePlan(
                "bump_and_release",
                f"HEAD moved past latest release {latest_release_tag} without a new version",
            )
        return ReleasePlan(
            "block",
            f"{expected_tag} exists but does not point at HEAD",
        )

    if latest_release_tag is None:
        return ReleasePlan("tag_only", "no prior semver release tag exists")

    if not is_semver_tag(latest_release_tag):
        return ReleasePlan("block", f"latest tag {latest_release_tag!r} is not semver")

    latest_version = latest_release_tag.lstrip("v")
    current_tuple = _parse_version(current_version)
    latest_tuple = _parse_version(latest_version)

    if current_tuple > latest_tuple:
        return ReleasePlan(
            "tag_only",
            f"version {current_version} is already ahead of latest release {latest_release_tag}",
        )

    if current_tuple == latest_tuple:
        return ReleasePlan(
            "bump_and_release",
            f"HEAD moved past latest release {latest_release_tag} without a new version",
        )

    return ReleasePlan(
        "block",
        f"current version {current_version} is behind latest release {latest_release_tag}",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_workflow.py")
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--latest-release-tag", default="")
    parser.add_argument("--expected-tag-exists", choices=("0", "1"), required=True)
    parser.add_argument("--expected-tag-points-at-head", choices=("0", "1"), required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    plan = decide_release_action(
        current_version=args.current_version,
        latest_release_tag=args.latest_release_tag or None,
        expected_tag_exists=args.expected_tag_exists == "1",
        expected_tag_points_at_head=args.expected_tag_points_at_head == "1",
    )
    print(json.dumps(dataclasses.asdict(plan)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
