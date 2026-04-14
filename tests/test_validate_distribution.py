"""Tests for release/distribution validation helpers."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from validate_distribution import main, validate_release_zip  # type: ignore[reportMissingImports]


def _write_zip(tmp_path: Path, members: dict[str, str]) -> Path:
    zip_path = tmp_path / "secondbrain.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return zip_path


class TestValidateReleaseZip:
    def test_accepts_valid_shipped_layout(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/skills/init/SKILL.md": "# init\n",
                "secondbrain/scripts/init_obsidian.py": "print('ok')\n",
            },
        )

        assert validate_release_zip(zip_path) == []

    def test_rejects_missing_plugin_manifest(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/skills/init/SKILL.md": "# init\n",
            },
        )

        errors = validate_release_zip(zip_path)

        assert any(".claude-plugin/plugin.json" in error for error in errors)

    def test_rejects_unprefixed_repo_root_files(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "README.md": "# repo root leak\n",
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("must be under secondbrain/" in error for error in errors)

    def test_rejects_tests_in_release_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/tests/test_something.py": "def test_nope(): pass\n",
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("must not ship tests/" in error for error in errors)


class TestMain:
    def test_main_returns_zero_for_valid_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
            },
        )

        assert main(["--zip-path", str(zip_path)]) == 0

    def test_main_returns_nonzero_for_invalid_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "README.md": "# broken\n",
            },
        )

        assert main(["--zip-path", str(zip_path)]) == 1
