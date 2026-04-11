"""Tests for hot_memory_schema.py — schema + validator for brain/hot-memory.md.

The hot-memory file is a pre-computed "always-loaded" context file that the
SessionStart hook emits as a systemMessage. It replaces the current pattern of
the agent burning tool calls loading context at session start.

Two writers maintain it:
  - dream-protocol nightly: full regenerate from current vault state
  - ingest subagent (T13): incremental updates for critical content

The schema module is side-effect-free and importable; it exposes the validator
and canonical section layout. Tests exercise the validator on:
  - Valid content from INITIAL_TEMPLATE (must pass)
  - Missing frontmatter → error
  - Unknown schema version (future) → error
  - Missing required sections → error
  - Extra unknown sections → warning, not error
  - Over hard token limit → error
  - Over soft but under hard → warning
  - parse_sections round-trip through an arbitrary document
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from hot_memory_schema import (  # type: ignore[reportMissingImports]
    INITIAL_TEMPLATE,
    OPTIONAL_SECTIONS,
    REQUIRED_SECTIONS,
    SCHEMA_VERSION,
    TOKEN_HARD_LIMIT,
    TOKEN_SOFT_LIMIT,
    ValidationResult,
    assemble_document,
    estimate_tokens,
    initial_template,
    parse_sections,
    validate,
)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_schema_version_is_1(self):
        assert SCHEMA_VERSION == 1

    def test_required_sections_count_is_eight(self):
        assert len(REQUIRED_SECTIONS) == 8

    def test_required_sections_order(self):
        assert REQUIRED_SECTIONS == [
            "Identity & Directive",
            "User Snapshot",
            "Top Deadlines",
            "Urgent This Week",
            "Recent Activity",
            "Vault Layout",
            "Routing — When You Detect This, Do This",
            "File Pointers",
        ]

    def test_optional_sections_exist(self):
        # At minimum, Active Project Context must be listed (appended by
        # session-start hook at runtime). Morning Brief Status is allowed.
        assert "Active Project Context" in OPTIONAL_SECTIONS
        assert "Morning Brief Status" in OPTIONAL_SECTIONS

    def test_soft_limit_is_1200(self):
        assert TOKEN_SOFT_LIMIT == 1200

    def test_hard_limit_is_1500(self):
        assert TOKEN_HARD_LIMIT == 1500

    def test_hard_limit_greater_than_soft_limit(self):
        assert TOKEN_HARD_LIMIT > TOKEN_SOFT_LIMIT


# ---------------------------------------------------------------------------
# estimate_tokens — Claude's conservative approximation is len(text) / 4.
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("abcd") == 1

    def test_approximation_is_len_divided_by_four(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_partial_division_floors(self):
        # len("abcde") == 5, 5//4 == 1
        assert estimate_tokens("abcde") == 1

    def test_realistic_text(self):
        text = "The quick brown fox jumps over the lazy dog."
        assert estimate_tokens(text) == len(text) // 4


# ---------------------------------------------------------------------------
# INITIAL_TEMPLATE — must itself be a valid hot-memory.md file
# ---------------------------------------------------------------------------

class TestInitialTemplate:
    def test_initial_template_is_a_string(self):
        assert isinstance(INITIAL_TEMPLATE, str)
        assert INITIAL_TEMPLATE.strip()

    def test_initial_template_has_frontmatter(self):
        assert INITIAL_TEMPLATE.startswith("---\n")

    def test_initial_template_declares_schema_version(self):
        assert "schema_version: 1" in INITIAL_TEMPLATE

    def test_initial_template_has_generated_by_field(self):
        assert "generated_by:" in INITIAL_TEMPLATE

    def test_initial_template_has_generated_at_field(self):
        assert "generated_at:" in INITIAL_TEMPLATE

    def test_initial_template_validates_cleanly(self):
        result = validate(INITIAL_TEMPLATE)
        assert result.ok, (
            f"INITIAL_TEMPLATE must pass validation out of the box. "
            f"errors={result.errors}, warnings={result.warnings}"
        )

    def test_initial_template_under_soft_limit(self):
        result = validate(INITIAL_TEMPLATE)
        assert result.token_estimate < TOKEN_SOFT_LIMIT, (
            f"INITIAL_TEMPLATE should stay well under the soft limit; "
            f"got {result.token_estimate} tokens"
        )

    def test_initial_template_has_all_required_sections(self):
        result = validate(INITIAL_TEMPLATE)
        for section in REQUIRED_SECTIONS:
            assert section in result.sections_found

    def test_initial_template_function_accepts_fields(self):
        """initial_template(generated_by=..., generated_at=...) returns a
        parameterized version of INITIAL_TEMPLATE so callers can stamp their
        own 'generated_by' and ISO timestamp.
        """
        doc = initial_template(
            generated_by="test-writer",
            generated_at="2026-04-11T10:00:00Z",
        )
        assert "generated_by: test-writer" in doc
        assert "generated_at: 2026-04-11T10:00:00Z" in doc
        assert validate(doc).ok


# ---------------------------------------------------------------------------
# validate — happy path
# ---------------------------------------------------------------------------

class TestValidateHappyPath:
    def test_valid_content_passes(self):
        result = validate(INITIAL_TEMPLATE)
        assert result.ok is True
        assert result.errors == []
        assert result.schema_version == SCHEMA_VERSION

    def test_valid_content_finds_all_sections(self):
        result = validate(INITIAL_TEMPLATE)
        assert set(result.sections_found) >= set(REQUIRED_SECTIONS)
        assert result.missing_sections == []


# ---------------------------------------------------------------------------
# validate — frontmatter errors
# ---------------------------------------------------------------------------

class TestValidateFrontmatter:
    def test_missing_frontmatter_fails(self):
        content = _body_only()
        result = validate(content)
        assert result.ok is False
        assert any("frontmatter" in err.lower() for err in result.errors)

    def test_unterminated_frontmatter_fails(self):
        content = "---\nschema_version: 1\n# missing closing delimiter\n\n## Identity & Directive\n"
        result = validate(content)
        assert result.ok is False
        assert any("frontmatter" in err.lower() for err in result.errors)

    def test_missing_schema_version_fails(self):
        content = "---\ngenerated_by: test\ngenerated_at: 2026-04-11\n---\n" + _body_only()
        result = validate(content)
        assert result.ok is False
        assert any("schema_version" in err.lower() for err in result.errors)

    def test_unknown_future_schema_version_fails(self):
        content = (
            "---\nschema_version: 99\ngenerated_by: test\ngenerated_at: 2026-04-11\n---\n"
            + _body_only()
        )
        result = validate(content)
        assert result.ok is False
        assert any(
            "schema" in err.lower() and ("99" in err or "unknown" in err.lower())
            for err in result.errors
        )

    def test_older_schema_version_warns(self):
        # Older schema = known but deprecated; allowed to pass with a warning.
        # Since SCHEMA_VERSION is 1, "older" means 0.
        content = (
            "---\nschema_version: 0\ngenerated_by: test\ngenerated_at: 2026-04-11\n---\n"
            + _body_only()
        )
        result = validate(content)
        # Not an error; reported as a warning.
        assert any("schema" in warning.lower() for warning in result.warnings)

    def test_missing_generated_by_is_warning(self):
        content = (
            "---\nschema_version: 1\ngenerated_at: 2026-04-11\n---\n"
            + _body_only()
        )
        result = validate(content)
        # generated_by is a soft requirement — we warn but still validate.
        assert any("generated_by" in warning.lower() for warning in result.warnings) or any(
            "generated_by" in err.lower() for err in result.errors
        )


# ---------------------------------------------------------------------------
# validate — required sections
# ---------------------------------------------------------------------------

class TestValidateSections:
    def test_all_required_sections_passes(self):
        # Content with all required sections present.
        result = validate(INITIAL_TEMPLATE)
        assert result.missing_sections == []

    def test_missing_required_sections_fails(self):
        # Remove "File Pointers" entirely from the template.
        content = INITIAL_TEMPLATE.replace(
            "## File Pointers\n\nNone yet.\n",
            "",
        )
        result = validate(content)
        assert result.ok is False
        assert "File Pointers" in result.missing_sections
        assert any("file pointers" in err.lower() for err in result.errors)

    def test_extra_unknown_section_is_warning(self):
        content = INITIAL_TEMPLATE + "\n## Something Exotic\n\nCool stuff.\n"
        result = validate(content)
        # Warning, not error — don't reject; the schema should be forward-compat.
        assert result.ok is True
        assert any("Something Exotic" in warning for warning in result.warnings)
        assert "Something Exotic" in result.extra_sections

    def test_optional_section_does_not_warn(self):
        content = INITIAL_TEMPLATE + "\n## Active Project Context\n\nNone.\n"
        result = validate(content)
        assert result.ok is True
        # OPTIONAL_SECTIONS must not trigger the "extra" warning.
        assert not any("Active Project Context" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# validate — token budget
# ---------------------------------------------------------------------------

class TestValidateTokenBudget:
    def test_under_soft_limit_passes_cleanly(self):
        result = validate(INITIAL_TEMPLATE)
        assert result.token_estimate < TOKEN_SOFT_LIMIT
        # No token warnings.
        assert not any("token" in warning.lower() for warning in result.warnings)

    def test_over_soft_but_under_hard_warns(self):
        # Compute filler so the final doc lands at roughly the midpoint of
        # soft..hard, independent of the template's own token count.
        base_tokens = estimate_tokens(INITIAL_TEMPLATE)
        target_tokens = (TOKEN_SOFT_LIMIT + TOKEN_HARD_LIMIT) // 2  # midpoint
        needed_tokens = max(0, target_tokens - base_tokens)
        filler_size = needed_tokens * 4
        filler = "x" * filler_size
        content = INITIAL_TEMPLATE.replace(
            "## File Pointers\n\nNone yet.\n",
            "## File Pointers\n\n" + filler + "\n",
        )
        result = validate(content)
        assert result.token_estimate > TOKEN_SOFT_LIMIT
        assert result.token_estimate < TOKEN_HARD_LIMIT
        assert any("token" in warning.lower() for warning in result.warnings)
        # Still OK — warning only.
        assert result.ok is True

    def test_over_hard_limit_fails(self):
        # Pad a section with enough text to exceed the hard limit.
        filler_size = (TOKEN_HARD_LIMIT + 500) * 4
        filler = "x" * filler_size
        content = INITIAL_TEMPLATE.replace(
            "## File Pointers\n\nNone yet.\n",
            "## File Pointers\n\n" + filler + "\n",
        )
        result = validate(content)
        assert result.token_estimate > TOKEN_HARD_LIMIT
        assert any("token" in err.lower() for err in result.errors)
        assert result.ok is False


# ---------------------------------------------------------------------------
# validate — misc
# ---------------------------------------------------------------------------

class TestValidateMisc:
    def test_empty_content_fails(self):
        result = validate("")
        assert result.ok is False
        assert result.errors

    def test_whitespace_only_fails(self):
        result = validate("   \n\n  \n")
        assert result.ok is False
        assert result.errors


# ---------------------------------------------------------------------------
# parse_sections — structural parser used by update_hot_memory.py
# ---------------------------------------------------------------------------

class TestParseSections:
    def test_extracts_all_required_sections(self):
        sections = parse_sections(INITIAL_TEMPLATE)
        for name in REQUIRED_SECTIONS:
            assert name in sections

    def test_returns_section_bodies_without_heading(self):
        sections = parse_sections(INITIAL_TEMPLATE)
        for body in sections.values():
            # Heading line itself should not appear in the body.
            for name in REQUIRED_SECTIONS:
                assert not body.startswith("## " + name)
                assert not body.startswith("# " + name)

    def test_preserves_section_order(self):
        sections = parse_sections(INITIAL_TEMPLATE)
        # Dict insertion order is preserved in Python 3.7+.
        observed = [k for k in sections.keys() if k in REQUIRED_SECTIONS]
        # The sections should appear in REQUIRED_SECTIONS order (since the
        # INITIAL_TEMPLATE is assembled in that order).
        assert observed == REQUIRED_SECTIONS

    def test_handles_empty_section_body(self):
        content = (
            "---\nschema_version: 1\ngenerated_by: t\ngenerated_at: 2026-04-11\n---\n\n"
            "## First\n\n"
            "## Second\n\nBody of second.\n"
        )
        sections = parse_sections(content)
        assert sections["First"].strip() == ""
        assert sections["Second"].strip() == "Body of second."

    def test_strips_frontmatter(self):
        sections = parse_sections(INITIAL_TEMPLATE)
        # Frontmatter keys must not leak into any section body.
        for body in sections.values():
            assert "schema_version" not in body
            assert "generated_by" not in body

    def test_section_body_preserves_internal_content(self):
        content = (
            "---\nschema_version: 1\ngenerated_by: t\ngenerated_at: 2026-04-11\n---\n\n"
            "## Identity & Directive\n\n"
            "Line 1\nLine 2\n\n- Bullet\n"
        )
        sections = parse_sections(content)
        body = sections["Identity & Directive"]
        assert "Line 1" in body
        assert "Line 2" in body
        assert "- Bullet" in body


# ---------------------------------------------------------------------------
# assemble_document — reverse of parse_sections
# ---------------------------------------------------------------------------

class TestAssembleDocument:
    def test_round_trip_through_parse_sections(self):
        sections = parse_sections(INITIAL_TEMPLATE)
        # assemble_document needs sections in the correct order.
        ordered = {name: sections[name] for name in REQUIRED_SECTIONS}
        doc = assemble_document(
            ordered,
            generated_by="test",
            generated_at="2026-04-11T00:00:00Z",
        )
        result = validate(doc)
        assert result.ok, f"round-trip must validate: {result.errors}"

    def test_result_contains_all_sections(self):
        sections = {name: "content for " + name for name in REQUIRED_SECTIONS}
        doc = assemble_document(
            sections,
            generated_by="test",
            generated_at="2026-04-11T00:00:00Z",
        )
        for name in REQUIRED_SECTIONS:
            assert "## " + name in doc

    def test_result_has_updated_frontmatter(self):
        sections = {name: "stub" for name in REQUIRED_SECTIONS}
        doc = assemble_document(
            sections,
            generated_by="updater-xyz",
            generated_at="2026-04-11T10:30:00Z",
        )
        assert "schema_version: 1" in doc
        assert "generated_by: updater-xyz" in doc
        assert "generated_at: 2026-04-11T10:30:00Z" in doc


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_default_fields(self):
        r = ValidationResult(
            ok=True,
            schema_version=1,
            token_estimate=100,
            sections_found=[],
            missing_sections=[],
            extra_sections=[],
            errors=[],
            warnings=[],
        )
        assert r.ok is True
        assert r.schema_version == 1
        assert r.token_estimate == 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body_only() -> str:
    """Build a well-formed body (without frontmatter) containing all required
    sections. Used to isolate frontmatter failures from section failures.
    """
    parts = []
    for name in REQUIRED_SECTIONS:
        parts.append("## " + name)
        parts.append("")
        parts.append("placeholder")
        parts.append("")
    return "\n".join(parts) + "\n"
