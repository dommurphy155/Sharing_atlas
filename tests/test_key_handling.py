"""Tests for installer key handling.

Two things are guarded here:

1. ``key_prefix_ok`` accepts every prefix the proxy's loader accepts, so the
   installer no longer warns on keys that would actually load.
2. The key file is never truncated, overwritten or reordered by the helpers
   under test.  Key loss is unrecoverable, so these are deliberately paranoid.

Run:
    cd ~/atlas && .venv/bin/python -m pytest tests/test_key_handling.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setup import core  # noqa: E402

# Prefixes the proxy loader accepts (proxy/keypool.py::_KEY_PREFIXES).
PROXY_ACCEPTED = [
    "sk-or-v1-abc",
    "sk-ant-api03-abc",
    "sk-proj-abc",
    "sk-openai-abc",
    "nvapi-abc",
]

NOT_KEYS = ["", "   ", "hf_abc", "key=abc", "Bearer sk-or-v1-abc", "sk", "nvapi"]


class TestKeyPrefixOk:
    @pytest.mark.parametrize("key", PROXY_ACCEPTED)
    def test_accepts_every_prefix_the_proxy_loads(self, key):
        """Regression: 'sk-or-' only used to reject valid non-OpenRouter keys."""
        assert core.key_prefix_ok(key) is True

    @pytest.mark.parametrize("key", NOT_KEYS)
    def test_rejects_non_keys(self, key):
        assert core.key_prefix_ok(key) is False

    def test_prefixes_match_proxy_loader(self):
        """The installer tuple and the proxy tuple must agree on sk- prefixes."""
        from proxy.keypool import _KEY_PREFIXES

        for prefix in _KEY_PREFIXES:
            assert core.key_prefix_ok(f"{prefix}xyz"), prefix

    def test_real_key_shape_accepted(self):
        """The 1170 keys on disk are 'sk-or-v1-'; that must stay valid."""
        assert core.key_prefix_ok("sk-or-v1-" + "a" * 64) is True


class TestKeyFileSafety:
    """Helpers must never lose keys."""

    def test_append_keys_preserves_existing(self, tmp_path):
        kf = tmp_path / "keys.txt"
        kf.write_text("sk-or-v1-aaa\nsk-or-v1-bbb\n", encoding="utf-8")

        added = core.append_keys(kf, ["sk-or-v1-ccc"])

        assert added == 1
        assert kf.read_text(encoding="utf-8").splitlines() == [
            "sk-or-v1-aaa", "sk-or-v1-bbb", "sk-or-v1-ccc",
        ]

    def test_append_keys_deduplicates_without_dropping(self, tmp_path):
        kf = tmp_path / "keys.txt"
        kf.write_text("sk-or-v1-aaa\n", encoding="utf-8")

        added = core.append_keys(kf, ["sk-or-v1-aaa", "sk-or-v1-bbb"])

        assert added == 1
        assert kf.read_text(encoding="utf-8").splitlines() == [
            "sk-or-v1-aaa", "sk-or-v1-bbb",
        ]

    def test_append_keys_is_noop_for_empty_input(self, tmp_path):
        kf = tmp_path / "keys.txt"
        original = "sk-or-v1-aaa\n"
        kf.write_text(original, encoding="utf-8")

        assert core.append_keys(kf, []) == 0
        assert kf.read_text(encoding="utf-8") == original

    def test_append_keys_adds_missing_trailing_newline(self, tmp_path):
        kf = tmp_path / "keys.txt"
        kf.write_text("sk-or-v1-aaa", encoding="utf-8")  # no trailing \n

        core.append_keys(kf, ["sk-or-v1-bbb"])

        assert kf.read_text(encoding="utf-8").splitlines() == [
            "sk-or-v1-aaa", "sk-or-v1-bbb",
        ]

    def test_read_keys_skips_blanks_and_comments(self, tmp_path):
        kf = tmp_path / "keys.txt"
        kf.write_text(
            "# comment\n\nsk-or-v1-aaa\n   \nsk-or-v1-bbb\n", encoding="utf-8"
        )
        assert core.read_keys(kf) == ["sk-or-v1-aaa", "sk-or-v1-bbb"]

    def test_read_keys_on_missing_file_is_empty(self, tmp_path):
        assert core.read_keys(tmp_path / "nope.txt") == []

    def test_no_helper_truncates_the_key_file(self, tmp_path):
        """Guard against reintroducing a key-clearing helper.

        A deleted ``_clear_windows_key_files`` used to write '' over the key
        file.  Nothing in the installer may do that.
        """
        import inspect

        import setup.wizard as wizard

        assert not hasattr(wizard, "_clear_windows_key_files")

        src = inspect.getsource(wizard)
        # No bare truncation of a key path anywhere in the wizard.
        assert 'write_text("", encoding="utf-8")' not in src

    def test_wizard_module_has_no_key_clearing_helper(self):
        import setup.wizard as wizard

        for name in dir(wizard):
            assert "clear" not in name.lower(), name
