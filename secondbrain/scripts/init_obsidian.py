#!/usr/bin/env python3
"""
init_obsidian.py — Automated Obsidian setup for secondbrain.

Detects platform, installs Obsidian if missing, installs required plugins,
configures MCP connection, and scaffolds vault structure. The user should
barely need to do anything.

Python 3.8+, zero external dependencies.

Usage:
    python3 init_obsidian.py                          # full auto
    python3 init_obsidian.py --vault-path ~/my-vault  # specify vault
    python3 init_obsidian.py --skip-install            # skip Obsidian install
    python3 init_obsidian.py --dry-run                 # show what would happen
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform() -> str:
    """Return 'macos', 'linux', or 'windows'. WSL returns 'linux'."""
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "linux":
        return "linux"  # WSL reports as Linux — treat it as Linux
    if s == "windows":
        return "windows"
    return "linux"


def is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux."""
    try:
        release = Path("/proc/version").read_text().lower()
        return "microsoft" in release or "wsl" in release
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Obsidian detection and installation
# ---------------------------------------------------------------------------

OBSIDIAN_PATHS = {
    "macos": [Path("/Applications/Obsidian.app")],
    "linux": [
        Path("/usr/bin/obsidian"),
        Path("/snap/bin/obsidian"),
        Path.home() / "AppImage" / "Obsidian.AppImage",
        Path.home() / ".local/bin/obsidian",
    ],
    "windows": [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Obsidian" / "Obsidian.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Obsidian" / "Obsidian.exe",
    ],
}

OBSIDIAN_CONFIG_PATHS = {
    "macos": Path.home() / "Library" / "Application Support" / "obsidian",
    "linux": Path.home() / ".config" / "obsidian",
    "windows": Path(os.environ.get("APPDATA", "")) / "obsidian",
}


def find_obsidian(plat: str) -> Optional[Path]:
    """Find Obsidian installation path, or None."""
    for p in OBSIDIAN_PATHS.get(plat, []):
        if p.exists():
            return p
    # Also try 'which' / 'where' as fallback
    cmd = "where.exe" if plat == "windows" else "which"
    try:
        result = subprocess.run([cmd, "obsidian"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip().splitlines()[0])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def install_obsidian(plat: str, dry_run: bool = False) -> bool:
    """Install Obsidian. Returns True on success."""
    commands = {
        "macos": ["brew", "install", "--cask", "obsidian"],
        "linux": ["snap", "install", "obsidian", "--classic"],
        "windows": ["winget", "install", "--id", "Obsidian.Obsidian", "--accept-source-agreements", "--accept-package-agreements"],
    }

    # Check if package manager exists
    pkg_managers = {"macos": "brew", "linux": "snap", "windows": "winget"}
    mgr = pkg_managers.get(plat, "")
    which_cmd = "where.exe" if plat == "windows" else "which"

    try:
        r = subprocess.run([which_cmd, mgr], capture_output=True, timeout=5)
        if r.returncode != 0:
            # Try apt for Linux if snap not available
            if plat == "linux":
                r2 = subprocess.run(["which", "apt"], capture_output=True, timeout=5)
                if r2.returncode == 0:
                    commands["linux"] = ["sudo", "apt", "install", "-y", "obsidian"]
                else:
                    print(f"  No package manager found ({mgr}, apt). Install Obsidian manually.")
                    return False
            else:
                print(f"  Package manager '{mgr}' not found. Install Obsidian manually.")
                return False
    except (OSError, subprocess.TimeoutExpired):
        print(f"  Cannot check for '{mgr}'. Install Obsidian manually.")
        return False

    cmd = commands.get(plat, [])
    if not cmd:
        print(f"  No install command for platform '{plat}'.")
        return False

    if dry_run:
        print(f"  WOULD RUN: {' '.join(cmd)}")
        return True

    print(f"  Installing Obsidian: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, timeout=300)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  Install failed: {e}")
        return False


def ensure_obsidian_running(plat: str, dry_run: bool = False) -> bool:
    """Launch Obsidian if not running. Returns True if running after call."""
    # Check if already running
    try:
        if plat == "macos":
            r = subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True, timeout=5)
        elif plat == "windows":
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"],
                             capture_output=True, text=True, timeout=5)
            if "Obsidian.exe" in r.stdout:
                return True
            r.returncode = 1  # not found
        else:  # linux
            r = subprocess.run(["pgrep", "-x", "obsidian"], capture_output=True, timeout=5)
        if r.returncode == 0:
            print("  Obsidian is running")
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass

    if dry_run:
        print("  WOULD LAUNCH Obsidian")
        return True

    print("  Launching Obsidian...")
    launch_cmds = {
        "macos": ["open", "-a", "Obsidian"],
        "linux": ["nohup", "obsidian"],
        "windows": ["cmd", "/c", "start", "", "Obsidian"],
    }
    cmd = launch_cmds.get(plat, [])
    # Hard cap on the foreground wait. If Obsidian isn't up in 60s something
    # is wrong (headless Linux, crash-on-startup, wedged machine) and the
    # user needs a clear message — not a silent hang.
    LAUNCH_TIMEOUT_SECONDS = 60
    TIMEOUT_MESSAGE = (
        "Obsidian did not launch within 60 seconds. Possible causes: "
        "(a) headless Linux environment (init requires a desktop session, "
        "not SSH-only); (b) Obsidian crashed during startup — try launching "
        "manually and re-running init; (c) slow machine — re-run init once "
        "Obsidian is ready."
    )
    try:
        if plat == "linux":
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(cmd, timeout=10)
        # Wait for it to be ready — poll once per second up to LAUNCH_TIMEOUT_SECONDS.
        import time
        deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                if plat == "macos":
                    r = subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True, timeout=3)
                elif plat == "windows":
                    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"],
                                     capture_output=True, text=True, timeout=3)
                    if "Obsidian.exe" in r.stdout:
                        print("  Obsidian started")
                        return True
                    continue
                else:
                    r = subprocess.run(["pgrep", "-x", "obsidian"], capture_output=True, timeout=3)
                if r.returncode == 0:
                    print("  Obsidian started")
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        print(f"  {TIMEOUT_MESSAGE}")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  Failed to launch Obsidian: {e}")
        return False


