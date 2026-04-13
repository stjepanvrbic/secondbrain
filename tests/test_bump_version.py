"""Tests for bump_version.py — version consistency, bumping, tagging, and release."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from bump_version import (  # type: ignore[reportMissingImports]
    parse_version, bump_patch, set_version, get_all_versions,
    check_consistency, read_current_version, main,
    create_tag, release,
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
        # marketplace metadata + marketplace plugin version + 13 skills = 15
        assert len(versions) >= 15


class TestReadCurrentVersion:
    def test_reads_from_marketplace_json(self):
        v = read_current_version()
        assert v.count(".") == 2
        parsed = parse_version(v)
        assert parsed[0] >= 3


class TestSetVersion:
    def test_updates_all_files(self, tmp_path: Path):
        """Test set_version against a mock repo structure."""
        # Create mock plugin.json
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test"
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
        import bump_version  # pyright: ignore[reportMissingImports]
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
            assert changed >= 3  # marketplace + 2 skills

            # Verify plugin.json
            data = json.loads((plugin_dir / "plugin.json").read_text())
            assert "version" not in data

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
            "name": "test"
        }))
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "name": "test",
            "owner": {"name": "test"},
            "metadata": {"description": "test", "version": "2.0.0"},
            "plugins": [{"name": "test", "version": "2.0.0", "source": "./"}]
        }))

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
        """--check against real repo should pass (we just bumped)."""
        code = main(["--check"])
        assert code == 0

    def test_explicit_version(self, tmp_path: Path):
        """Test setting explicit version via CLI."""
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test"
        }))
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "name": "test",
            "owner": {"name": "test"},
            "metadata": {"description": "test", "version": "1.0.0"},
            "plugins": [{"name": "test", "version": "1.0.0", "source": "./"}]
        }))

        import bump_version  # pyright: ignore[reportMissingImports]
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
            assert "version" not in data
            marketplace = json.loads((plugin_dir / "marketplace.json").read_text())
            assert marketplace["plugins"][0]["version"] == "5.0.0"
        finally:
            bump_version.REPO_ROOT = orig_root
            bump_version.VERSION_FILES = orig_files
            bump_version.SKILLS_DIR = orig_skills


class TestCreateTag:
    """Tests for the create_tag() function used in the release pipeline."""

    def test_refuses_dirty_tree(self):
        """create_tag must refuse if working tree has uncommitted changes."""
        dirty_result = MagicMock()
        dirty_result.stdout = "M some-file.py\n"

        with patch("bump_version._git") as mock_git:
            mock_git.return_value = dirty_result
            rc = create_tag("9.9.9")
        assert rc == 1

    def test_refuses_existing_tag(self):
        """create_tag must refuse if the tag already exists."""
        call_count = 0

        def fake_git(*args):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if args[0] == "status":
                result.stdout = ""  # clean tree
            elif args[0] == "tag" and args[1] == "-l":
                result.stdout = "v9.9.9\n"  # tag exists
            else:
                result.stdout = ""
            return result

        with patch("bump_version._git", side_effect=fake_git), \
             patch("bump_version.check_consistency", return_value=(True, ["ok"])):
            rc = create_tag("9.9.9")
        assert rc == 1

    def test_happy_path(self):
        """create_tag creates an annotated tag when preconditions are met."""
        calls: list[tuple[str, ...]] = []

        def fake_git(*args):
            calls.append(args)
            result = MagicMock()
            if args[0] == "status":
                result.stdout = ""  # clean tree
            elif args[0] == "tag" and args[1] == "-l":
                result.stdout = ""  # no existing tag
            else:
                result.stdout = ""
            return result

        with patch("bump_version._git", side_effect=fake_git), \
             patch("bump_version.check_consistency", return_value=(True, ["ok"])):
            rc = create_tag("9.9.9")
        assert rc == 0
        # Verify git tag -a was called
        tag_calls = [c for c in calls if c[0] == "tag" and "-a" in c]
        assert len(tag_calls) == 1
        assert "v9.9.9" in tag_calls[0]


class TestRelease:
    """Tests for the release() function — full pipeline: bump + commit + tag."""

    def test_release_bumps_commits_and_tags(self):
        """release() must call set_version, stage only release files, commit, and tag."""
        calls: list[tuple[str, ...]] = []

        def fake_git(*args):
            calls.append(args)
            result = MagicMock()
            result.stdout = ""
            return result

        with patch("bump_version._git", side_effect=fake_git), \
             patch("bump_version.set_version", return_value=1) as mock_set, \
             patch("bump_version.check_consistency", return_value=(True, ["ok"])), \
             patch("bump_version.read_current_version", return_value="1.0.0"), \
             patch("bump_version.create_tag", return_value=0) as mock_tag:
            rc = release("2.0.0")

        assert rc == 0
        mock_set.assert_called_once_with("2.0.0")
        mock_tag.assert_called_once_with("2.0.0")
        # Verify git add and git commit were called
        add_calls = [c for c in calls if c[0] == "add"]
        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(add_calls) == 1
        assert len(commit_calls) == 1
        assert "-u" not in add_calls[0], (
            "release() must not use `git add -u`; that can capture unrelated tracked changes"
        )
        assert "Bump to 2.0.0" in commit_calls[0][-1]

    def test_release_auto_bumps_patch(self):
        """release(None) should auto-bump the patch version."""
        with patch("bump_version._git") as mock_git, \
             patch("bump_version.set_version", return_value=1) as mock_set, \
             patch("bump_version.check_consistency", return_value=(True, ["ok"])), \
             patch("bump_version.read_current_version", return_value="3.5.0"), \
             patch("bump_version.create_tag", return_value=0):
            mock_git.return_value = MagicMock(stdout="")
            rc = release(None)

        assert rc == 0
        mock_set.assert_called_once_with("3.5.1")
