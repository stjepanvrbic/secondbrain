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
import shlex
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
    "emit_hot_memory.py",
    "setup_steps.py",
    "init_obsidian.py",
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


def _path_variants(path):
    """Return the set of string forms a registered vault path might take in
    a Bash command or file_path argument.

    Handles the macOS symlink quirk where /tmp ↔ /private/tmp and
    /var/folders ↔ /private/var/folders refer to the same inode. The hook
    must match the command's literal form, which may be either side of that
    symlink depending on how the agent wrote it.
    """
    if path is None:
        return set()
    variants = set()
    s = str(path)
    variants.add(s)
    # Add the resolved form.
    try:
        r = str(Path(s).resolve())
        variants.add(r)
    except Exception:
        r = s
    # macOS: /private/tmp/x ↔ /tmp/x, /private/var/folders/x ↔ /var/folders/x.
    # If we have a /private-prefixed form, add the stripped form as a variant.
    # If we have a non-/private form, add the /private-prefixed form.
    for v in (s, r):
        if v.startswith("/private/"):
            variants.add(v[len("/private"):])
        elif v.startswith("/tmp/") or v == "/tmp":
            variants.add("/private" + v)
        elif v.startswith("/var/folders/") or v == "/var/folders":
            variants.add("/private" + v)
    return variants


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


def _split_command_segments(command):
    """Split a Bash command into segments delimited by shell separators.

    Returns the list of individual command strings. Uses a regex split so we
    don't need a full shell parser — good enough for common agent commands.
    """
    # Split on &&, ||, ;, |, & — but not |&, ||, && (handled by order).
    # Regex matches: && | || | ; | & | | (pipe) (but not parts of && or ||).
    parts = re.split(r"\s*(?:\|\||&&|;|\||&)\s*", command)
    return [p.strip() for p in parts if p.strip()]


def _segment_is_sanctioned_call(segment):
    """Return True iff this single command segment is an invocation of a
    sanctioned Python script.

    Accepts forms like:
        python3 scripts/verify_vault.py ARGS...
        python scripts/archive_inbox.py ARGS...
        python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_hot_memory.py ARGS...
        ./scripts/foo.py ARGS...  (if foo.py is sanctioned)

    The script must be a *token* (a shell argument) — not a substring of a
    redirection target or an echo argument.
    """
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        # Malformed quoting — be conservative, don't treat as sanctioned.
        return False
    if not tokens:
        return False

    # Strip leading env assignments (`FOO=bar python3 ...`).
    idx = 0
    while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return False

    head = tokens[idx]
    head_base = Path(head).name

    # `python[3] <script_path>` — the next positional arg (skipping `-` flags
    # that aren't `-c/-m`) must be a sanctioned script.
    if head_base in ("python", "python3"):
        # Walk past python options until we hit a positional arg.
        j = idx + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            # `-c` / `-m` consume the next token as inline code/module —
            # neither is a sanctioned script invocation.
            if tokens[j] in ("-c", "-m"):
                return False
            j += 1
        if j >= len(tokens):
            return False
        script_token = tokens[j]
        # The script token may be ${VAR}/path/foo.py or /abs/foo.py or foo.py.
        script_base = Path(script_token).name
        return script_base in SANCTIONED_SCRIPTS

    # Direct invocation of a sanctioned script (rare but possible).
    if head_base in SANCTIONED_SCRIPTS:
        return True

    return False


def _command_invokes_sanctioned_script(command):
    """Return True iff every segment of `command` is a sanctioned script call.

    A sanctioned call is only a free pass when it's the *entire* command. If
    any segment is a non-sanctioned action (`rm`, `echo > ...`, etc.) the
    whole command must go through the normal write-verb check.
    """
    segments = _split_command_segments(command)
    if not segments:
        return False
    return all(_segment_is_sanctioned_call(seg) for seg in segments)


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


def _command_has_non_redirect_write_verb(command):
    """Return True iff the command contains a write verb OTHER than output
    redirection (`>` / `>>`). Used to distinguish `rm /vault/x` (always a
    vault-touching write) from `cat /vault/x > /tmp/out` (a read that happens
    to produce output elsewhere).
    """
    tokens = command.split()
    if tokens:
        head = Path(tokens[0]).name
        if head in WRITE_VERBS:
            return True
    if re.search(r"(^|[;&|`$(])\s*(mv|rm|cp|touch|tee|dd|install)\b", command):
        return True
    # `sed -i` in all its spellings — see _command_has_write_verb.
    if re.search(r"\bsed\s+(?:-[^i\s]*\s+)*-i", command):
        return True
    return False