def install_plugin_via_cli(plugin_id: str, dry_run: bool = False) -> bool:
    """Install and enable a plugin using the Obsidian CLI. Requires Obsidian running."""
    if dry_run:
        print(f"  WOULD RUN: obsidian plugin:install id={plugin_id} enable")
        return True
    try:
        result = subprocess.run(
            ["obsidian", f"plugin:install", f"id={plugin_id}", "enable"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"  CLI installed: {plugin_id}")
            return True
        # CLI might fail if plugin already installed
        if "already installed" in result.stderr.lower() or "already installed" in result.stdout.lower():
            print(f"  {plugin_id}: already installed")
            return True
        print(f"  CLI install failed: {result.stderr or result.stdout}")
        return False
    except FileNotFoundError:
        return False  # CLI not available
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  CLI install failed: {e}")
        return False


def ensure_node_installed(plat: str, dry_run: bool = False) -> bool:
    """Check for Node.js/npx. Install if missing. Returns True if available."""
    try:
        result = subprocess.run(["npx", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"  Node.js/npx available (npx {result.stdout.strip()})")
            return True
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    print("  Node.js/npx not found — required for MCP bridge")
    if dry_run:
        install_cmds = {"macos": "brew install node", "linux": "sudo apt install -y nodejs npm", "windows": "winget install OpenJS.NodeJS"}
        print(f"  WOULD RUN: {install_cmds.get(plat, 'install Node.js manually')}")
        return True

    install_cmds = {
        "macos": ["brew", "install", "node"],
        "linux": ["sudo", "apt", "install", "-y", "nodejs", "npm"],
        "windows": ["winget", "install", "--id", "OpenJS.NodeJS", "--accept-source-agreements"],
    }
    cmd = install_cmds.get(plat)
    if not cmd:
        print("  Install Node.js manually: https://nodejs.org")
        return False

    print(f"  Installing Node.js: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, timeout=120)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  Node.js install failed: {e}")
        print("  Install manually: https://nodejs.org")
        return False


# ---------------------------------------------------------------------------
# Vault detection
# ---------------------------------------------------------------------------

def find_existing_vaults(plat: str) -> List[Tuple[str, Path]]:
    """Find existing Obsidian vaults from obsidian.json config."""
    config_dir = OBSIDIAN_CONFIG_PATHS.get(plat)
    if not config_dir:
        return []

    obsidian_json = config_dir / "obsidian.json"
    if not obsidian_json.exists():
        return []

    try:
        data = json.loads(obsidian_json.read_text(encoding="utf-8"))
        vaults = data.get("vaults", {})
        results = []
        for info in vaults.values():
            vault_path = Path(info.get("path", ""))
            if vault_path.is_dir():
                # Try to get vault name from path
                results.append((vault_path.name, vault_path))
        return results
    except (json.JSONDecodeError, OSError):
        return []


def default_vault_path() -> Path:
    return Path.home() / "secondbrain-vault"


# ---------------------------------------------------------------------------
# Plugin installation
# ---------------------------------------------------------------------------

PLUGINS = {
    "dataview": {
        "repo": "blacksmithgu/obsidian-dataview",
        "id": "dataview",
    },
    "connect-mcp": {
        "repo": "joch/obsidian-connect-mcp",
        "id": "connect-mcp",
    },
}

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


def fetch_latest_release(repo: str) -> Optional[Dict[str, Any]]:
    """Fetch latest release info from GitHub."""
    url = GITHUB_API.format(repo=repo)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "secondbrain-init"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  Failed to fetch release from {repo}: {e}")
        return None


def download_file(url: str, dest: Path) -> bool:
    """Download a file from URL to dest."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "secondbrain-init"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"  Download failed: {e}")
        return False


def install_plugin(vault_path: Path, plugin_name: str, plugin_info: Dict, dry_run: bool = False) -> bool:
    """Install an Obsidian plugin into the vault's .obsidian/plugins/ directory."""
    plugin_id = plugin_info["id"]
    plugin_dir = vault_path / ".obsidian" / "plugins" / plugin_id

    if plugin_dir.exists() and (plugin_dir / "main.js").exists():
        print(f"  {plugin_name}: already installed")
        return True

    if dry_run:
        print(f"  WOULD INSTALL plugin: {plugin_name} ({plugin_id})")
        return True

    release = fetch_latest_release(plugin_info["repo"])
    if not release:
        return False

    # Find the required assets
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    required = ["main.js", "manifest.json"]
    optional = ["styles.css"]

    for filename in required:
        if filename not in assets:
            print(f"  {plugin_name}: missing required asset '{filename}' in release")
            return False

    plugin_dir.mkdir(parents=True, exist_ok=True)

    for filename in required + optional:
        if filename in assets:
            dest = plugin_dir / filename
            print(f"  Downloading {plugin_name}/{filename}...")
            if not download_file(assets[filename], dest):
                return False

    print(f"  {plugin_name}: installed successfully")
    return True


def enable_plugins(vault_path: Path, plugin_ids: List[str], dry_run: bool = False) -> None:
    """Add plugins to .obsidian/community-plugins.json."""
    plugins_file = vault_path / ".obsidian" / "community-plugins.json"

    existing: List[str] = []
    if plugins_file.exists():
        try:
            existing = json.loads(plugins_file.read_text())
        except json.JSONDecodeError:
            pass

    updated = list(existing)
    for pid in plugin_ids:
        if pid not in updated:
            updated.append(pid)

    if updated == existing:
        return

    if dry_run:
        print(f"  WOULD UPDATE community-plugins.json: {updated}")
        return

    plugins_file.parent.mkdir(parents=True, exist_ok=True)
    plugins_file.write_text(json.dumps(updated, indent=2))
    print(f"  Enabled plugins: {updated}")


# ---------------------------------------------------------------------------
# REST API configuration
# ---------------------------------------------------------------------------

DEFAULT_REST_PORT = 27124


def configure_mcp_plugin(vault_path: Path, dry_run: bool = False) -> Tuple[Optional[int], Optional[str]]:
    """Configure connect-mcp plugin and return (port, api_key) if found."""
    config_path = vault_path / ".obsidian" / "plugins" / "connect-mcp" / "data.json"

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            port = data.get("port", DEFAULT_REST_PORT)
            api_key = data.get("apiKey") or data.get("api_key")
            if api_key:
                print(f"  REST API config found: port={port}")
                return port, api_key
        except json.JSONDecodeError:
            pass

    # Write default config if missing
    if dry_run:
        print(f"  WOULD WRITE default REST API config (port={DEFAULT_REST_PORT})")
        return DEFAULT_REST_PORT, None

    config_path.parent.mkdir(parents=True, exist_ok=True)
    default_config = {"port": DEFAULT_REST_PORT, "crypto": True}
    config_path.write_text(json.dumps(default_config, indent=2))
    print(f"  Wrote default REST API config (port={DEFAULT_REST_PORT})")
    print("  NOTE: API key will be generated when Obsidian starts. Re-run init after opening Obsidian.")
    return DEFAULT_REST_PORT, None


# ---------------------------------------------------------------------------
# Environment variable configuration
# ---------------------------------------------------------------------------

SHELL_CONFIGS = {
    "zsh": Path.home() / ".zshrc",
    "bash": Path.home() / ".bashrc",
    "fish": Path.home() / ".config" / "fish" / "config.fish",
}

ENV_VAR_RE = re.compile(r"^export\s+(OBSIDIAN_API_KEY|OBSIDIAN_MCP_PORT)=", re.MULTILINE)
FISH_VAR_RE = re.compile(r"^set\s+-gx\s+(OBSIDIAN_API_KEY|OBSIDIAN_MCP_PORT)\s+", re.MULTILINE)


def detect_shell() -> str:
    """Detect current shell."""
    shell = os.environ.get("SHELL", "")
    if "fish" in shell:
        return "fish"
    if "zsh" in shell:
        return "zsh"
    if "bash" in shell:
        return "bash"
    # Windows: check for PowerShell profile
    if detect_platform() == "windows":
        return "powershell"
    return "bash"


def set_env_vars(port: int, api_key: Optional[str], shell: str, dry_run: bool = False) -> bool:
    """Append env vars to shell config file."""
    if shell == "powershell":
        return _set_env_vars_powershell(port, api_key, dry_run)

    config_file = SHELL_CONFIGS.get(shell)
    if not config_file:
        print(f"  Unknown shell '{shell}'. Set these manually:")
        _print_manual_env(port, api_key)
        return False

    if shell == "fish":
        lines = [f"set -gx OBSIDIAN_MCP_PORT {port}"]
        if api_key:
            lines.append(f"set -gx OBSIDIAN_API_KEY {api_key}")
        var_re = FISH_VAR_RE
    else:
        lines = [f'export OBSIDIAN_MCP_PORT="{port}"']
        if api_key:
            lines.append(f'export OBSIDIAN_API_KEY="{api_key}"')
        var_re = ENV_VAR_RE

    # Check if already set
    if config_file.exists():
        content = config_file.read_text()
        existing = set(var_re.findall(content))
        lines = [l for l in lines if not any(v in l for v in existing)]

    if not lines:
        print("  Environment variables already set")
        return True

    if dry_run:
        print(f"  WOULD APPEND to {config_file}:")
        for line in lines:
            print(f"    {line}")
        return True

    config_file.parent.mkdir(parents=True, exist_ok=True)
    with config_file.open("a") as f:
        f.write(f"\n# secondbrain — Obsidian MCP connection\n")
        for line in lines:
            f.write(line + "\n")

    print(f"  Appended to {config_file}")
    return True


def _set_env_vars_powershell(port: int, api_key: Optional[str], dry_run: bool = False) -> bool:
    """Set env vars for Windows via PowerShell profile or setx."""
    cmds = [f'[Environment]::SetEnvironmentVariable("OBSIDIAN_MCP_PORT", "{port}", "User")']
    if api_key:
        cmds.append(f'[Environment]::SetEnvironmentVariable("OBSIDIAN_API_KEY", "{api_key}", "User")')

    if dry_run:
        print("  WOULD SET Windows environment variables:")
        for cmd in cmds:
            print(f"    {cmd}")
        return True

    for cmd in cmds:
        try:
            subprocess.run(["powershell", "-Command", cmd], timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"  Failed to set env var: {e}")
            return False

    print("  Windows environment variables set (user scope)")
    return True


def _print_manual_env(port: int, api_key: Optional[str]) -> None:
    print(f"  OBSIDIAN_MCP_PORT={port}")
    if api_key:
        print(f"  OBSIDIAN_API_KEY={api_key}")


# ---------------------------------------------------------------------------
# Vault scaffolding
# ---------------------------------------------------------------------------

REQUIRED_DIRS = ["brain", "entities", "me", "inbox", "archive", "archive/inbox", "scratch"]

# `me/profile.md` is seeded from skills/init/templates/profile.md rather than
# an inline string — the template is long enough that inlining it here would
# be harder to read than a separate file, and keeping it on disk lets the init
# skill and the scaffold stay in sync.
PROFILE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "init" / "templates" / "profile.md"
)

# Placeholders the profile template uses. scaffold_vault writes the template
# unchanged (placeholders intact) — the init skill fills them in during the
# profile-seeding step after talking to the user. Stored as bare names (no
# `{{...}}` wrapping) so the verification helper can compare them directly
# against the set parsed out of the template text.
PROFILE_PLACEHOLDERS = (
    "USER_NAME", "USER_ROLE", "USER_NEXT_ROLE",
    "USER_PARTNER", "USER_PREFERENCES",
    "WAKEUP_TIME", "MORNING_WINDOW",
    "AFTERNOON_WINDOW", "EVENING_WINDOW",
)

CRITICAL_FILES = {
    "brain/status.md": "---\nupdated: {date}\n---\n# Status\n\n## Current Focus\n\n_No focus set yet._\n",
    "brain/deadlines.md": "# Deadlines\n\n_No deadlines tracked yet._\n",
    "brain/goals.md": "# Goals\n\n_No goals set yet._\n",
    "brain/decisions.md": "# Decisions\n\n_No decisions recorded yet._\n",
    "brain/session-log.md": "# Session Log\n",
    "glossary.md": "# Glossary\n\n_Add terms and acronyms here._\n",
    "log.md": f"# Log\n\n## [{date.today().isoformat()} 00:00] init | Vault created\nInitial vault scaffolding.\n",
    "_MANIFEST.md": "# Vault Manifest\n\n**Files:** 0\n**Last updated:** {date}\n",
}


def _verify_profile_template_placeholders(template_text: str) -> None:
    """Sanity-check the profile template has the expected placeholder set.

    Logs a warning to stderr if the template drifts from PROFILE_PLACEHOLDERS
    but does not fail — scaffolding must proceed even if the template is
    slightly off. This turns the PROFILE_PLACEHOLDERS constant into an
    enforced contract: edits to the template that drop or add placeholders
    surface immediately instead of silently going wrong during init.
    """
    found = set(re.findall(r"\{\{([A-Z_]+)\}\}", template_text))
    expected = set(PROFILE_PLACEHOLDERS)
    missing = expected - found
    unexpected = found - expected
    if missing:
        print(
            f"Warning: profile template missing placeholders: {sorted(missing)}",
            file=sys.stderr,
        )
    if unexpected:
        print(
            f"Warning: profile template has unexpected placeholders: {sorted(unexpected)}",
            file=sys.stderr,
        )


def _load_profile_template() -> str:
    """Read the profile.md template shipped with the plugin.

    Returns a minimal fallback string if the template file is missing, so
    `scaffold_vault` still seeds a readable profile rather than crashing.
    The fallback path only fires if the plugin install is broken; normal
    runs read the full template.
    """
    try:
        text = PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "# Profile\n\n"
            "_Run /secondbrain:init to seed your profile "
            "(the template file is missing from the plugin install)._\n"
        )
    _verify_profile_template_placeholders(text)
    return text


