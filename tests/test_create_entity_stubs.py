"""Tests for create_entity_stubs.py."""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from create_entity_stubs import main, kebab_to_display, extract_names_from_json  # type: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# kebab_to_display
# ---------------------------------------------------------------------------

class TestKebabToDisplay:
    def test_single_word(self):
        assert kebab_to_display("alice") == "Alice"

    def test_multi_word(self):
        assert kebab_to_display("john-petrizzo") == "John Petrizzo"

    def test_with_numbers(self):
        assert kebab_to_display("project-42-alpha") == "Project 42 Alpha"

    def test_company_name(self):
        assert kebab_to_display("acme-corp") == "Acme Corp"


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

class TestFileCreation:
    def test_creates_file_with_correct_frontmatter(self, tmp_vault: Path):
        main([str(tmp_vault), "new-person"])
        content = (tmp_vault / "entities" / "new-person.md").read_text()
        assert "type: unknown" in content
        assert "domains: []" in content
        assert "# New Person" in content
        assert "Stub" in content

    def test_creates_file_with_today_date(self, tmp_vault: Path):
        from datetime import date
        today = date.today().isoformat()
        main([str(tmp_vault), "new-person"])
        content = (tmp_vault / "entities" / "new-person.md").read_text()
        assert f"created: {today}" in content
        assert f"updated: {today}" in content

    def test_no_overwrite_existing(self, tmp_vault: Path):
        original = (tmp_vault / "entities" / "alice.md").read_text()
        main([str(tmp_vault), "alice"])
        assert (tmp_vault / "entities" / "alice.md").read_text() == original

    def test_no_overwrite_prints_skip(self, tmp_vault: Path, capsys):
        main([str(tmp_vault), "alice"])
        assert "SKIP" in capsys.readouterr().out

    def test_multiple_entities(self, tmp_vault: Path):
        main([str(tmp_vault), "first-entity", "second-entity", "third-entity"])
        for name in ("first-entity", "second-entity", "third-entity"):
            assert (tmp_vault / "entities" / f"{name}.md").exists()

    def test_mix_new_and_existing(self, tmp_vault: Path, capsys):
        main([str(tmp_vault), "alice", "brand-new"])
        out = capsys.readouterr().out
        assert "SKIP" in out
        assert "CREATED" in out
        assert (tmp_vault / "entities" / "brand-new.md").exists()

    def test_creates_entities_dir_if_missing(self, tmp_path: Path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        main([str(vault), "some-entity"])
        assert (vault / "entities" / "some-entity.md").exists()


# ---------------------------------------------------------------------------
# --from-json
# ---------------------------------------------------------------------------

class TestFromJson:
    @staticmethod
    def _write_verify_json(path: Path, missing_names: list) -> Path:
        data = {
            "timestamp": "2026-04-10T00:00:00+00:00",
            "checks": [
                {
                    "name": "entity-stubs",
                    "stats": {"missing_entities": len(missing_names)},
                    "issues": [
                        {
                            "check": "entity-stubs",
                            "severity": "error",
                            "file": f"entities/{n}.md",
                            "line": 0,
                            "message": f"Missing entity file — referenced by: brain/status.md",
                            "suggestion": f"Run: python3 scripts/create_entity_stubs.py <vault> {n}",
                        }
                        for n in missing_names
                    ],
                },
                {
                    "name": "wikilinks",
                    "stats": {},
                    "issues": [],
                },
            ],
            "summary": {"errors": len(missing_names), "warnings": 0, "info": 0},
        }
        json_file = path / "verify-output.json"
        json_file.write_text(json.dumps(data, indent=2))
        return json_file

    def test_parses_entity_names(self, tmp_path: Path):
        json_file = self._write_verify_json(tmp_path, ["charlie", "delta-force"])
        names = extract_names_from_json(json_file)
        assert names == ["charlie", "delta-force"]

    def test_creates_stubs_from_json(self, tmp_vault: Path):
        json_file = self._write_verify_json(tmp_vault, ["charlie", "delta-force"])
        main([str(tmp_vault), "--from-json", str(json_file)])
        assert (tmp_vault / "entities" / "charlie.md").exists()
        assert (tmp_vault / "entities" / "delta-force.md").exists()

    def test_ignores_non_entity_checks(self, tmp_path: Path):
        data = {
            "checks": [
                {"name": "wikilinks", "issues": [{"file": "brain/broken.md"}]},
            ],
        }
        json_file = tmp_path / "output.json"
        json_file.write_text(json.dumps(data))
        assert extract_names_from_json(json_file) == []

    def test_combined_with_positional_names(self, tmp_vault: Path):
        json_file = self._write_verify_json(tmp_vault, ["from-json-entity"])
        main([str(tmp_vault), "positional-entity", "--from-json", str(json_file)])
        assert (tmp_vault / "entities" / "positional-entity.md").exists()
        assert (tmp_vault / "entities" / "from-json-entity.md").exists()

    def test_missing_json_file(self, capsys):
        code = main(["/tmp", "--from-json", "/nonexistent/file.json"])
        assert code == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_nonexistent_vault(self, capsys):
        code = main(["/nonexistent/vault", "some-entity"])
        assert code == 1
        assert "not found" in capsys.readouterr().err

    def test_no_names_provided(self, tmp_vault: Path, capsys):
        code = main([str(tmp_vault)])
        assert code == 1
        assert "no entity names" in capsys.readouterr().err.lower()

    def test_success_exit_code(self, tmp_vault: Path):
        code = main([str(tmp_vault), "new-entity"])
        assert code == 0
