"""cmd_init is the very first command a new, non-technical user runs.

It must never write outside the user's own config directory, and it must catch
the single most common setup mistake -- pointing at the wrong folder -- while
the user is still thinking about paths, not three commands later when 'status'
silently reports zero files.
"""

import io
import json
import os
from contextlib import redirect_stdout
from unittest import mock

import pytest

from media_agent.doctor import _confirm_library_root, cmd_init


class Args:
    pass


def run_init(monkeypatch, tmp_path, inputs):
    """Run cmd_init with HOME redirected into tmp_path and stdin scripted."""
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path / "home")
    it = iter(inputs)
    monkeypatch.setattr('builtins.input', lambda *a: next(it))
    monkeypatch.setattr('shutil.which', lambda *a: None)   # no ffprobe on this box
    out = io.StringIO()
    with redirect_stdout(out):
        cmd_init(Args())
    return out.getvalue()


def config_path(tmp_path):
    return tmp_path / "home" / ".config" / "media-agent" / "config.json"


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Media"
    (root / "Movies").mkdir(parents=True)
    (root / "TV Shows").mkdir()
    return root


class TestWritesOnlyToUserConfigDir:
    def test_config_lands_under_home(self, tmp_path, library, monkeypatch):
        run_init(monkeypatch, tmp_path, [str(library), ""])
        assert config_path(tmp_path).exists()

    def test_nothing_written_outside_home(self, tmp_path, library, monkeypatch):
        before = set(tmp_path.rglob("*"))
        run_init(monkeypatch, tmp_path, [str(library), ""])
        after = set(tmp_path.rglob("*"))
        new_files = {p for p in (after - before) if p.is_file()}
        cfg_dir = config_path(tmp_path).parent
        assert all(cfg_dir in p.parents for p in new_files), (
            f"init wrote a file outside its own config directory: {new_files}"
        )

    def test_declining_overwrite_leaves_existing_config_untouched(self, tmp_path,
                                                                   library, monkeypatch):
        config_path(tmp_path).parent.mkdir(parents=True)
        config_path(tmp_path).write_text('{"library_root": "keep-me"}', encoding='utf-8')
        run_init(monkeypatch, tmp_path, ["n"])
        assert json.loads(config_path(tmp_path).read_text())["library_root"] == "keep-me"


class TestLibraryRootValidation:
    def test_accepts_a_real_folder_with_sections(self, tmp_path, library, monkeypatch):
        out = run_init(monkeypatch, tmp_path, [str(library), ""])
        saved = json.loads(config_path(tmp_path).read_text(encoding='utf-8'))
        assert saved["library_root"] == str(library.resolve())
        assert "Found: Movies, TV Shows" in out

    def test_rejects_nonexistent_path_and_reprompts(self, tmp_path, library, monkeypatch):
        out = run_init(monkeypatch, tmp_path,
                       [str(tmp_path / "DoesNotExist"), str(library), ""])
        assert "does not exist" in out
        assert config_path(tmp_path).exists()

    def test_quoted_path_is_accepted(self, tmp_path, library, monkeypatch):
        """Users commonly paste a path wrapped in quotes from Explorer."""
        run_init(monkeypatch, tmp_path, [f'"{library}"', ""])
        saved = json.loads(config_path(tmp_path).read_text(encoding='utf-8'))
        assert saved["library_root"] == str(library.resolve())

    def test_empty_input_reprompts_rather_than_crashing(self, tmp_path, library, monkeypatch):
        out = run_init(monkeypatch, tmp_path, ["", str(library), ""])
        assert "required" in out.lower()
        assert config_path(tmp_path).exists()


class TestTmdbTokenPrompt:
    """P2-4: the token prompt must not echo on a real terminal (getpass),
    but must still work under the piped/non-interactive stdin this test
    suite (and some CI/non-technical-user setups) uses.
    """

    def test_noninteractive_stdin_uses_plain_input_not_getpass(self, tmp_path, library,
                                                                monkeypatch):
        """isatty() is False under the test harness -- confirms init doesn't
        hang or blow up trying to control a terminal that isn't there."""
        import media_agent.doctor as doctor_mod

        def boom(*a, **kw):
            raise AssertionError("getpass.getpass should not be called on non-tty stdin")
        monkeypatch.setattr(doctor_mod, 'getpass', type('X', (), {'getpass': staticmethod(boom)}))

        out = run_init(monkeypatch, tmp_path, [str(library), "my-token-123"])
        saved = json.loads(config_path(tmp_path).read_text(encoding='utf-8'))
        assert saved["tmdb_read_access_token"] == "my-token-123"

    def test_interactive_stdin_uses_getpass(self, tmp_path, library, monkeypatch):
        """On a real terminal, use getpass so the token doesn't echo."""
        monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path / "home")
        monkeypatch.setattr('shutil.which', lambda *a: None)
        inputs = iter([str(library), ""])
        monkeypatch.setattr('builtins.input', lambda *a: next(inputs))
        monkeypatch.setattr('sys.stdin.isatty', lambda: True)

        calls = []
        def fake_getpass(prompt=''):
            calls.append(prompt)
            return "hidden-token"
        monkeypatch.setattr('media_agent.doctor.getpass.getpass', fake_getpass)

        out = io.StringIO()
        with redirect_stdout(out):
            cmd_init(Args())

        assert calls, "getpass.getpass was never called on a tty"
        saved = json.loads(config_path(tmp_path).read_text(encoding='utf-8'))
        assert saved["tmdb_read_access_token"] == "hidden-token"

    def test_config_file_restricted_to_owner_when_token_present(self, tmp_path, library,
                                                                 monkeypatch):
        """The config file now holds a credential -- lock it down where the
        OS supports it (POSIX chmod). Forces the POSIX branch via the
        `platform_name` seam rather than monkeypatching the real os.name,
        which pathlib itself reads and would break unrelated code on
        whichever OS actually runs this test (this one runs on Windows)."""
        import media_agent.doctor as doctor_mod

        chmod_calls = []
        monkeypatch.setattr(doctor_mod.os, 'chmod',
                            lambda path, mode: chmod_calls.append((path, mode)))
        real_restrict = doctor_mod._restrict_config_permissions
        monkeypatch.setattr(
            doctor_mod, '_restrict_config_permissions',
            lambda path: real_restrict(path, platform_name='posix'))

        run_init(monkeypatch, tmp_path, [str(library), "a-real-token"])

        assert chmod_calls, "config file was not chmod'd after writing a token"
        _, mode = chmod_calls[0]
        assert mode == 0o600

    def test_no_chmod_call_when_no_token_given(self, tmp_path, library, monkeypatch):
        import media_agent.doctor as doctor_mod

        chmod_calls = []
        monkeypatch.setattr(doctor_mod.os, 'chmod',
                            lambda path, mode: chmod_calls.append((path, mode)))
        real_restrict = doctor_mod._restrict_config_permissions
        monkeypatch.setattr(
            doctor_mod, '_restrict_config_permissions',
            lambda path: real_restrict(path, platform_name='posix'))

        run_init(monkeypatch, tmp_path, [str(library), ""])

        assert not chmod_calls, "chmod should not run when no token was ever written"

    def test_token_file_created_with_restrictive_mode_not_chmod_after(self, tmp_path,
                                                                        library, monkeypatch):
        """A plain open() creates the file at the umask's default permissions
        (often group/other-readable), leaving a window between creation and
        a later chmod where the token sits on disk more exposed than
        intended. os.open() with an explicit mode applies it atomically at
        creation instead -- assert that path is actually used for the
        token-bearing write, not just that chmod eventually runs (the
        pre-existing tests above already cover that part).
        """
        import media_agent.doctor as doctor_mod

        real_os_open = doctor_mod.os.open
        calls = []

        def spy_open(path, flags, mode=0o777):
            calls.append((path, flags, mode))
            return real_os_open(path, flags, mode)
        monkeypatch.setattr(doctor_mod.os, 'open', spy_open)

        run_init(monkeypatch, tmp_path, [str(library), "a-real-token"])

        assert calls, "the token-bearing config file must be created via os.open() " \
                       "with an explicit restrictive mode, not a plain open()"
        _, flags, mode = calls[0]
        assert mode == 0o600, f"expected the file created at mode 0o600, got {oct(mode)}"
        assert flags & os.O_CREAT, "must create the file (not just open an existing one)"

    def test_no_token_config_uses_plain_open_not_os_open(self, tmp_path, library, monkeypatch):
        """The restrictive-mode path only matters once a credential is being
        written -- a token-free config has nothing to protect, so it should
        take the ordinary open() path unchanged."""
        import media_agent.doctor as doctor_mod

        calls = []
        monkeypatch.setattr(doctor_mod.os, 'open',
                            lambda *a, **kw: calls.append(a) or pytest.fail(
                                "os.open should not be used when no token is present"))

        run_init(monkeypatch, tmp_path, [str(library), ""])
        assert not calls

    def test_restrict_config_permissions_is_a_noop_on_windows(self, tmp_path, monkeypatch):
        import media_agent.doctor as doctor_mod

        calls = []
        monkeypatch.setattr(doctor_mod.os, 'chmod',
                            lambda path, mode: calls.append((path, mode)))
        f = tmp_path / "config.json"
        f.write_text('{}', encoding='utf-8')
        doctor_mod._restrict_config_permissions(f, platform_name='nt')
        assert not calls


class TestConfirmLibraryRoot:
    """The check that catches the #1 real-world setup mistake."""

    def test_folder_with_section_dirs_is_accepted_silently(self, library):
        assert _confirm_library_root(library) is True

    def test_pointing_at_a_section_itself_is_flagged(self, library, monkeypatch, capsys):
        """Someone points straight at .../Movies instead of its parent."""
        movies = library / "Movies"
        (movies / "film.mkv").touch()
        monkeypatch.setattr('builtins.input', lambda *a: 'n')
        accepted = _confirm_library_root(movies)
        out = capsys.readouterr().out
        assert accepted is False
        assert "might BE one of those folders" in out
        assert str(library) in out, "must name the parent as the likely fix"

    def test_user_can_override_and_use_the_flagged_folder_anyway(self, library, monkeypatch):
        movies = library / "Movies"
        monkeypatch.setattr('builtins.input', lambda *a: 'y')
        assert _confirm_library_root(movies) is True

    def test_empty_unrelated_folder_is_flagged_but_not_misdiagnosed(self, tmp_path,
                                                                     monkeypatch):
        empty = tmp_path / "SomethingElse"
        empty.mkdir()
        monkeypatch.setattr('builtins.input', lambda *a: 'n')
        out = io.StringIO()
        with redirect_stdout(out):
            accepted = _confirm_library_root(empty)
        assert accepted is False
        assert "empty" in out.getvalue().lower()

    def test_unreadable_folder_is_rejected_not_crashed(self, tmp_path):
        missing = tmp_path / "Gone"
        # never created
        assert _confirm_library_root(missing) is False