def scaffold_vault(vault_path: Path, dry_run: bool = False) -> int:
    """Create vault structure. Returns count of created items. Never overwrites.

    me/profile.md is seeded from skills/init/templates/profile.md if absent.
    Existing profile.md files are never touched (same rule as every other
    critical file).
    """
    created = 0
    today = date.today().isoformat()

    for d in REQUIRED_DIRS:
        dir_path = vault_path / d
        if not dir_path.exists():
            if dry_run:
                print(f"  WOULD CREATE dir: {d}/")
            else:
                dir_path.mkdir(parents=True, exist_ok=True)
            created += 1

    for filename, template in CRITICAL_FILES.items():
        file_path = vault_path / filename
        if not file_path.exists():
            content = template.replace("{date}", today)
            if dry_run:
                print(f"  WOULD CREATE file: {filename}")
            else:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)
            created += 1

    # me/profile.md — seeded from the shipped template. Never overwrite an
    # existing file; the init skill fills placeholders in a separate step.
    profile_path = vault_path / "me" / "profile.md"
    if not profile_path.exists():
        if dry_run:
            print("  WOULD CREATE file: me/profile.md (from template)")
        else:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(_load_profile_template())
        created += 1

    # Ensure .obsidian directory exists
    obsidian_dir = vault_path / ".obsidian"
    if not obsidian_dir.exists():
        if dry_run:
            print("  WOULD CREATE dir: .obsidian/")
        else:
            obsidian_dir.mkdir(parents=True, exist_ok=True)

    return created