def _command_has_redirect_write(command):
    """Return True iff the command contains a `>` or `>>` output redirection.
    Ignores `&>` and numeric-FD redirections like `2>`.
    """
    return bool(re.search(r"(?<![&0-9])>\s*(?!&)", command))


def _command_has_write_verb(command):
    return _command_has_non_redirect_write_verb(command) or _command_has_redirect_write(command)


def _command_references_path(command, path):
    candidates = set(_path_variants(path))
    home = os.environ.get("HOME")
    if home:
        for c in list(candidates):
            if c.startswith(home):
                candidates.add("~" + c[len(home):])
    for c in candidates:
        if c in command:
            return True
    return False


def _redirection_targets(command):
    """Return the set of paths that appear as `>` or `>>` redirection targets
    in `command`. Used to distinguish "read from vault, write elsewhere" from
    "write into vault".

    This is a heuristic parser — it handles the common agent cases
    (`cat a > b`, `echo x >> b`, `cmd > b 2>&1`) and skips stderr redirections
    like `2>` and `&>`.
    """
    targets = []
    # Match `>` or `>>` followed by whitespace and a token. Exclude `&>` and
    # numeric FD prefixes like `2>`. The target token runs until whitespace or
    # another shell metachar.
    for m in re.finditer(r"(?<![&0-9])>>?\s*([^\s|;&<>]+)", command):
        token = m.group(1)
        # Strip surrounding quotes.
        if (token.startswith('"') and token.endswith('"')) or (
            token.startswith("'") and token.endswith("'")
        ):
            token = token[1:-1]
        targets.append(token)
    return targets


def _path_is_under_any_vault(path_str, vaults):
    """Return True iff `path_str` (treated literally and after resolving)
    points inside any of the registered vault paths. Uses the same
    variant-aware matching as `_command_references_path` so macOS symlink
    paths match correctly.
    """
    if not path_str:
        return False
    try:
        p = Path(path_str).expanduser()
    except Exception:
        return False
    # Build candidate absolute-path strings for this target.
    cand_strs = set()
    cand_strs.add(str(p))
    try:
        cand_strs.add(str(p.resolve()))
    except Exception:
        pass
    for c in list(cand_strs):
        if c.startswith("/private/"):
            cand_strs.add(c[len("/private"):])
        elif c.startswith("/tmp/") or c.startswith("/var/folders/"):
            cand_strs.add("/private" + c)
    for vault in vaults:
        vault_variants = _path_variants(vault)
        for cs in cand_strs:
            for vv in vault_variants:
                try:
                    if cs == vv or cs.startswith(vv.rstrip("/") + "/"):
                        return True
                except Exception:
                    continue
    return False


def _redirection_target_touches_vault(command, vaults):
    """Return True iff the command has a redirection target inside any vault."""
    targets = _redirection_targets(command)
    if not targets:
        # No redirection in this command; the caller must fall back to the
        # broader reference check.
        return None
    for t in targets:
        if _path_is_under_any_vault(t, vaults):
            return True
    return False


def _handle_bash(tool_input):
    command = tool_input.get("command", "") or ""
    if not command:
        _allow()

    cfg = _config_path()
    if not cfg.exists():
        _allow()

    cfg_variants = _path_variants(cfg)
    touches_cfg = any(v in command for v in cfg_variants)
    if touches_cfg and _command_has_write_verb(command):
        # Special case: redirection-only writes whose target is not the
        # config file itself are legitimate (e.g. `cat vaults.json > /tmp/x`).
        if (
            not _command_has_non_redirect_write_verb(command)
            and _redirection_target_touches_vault(command, [cfg]) is False
        ):
            pass  # fall through to vault check
        else:
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

    # If there's a non-redirect write verb (rm/mv/cp/touch/tee/sed -i/etc.)
    # and the command references a vault path, block unconditionally. These
    # verbs take the vault path as a positional arg, so its mere presence is
    # the smoking gun.
    if _command_has_non_redirect_write_verb(command):
        _block(_bash_block_message(command, "write targeting a registered vault path"))

    # Otherwise the only write indicator is a redirection. Check whether the
    # redirection target is inside a vault. If it is → block; if not → allow
    # (legitimate read-from-vault-write-elsewhere, e.g. `cat /vault/x > /tmp/y`).
    if _command_has_redirect_write(command):
        redir_hits_vault = _redirection_target_touches_vault(command, vaults)
        if redir_hits_vault:
            _block(_bash_block_message(command, "write targeting a registered vault path"))
        # redir_hits_vault is False or None — safe to allow.
        _allow()

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
