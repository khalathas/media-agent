"""P3-4: a case-only rename target must not be reported as a false
"already exists" conflict on a case-insensitive filesystem (Windows, and
macOS by default). os.path.exists(new_path) is True for both the real
target and the source itself in that scenario, since they're the same
file -- the old code treated that as "someone else already has this name"
and skipped the rename outright, even though nothing was actually wrong.
"""

import json
import os

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.probe import is_case_only_rename, rename_case_only
from media_agent.tmdb import cmd_tmdb_canonicalize


class Args:
    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = True
        self.include_ambiguous = False
        self.__dict__.update(kw)


def _filesystem_is_case_insensitive(tmp_path):
    """Empirically probe whether tmp_path's filesystem folds case, rather
    than assume by OS name -- macOS can be configured case-sensitive, so
    writing a real file and checking whether the other-cased name also
    resolves to it is the only way to know for certain on this host.
    """
    probe = tmp_path / "case-probe.tmp"
    probe.write_bytes(b"x")
    is_insensitive = (tmp_path / "CASE-PROBE.tmp").exists()
    probe.unlink()
    return is_insensitive


class TestIsCaseOnlyRename:
    def test_true_for_pure_case_difference_on_a_case_insensitive_filesystem(self, tmp_path):
        """is_case_only_rename's whole point is answering "does the OS
        consider these the same file" -- which is genuinely filesystem-
        dependent (the function itself uses os.path.normcase, a no-op on
        POSIX and case-folding on Windows). Found via a real CI run on
        ubuntu-latest, not local testing: an earlier version of this test
        asserted a fixed True regardless of platform, which happened to
        pass on this project's only locally-tested platform (Windows) for
        14 review rounds, then failed the very first time it ran on Linux
        -- correctly, since "the.matrix.mkv" and "The.Matrix.mkv" are two
        genuinely distinct files on a case-sensitive filesystem, not a
        same-file case difference. Probing real filesystem behavior
        (rather than assuming by OS name) keeps this meaningful even on a
        case-sensitive macOS configuration.
        """
        if not _filesystem_is_case_insensitive(tmp_path):
            pytest.skip("this filesystem is case-sensitive -- see the sibling test below")
        old = str(tmp_path / "the.matrix.mkv")
        new = str(tmp_path / "The.Matrix.mkv")
        assert is_case_only_rename(old, new)

    def test_false_for_pure_case_difference_on_a_case_sensitive_filesystem(self, tmp_path):
        """The other half of the platform split above: on a case-sensitive
        filesystem (Linux, or macOS configured that way), a pure case
        difference names two genuinely distinct files -- there's no
        same-file aliasing risk for rename_case_only's double-hop dance to
        protect against, so a normal rename is correct and
        is_case_only_rename correctly says so.
        """
        if _filesystem_is_case_insensitive(tmp_path):
            pytest.skip("this filesystem is case-insensitive -- see the sibling test above")
        old = str(tmp_path / "the.matrix.mkv")
        new = str(tmp_path / "The.Matrix.mkv")
        assert not is_case_only_rename(old, new)

    def test_false_for_a_genuinely_different_name(self, tmp_path):
        old = str(tmp_path / "the.matrix.mkv")
        new = str(tmp_path / "the.matrix.2.mkv")
        assert not is_case_only_rename(old, new)

    def test_false_when_paths_are_identical(self, tmp_path):
        old = str(tmp_path / "The.Matrix.mkv")
        assert not is_case_only_rename(old, old)


