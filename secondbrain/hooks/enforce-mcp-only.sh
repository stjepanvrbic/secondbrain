#!/usr/bin/env bash
# PreToolUse hook — enforces that vault writes only happen via MCP or sanctioned scripts.
#
# This is the BROAD enforcement hook. Companion to enforce-immutability.sh (MCP
# inbox/archive) and enforce-immutability-bash.sh (Bash inbox/archive). Those
# hooks keep their narrower semantics; this hook catches the wider class of
# "agent tries to mutate a managed vault path via Edit/Write/NotebookEdit/Bash
# outside the sanctioned channels".
#
# Behavior:
#
#   Edit / Write / NotebookEdit
#     - Resolve file_path to absolute
#     - If the target is inside any registered vault path OR is vaults.json,
#       exit 2 with an explanation.
#     - Otherwise exit 0.
#
#   Bash
#     - Sanctioned Python scripts (archive_inbox.py, verify_vault.py, etc.) are
#       always allowed — they're the only sanctioned way to mutate vault state
#       from the shell.
#     - Pure read commands (cat, ls, grep, etc.) are always allowed.
#     - Writes (mv/rm/cp/touch/tee/sed -i/redirection) that touch a registered
#       vault path or vaults.json are blocked.
#
# Pre-init (vaults.json missing): fail open — we can't protect vaults that
# aren't registered yet. The hook returns 0 without Python startup cost when
# the config is missing.
#
# All logic lives in a single Python invocation (no per-check subprocess) so
# the hook stays fast enough to run on every tool call.

set -euo pipefail

# Read the hook JSON from stdin into an env var so the inline Python below
# can read the source from its own stdin (the heredoc).
SECONDBRAIN_HOOK_INPUT=$(cat)
export SECONDBRAIN_HOOK_INPUT

# Run the inline Python dispatcher. If anything crashes inside it, fail open
# (exit 0) so a hook bug never blocks the agent unrecoverably. Genuine blocks
# come from sys.exit(2), which we propagate explicitly below.
set +e
python3 <<'PY'
import json
import os
import re
import sys
from pathlib import Path

SANCTIONED_SCRIPTS = {
    "archive_inbox.py",
    "migrate_v2_to_v3.py",
    "verify_vault.py",
    "archive_contradiction.py",
    "rebuild_manifest.py",
    "update_hot_memory.py",
    "extract_new_turns.py",
    "advance_cursor.py",
    "log_session_end.py",
    "emit_hot_memory.py",
    "setup_steps.py",
    "init_obsidian.py",
    "vault_git.py",
    "create_entity_stubs.py",
    "bump_version.py",
    "connect_mcp_client.py",
    "validate_hot_memory.py",
    "vault_lookup_cwd.py",
}

READ_VERBS = {
    "cat", "ls", "find", "grep", "head", "tail", "less", "more",
    "wc", "file", "stat", "diff", "md5", "sha256sum",
}

WRITE_VERBS = {"mv", "rm", "cp", "touch", "tee", "dd", "install"}


def _allow():
    sys.exit(0)


def _block(message):
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.exit(2)


def _config_path():
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "secondbrain" / "vaults.json"


def _registered_vault_paths():
    cfg = _config_path()
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text())
    except (OSError, json.JSONDecodeError):
        # Malformed config — fail open; a separate tool will scream about it.
        return []
    out = []
    for entry in data.get("vaults", []) or []:
        p = entry.get("path")
        if not p:
            continue
        try:
            out.append(Path(p).expanduser().resolve())
        except Exception:
            continue
    return out


def _resolve(path_str):
    if not path_str:
        return None
    try:
        return Path(path_str).expanduser().resolve()
    except Exception:
        return None


def _is_under(target, root):
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return target == root


def _edit_block_message(target):
    return (
        "BLOCKED: direct edits to vault paths are not allowed.\n"
        "\n"
        "Target: {target}\n"
        "\n"
        "The secondbrain vault can only be mutated through two sanctioned "
        "channels:\n"
        "  1. MCP tools: mcp__obsidian__vault_create / vault_update / vault_patch / vault_edit / vault_edit_line / vault_delete\n"
        "  2. Sanctioned Python scripts under secondbrain/scripts/\n"
        "     (archive_inbox.py, update_hot_memory.py, verify_vault.py, etc.)\n"
        "\n"
        "Use one of those instead of Edit/Write/NotebookEdit.\n"
    ).format(target=target)


