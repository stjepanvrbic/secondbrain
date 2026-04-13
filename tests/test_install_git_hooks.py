"""Tests for install_git_hooks.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

import install_git_hooks  # type: ignore[reportMissingImports]


def test_only_real_git_hooks_are_installed():
    assert install_git_hooks.EXPECTED_HOOKS == ["pre-push"], (
        "post-push is not a real git hook here; install_git_hooks must only wire real hooks"
    )
