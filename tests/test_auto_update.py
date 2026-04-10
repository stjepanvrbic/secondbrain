"""Tests for auto_update.py — marketplace repo auto-pull."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from auto_update import find_marketplace, get_local_hash, main


class TestFindMarketplace:
    def test_returns_none_when_missing(self, tmp_path: Path):
        with patch("auto_update.MARKETPLACE_DIRS", [tmp_path / "nonexistent"]):
            assert find_marketplace() is None

    def test_finds_existing(self, tmp_path: Path):
        repo = tmp_path / "secondbrain"
        (repo / ".git").mkdir(parents=True)
        with patch("auto_update.MARKETPLACE_DIRS", [repo]):
            assert find_marketplace() == repo


class TestGetLocalHash:
    def test_returns_hash_in_real_repo(self):
        # Our own repo should return a hash
        repo = Path(__file__).resolve().parent.parent
        h = get_local_hash(repo)
        assert h is not None
        assert len(h) == 40

    def test_returns_none_for_nonrepo(self, tmp_path: Path):
        assert get_local_hash(tmp_path) is None


class TestMain:
    def test_no_marketplace_exits_clean(self):
        with patch("auto_update.find_marketplace", return_value=None):
            assert main([]) == 0

    def test_up_to_date_exits_clean(self):
        with patch("auto_update.find_marketplace", return_value=Path("/fake")), \
             patch("auto_update.get_local_hash", return_value="abc1234"), \
             patch("auto_update.get_remote_hash", return_value="abc1234"):
            assert main([]) == 0

    def test_check_mode_detects_update(self, capsys):
        with patch("auto_update.find_marketplace", return_value=Path("/fake")), \
             patch("auto_update.get_local_hash", return_value="aaa"), \
             patch("auto_update.get_remote_hash", return_value="bbb"):
            code = main(["--check"])
            assert code == 1
            assert "Update available" in capsys.readouterr().out

    def test_pulls_when_behind(self, capsys):
        with patch("auto_update.find_marketplace", return_value=Path("/fake")), \
             patch("auto_update.get_local_hash", side_effect=["aaa", "bbb"]), \
             patch("auto_update.get_remote_hash", return_value="bbb"), \
             patch("auto_update.pull", return_value=True):
            code = main([])
            assert code == 0
            assert "updated" in capsys.readouterr().out

    def test_silent_on_network_failure(self):
        with patch("auto_update.find_marketplace", return_value=Path("/fake")), \
             patch("auto_update.get_local_hash", return_value="aaa"), \
             patch("auto_update.get_remote_hash", return_value=None):
            assert main([]) == 0  # fails silently