def _vaults_json_block_message(target):
    return (
        "BLOCKED: vaults.json is not agent-editable.\n"
        "\n"
        "Target: {target}\n"
        "\n"
        "~/.config/secondbrain/vaults.json is the canonical registry of\n"
        "managed vaults and is maintained by scripts/setup_steps.py.\n"
        "Use /secondbrain:init or /secondbrain:doctor to change it.\n"
    ).format(target=target)


def _bash_block_message(command, reason):
    return (
        "BLOCKED: {reason}\n"
        "\n"
        "Command: {command}\n"
        "\n"
        "Vault paths can only be mutated via MCP tools or sanctioned Python\n"
        "scripts (archive_inbox.py, update_hot_memory.py, verify_vault.py, ...).\n"
        "Don't mv/rm/cp/touch/sed-i/redirect into a managed vault from Bash.\n"
    ).format(reason=reason, command=command)


def _handle_edit(tool_input):
    raw = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
        or ""
    )
    if not raw:
        _allow()
    target = _resolve(raw)
    if target is None:
        _allow()

    cfg = _config_path()
    if not cfg.exists():
        _allow()

    try:
        cfg_resolved = cfg.resolve()
    except Exception:
        cfg_resolved = cfg
    if target == cfg_resolved:
        _block(_vaults_json_block_message(target))

    vaults = _registered_vault_paths()
    for v in vaults:
        if _is_under(target, v):
            _block(_edit_block_message(target))

    _allow()


def _command_invokes_sanctioned_script(command):
    for script in SANCTIONED_SCRIPTS:
        if script in command:
            return True
    return False


def _starts_with_read_verb(command):
    stripped = command.strip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0]
    first = Path(first).name
    if first in READ_VERBS:
        return True
    if first in ("python", "python3") and " -c " in stripped:
        return True
    if first == "echo":
        return True
    return False


def _command_has_write_verb(command):
    tokens = command.split()
    if tokens:
        head = Path(tokens[0]).name
        if head in WRITE_VERBS:
            return True
    if re.search(r"(^|[;&|`$(])\s*(mv|rm|cp|touch|tee|dd|install)\b", command):
        return True
    if re.search(r"\bsed\s+-i\b", command):
        return True
    # Output redirection. Ignore `&>` and `2>` (stderr redirection).
    if re.search(r"(?<![&0-9])>\s*(?!&)", command):
        return True
    return False


def _command_references_path(command, path):
    candidates = {str(path)}
    try:
        candidates.add(str(path.resolve()))
    except Exception:
        pass
    home = os.environ.get("HOME")
    if home and str(path).startswith(home):
        candidates.add("~" + str(path)[len(home):])
    for c in candidates:
        if c in command:
            return True
    return False


def _handle_bash(tool_input):
    command = tool_input.get("command", "") or ""
    if not command:
        _allow()

    cfg = _config_path()
    if not cfg.exists():
        _allow()

    try:
        cfg_resolved_str = str(cfg.resolve())
    except Exception:
        cfg_resolved_str = str(cfg)
    touches_cfg = (
        str(cfg) in command
        or cfg_resolved_str in command
    )
    if touches_cfg and _command_has_write_verb(command):
        _block(_bash_block_message(command, "write targeting vaults.json"))

    if _command_invokes_sanctioned_script(command):
        _allow()

    if _starts_with_read_verb(command) and not _command_has_write_verb(command):
        _allow()

    vaults = _registered_vault_paths()
    touches_vault = False
    for v in vaults:
        if _command_references_path(command, v):
            touches_vault = True
            break

    if not touches_vault:
        _allow()

    if _command_has_write_verb(command):
        _block(_bash_block_message(command, "write targeting a registered vault path"))

    _allow()


def main():
    raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")
    if not raw:
        _allow()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        _allow()
    if not isinstance(data, dict):
        _allow()

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        _handle_edit(tool_input)
    elif tool_name == "Bash":
        _handle_bash(tool_input)
    else:
        _allow()


try:
    main()
except SystemExit:
    raise
except Exception:
    # Fail-open on any unexpected crash. The hook must never wedge the agent.
    sys.exit(0)
PY
RC=$?

# Translate Python exit codes to Claude Code semantics:
#   0 → allow
#   2 → block
#   anything else (Python crash, unexpected) → fail open (allow)
if [ "$RC" -eq 0 ] || [ "$RC" -eq 2 ]; then
    exit "$RC"
fi
exit 0
