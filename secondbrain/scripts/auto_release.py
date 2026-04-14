#!/usr/bin/env python3
"""Helpers for automatic patch releases on pushes to main."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
RELEASE_COMMIT_RE = re.compile(r"^release:\s+v\d+\.\d+\.\d+$")


def _parse_tag(tag: str) -> tuple[int, int, int]:
    match = SEMVER_TAG_RE.fullmatch(tag)
    if not match:
        raise ValueError(f"not a semver tag: {tag!r}")
    return tuple(int(part) for part in match.groups())


def is_semver_tag(tag: str) -> bool:
    return bool(SEMVER_TAG_RE.fullmatch(tag))


def latest_semver_tag(tags: Iterable[str]) -> Optional[str]:
    semver_tags = [tag for tag in tags if is_semver_tag(tag)]
    if not semver_tags:
        return None
    return max(semver_tags, key=_parse_tag)


def next_patch_version(tag: str) -> str:
    major, minor, patch = _parse_tag(tag)
    return f"{major}.{minor}.{patch + 1}"


def release_asset_name(version: str) -> str:
    return f"secondbrain-v{version}.zip"


def is_release_commit_message(message: str) -> bool:
    return bool(RELEASE_COMMIT_RE.fullmatch(message.strip()))


def should_skip_auto_release(*, actor: str, head_commit_message: str) -> bool:
    return actor == "github-actions[bot]" and is_release_commit_message(head_commit_message)


def git_tags(repo_root: Path = REPO_ROOT) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", "v*", "--sort=-v:refname"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def compute_next_release_version(repo_root: Path = REPO_ROOT) -> str:
    tag = latest_semver_tag(git_tags(repo_root))
    if tag is None:
        raise RuntimeError("repository has no semver release tag")
    return next_patch_version(tag)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto_release.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_version = subparsers.add_parser("next-version")
    next_version.add_argument("--repo-root", default=str(REPO_ROOT))

    asset = subparsers.add_parser("release-asset")
    asset.add_argument("version")

    skip = subparsers.add_parser("should-skip")
    skip.add_argument("--actor", required=True)
    skip.add_argument("--head-commit-message", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "next-version":
        print(compute_next_release_version(Path(args.repo_root).resolve()))
        return 0

    if args.command == "release-asset":
        print(release_asset_name(args.version))
        return 0

    if args.command == "should-skip":
        print("1" if should_skip_auto_release(actor=args.actor, head_commit_message=args.head_commit_message) else "0")
        return 0

    raise AssertionError(f"unexpected command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
