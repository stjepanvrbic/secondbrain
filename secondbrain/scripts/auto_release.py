#!/usr/bin/env python3
"""Helpers for automatic patch version bumps on pushes to main."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_MANIFEST = REPO_ROOT / "secondbrain" / ".claude-plugin" / "plugin.json"
VERSION_BUMP_COMMIT_RE = re.compile(r"^chore:\s+bump version to \d+\.\d+\.\d+$")


def parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def read_current_version(repo_root: Path = REPO_ROOT) -> str:
    manifest = json.loads((repo_root / "secondbrain" / ".claude-plugin" / "plugin.json").read_text())
    return manifest["version"]


def next_patch_version(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def compute_next_version(repo_root: Path = REPO_ROOT) -> str:
    return next_patch_version(read_current_version(repo_root))


def is_version_bump_commit_message(message: str) -> bool:
    return bool(VERSION_BUMP_COMMIT_RE.fullmatch(message.strip()))


def should_skip_auto_release(*, actor: str, head_commit_message: str) -> bool:
    return actor == "github-actions[bot]" and is_version_bump_commit_message(head_commit_message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto_release.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_version = subparsers.add_parser("next-version")
    next_version.add_argument("--repo-root", default=str(REPO_ROOT))

    skip = subparsers.add_parser("should-skip")
    skip.add_argument("--actor", required=True)
    skip.add_argument("--head-commit-message", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "next-version":
        print(compute_next_version(Path(args.repo_root).resolve()))
        return 0

    if args.command == "should-skip":
        print("1" if should_skip_auto_release(actor=args.actor, head_commit_message=args.head_commit_message) else "0")
        return 0

    raise AssertionError(f"unexpected command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