class TestRenameCaseOnly:
    def test_corrects_the_case_on_disk(self, tmp_path):
        old = tmp_path / "the.matrix.mkv"
        old.write_bytes(b"content")
        new = tmp_path / "The.Matrix.mkv"

        rename_case_only(str(old), str(new))

        on_disk = os.listdir(tmp_path)
        assert on_disk == ["The.Matrix.mkv"], (
            f"case was not corrected on disk: {on_disk}"
        )
        assert new.read_bytes() == b"content", "file content was lost during rename"

    def test_does_not_collide_with_an_existing_temp_name(self, tmp_path):
        """If some earlier run left a stray '<new>.case-rename-tmp' file
        behind, the helper must not clobber it."""
        old = tmp_path / "the.matrix.mkv"
        old.write_bytes(b"real content")
        new = tmp_path / "The.Matrix.mkv"
        stray = tmp_path / "The.Matrix.mkv.case-rename-tmp"
        stray.write_bytes(b"unrelated stray file")

        rename_case_only(str(old), str(new))

        assert new.read_bytes() == b"real content"
        assert stray.read_bytes() == b"unrelated stray file", "stray temp file was overwritten"

    def test_rolls_back_if_the_second_move_fails(self, tmp_path, monkeypatch):
        """A network hiccup, AV lock, or permissions change on the second
        move must not strand the file at its internal tmp name -- it goes
        back to old_path, so the caller's "rename failed, nothing changed"
        assumption still holds.
        """
        import shutil as shutil_mod
        old = tmp_path / "the.matrix.mkv"
        old.write_bytes(b"content")
        new = tmp_path / "The.Matrix.mkv"

        real_move = shutil_mod.move
        calls = {"n": 0}
        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated failure")
            return real_move(src, dst)
        monkeypatch.setattr('media_agent.probe.shutil.move', flaky)

        with pytest.raises(OSError):
            rename_case_only(str(old), str(new))

        # os.listdir, not Path.exists() -- exists() can't tell the two paths
        # apart on a case-insensitive filesystem, which would hide the bug.
        on_disk = os.listdir(tmp_path)
        assert on_disk == ["the.matrix.mkv"], (
            f"file was not rolled back to its original name: {on_disk}"
        )
        assert old.read_bytes() == b"content"

    def test_reports_the_real_location_if_rollback_also_fails(self, tmp_path,
                                                               monkeypatch):
        """Genuinely stranded is possible (rollback can fail too). The file's
        actual location must never be silently lost -- it's the one thing
        that lets a human recover it by hand.
        """
        import shutil as shutil_mod
        old = tmp_path / "the.matrix.mkv"
        old.write_bytes(b"content")
        new = tmp_path / "The.Matrix.mkv"

        real_move = shutil_mod.move
        calls = {"n": 0}
        def always_fail_after_first(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_move(src, dst)
            raise OSError(f"simulated failure {calls['n']}")
        monkeypatch.setattr('media_agent.probe.shutil.move', always_fail_after_first)

        with pytest.raises(OSError) as exc_info:
            rename_case_only(str(old), str(new))

        assert ".case-rename-tmp" in str(exc_info.value), (
            "error message doesn't say where the file actually ended up"
        )


@pytest.fixture
def canonicalize_case_only_library(tmp_path):
    """A movie whose on-disk name matches its TMDB-canonical name except
    for case -- the concrete reproduction scenario."""
    root = tmp_path / "Library"
    movies = root / "Movies"
    movies.mkdir(parents=True)
    (root / "TV Shows").mkdir()
    (root / "Music").mkdir()

    movie = movies / "the matrix (1999) {tmdb-603}.mkv"
    movie.write_bytes(b"the matrix content")

    (root / "movies.json").write_text(json.dumps({"movies": []}), encoding='utf-8')
    (root / "movies_tmdb.json").write_text(json.dumps({
        "movies": [{"filename": movie.name, "tmdb_id": 603,
                    "tmdb_title": "The Matrix", "tmdb_year": "1999",
                    "match_status": "confident"}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "movies": movies, "movie": movie}
    config_mod.CONFIG = None


class TestTmdbCanonicalizeAppliesCaseCorrections:
    def test_case_only_target_is_not_treated_as_a_conflict(
            self, canonicalize_case_only_library, capsys):
        cmd_tmdb_canonicalize(Args(apply=True))
        out = capsys.readouterr().out
        assert "SKIP (already exists)" not in out, (
            "a pure case correction was reported as a false conflict:\n" + out
        )

    def test_case_is_actually_corrected_on_disk(self, canonicalize_case_only_library):
        cmd_tmdb_canonicalize(Args(apply=True))

        movies_dir = canonicalize_case_only_library["movies"]
        on_disk = os.listdir(movies_dir)
        assert on_disk == ["The Matrix (1999) {tmdb-603}.mkv"], (
            f"case was not corrected on disk: {on_disk}"
        )

    def test_content_survives_the_case_correction(self, canonicalize_case_only_library):
        cmd_tmdb_canonicalize(Args(apply=True))

        movies_dir = canonicalize_case_only_library["movies"]
        corrected = movies_dir / "The Matrix (1999) {tmdb-603}.mkv"
        assert corrected.read_bytes() == b"the matrix content"
