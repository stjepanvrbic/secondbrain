#!/usr/bin/env python3
"""
entity_resolver.py — shared entity-name resolution helpers for secondbrain.

The verifier needs two distinct behaviors:

  - strict resolution for "does this wikilink already point at a real entity?"
  - suggestion resolution for "what canonical entity should this probably use?"

This module keeps those behaviors in one place so broken-link repair, stub
creation, and plain-text suggestions all agree on the same matching rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


TITLE_RE = re.compile(r"^#\s+(.+?)(?:\s+#+)?$", re.MULTILINE)
ACRONYM_RE = re.compile(r"^[A-Z0-9&]{2,8}$")
LEGAL_SUFFIXES = {
    "inc",
    "corp",
    "corporation",
    "company",
    "co",
    "llc",
    "ltd",
    "limited",
    "pc",
    "pllc",
    "llp",
    "lp",
}
PARENT_FALLBACK_MODIFIERS = {
    "team",
    "movers",
    "moving",
    "visa",
    "card",
    "plus",
    "pro",
}


@dataclass(frozen=True)
class NormalizedEntityName:
    raw: str
    tokens: Tuple[str, ...]
    joined: str


@dataclass(frozen=True)
class EntityRecord:
    path: Path
    rel_path: str
    slug: str
    title: str
    aliases: Tuple[str, ...]
    parent_entity: Optional[str]
    long_form_alias: Optional[str]

    @property
    def display_name(self) -> str:
        return self.title or humanize_entity_name(self.slug)

    @property
    def strict_names(self) -> Tuple[str, ...]:
        names: List[str] = []
        for value in (self.display_name, *self.aliases):
            if value and value not in names:
                names.append(value)
        slug_display = self.slug.replace("-", " ")
        if slug_display and slug_display not in names:
            names.append(slug_display)
        return tuple(names)

    @property
    def search_names(self) -> Tuple[str, ...]:
        names = list(self.strict_names)
        if self.long_form_alias and self.long_form_alias not in names:
            names.append(self.long_form_alias)
        return tuple(names)


@dataclass(frozen=True)
class EntityMatch:
    record: EntityRecord
    kind: str
    matched_name: str


def humanize_entity_name(name: str) -> str:
    parts = re.split(r"[-_/\s]+", name.strip())
    return " ".join(part.capitalize() for part in parts if part)


def normalize_entity_name(name: str) -> NormalizedEntityName:
    text = name.casefold().strip()
    text = text.replace("&", " and ")
    text = text.replace("’", "").replace("'", "")
    tokens = tuple(re.findall(r"[a-z0-9]+", text))
    trimmed_tokens = list(tokens)
    while trimmed_tokens and trimmed_tokens[-1] in LEGAL_SUFFIXES:
        trimmed_tokens.pop()
    normalized_tokens = tuple(trimmed_tokens)
    return NormalizedEntityName(
        raw=name,
        tokens=normalized_tokens,
        joined="".join(normalized_tokens),
    )


def _extract_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, text

    frontmatter: Dict[str, object] = {}
    fm_lines = lines[1:end_idx]
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            i += 1
            continue

        key = match.group(1)
        value = match.group(2).strip()
        if value == "":
            items: List[str] = []
            i += 1
            while i < len(fm_lines):
                stripped = fm_lines[i].strip()
                if not stripped:
                    i += 1
                    continue
                if not stripped.startswith("- "):
                    break
                items.append(_strip_quotes(stripped[2:].strip()))
                i += 1
            frontmatter[key] = items
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            frontmatter[key] = [
                _strip_quotes(part.strip())
                for part in inner.split(",")
                if part.strip()
            ]
        else:
            frontmatter[key] = _strip_quotes(value)
        i += 1

    body = "\n".join(lines[end_idx + 1 :])
    return frontmatter, body


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _extract_title(text: str, slug: str) -> str:
    match = TITLE_RE.search(text)
    if match:
        return match.group(1).strip()
    return humanize_entity_name(slug)


def _first_prose_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            continue
        if stripped.startswith("- "):
            continue
        if stripped.startswith("[["):
            continue
        return stripped
    return None


def _extract_long_form_alias(title: str, body: str) -> Optional[str]:
    if not ACRONYM_RE.fullmatch(title.strip()):
        return None
    first_line = _first_prose_line(body)
    if not first_line:
        return None
    candidate = re.sub(r"[.?!]+$", "", first_line).strip()
    if len(candidate.split()) < 2:
        return None
    if candidate.casefold() == title.casefold():
        return None
    return candidate


def _alias_list(frontmatter: Dict[str, object]) -> Tuple[str, ...]:
    aliases = frontmatter.get("aliases")
    if isinstance(aliases, str):
        return (aliases,) if aliases.strip() else ()
    if isinstance(aliases, list):
        return tuple(
            str(item).strip()
            for item in aliases
            if str(item).strip()
        )
    return ()


def _parent_entity(frontmatter: Dict[str, object]) -> Optional[str]:
    value = frontmatter.get("parent_entity")
    if not isinstance(value, str):
        return None
    slug = value.strip().removeprefix("entities/").removesuffix(".md")
    return slug or None


def load_entity_records(vault_root: Path, all_paths: Iterable[Path]) -> Tuple[EntityRecord, ...]:
    records: List[EntityRecord] = []
    for path in all_paths:
        try:
            rel = path.relative_to(vault_root).as_posix()
        except ValueError:
            continue
        if not rel.startswith("entities/") or not rel.endswith(".md"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, body = _extract_frontmatter(text)
        slug = path.stem
        title = _extract_title(body or text, slug)
        records.append(
            EntityRecord(
                path=path,
                rel_path=rel,
                slug=slug,
                title=title,
                aliases=_alias_list(frontmatter),
                parent_entity=_parent_entity(frontmatter),
                long_form_alias=_extract_long_form_alias(title, body),
            )
        )
    return tuple(sorted(records, key=lambda record: record.slug))


class EntityRegistry:
    def __init__(self, records: Sequence[EntityRecord]):
        self.records = tuple(records)
        self.by_slug: Dict[str, EntityRecord] = {
            record.slug.casefold(): record for record in self.records
        }
        self._strict_name_index: Dict[str, List[EntityRecord]] = {}
        self._search_name_index: Dict[str, List[EntityRecord]] = {}
        self._record_normalized_names: Dict[str, List[NormalizedEntityName]] = {}
        for record in self.records:
            normalized_search_names: List[NormalizedEntityName] = []
            for name in record.search_names:
                normalized = normalize_entity_name(name)
                if not normalized.joined:
                    continue
                self._search_name_index.setdefault(normalized.joined, []).append(record)
                normalized_search_names.append(normalized)
            for name in record.strict_names:
                normalized = normalize_entity_name(name)
                if not normalized.joined:
                    continue
                self._strict_name_index.setdefault(normalized.joined, []).append(record)
            self._record_normalized_names[record.slug] = normalized_search_names

    @classmethod
    def from_vault(cls, vault_root: Path, all_paths: Iterable[Path]) -> "EntityRegistry":
        return cls(load_entity_records(vault_root, all_paths))

    def resolve_existing(self, name: str) -> Optional[EntityMatch]:
        raw_stem = entity_target_stem(name)
        if not raw_stem:
            return None

        exact = self.by_slug.get(raw_stem.casefold())
        if exact is not None:
            return EntityMatch(record=exact, kind="exact", matched_name=raw_stem)

        normalized = normalize_entity_name(raw_stem)
        if not normalized.joined:
            return None

        strict = _unique_record(self._strict_name_index.get(normalized.joined, []))
        if strict is not None:
            kind = "alias" if normalized.joined not in {
                normalize_entity_name(strict.display_name).joined,
                normalize_entity_name(strict.slug.replace("-", " ")).joined,
            } else "normalized"
            return EntityMatch(record=strict, kind=kind, matched_name=raw_stem)
        return None

    def suggest_canonical(self, name: str) -> Optional[EntityMatch]:
        existing = self.resolve_existing(name)
        if existing is not None:
            return existing

        parent = self._parent_fallback(name)
        if parent is not None:
            return parent

        return self._fuzzy_match(name)

    def expand_search(self, name: str) -> Tuple[EntityRecord, ...]:
        match = self.resolve_existing(name)
        if match is None:
            suggestion = self.suggest_canonical(name)
            return (suggestion.record,) if suggestion is not None else ()

        records = [match.record]
        parent = self.parent_for_record(match.record)
        if parent is not None and parent.slug != match.record.slug:
            records.append(parent)
        return tuple(records)

    def parent_for_record(self, record: EntityRecord) -> Optional[EntityRecord]:
        if record.parent_entity:
            return self.by_slug.get(record.parent_entity.casefold())
        return self._parent_record_from_tokens(
            record,
            normalize_entity_name(record.slug.replace("-", " ")),
        )

    def _parent_fallback(self, name: str) -> Optional[EntityMatch]:
        normalized = normalize_entity_name(entity_target_stem(name))
        if not normalized.tokens:
            return None

        candidate = self._parent_record_from_tokens(None, normalized)
        if candidate is None:
            return None
        return EntityMatch(record=candidate, kind="parent", matched_name=name)

    def _parent_record_from_tokens(
        self,
        source_record: Optional[EntityRecord],
        target_name: NormalizedEntityName,
    ) -> Optional[EntityRecord]:
        best: Optional[Tuple[int, EntityRecord]] = None
        for record in self.records:
            if source_record is not None and record.slug == source_record.slug:
                continue
            candidate = normalize_entity_name(record.slug.replace("-", " "))
            if not candidate.tokens:
                continue
            if len(target_name.tokens) <= len(candidate.tokens):
                continue
            if target_name.tokens[: len(candidate.tokens)] != candidate.tokens:
                continue
            modifiers = target_name.tokens[len(candidate.tokens) :]
            if not modifiers or not all(_is_parent_modifier(token) for token in modifiers):
                continue
            score = len(candidate.tokens)
            if best is None or score > best[0]:
                best = (score, record)
            elif best is not None and score == best[0] and best[1].slug != record.slug:
                best = None
        return best[1] if best is not None else None

    def _fuzzy_match(self, name: str) -> Optional[EntityMatch]:
        normalized = normalize_entity_name(entity_target_stem(name))
        if not normalized.joined:
            return None

        scored: List[Tuple[float, EntityRecord, str]] = []
        for record in self.records:
            best_score = 0.0
            best_name = record.display_name
            for candidate in self._record_normalized_names.get(record.slug, []):
                if not candidate.joined:
                    continue
                score = SequenceMatcher(None, normalized.joined, candidate.joined).ratio()
                if score > best_score:
                    best_score = score
                    best_name = candidate.raw
            if best_score > 0:
                scored.append((best_score, record, best_name))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_record, best_name = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score > 0.80 and best_score - second_score > 0.20:
            return EntityMatch(record=best_record, kind="fuzzy", matched_name=best_name)
        return None


def entity_target_stem(target: str) -> str:
    stem = target.strip()
    if stem.startswith("entities/"):
        stem = stem.removeprefix("entities/")
    stem = stem.removesuffix(".md").rsplit("/", 1)[-1]
    return stem.strip()


def _is_parent_modifier(token: str) -> bool:
    return token in PARENT_FALLBACK_MODIFIERS or bool(re.fullmatch(r"\d+[a-z]*", token))


def _unique_record(records: Sequence[EntityRecord]) -> Optional[EntityRecord]:
    unique = {record.slug: record for record in records}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None
