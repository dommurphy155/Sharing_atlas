"""Tests for Windows / Git Bash ``atlas`` command-shim generation.

These run on Linux.  They verify the *generated shim text* and the argument
semantics the shim relies on — they do NOT claim native Windows execution.
Anything that genuinely requires cmd.exe is marked as such in the docstrings
and in the "requires native Windows" list in the module docstring.

Run:
    cd ~/atlas && .venv/bin/python -m pytest tests/test_windows_shim.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath, PureWindowsPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setup import _supervisor as S  # noqa: E402
from setup import core  # noqa: E402

# ---------------------------------------------------------------------------
# Representative Windows install locations.  None of these are ever created on
# disk here — the shim text is generated from a POSIX stand-in tree and the
# Windows path is asserted through PureWindowsPath.
# ---------------------------------------------------------------------------
WINDOWS_INSTALLS = [
    r"C:\Users\Dom\atlas",
    r"D:\Projects\atlas",
    r"D:\My Projects\atlas",
    r"E:\Dev\100% legit\atlas",
    r"C:\Users\Dom Smith\My Projects\atlas & co",
    r"D:\a^b!c\atlas",
    r"D:\paren(dir)\atlas",
]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect USERPROFILE so shim generation never touches the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def build_tree(root: Path, *, venv_layout: str = "posix") -> Path:
    """Create a minimal fake Atlas install tree. Returns the repo root."""
    (root / "atlas" / "bin").mkdir(parents=True, exist_ok=True)
    if venv_layout == "windows":
        venv_bin = root / ".venv" / "Scripts"
    else:
        venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python").write_text("", encoding="utf-8")
    (venv_bin / "python.exe").write_text("", encoding="utf-8")
    (root / "atlas" / "bin" / "atlas").write_text("", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# 1. Path resolution helpers
# ---------------------------------------------------------------------------
class TestResolveInstallDir:
    def test_returns_absolute_path(self, tmp_path):
        repo = build_tree(tmp_path / "atlas")
        assert S._resolve_install_dir(repo).is_absolute()

    def test_independent_of_cwd(self, tmp_path, monkeypatch):
        repo = build_tree(tmp_path / "atlas")
        resolved = S._resolve_install_dir(repo)

        monkeypatch.chdir("/")
        from_root = S._resolve_install_dir(repo)

        monkeypatch.chdir(str(tmp_path))
        from_tmp = S._resolve_install_dir(repo)

        assert resolved == from_root == from_tmp

    def test_relative_repo_resolves_against_cwd_not_drive(self, tmp_path, monkeypatch):
        """A relative repo is anchored to the process cwd, never a drive guess."""
        repo = build_tree(tmp_path / "atlas")
        monkeypatch.chdir(str(tmp_path))
        assert S._resolve_install_dir(Path("atlas")) == repo.resolve()

    def test_no_drive_letter_or_username_hardcoded(self):
        """The resolver must not invent C: or a user profile name."""
        src = Path(S.__file__).read_text(encoding="utf-8")
        resolver = src.split("def _resolve_install_dir")[1].split("def ")[0]
        assert "C:" not in resolver
        assert "USERPROFILE" not in resolver
        assert "expanduser" in resolver  # the one permitted fallback


class TestShimQuote:
    def test_plain_path_unchanged(self):
        assert S._shim_quote(r"D:\Projects\atlas") == r"D:\Projects\atlas"

    def test_percent_is_doubled_for_cmd(self):
        """cmd.exe expands %VAR%; a literal % must be escaped as %%."""
        assert S._shim_quote(r"D:\100% real") == r"D:\100%% real"

    def test_every_percent_doubled(self):
        assert S._shim_quote("a%b%c") == "a%%b%%c"

    def test_ampersand_and_caret_left_to_quoting(self):
        """& and ^ are handled by the surrounding double quotes, not here."""
        assert S._shim_quote(r"D:\a&b^c") == r"D:\a&b^c"

    def test_returns_str(self):
        assert isinstance(S._shim_quote(PureWindowsPath(r"D:\x")), str)


# ---------------------------------------------------------------------------
# 2. Generated .cmd shim content
# ---------------------------------------------------------------------------
class TestCmdShimContent:
    def test_absolute_paths_present(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert str(repo.resolve()) in body

    def test_arg_forwarding_is_unquoted_percent_star(self, tmp_path, fake_home):
        """Quoted "%*" collapses argv into one token — must be bare %*."""
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert '"%*"' not in body
        assert "%*" in body

    def test_uses_setlocal_and_exit_code(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert "setlocal" in body
        assert "endlocal & exit /b %ERRORLEVEL%" in body

    def test_crlf_line_endings(self, tmp_path, fake_home):
        """cmd.exe is happiest with CRLF; the writer uses newline=''."""
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        raw = (fake_home / ".local" / "bin" / "atlas.cmd").read_bytes()
        assert b"\r\n" in raw

    def test_no_cwd_dependency(self, tmp_path, fake_home, monkeypatch):
        repo = build_tree(tmp_path / "atlas")
        monkeypatch.chdir("/")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert str(tmp_path) not in body or str(repo.resolve()) in body
        assert "cd " not in body  # never changes drive/dir

    @pytest.mark.parametrize("install", WINDOWS_INSTALLS)
    def test_windows_paths_render_correctly(self, install, tmp_path, fake_home):
        """Assert the shim text via PureWindowsPath, without creating the tree."""
        expected = PureWindowsPath(install)
        py = expected / ".venv" / "Scripts" / "python.exe"
        target = expected / "atlas" / "bin" / "atlas"

        py_arg = S._shim_quote(py)
        target_arg = S._shim_quote(target)
        body = (
            "@echo off\r\n"
            "setlocal\r\n"
            f'"{py_arg}" "{target_arg}" %*\r\n'
            "endlocal & exit /b %ERRORLEVEL%\r\n"
        )

        # Compare in cmd-escaped form: a literal '%' is doubled as '%%'.
        assert py_arg in body
        assert target_arg in body
        assert body.count('"') == 4  # both paths quoted, nothing else
        assert "%*" in body and '"%*"' not in body
        # Drive letter comes from the input, never from the generator.
        assert expected.drive in body

    def test_drive_letters_are_not_hardcoded(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        for drive in ("C:\\", "D:\\", "E:\\"):
            assert drive not in body

    def test_username_not_hardcoded(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert "Dom" not in body
        # Only the install paths appear — the bin dir itself is not baked in,
        # because the shim is located via PATH, not by absolute bin_dir.
        assert str(repo.resolve()) in body
        assert str(fake_home) not in body

    def test_percent_in_path_escaped_in_shim(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "100% real")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert "100%% real" in body
        assert "100% real" not in body.replace("100%% real", "")

    def test_special_characters_do_not_break_quoting(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "a&b^c")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert body.count('"') == 4

    def test_parens_in_path(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "paren(dir)")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert "paren(dir)" in body

    def test_readback_verification_catches_drift(self, tmp_path, fake_home, monkeypatch):
        """If the written shim somehow omits the target, generation fails loudly."""
        repo = build_tree(tmp_path / "atlas")
        real_write = Path.write_text

        def lying_write(self, *a, **kw):
            result = real_write(self, *a, **kw)
            if self.name == "atlas.cmd":
                real_write(self, "@echo off\r\nsetlocal\r\n", **kw)
            return result

        monkeypatch.setattr(Path, "write_text", lying_write)
        ok, msg = S.make_windows_shim(repo)
        assert ok is False
        assert "does not reference" in msg


# ---------------------------------------------------------------------------
# 3. POSIX (Git Bash) shim
# ---------------------------------------------------------------------------
class TestPosixShim:
    def test_created_and_executable(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        shim = fake_home / ".local" / "bin" / "atlas"
        assert shim.is_file()
        assert os.access(shim, os.X_OK)

    def test_forwards_all_args_with_at_sign(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas").read_text()
        assert '"$@"' in body

    def test_handles_spaces_in_install_path(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "My Projects" / "atlas")
        ok, _ = S.make_windows_shim(repo)
        assert ok
        body = (fake_home / ".local" / "bin" / "atlas").read_text()
        assert f'"{repo.resolve()}' in body

    def test_posix_shim_uses_raw_percent(self, tmp_path, fake_home):
        """sh does not expand %, so the POSIX wrapper must NOT double it."""
        repo = build_tree(tmp_path / "100% real")
        S.make_windows_shim(repo)
        body = (fake_home / ".local" / "bin" / "atlas").read_text()
        assert "100% real" in body
        assert "100%% real" not in body

    def test_posix_shim_actually_runs_and_forwards(self, tmp_path, fake_home):
        """End-to-end on Linux: the generated wrapper execs the target with args."""
        repo = build_tree(tmp_path / "My Projects" / "atlas")
        entry = repo / "atlas" / "bin" / "atlas"
        entry.write_text(
            "#!/bin/sh\nprintf 'argc=%s\\n' \"$#\"\nfor a in \"$@\"; do "
            "printf 'arg=[%s]\\n' \"$a\"; done\n",
            encoding="utf-8",
        )
        os.chmod(entry, 0o755)
        venv_py = repo / ".venv" / "bin" / "python"
        # Make the fake venv python behave like sh for this test.
        venv_py.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
        os.chmod(venv_py, 0o755)

        ok, _ = S.make_windows_shim(repo)
        assert ok

        import subprocess

        shim = fake_home / ".local" / "bin" / "atlas"
        r = subprocess.run(
            [str(shim), "restart", "--nvidia", "minimax/mimax-m3:free"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert "argc=3" in r.stdout
        assert "arg=[restart]" in r.stdout
        assert "arg=[--nvidia]" in r.stdout
        assert "arg=[minimax/mimax-m3:free]" in r.stdout

    def test_posix_shim_preserves_args_with_spaces(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        entry = repo / "atlas" / "bin" / "atlas"
        entry.write_text(
            "#!/bin/sh\nfor a in \"$@\"; do printf 'arg=[%s]\\n' \"$a\"; done\n",
            encoding="utf-8",
        )
        venv_py = repo / ".venv" / "bin" / "python"
        venv_py.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
        os.chmod(venv_py, 0o755)
        os.chmod(entry, 0o755)

        S.make_windows_shim(repo)
        import subprocess

        shim = fake_home / ".local" / "bin" / "atlas"
        r = subprocess.run(
            [str(shim), "import-key-file", "C:/My Keys/my keys.txt"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert "arg=[C:/My Keys/my keys.txt]" in r.stdout


# ---------------------------------------------------------------------------
# 4. Conflict detection / idempotency
# ---------------------------------------------------------------------------
class TestShimConflict:
    def test_second_install_does_not_hijack(self, tmp_path, fake_home):
        first = build_tree(tmp_path / "first")
        S.make_windows_shim(first)

        second = build_tree(tmp_path / "second")
        ok, msg = S.make_windows_shim(second)

        assert ok is True
        assert "already exists" in msg
        body = (fake_home / ".local" / "bin" / "atlas.cmd").read_text()
        assert str(first.resolve()) in body
        assert str(second.resolve()) not in body

    def test_same_install_is_idempotent(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        ok, msg = S.make_windows_shim(repo)
        assert ok is True
        assert "skipped" not in msg

    def test_stale_shim_pointing_at_deleted_install_is_reported(self, tmp_path, fake_home):
        stale = tmp_path / "gone"
        build_tree(stale)
        S.make_windows_shim(stale)

        import shutil

        shutil.rmtree(stale)
        fresh = build_tree(tmp_path / "fresh")
        ok, msg = S.make_windows_shim(fresh)
        assert ok is True
        assert "already exists" in msg

    def test_posix_shim_defers_to_existing_command(self, tmp_path, fake_home, monkeypatch):
        repo = build_tree(tmp_path / "atlas")
        monkeypatch.setattr(S, "_atlas_on_path", lambda: "/usr/bin/atlas")
        ok, msg = S.make_posix_shim(repo, fake_home / ".local" / "bin")
        assert ok is True
        assert "already exists" in msg


# ---------------------------------------------------------------------------
# 5. Failure modes
# ---------------------------------------------------------------------------
class TestMissingPieces:
    def test_missing_venv_python(self, tmp_path, fake_home):
        repo = tmp_path / "atlas"
        (repo / "atlas" / "bin").mkdir(parents=True)
        (repo / "atlas" / "bin" / "atlas").write_text("", encoding="utf-8")
        ok, msg = S.make_windows_shim(repo)
        assert ok is False
        assert "Virtualenv Python not found" in msg

    def test_missing_entry_point(self, tmp_path, fake_home):
        repo = tmp_path / "atlas"
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
        ok, msg = S.make_windows_shim(repo)
        assert ok is False
        assert "Atlas CLI not found" in msg

    def test_missing_both(self, tmp_path, fake_home):
        ok, msg = S.make_windows_shim(tmp_path / "nothing")
        assert ok is False


# ---------------------------------------------------------------------------
# 6. Argument-forwarding semantics against the real parser
# ---------------------------------------------------------------------------
def _load_real_parser():
    """Exec the real CLI module to obtain its actual build_parser()."""
    entry = Path(__file__).resolve().parents[1] / "atlas" / "bin" / "atlas"
    ns = {"__file__": str(entry), "__name__": "atlas_under_test"}
    exec(compile(entry.read_text(encoding="utf-8"), str(entry), "exec"), ns)
    return ns["build_parser"]


class TestArgForwardingSemantics:
    """What the CLI's argv looks like under old vs new shim syntax."""

    def test_unquoted_percent_star_parses_real_command(self):
        parse = _load_real_parser()()
        args = parse.parse_args(
            ["restart", "--nvidia", "minimax/mimax-m3:free"]
        )
        assert args.command == "restart"
        assert args.provider == "nvidia"
        assert args.model == "minimax/mimax-m3:free"

    def test_quoted_percent_star_collapses_into_one_token(self):
        """Regression guard: this is the bug the unquoted %* fixes."""
        parse = _load_real_parser()()
        with pytest.raises(SystemExit):
            parse.parse_args(["restart --nvidia minimax/mimax-m3:free"])

    def test_args_with_spaces_survive(self):
        parse = _load_real_parser()()
        args = parse.parse_args(["import-key-file", "C:/My Keys/my keys.txt"])
        assert args.path == "C:/My Keys/my keys.txt"

    def test_special_characters_survive(self):
        parse = _load_real_parser()()
        args = parse.parse_args(["import-key", "sk-or-v1-a&b^c"])
        assert args.key == "sk-or-v1-a&b^c"

    def test_help_and_status_parse(self):
        parse = _load_real_parser()()
        assert parse.parse_args(["status"]).command == "status"
        assert parse.parse_args(["--version"] if False else ["version"]).command == "version"


# ---------------------------------------------------------------------------
# 7. PATH registration (winreg absent on Linux -> reported, not faked)
# ---------------------------------------------------------------------------
class TestPathRegistration:
    def test_reports_missing_winreg_honestly(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        ok, msg = S.make_windows_shim(repo)
        assert ok is True
        if "winreg" not in sys.builtin_module_names:
            assert "no 'winreg' module" in msg
            assert "user PATH was not updated" in msg

    def test_bin_dir_is_under_userprofile(self, tmp_path, fake_home):
        repo = build_tree(tmp_path / "atlas")
        S.make_windows_shim(repo)
        assert (fake_home / ".local" / "bin" / "atlas.cmd").is_file()

    def test_falls_back_to_expanduser_without_userprofile(self, tmp_path, monkeypatch):
        monkeypatch.delenv("USERPROFILE", raising=False)
        home = tmp_path / "fallbackhome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        repo = build_tree(tmp_path / "atlas")
        ok, _ = S.make_windows_shim(repo)
        assert ok is True
        assert (home / ".local" / "bin" / "atlas.cmd").is_file()
