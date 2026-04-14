#!/usr/bin/env python3
"""Validate the shipped release ZIP and local Claude install smoke path."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
ZIP_PREFIX = "secondbrain/"
PLUGIN_MANIFEST = f"{ZIP_PREFIX}.claude-plugin/plugin.json"
FORBIDDEN_SEGMENTS = {"__pycache__"}
FORBIDDEN_SUFFIXES = (".pyc",)
FORBIDDEN_BASENAMES = {".DS_Store"}


def validate_release_zip(zip_path: Path) -> list[str]:
    """Return validation errors for a built release ZIP."""
    errors: list[str] = []

    if not zip_path.is_file():
        return [f"release ZIP does not exist: {zip_path}"]

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
            name_set = set(names)

            if not names:
                errors.append("release ZIP is empty")
                return errors

            for name in names:
                if not name.startswith(ZIP_PREFIX):
                    errors.append(
                        f"release ZIP entries must be under {ZIP_PREFIX}: found {name}"
                    )

                parts = Path(name).parts
                if any(segment in FORBIDDEN_SEGMENTS for segment in parts):
                    errors.append(f"release ZIP must not ship __pycache__ directories: {name}")
                if name.endswith(FORBIDDEN_SUFFIXES):
                    errors.append(f"release ZIP must not ship compiled Python files: {name}")
                if Path(name).name in FORBIDDEN_BASENAMES:
                    errors.append(f"release ZIP must not ship macOS metadata files: {name}")
                if name.startswith(f"{ZIP_PREFIX}tests/"):
                    errors.append(f"release ZIP must not ship tests/: {name}")

            if PLUGIN_MANIFEST not in name_set:
                errors.append(
                    f"release ZIP is missing required manifest: {PLUGIN_MANIFEST}"
                )
            else:
                try:
                    plugin_data = json.loads(zf.read(PLUGIN_MANIFEST))
                except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as exc:
                    errors.append(f"cannot read release plugin.json: {exc}")
                else:
                    if plugin_data.get("name") != "secondbrain":
                        errors.append("release plugin.json must declare name 'secondbrain'")
                    version = plugin_data.get("version")
                    if not isinstance(version, str) or version.count(".") != 2:
                        errors.append("release plugin.json must contain a semver version")
    except zipfile.BadZipFile as exc:
        errors.append(f"invalid ZIP file: {exc}")

    return errors


def build_release_zip(repo_root: Path, output_path: Path) -> None:
    """Build the exact ZIP shape the GitHub release workflow ships."""
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "archive",
            "--format=zip",
            "--prefix=secondbrain/",
            "-o",
            str(output_path),
            "HEAD:secondbrain",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def run_claude_marketplace_smoke(repo_root: Path, claude_bin: str) -> None:
    """Exercise the actual Claude CLI marketplace + install path locally."""
    if shutil.which(claude_bin) is None:
        raise RuntimeError(f"{claude_bin!r} is not on PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["HOME"] = tmpdir

        commands: Sequence[list[str]] = (
            [claude_bin, "plugins", "marketplace", "add", str(repo_root)],
            [claude_bin, "plugins", "install", "secondbrain@secondbrain"],
            [claude_bin, "plugins", "update", "secondbrain@secondbrain"],
        )

        for command in commands:
            subprocess.run(
                command,
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate_distribution.py")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repo root to validate/build from",
    )
    parser.add_argument(
        "--zip-path",
        help="Existing release ZIP to validate. If omitted, build one from HEAD:secondbrain",
    )
    parser.add_argument(
        "--claude-smoke",
        action="store_true",
        help="Run a local Claude CLI marketplace/add install smoke test in a temp HOME",
    )
    parser.add_argument(
        "--claude-bin",
        default="claude",
        help="Claude CLI binary to use for smoke tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(args.zip_path).resolve() if args.zip_path else Path(tmpdir) / "release.zip"
        if not args.zip_path:
            build_release_zip(repo_root, zip_path)

        errors = validate_release_zip(zip_path)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

        if args.claude_smoke:
            try:
                run_claude_marketplace_smoke(repo_root, args.claude_bin)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                print(f"Claude marketplace smoke test failed: {exc}", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
