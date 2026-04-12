"""Tests for `setup_steps.create_hot_memory_initial` (T14).

Seeding a fresh vault with a valid `brain/hot-memory.md` from the T10
`INITIAL_TEMPLATE` is the missing piece in the init → doctor → ingester
chain:

    - init runs once per vault, but historically did not create
      hot-memory.md. The first time the ingester or dream-protocol ran
      on a fresh vault, they'd fail the "hot-memory file missing" check.
    - doctor exposes a `hot_memory_schema` check, and T14 now wires
      `create_hot_memory_initial` as the treatment option for the
      "file missing" failure mode so `doctor --treat` can fix it without
      having to fall back to `/secondbrain:dream-protocol`.

The helper must be idempotent: re-running on a vault whose hot-memory.md
already validates MUST NOT touch the file and must return
`did_work=False`. Re-running on a vault with a corrupt (non-validating)
hot-memory.md MUST overwrite it — the initial template is strictly a
recovery path, so preserving a broken file would defeat the purpose.

This file extends the existing test_setup_steps.py surface rather than
inlining the tests there because the helper is new and cross-cuts with
T10's schema module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from setup_steps import create_hot_memory_initial  # type: ignore[reportMissingImports]  # noqa: E402
from hot_memory_schema import validate  # type: ignore[reportMissingImports]  # noqa: E402


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _read_hot_memory(vault: Path) -> str:
    return (vault / "brain" / "hot-memory.md").read_text()


class TestCreateHotMemoryInitialOnEmptyVault:
    def test_creates_file_when_missing(self, empty_vault: Path):
        result = create_hot_memory_initial(empty_vault)
        assert result.success is True
        assert result.did_work is True
        hot_memory = empty_vault / "brain" / "hot-memory.md"
        assert hot_memory.exists()

    def test_created_file_validates(self, empty_vault: Path):
        result = create_hot_memory_initial(empty_vault)
        assert result.success
        validation = validate(_read_hot_memory(empty_vault))
        assert validation.ok, (
            f"created hot-memory.md must validate cleanly — errors: "
            f"{validation.errors}"
        )

    def test_creates_parent_brain_dir(self, empty_vault: Path):
        """If brain/ doesn't exist yet, the helper must create it. Init's
        scaffold step usually handles this, but create_hot_memory_initial
        should be safe to call standalone.
        """
        # Vault has no brain/ yet.
        assert not (empty_vault / "brain").exists()
        result = create_hot_memory_initial(empty_vault)
        assert result.success
        assert (empty_vault / "brain").is_dir()


class TestCreateHotMemoryInitialIdempotence:
    def test_second_run_on_valid_file_is_noop(self, empty_vault: Path):
        # First run: creates the file.
        first = create_hot_memory_initial(empty_vault)
        assert first.did_work is True
        original = _read_hot_memory(empty_vault)

        # Second run: file is already valid, must NOT touch it.
        second = create_hot_memory_initial(empty_vault)
        assert second.success is True
        assert second.did_work is False
        # File content unchanged.
        assert _read_hot_memory(empty_vault) == original

    def test_second_run_preserves_user_edits_to_valid_file(self, empty_vault: Path):
        """If the user hand-edited the brief and it still validates,
        the helper must leave their edits alone — even if the edits
        differ from the template."""
        create_hot_memory_initial(empty_vault)
        hot_memory = empty_vault / "brain" / "hot-memory.md"

        original = hot_memory.read_text()
        # Tack on a user note inside one of the sections — still valid.
        edited = original.replace(
            "_None yet._",
            "_None yet._ User note: keep this file — it's mine.",
            1,
        )
        hot_memory.write_text(edited)

        # Re-run — it should be a no-op since the edited file still validates.
        result = create_hot_memory_initial(empty_vault)
        assert result.success is True
        assert result.did_work is False
        assert "User note: keep this file" in hot_memory.read_text()


class TestCreateHotMemoryInitialOverwritesCorrupt:
    def test_corrupt_file_is_overwritten(self, empty_vault: Path):
        """A file that doesn't validate (no frontmatter, wrong sections,
        etc.) is treated as recoverable garbage. The helper MUST
        overwrite it with the template so the next session-start hook
        doesn't inject malformed context.
        """
        brain = empty_vault / "brain"
        brain.mkdir(parents=True, exist_ok=True)
        corrupt = brain / "hot-memory.md"
        corrupt.write_text("# not a valid hot-memory — no frontmatter\n")

        result = create_hot_memory_initial(empty_vault)
        assert result.success is True
        assert result.did_work is True

        # New content must validate.
        validation = validate(corrupt.read_text())
        assert validation.ok, (
            f"overwritten hot-memory.md must validate — errors: "
            f"{validation.errors}"
        )

    def test_empty_file_is_overwritten(self, empty_vault: Path):
        brain = empty_vault / "brain"
        brain.mkdir(parents=True, exist_ok=True)
        (brain / "hot-memory.md").write_text("")

        result = create_hot_memory_initial(empty_vault)
        assert result.success is True
        assert result.did_work is True

        validation = validate(_read_hot_memory(empty_vault))
        assert validation.ok


class TestStepResultShape:
    def test_returns_stepresult(self, empty_vault: Path):
        result = create_hot_memory_initial(empty_vault)
        # Every setup_steps helper returns a StepResult — that's the
        # interface doctor's treatment dispatcher relies on.
        assert hasattr(result, "success")
        assert hasattr(result, "message")
        assert hasattr(result, "did_work")
        assert hasattr(result, "error")

    def test_success_message_mentions_hot_memory(self, empty_vault: Path):
        result = create_hot_memory_initial(empty_vault)
        assert "hot-memory" in result.message.lower()


class TestDoctorDispatch:
    """create_hot_memory_initial must be discoverable as a setup_steps
    attribute — the doctor treatment dispatcher looks it up via
    `getattr(setup_steps, result.fix_function)`."""

    def test_function_is_attribute_of_setup_steps(self):
        import setup_steps  # type: ignore[reportMissingImports]

        assert hasattr(setup_steps, "create_hot_memory_initial"), (
            "setup_steps must export create_hot_memory_initial so "
            "doctor's run_fixable_treatments can dispatch it by name."
        )