# ---------------------------------------------------------------------------
# Note import (Scenario 3: bring in existing notes)
# ---------------------------------------------------------------------------

IMPORTABLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf", ".html", ".rtf", ".csv", ".json"}


def import_notes(vault_path: Path, source: Path, dry_run: bool = False) -> int:
    """Copy files from source into vault's inbox/. Never modifies originals.

    Preserves directory structure as filename prefixes:
    source/work/meeting.md -> inbox/work--meeting.md
    """
    inbox = vault_path / "inbox"
    if not dry_run:
        inbox.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0

    for dirpath, _, filenames in os.walk(source):
        for fname in filenames:
            src = Path(dirpath) / fname
            if src.suffix.lower() not in IMPORTABLE_SUFFIXES:
                skipped += 1
                continue

            # Build a flat name preserving directory context
            rel = src.relative_to(source)
            parts = list(rel.parts)
            if len(parts) > 1:
                flat_name = "--".join(parts[:-1]) + "--" + parts[-1]
            else:
                flat_name = parts[0]

            dest = inbox / flat_name
            # Avoid overwriting
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                n = 1
                while dest.exists():
                    dest = inbox / f"{stem}-{n}{suffix}"
                    n += 1

            if dry_run:
                print(f"  WOULD COPY: {rel} -> inbox/{dest.name}")
            else:
                import shutil
                shutil.copy2(str(src), str(dest))
            copied += 1

    print(f"  {'Would copy' if dry_run else 'Copied'} {copied} files to inbox/ ({skipped} skipped, unsupported type)")
    if not dry_run:
        print(f"  Originals at {source} are untouched.")
        print(f"  Run 'process inbox' or /secondbrain:ingest to process the imported notes.")
    return copied


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def run_verify(vault_path: Path) -> Optional[Dict]:
    """Run verify_vault.py and return summary."""
    script = Path(__file__).parent / "verify_vault.py"
    if not script.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(vault_path), "--json", "--quiet"],
            capture_output=True, text=True, timeout=30,
        )
        return json.loads(result.stdout).get("summary", {})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def run_rebuild_manifest(vault_path: Path, dry_run: bool = False) -> bool:
    """Run rebuild_manifest.py."""
    script = Path(__file__).parent / "rebuild_manifest.py"
    if not script.exists():
        return False
    if dry_run:
        print("  WOULD RUN: rebuild_manifest.py")
        return True
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(vault_path)],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Install marker — .secondbrain-installed with vault_id + timestamps
# ---------------------------------------------------------------------------

