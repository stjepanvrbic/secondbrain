"""Unit tests for entity_resolver.py."""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from entity_resolver import (  # type: ignore[reportMissingImports]
    EntityRegistry,
    load_entity_records,
)


class TestEntityRegistry:
    def test_resolve_existing_uses_alias_frontmatter(self, tmp_vault: Path):
        (tmp_vault / "entities" / "minsky-mccormick-hallagan.md").write_text(
            """---
type: company
aliases: [MMH]
---
# Minsky McCormick & Hallagan
"""
        )

        registry = EntityRegistry.from_vault(tmp_vault, tmp_vault.rglob("*.md"))
        match = registry.resolve_existing("MMH")

        assert match is not None
        assert match.record.slug == "minsky-mccormick-hallagan"
        assert match.kind == "alias"

    def test_parent_fallback_suggests_parent_entity(self, tmp_vault: Path):
        (tmp_vault / "entities" / "stairhopper.md").write_text("# Stairhopper\n")

        registry = EntityRegistry.from_vault(tmp_vault, tmp_vault.rglob("*.md"))
        assert registry.resolve_existing("stairhopper movers") is None

        match = registry.suggest_canonical("stairhopper movers")
        assert match is not None
        assert match.record.slug == "stairhopper"
        assert match.kind == "parent"

    def test_expand_search_returns_exact_entity_then_parent(self, tmp_vault: Path):
        (tmp_vault / "entities" / "prime-trading.md").write_text("# Prime Trading\n")
        (tmp_vault / "entities" / "prime-trading-team.md").write_text(
            """---
parent_entity: prime-trading
---
# Prime Trading Team
"""
        )

        records = EntityRegistry.from_vault(tmp_vault, tmp_vault.rglob("*.md")).expand_search("prime-trading-team")

        assert [record.slug for record in records] == ["prime-trading-team", "prime-trading"]

    def test_fuzzy_match_requires_clear_winner(self, tmp_vault: Path):
        (tmp_vault / "entities" / "stairhopper.md").write_text("# Stairhopper\n")

        registry = EntityRegistry.from_vault(tmp_vault, tmp_vault.rglob("*.md"))
        match = registry.suggest_canonical("stairhoppr")

        assert match is not None
        assert match.record.slug == "stairhopper"
        assert match.kind == "fuzzy"

    def test_long_form_alias_is_extracted_for_acronym_entities(self, tmp_vault: Path):
        (tmp_vault / "entities" / "mmh.md").write_text("# MMH\n\nMinsky McCormick & Hallagan.\n")

        records = load_entity_records(tmp_vault, tmp_vault.rglob("*.md"))
        record = next(entity for entity in records if entity.slug == "mmh")

        assert record.long_form_alias == "Minsky McCormick & Hallagan"
