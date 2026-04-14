#!/usr/bin/env python3
"""Shared Cowork runtime hygiene helpers for secondbrain."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from runtime_resolver import (
    resolve_claude_desktop_config_path,
    resolve_vaults_config_path,
)

COMPAT_START = "<!-- secondbrain:compatibility-memory:start -->"
COMPAT_END = "<!-- secondbrain:compatibility-memory:end -->"


@dataclass
class CoworkHygieneReport:
    applicable: bool
    changed: bool = False
    runtime_root: Optional[Path] = None
    app_root: Optional[Path] = None
    compatibility_targets: list[Path] = field(default_factory=list)
    stale_memory_files: list[Path] = field(default_factory=list)
    rewritten_memory_files: list[Path] = field(default_factory=list)
    legacy_artifacts: list[Path] = field(default_factory=list)
    quarantined_paths: list[Path] = field(default_factory=list)
    registry_files_with_memex: list[Path] = field(default_factory=list)
    sanitized_registry_files: list[Path] = field(default_factory=list)


def _path_from_parts(parts: tuple[str, ...]) -> Path:
    if not parts:
        return Path('.')
    return Path(parts[0], *parts[1:])


def _runtime_context(
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> tuple[Optional[Path], Optional[Path], Optional[str], Optional[str]]:
    resolved_plugin_root = Path(plugin_root).expanduser().resolve() if plugin_root else None
    if resolved_plugin_root is not None:
        parts = resolved_plugin_root.parts
        try:
            idx = parts.index('local-agent-mode-sessions')
        except ValueError:
            idx = -1
        if idx >= 0 and idx + 2 < len(parts):
            app_root = _path_from_parts(parts[:idx])
            runtime_root = _path_from_parts(parts[: idx + 3])
            return runtime_root, app_root, parts[idx + 1], parts[idx + 2]

    if desktop_config_path is not None:
        app_root = resolve_claude_desktop_config_path(desktop_config_path).parent
        return None, app_root, None, None
    return None, None, None, None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _plugin_version(plugin_root: Optional[Path]) -> Optional[str]:
    if plugin_root is None:
        return None
    plugin_root = Path(plugin_root)
    candidates = (
        plugin_root / '.claude-plugin' / 'plugin.json',
        plugin_root / 'secondbrain' / '.claude-plugin' / 'plugin.json',
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text())
        except Exception:
            continue
        version = data.get('version')
        if isinstance(version, str) and version:
            return version
    return None


def render_compatibility_memory(vault_path: Path) -> str:
    vault_display = str(Path(vault_path).expanduser())
    return (
        f"# Secondbrain Compatibility Memory\n\n"
        f"{COMPAT_START}\n"
        "This file is a compatibility bridge for Cowork memory surfaces.\n"
        "Primary session context comes from `brain/hot-memory.md` via the `SessionStart` hook.\n\n"
        f"Canonical vault: `{vault_display}`\n\n"
        "Current plugin namespace: `secondbrain:*`. Ignore stale legacy plugin names.\n"
        "If session-start context is missing or obviously stale, load `brain/hot-memory.md`\n"
        "and then search recursively.\n\n"
        "Useful commands:\n"
        "- `/secondbrain:init`\n"
        "- `/secondbrain:doctor`\n"
        "- `/secondbrain:dream-protocol`\n"
        "- `/secondbrain:knowledge-search`\n\n"
        "Rules:\n"
        "- This file is not an authoritative manifest.\n"
        "- Do not assume the listed files are exhaustive.\n"
        "- Recursively search the vault before claiming something is missing.\n"
        f"{COMPAT_END}\n"
    )


def _looks_like_legacy_memex(text: str) -> bool:
    lower = text.lower()
    return (
        'memex:' in lower
        or 'memex skills' in lower
        or '# memory index' in lower
        or 'run /memex:session-start' in lower
    )


def _merge_compatibility_memory(existing: str, compatibility: str) -> str:
    if not existing.strip():
        return compatibility
    start = existing.find(COMPAT_START)
    end = existing.find(COMPAT_END)
    if start >= 0 and end >= start:
        end += len(COMPAT_END)
        body = compatibility.rstrip() + existing[end:]
        return body.strip() + '\n'
    if _looks_like_legacy_memex(existing):
        return compatibility
    return compatibility.rstrip() + '\n\n' + existing.lstrip()


def _compatibility_targets(runtime_root: Path) -> list[Path]:
    targets = [
        runtime_root / 'agent' / 'memory' / 'MEMORY.md',
        runtime_root / 'outputs' / '.auto-memory' / 'MEMORY.md',
    ]
    targets.extend(sorted(runtime_root.glob('spaces/*/memory/MEMORY.md')))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for target in targets:
        if target in seen:
            continue
        deduped.append(target)
        seen.add(target)
    return deduped


def _contains_memex(value: Any) -> bool:
    if isinstance(value, str):
        return 'memex' in value.lower()
    if isinstance(value, list):
        return any(_contains_memex(item) for item in value)
    if isinstance(value, dict):
        return any('memex' in str(key).lower() or _contains_memex(item) for key, item in value.items())
    return False


def _strip_memex_entries(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_memex_entries(item) for item in value if not _contains_memex(item)]
    if isinstance(value, dict):
        return {
            key: _strip_memex_entries(item)
            for key, item in value.items()
            if 'memex' not in str(key).lower()
        }
    return value


def inspect_cowork_hygiene(
    vault_path: Path,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CoworkHygieneReport:
    runtime_root, app_root, _, _ = _runtime_context(plugin_root, desktop_config_path)
    if runtime_root is None or app_root is None:
        return CoworkHygieneReport(applicable=False, runtime_root=runtime_root, app_root=app_root)

    report = CoworkHygieneReport(applicable=True, runtime_root=runtime_root, app_root=app_root)
    compatibility = render_compatibility_memory(vault_path)

    for target in _compatibility_targets(runtime_root):
        report.compatibility_targets.append(target)
        existing = target.read_text() if target.is_file() else ''
        merged = _merge_compatibility_memory(existing, compatibility)
        if merged != existing:
            report.stale_memory_files.append(target)

    for artifact in (
        app_root / 'cowork_plugins' / '.install-manifests' / 'memex@memex.json',
        app_root / 'cowork_plugins' / 'marketplaces' / 'memex',
    ):
        if artifact.exists():
            report.legacy_artifacts.append(artifact)

    for registry in (
        app_root / 'cowork_plugins' / 'known_marketplaces.json',
        app_root / 'cowork_plugins' / 'installed_plugins.json',
    ):
        if not registry.is_file():
            continue
        try:
            payload = json.loads(registry.read_text())
        except Exception:
            if 'memex' in registry.read_text(errors='replace').lower():
                report.registry_files_with_memex.append(registry)
            continue
        if _contains_memex(payload):
            report.registry_files_with_memex.append(registry)

    return report


def repair_cowork_hygiene(
    vault_path: Path,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CoworkHygieneReport:
    report = inspect_cowork_hygiene(vault_path, plugin_root, desktop_config_path)
    if not report.applicable or report.runtime_root is None or report.app_root is None:
        return report

    compatibility = render_compatibility_memory(vault_path)
    for target in report.compatibility_targets:
        existing = target.read_text() if target.is_file() else ''
        merged = _merge_compatibility_memory(existing, compatibility)
        if merged == existing:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(merged)
        report.rewritten_memory_files.append(target)
        report.changed = True

    quarantine_root = report.app_root / 'secondbrain-runtime' / 'quarantine' / 'legacy-memex'
    for artifact in report.legacy_artifacts:
        relative = artifact.relative_to(report.app_root)
        destination = quarantine_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if artifact.is_file():
                artifact.unlink()
            else:
                shutil.rmtree(artifact)
        else:
            shutil.move(str(artifact), str(destination))
        report.quarantined_paths.append(destination)
        report.changed = True

    for registry in report.registry_files_with_memex:
        original = registry.read_text()
        try:
            payload = json.loads(original)
            sanitized = _strip_memex_entries(payload)
            rendered = json.dumps(sanitized, indent=2) + '\n'
        except Exception:
            rendered = '\n'.join(
                line for line in original.splitlines() if 'memex' not in line.lower()
            ).strip() + '\n'
        if rendered == original:
            continue
        backup = quarantine_root / 'backups' / registry.relative_to(report.app_root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(original)
        registry.write_text(rendered)
        report.sanitized_registry_files.append(registry)
        report.changed = True

    return report


def _stamp_base_dir(vaults_config_path: Optional[Path] = None) -> Path:
    return (vaults_config_path or resolve_vaults_config_path()).parent


def session_start_stamp_path(
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
    vaults_config_path: Optional[Path] = None,
) -> Path:
    runtime_root, app_root, workspace_id, runtime_session_id = _runtime_context(plugin_root, desktop_config_path)
    if runtime_root is not None and app_root is not None and workspace_id and runtime_session_id:
        return app_root / 'secondbrain-runtime' / 'session-start' / workspace_id / f'{runtime_session_id}.json'
    return _stamp_base_dir(vaults_config_path) / 'session-start' / 'latest.json'


def write_session_start_stamp(
    vault_path: Path,
    status: str,
    fallback_reason: Optional[str],
    session_id: Optional[str],
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
    hot_memory_generated_at: Optional[str] = None,
    vaults_config_path: Optional[Path] = None,
) -> Path:
    stamp_path = session_start_stamp_path(plugin_root, desktop_config_path, vaults_config_path)
    _, _, workspace_id, runtime_session_id = _runtime_context(plugin_root, desktop_config_path)
    payload = {
        'timestamp': _utc_now(),
        'status': status,
        'fallback_reason': fallback_reason,
        'session_id': session_id,
        'plugin_version': _plugin_version(plugin_root),
        'vault_path': str(vault_path),
        'workspace_id': workspace_id,
        'runtime_session_id': runtime_session_id,
        'hot_memory_generated_at': hot_memory_generated_at,
    }
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(json.dumps(payload, indent=2) + '\n')
    return stamp_path


def read_session_start_stamp(
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
    vaults_config_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    stamp_path = session_start_stamp_path(plugin_root, desktop_config_path, vaults_config_path)
    if not stamp_path.is_file():
        return None
    try:
        data = json.loads(stamp_path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def latest_init_plugins(
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> Optional[list[str]]:
    runtime_root, app_root, workspace_id, runtime_session_id = _runtime_context(plugin_root, desktop_config_path)
    if runtime_root is None or app_root is None or workspace_id is None or runtime_session_id is None:
        return None

    bridge_key = f'{runtime_session_id}:{workspace_id}'
    audit_candidates: list[Path] = []
    bridge_state = app_root / 'bridge-state.json'
    if bridge_state.is_file():
        try:
            data = json.loads(bridge_state.read_text())
        except Exception:
            data = {}
        entry = data.get(bridge_key) if isinstance(data, dict) else None
        local_session_id = entry.get('localSessionId') if isinstance(entry, dict) else None
        if isinstance(local_session_id, str) and local_session_id:
            audit_candidates.append(runtime_root / 'agent' / local_session_id / 'audit.jsonl')

    if not audit_candidates:
        audit_candidates.extend(sorted(runtime_root.glob('agent/*/audit.jsonl')))

    last_plugins: Optional[list[str]] = None
    for audit_path in audit_candidates:
        if not audit_path.is_file():
            continue
        try:
            lines = audit_path.read_text().splitlines()
        except Exception:
            continue
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line)
            except Exception:
                continue
            if entry.get('type') != 'system' or entry.get('subtype') != 'init':
                continue
            plugins = entry.get('plugins')
            names: list[str] = []
            if isinstance(plugins, list):
                for item in plugins:
                    if isinstance(item, str) and item:
                        names.append(item)
                    elif isinstance(item, dict):
                        name = item.get('name') or item.get('id')
                        if isinstance(name, str) and name:
                            names.append(name)
            last_plugins = names
    return last_plugins


def _serialize_report(report: CoworkHygieneReport) -> dict[str, Any]:
    return {
        'applicable': report.applicable,
        'changed': report.changed,
        'runtime_root': str(report.runtime_root) if report.runtime_root else None,
        'app_root': str(report.app_root) if report.app_root else None,
        'compatibility_targets': [str(path) for path in report.compatibility_targets],
        'stale_memory_files': [str(path) for path in report.stale_memory_files],
        'rewritten_memory_files': [str(path) for path in report.rewritten_memory_files],
        'legacy_artifacts': [str(path) for path in report.legacy_artifacts],
        'quarantined_paths': [str(path) for path in report.quarantined_paths],
        'registry_files_with_memex': [str(path) for path in report.registry_files_with_memex],
        'sanitized_registry_files': [str(path) for path in report.sanitized_registry_files],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Diagnose or repair Cowork compatibility memory state.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--diagnose', action='store_true')
    mode.add_argument('--repair', action='store_true')
    parser.add_argument('--vault', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--plugin-root', default=None)
    parser.add_argument('--desktop-config', default=None)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    plugin_root = Path(args.plugin_root).expanduser() if args.plugin_root else None
    desktop_config = Path(args.desktop_config).expanduser() if args.desktop_config else None
    vault_path = Path(args.vault).expanduser()

    report = (
        repair_cowork_hygiene(vault_path, plugin_root, desktop_config)
        if args.repair
        else inspect_cowork_hygiene(vault_path, plugin_root, desktop_config)
    )

    print(json.dumps(_serialize_report(report), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