def write_install_marker(
    vault_path: Path,
    results: Dict[str, Any],
    dry_run: bool,
) -> None:
    """Write `${vault_path}/.secondbrain-installed` and register the vault.

    Marker lifecycle (idempotent):
      - On first install: generate a new UUID4 for `vault_id`, stamp
        `installed_at` and `last_init_at` with today's date.
      - On re-run: preserve the existing `vault_id` and `installed_at`; only
        update `last_init_at`.
      - Legacy markers missing these fields get them added without losing
        any existing `steps` / `errors` / `platform` keys.
      - An invalid (non-UUID) `vault_id` is replaced with a fresh UUID4 —
        defends against hand-edits that drop garbage into the file.

    After writing the marker, the vault is registered in
    `~/.config/secondbrain/vaults.json` via `setup_steps.add_vault_to_config`.
    Re-running is a no-op (the config helper deduplicates by vault_id).

    If `dry_run=True`, neither the marker nor the vaults.json entry is touched.
    The marker lives inside the vault (not in ~/) so it doesn't pollute the
    user's home directory during tests or CI runs.
    """
    marker = vault_path / ".secondbrain-installed"

    # Load any existing marker — we want to preserve vault_id + installed_at.
    existing: Dict[str, Any] = {}
    if marker.exists():
        try:
            raw = marker.read_text()
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    existing = parsed
        except (OSError, json.JSONDecodeError):
            # Corrupt marker — treat as empty and let the write overwrite it.
            existing = {}

    # Preserve or mint vault_id. An existing-but-invalid UUID is regenerated.
    vault_id = existing.get("vault_id")
    if not isinstance(vault_id, str) or not vault_id:
        vault_id = str(uuid.uuid4())
    else:
        try:
            uuid.UUID(vault_id)
        except ValueError:
            vault_id = str(uuid.uuid4())

    today = date.today().isoformat()
    installed_at = existing.get("installed_at") if isinstance(existing.get("installed_at"), str) else None
    if not installed_at:
        installed_at = today

    results["vault_id"] = vault_id
    results["installed_at"] = installed_at
    results["last_init_at"] = today

    if dry_run:
        print(f"\nWould write marker to {marker}")
        return

    marker.write_text(json.dumps(results, indent=2))
    print(f"\nResults written to {marker}")

    # Register this vault in ~/.config/secondbrain/vaults.json. Import locally
    # so setup_steps is only loaded when init actually runs (avoids circular
    # import risk and keeps module-level import surface small).
    try:
        import setup_steps  # type: ignore[reportMissingImports]
    except ImportError as exc:
        print(f"  Note: could not import setup_steps ({exc}); vault not registered")
        return

    reg = setup_steps.add_vault_to_config(
        vault_path=vault_path,
        vault_id=vault_id,
        name=vault_path.name,
        role="personal",
    )
    if reg.success:
        print(f"  Vault registered: {reg.message}")
    else:
        print(f"  Warning: vault registration failed: {reg.message}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Automated Obsidian setup for secondbrain")
    p.add_argument("--vault-path", type=Path, help="Path to vault (default: auto-detect or ~/secondbrain-vault)")
    p.add_argument("--import-from", type=Path, help="Copy existing notes from this path into the vault's inbox")
    p.add_argument("--skip-install", action="store_true", help="Skip Obsidian installation")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run
    results: Dict[str, Any] = {"steps": [], "errors": []}

    print("=" * 50)
    print("  secondbrain — Automated Setup")
    print("=" * 50)

    # Step 1: Detect platform
    plat = detect_platform()
    wsl = is_wsl()
    print(f"\n[1/8] Platform: {plat}" + (" (WSL)" if wsl else ""))
    results["platform"] = plat
    results["wsl"] = wsl

    # Step 2: Find or install Obsidian + launch
    print(f"\n[2/8] Obsidian")
    obsidian_path = find_obsidian(plat)
    if obsidian_path:
        print(f"  Found: {obsidian_path}")
        results["steps"].append("obsidian: found")
    elif args.skip_install:
        print("  Not found (--skip-install, skipping)")
        results["steps"].append("obsidian: skipped")
    else:
        print("  Not found — installing...")
        if install_obsidian(plat, dry_run):
            results["steps"].append("obsidian: installed")
        else:
            results["errors"].append("obsidian: install failed")
            print("  Install Obsidian manually: https://obsidian.md/download")

    # Step 2b: Ensure Obsidian is running
    if not args.skip_install:
        ensure_obsidian_running(plat, dry_run)

    # Step 3: Detect or create vault
    print(f"\n[3/8] Vault")
    vault_path = args.vault_path
    if vault_path:
        vault_path = vault_path.expanduser().resolve()
        print(f"  Using specified path: {vault_path}")
    else:
        existing = find_existing_vaults(plat)
        if existing:
            print(f"  Found existing vault(s):")
            for name, path in existing:
                print(f"    - {name}: {path}")
            vault_path = existing[0][1]
            print(f"  Using: {vault_path}")
        else:
            vault_path = default_vault_path()
            print(f"  No existing vaults found. Using default: {vault_path}")

    if not vault_path.exists():
        if dry_run:
            print(f"  WOULD CREATE vault directory: {vault_path}")
        else:
            vault_path.mkdir(parents=True, exist_ok=True)
            print(f"  Created vault directory: {vault_path}")

    results["vault_path"] = str(vault_path)

    # Step 4: Scaffold vault structure
    print(f"\n[4/8] Scaffold vault")
    created = scaffold_vault(vault_path, dry_run)
    if created:
        print(f"  Created {created} items")
        results["steps"].append(f"scaffold: {created} items")
    else:
        print("  All directories and files already exist")
        results["steps"].append("scaffold: already complete")

    # Step 4b: Import notes (if --import-from specified)
    if args.import_from:
        source = args.import_from.expanduser().resolve()
        print(f"\n[4b] Import notes from {source}")
        if not source.is_dir():
            print(f"  Error: source path not found: {source}")
            results["errors"].append(f"import: source not found")
        else:
            count = import_notes(vault_path, source, dry_run)
            results["steps"].append(f"import: {count} files copied to inbox/")

    # Step 5: Install plugins
    print(f"\n[5/8] Plugins")
    plugin_ids = []
    for name, info in PLUGINS.items():
        plugin_id = info["id"]
        plugin_ids.append(plugin_id)
        # Try CLI first (requires Obsidian running)
        if install_plugin_via_cli(plugin_id, dry_run):
            results["steps"].append(f"plugin {name}: ok (CLI)")
        elif install_plugin(vault_path, name, info, dry_run):
            results["steps"].append(f"plugin {name}: ok (download)")
        else:
            results["errors"].append(f"plugin {name}: failed")

    enable_plugins(vault_path, plugin_ids, dry_run)

    # Step 6: Node.js
    print(f"\n[6/8] Node.js")
    if ensure_node_installed(plat, dry_run):
        results["steps"].append("nodejs: ok")
    else:
        results["errors"].append("nodejs: not available — MCP bridge won't work")

    # Step 7: MCP connection
    print(f"\n[7/8] MCP connection")
    port, api_key = configure_mcp_plugin(vault_path, dry_run)

    shell = detect_shell()
    print(f"  Shell: {shell}")
    if port:
        # T6: delegate env var writing to setup_steps.setup_env_vars so
        # init and doctor share exactly one code path for this write.
        # The inline set_env_vars() / _set_env_vars_powershell() helpers
        # stay around because setup_steps ultimately calls back into them;
        # this branch just routes the main() flow through the shared wrapper.
        try:
            import setup_steps  # type: ignore[reportMissingImports]
        except ImportError as exc:
            print(f"  Note: setup_steps not importable ({exc}); "
                  "falling back to inline set_env_vars")
            set_env_vars(port, api_key, shell, dry_run)
            results["steps"].append(f"env vars: port={port}")
        else:
            env_result = setup_steps.setup_env_vars(
                api_key=api_key, port=port, dry_run=dry_run,
            )
            if env_result.success:
                print(f"  {env_result.message}")
                results["steps"].append(f"env vars: port={port}")
            else:
                print(f"  {env_result.message}")
                results["errors"].append(
                    f"env vars: {env_result.error or env_result.message}"
                )
    else:
        results["errors"].append("mcp plugin: no config")

    # Step 8: Verify
    print(f"\n[8/8] Verify")
    if dry_run:
        print("  WOULD RUN: verify_vault.py + rebuild_manifest.py")
    else:
        run_rebuild_manifest(vault_path)
        summary = run_verify(vault_path)
        if summary:
            errors = summary.get("errors", 0)
            warnings = summary.get("warnings", 0)
            if errors == 0 and warnings == 0:
                print("  All checks passed")
            else:
                print(f"  {errors} errors, {warnings} warnings (run verify_vault.py for details)")
            results["steps"].append(f"verify: {errors}e/{warnings}w")
        else:
            print("  Verification skipped (verify_vault.py not found)")

    # Summary
    print(f"\n{'=' * 50}")
    if results["errors"]:
        print("Setup completed with issues:")
        for e in results["errors"]:
            print(f"  - {e}")
    else:
        print("Setup complete!")

    print(f"\nVault: {vault_path}")

    if not api_key:
        print("\nNext step: Open Obsidian, enable the connect-mcp plugin,")
        print("then re-run this script to pick up the generated API key.")

    # Write marker inside the vault (not home dir — avoids polluting user env during tests).
    # write_install_marker handles vault_id + timestamps idempotently and
    # registers the vault in ~/.config/secondbrain/vaults.json on real runs.
    write_install_marker(vault_path, results, dry_run)

    return 1 if results["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
