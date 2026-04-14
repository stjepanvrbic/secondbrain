"""Tests for bump_version.py."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from bump_version import (  # type: ignore[reportMissingImports]
    bump_patch,
    check_consistency,
    get_all_versions,
    main,
    parse_version,
    read_current_version,
    set_version,
)


class TestParseVersion:
    def test_basic(self):
        assert parse_version("3.0.0") == (3, 0, 0)

    def test_double_digits(self):
        assert parse_version("12.34.56") == (12, 34, 56)


class TestBumpPatch:
    def test_basic(self):
        assert bump_patch("3.0.0") == "3.0.1"

    def test_rolls_over(self):
        assert bump_patch("3.0.9") == "3.0.10"

    def test_high_patch(self):
        assert bump_patch("1.2.99") == "1.2.100"


class TestCheckConsistency:
    def test_real_repo(self):
        ok, messages = check_consistency()
        assert ok, f"Version mismatch in repo: {messages}"

    def test_counts_all_managed_version_references(self):
        versions = get_all_versions()
        # plugin.json + marketplace metadata + marketplace plugin version + 13 skills
        assert len(versions) >= 16


class TestReadCurrentVersion:
    def test_reads_from_plugin_manifest(self):
        version = read_current_version()
        assert version.count(".") == 2
        assert parse_version(version)[0] >= 3


class TestSetVersion:
    def test_updates_all_managed_files(self, tmp_path: Path):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "1.0.0"}))
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "test",
                    "owner": {"name": "test"},
                    "metadata": {"description": "test", "version": "1.0.0"},
                    "plugins": [{"name": "test", "version": "1.0.0", "source": "./"}],
                }
            )
        )

        for name in ["skill-a", "skill-b"]:
            skill_dir = tmp_path / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                textwrap.dedent(
                    """\
                    ---
                    name: test
                    metadata:
                      version: "1.0.0"
                    ---
                    # Test
                    """
                )
            )

        import bump_version  # pyright: ignore[reportMissingImports]

        orig_files = bump_version.VERSION_FILES
        orig_skills = bump_version.SKILLS_DIR

        try:
            bump_version.VERSION_FILES = {
                "plugin.json": plugin_dir / "plugin.json",
                "marketplace.json": plugin_dir / "marketplace.json",
            }
            bump_version.SKILLS_DIR = tmp_path / "skills"

            changed = set_version("2.0.0")

            assert changed == 5

            plugin = json.loads((plugin_dir / "plugin.json").read_text())
            assert plugin["version"] == "2.0.0"

            marketplace = json.loads((plugin_dir / "marketplace.json").read_text())
            assert marketplace["metadata"]["version"] == "2.0.0"
            assert marketplace["plugins"][0]["version"] == "2.0.0"

            for name in ["skill-a", "skill-b"]:
                text = (tmp_path / "skills" / name / "SKILL.md").read_text()
                assert '"2.0.0"' in text
        finally:
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills

    def test_idempotent(self, tmp_path: Path):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "2.0.0"}))
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "test",
                    "owner": {"name": "test"},
                    "metadata": {"description": "test", "version": "2.0.0"},
                    "plugins": [{"name": "test", "version": "2.0.0", "source": "./"}],
                }
            )
        )

        import bump_version  # pyright: ignore[reportMissingImports]

        orig_files = bump_version.VERSION_FILES
        orig_skills = bump_version.SKILLS_DIR

        try:
            bump_version.VERSION_FILES = {
                "plugin.json": plugin_dir / "plugin.json",
                "marketplace.json": plugin_dir / "marketplace.json",
            }
            bump_version.SKILLS_DIR = tmp_path / "skills"
            (tmp_path / "skills").mkdir()

            changed = set_version("2.0.0")
            assert changed == 0
        finally:
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills


class TestMain:
    def test_check_mode(self):
        assert main(["--check"]) == 0

    def test_current_mode(self):
        code = main(["--current"])
        assert code == 0

    def test_explicit_version(self, tmp_path: Path):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "1.0.0"}))
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "test",
                    "owner": {"name": "test"},
                    "metadata": {"description": "test", "version": "1.0.0"},
                    "plugins": [{"name": "test", "version": "1.0.0", "source": "./"}],
                }
            )
        )

        import bump_version  # pyright: ignore[reportMissingImports]

        orig_files = bump_version.VERSION_FILES
        orig_skills = bump_version.SKILLS_DIR

        try:
            bump_version.VERSION_FILES = {
                "plugin.json": plugin_dir / "plugin.json",
                "marketplace.json": plugin_dir / "marketplace.json",
            }
            bump_version.SKILLS_DIR = tmp_path / "skills"
            (tmp_path / "skills").mkdir()

            code = main(["5.0.0"])

            assert code == 0

            plugin = json.loads((plugin_dir / "plugin.json").read_text())
            assert plugin["version"] == "5.0.0"

            marketplace = json.loads((plugin_dir / "marketplace.json").read_text())
            assert marketplace["metadata"]["version"] == "5.0.0"
            assert marketplace["plugins"][0]["version"] == "5.0.0"
        finally:
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills
