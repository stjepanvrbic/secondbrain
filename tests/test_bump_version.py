"""Tests for bump_version.py — version consistency and bumping."""

import json
import textwrap
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from bump_version import (  # type: ignore[reportMissingImports]
    parse_version, bump_patch, set_version, get_all_versions,
    check_consistency, read_current_version, main,
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
        """All versions in the actual repo should be consistent."""
        ok, messages = check_consistency()
        # After our bump, everything should match
        assert ok, f"Version mismatch in repo: {messages}"

    def test_counts_all_files(self):
        versions = get_all_versions()
        # plugin.json + marketplace.json + 14 skills = 16
        assert len(versions) >= 16


class TestReadCurrentVersion:
    def test_reads_from_plugin_json(self):
        v = read_current_version()
        assert v.count(".") == 2
        major, _minor, _patch = parse_version(v)
        assert major >= 3


class TestSetVersion:
    def test_updates_all_files(self, tmp_path: Path):
        """Test set_version against a mock repo structure."""
        # Create mock plugin.json
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test", "version": "1.0.0"
        }))
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "name": "test",
            "owner": {"name": "test"},
            "metadata": {"description": "test", "version": "1.0.0"},
            "plugins": [{"name": "test", "version": "1.0.0", "source": "./"}]
        }))

        # Create mock skills
        for name in ["skill-a", "skill-b"]:
            skill_dir = tmp_path / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
                ---
                name: test
                metadata:
                  version: "1.0.0"
                ---
                # Test
            """))

        # Patch the module-level paths
        import bump_version  # type: ignore[reportMissingImports]
        orig_root = bump_version.REPO_ROOT
        orig_files = bump_version.VERSION_FILES
        orig_skills = bump_version.SKILLS_DIR

        try:
            bump_version.REPO_ROOT = tmp_path
            bump_version.VERSION_FILES = {
                "plugin.json": plugin_dir / "plugin.json",
                "marketplace.json": plugin_dir / "marketplace.json",
            }
            bump_version.SKILLS_DIR = tmp_path / "skills"

            changed = set_version("2.0.0")
            assert changed >= 3  # plugin.json + marketplace + 2 skills

            # Verify plugin.json
            data = json.loads((plugin_dir / "plugin.json").read_text())
            assert data["version"] == "2.0.0"

            # Verify marketplace.json
            data = json.loads((plugin_dir / "marketplace.json").read_text())
            assert data["plugins"][0]["version"] == "2.0.0"

            # Verify skills
            for name in ["skill-a", "skill-b"]:
                text = (tmp_path / "skills" / name / "SKILL.md").read_text()
                assert '"2.0.0"' in text
        finally:
            bump_version.REPO_ROOT = orig_root
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills

    def test_idempotent(self, tmp_path: Path):
        """Setting the same version twice changes nothing."""
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test", "version": "2.0.0"
        }))
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "name": "test",
            "owner": {"name": "test"},
            "metadata": {"description": "test", "version": "2.0.0"},
            "plugins": [{"name": "test", "version": "2.0.0", "source": "./"}]
        }))

        import bump_version  # type: ignore[reportMissingImports]
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
        """--check against real repo should pass (we just bumped)."""
        code = main(["--check"])
        assert code == 0

    def test_explicit_version(self, tmp_path: Path):
        """Test setting explicit version via CLI."""
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test", "version": "1.0.0"
        }))
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "name": "test",
            "owner": {"name": "test"},
            "metadata": {"description": "test", "version": "1.0.0"},
            "plugins": [{"name": "test", "version": "1.0.0", "source": "./"}]
        }))

        import bump_version  # type: ignore[reportMissingImports]
        orig_root = bump_version.REPO_ROOT
        orig_files = bump_version.VERSION_FILES
        orig_skills = bump_version.SKILLS_DIR

        try:
            bump_version.REPO_ROOT = tmp_path
            bump_version.VERSION_FILES = {
                "plugin.json": plugin_dir / "plugin.json",
                "marketplace.json": plugin_dir / "marketplace.json",
            }
            bump_version.SKILLS_DIR = tmp_path / "skills"
            (tmp_path / "skills").mkdir()

            code = main(["5.0.0"])
            assert code == 0
            data = json.loads((plugin_dir / "plugin.json").read_text())
            assert data["version"] == "5.0.0"
        finally:
            bump_version.REPO_ROOT = orig_root
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills
