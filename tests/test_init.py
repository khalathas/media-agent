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
from pathlib import Path
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

    def test_token_config_is_written_via_atomic_replace(self, tmp_path, library, monkeypatch):
        """The whole file is built at a temporary path first (with
        restrictive permissions applied there) and swapped into place with
        a single os.replace(), rather than ever opening config_path itself
        for writing -- so the destination is never exposed mid-write under
        whatever permissions it happens to have, whether it's brand new or
        an existing file being overwritten. See the overwrite-specific test
        below for the exposure this specifically closes.
        """
        import media_agent.doctor as doctor_mod

        real_replace = doctor_mod.os.replace
        calls = []

        def spy_replace(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)
        monkeypatch.setattr(doctor_mod.os, 'replace', spy_replace)

        run_init(monkeypatch, tmp_path, [str(library), "a-real-token"])

        assert calls, "the token-bearing config file must be swapped into place via os.replace()"
        src, dst = calls[0]
        assert str(dst) == str(config_path(tmp_path))
        assert str(src) != str(config_path(tmp_path)), \
            "must write to a temporary path, not open the destination directly"

    def test_overwriting_existing_config_never_truncates_it_in_place(self, tmp_path, library,
                                                                       monkeypatch):
        """The MAJOR-finding reproduction: cmd_init supports overwriting an
        existing config. An earlier version of the restrictive-mode fix
        only protected a *brand-new* file -- os.open()'s mode argument is
        ignored when O_CREAT finds the file already there, so overwriting
        an existing (possibly permissive) config wrote the new token
        straight into that file under its old permissions, with the
        restrictive chmod only landing afterward. The whole point of the
        fix is that the destination is never opened for writing at all;
        confirm that by inspecting its content from inside a spy on
        os.replace() itself, at the moment the swap happens.
        """
        import media_agent.doctor as doctor_mod

        cfg = config_path(tmp_path)
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"library_root": "old-value", "tmdb_read_access_token": "OLD-TOKEN"}',
                       encoding='utf-8')

        seen_at_replace_time = {}
        real_replace = doctor_mod.os.replace

        def spy_replace(src, dst):
            seen_at_replace_time['dest_content'] = Path(dst).read_text(encoding='utf-8')
            return real_replace(src, dst)
        monkeypatch.setattr(doctor_mod.os, 'replace', spy_replace)

        run_init(monkeypatch, tmp_path, ["y", str(library), "NEW-TOKEN"])

        assert seen_at_replace_time, "os.replace() was never called"
        assert "OLD-TOKEN" in seen_at_replace_time['dest_content'], (
            "the existing config must still hold its OLD content right up until "
            "the atomic swap -- if this fails, the new token was written into the "
            "existing file (and therefore under its existing permissions) before "
            "the swap, reopening the exact exposure window this fix closes"
        )
        saved = json.loads(cfg.read_text(encoding='utf-8'))
        assert saved["tmdb_read_access_token"] == "NEW-TOKEN"

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
