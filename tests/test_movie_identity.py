"""Movies must be identified by path, not by filename.

Two folders can each hold a "Movie.mkv" -- different films, or different cuts
of one. Keying the index by basename collapsed them into a single entry, so one
copy was never indexed, stale detection was unreliable, and a rename acted on
whichever path the directory walk happened to return first.
"""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.movies import cmd_normalize, cmd_rescan, migrate_index_paths
from media_agent.probe import group_by_basename, scan_video_files, video_path_key
from media_agent.tmdb import cmd_tmdb_rename


class Args:
    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = True
        self.include_ambiguous = False
        self.__dict__.update(kw)


@pytest.fixture
def dupes(tmp_path):
    """One basename, two real files, in different folders."""
    root = tmp_path / "Library"
    movies = root / "Movies"
    (movies / "4K").mkdir(parents=True)
    (movies / "SD").mkdir()
    (root / "TV Shows").mkdir()
    (root / "Music").mkdir()

    (movies / "4K" / "The.Matrix.1999.mkv").write_bytes(b"the 4K copy")
    (movies / "SD" / "The.Matrix.1999.mkv").write_bytes(b"the SD copy")

    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "movies": movies}
    config_mod.CONFIG = None


class TestScan:
    def test_duplicate_basenames_stay_separate(self, dupes):
        found = scan_video_files(dupes["movies"])
        assert len(found) == 2, "two real files collapsed into one entry"
        assert set(found) == {"4K/The.Matrix.1999.mkv", "SD/The.Matrix.1999.mkv"}

    def test_every_value_is_a_path_not_a_list(self, dupes):
        """The old shape put a list under a duplicated name, and callers took [0]."""
        for value in scan_video_files(dupes["movies"]).values():
            assert isinstance(value, str)

    def test_path_key_is_relative_with_forward_slashes(self, dupes):
        key = video_path_key(str(dupes["movies"] / "4K" / "The.Matrix.1999.mkv"),
                             dupes["movies"])
        assert key == "4K/The.Matrix.1999.mkv"

    def test_group_by_basename_finds_the_collision(self, dupes):
        groups = group_by_basename(scan_video_files(dupes["movies"]))
        assert len(groups["The.Matrix.1999.mkv"]) == 2


class TestMigration:
    def test_unique_name_migrates_without_reprobing(self, tmp_path):
        """An old index must keep its probe data. Re-probing takes hours."""
        disk = {"Movies/Alien (1979).mkv": "/abs/Alien (1979).mkv"}
        movies = [{"name": "Alien (1979).mkv", "width": 1920, "height": 1080}]
        migrated, ambiguous = migrate_index_paths(movies, disk)
        assert migrated == 1
        assert ambiguous == []
        assert movies[0]["path"] == "Movies/Alien (1979).mkv"
        assert movies[0]["width"] == 1920, "probe data was lost during migration"

    def test_ambiguous_name_is_reported_not_guessed(self):
        disk = {"4K/M.mkv": "/a", "SD/M.mkv": "/b"}
        movies = [{"name": "M.mkv", "width": 1920}]
        migrated, ambiguous = migrate_index_paths(movies, disk)
        assert migrated == 0
        assert ambiguous == ["M.mkv"]
        assert "path" not in movies[0]

    def test_entries_that_already_have_paths_are_left_alone(self):
        movies = [{"name": "M.mkv", "path": "Keep/Me.mkv"}]
        migrate_index_paths(movies, {"Other/M.mkv": "/x"})
        assert movies[0]["path"] == "Keep/Me.mkv"


class TestRescan:
    def test_indexes_both_copies_separately(self, dupes, monkeypatch):
        """The regression: previously only one of the two was ever indexed."""
        monkeypatch.setattr('media_agent.movies.get_media_info',
                            lambda p: {'name': 'x', 'width': 1920, 'height': 1080,
                                       'extension': '.mkv', 'video_codec': 'h264',
                                       'audio_codec': 'aac', 'bitrate': '1 kbps',
                                       'has_subtitles': False, 'filesize': 1,
                                       'date_added': '2026-01-01 00:00:00'})
        cmd_rescan(Args())
        data = json.loads((dupes["root"] / "movies.json").read_text(encoding='utf-8'))
        paths = {m['path'] for m in data['movies']}
        assert paths == {"4K/The.Matrix.1999.mkv", "SD/The.Matrix.1999.mkv"}


class TestAmbiguousMigrationSafety:
    """P2-2: an ambiguous legacy entry (pathless, basename shared by 2+ files)
    must not be dropped from the index until its replacement(s) are
    successfully probed and added. Otherwise the user's classification/
    resolution history for that film silently vanishes -- no old entry, no
    new one either.
    """

    def test_declining_probe_keeps_legacy_entry(self, dupes, monkeypatch):
        (dupes["root"] / "movies.json").write_text(json.dumps({
            "movies": [{"name": "The.Matrix.1999.mkv", "width": 1920, "height": 1080,
                        "tmdb_id": 603}]
        }), encoding='utf-8')

        monkeypatch.setattr('media_agent.movies.confirm', lambda *a, **kw: False)
        cmd_rescan(Args(yes=False))

        data = json.loads((dupes["root"] / "movies.json").read_text(encoding='utf-8'))
        assert len(data['movies']) == 1, \
            "the only record of this film vanished with nothing to replace it"
        assert data['movies'][0]['name'] == "The.Matrix.1999.mkv"
        assert data['movies'][0].get('tmdb_id') == 603, "the old entry's data was lost"

    def test_all_probes_failing_keeps_legacy_entry(self, dupes, monkeypatch):
        (dupes["root"] / "movies.json").write_text(json.dumps({
            "movies": [{"name": "The.Matrix.1999.mkv", "tmdb_id": 603}]
        }), encoding='utf-8')

        monkeypatch.setattr('media_agent.movies.get_media_info',
                            lambda p: {'error': 'ffprobe not found'})
        cmd_rescan(Args(yes=True))

        data = json.loads((dupes["root"] / "movies.json").read_text(encoding='utf-8'))
        assert len(data['movies']) == 1, \
            "legacy entry was dropped even though no replacement was added"
        assert data['movies'][0].get('tmdb_id') == 603

    def test_successful_probe_of_both_copies_drops_legacy_entry(self, dupes, monkeypatch):
        """The happy path: once every file sharing the ambiguous name has a
        real per-path entry, the old unresolvable one is superseded."""
        (dupes["root"] / "movies.json").write_text(json.dumps({
            "movies": [{"name": "The.Matrix.1999.mkv", "tmdb_id": 603}]
        }), encoding='utf-8')

        monkeypatch.setattr('media_agent.movies.get_media_info',
                            lambda p: {'name': 'x', 'width': 1920, 'height': 1080,
                                       'extension': '.mkv', 'video_codec': 'h264',
                                       'audio_codec': 'aac', 'bitrate': '1 kbps',
                                       'has_subtitles': False, 'filesize': 1,
                                       'date_added': '2026-01-01 00:00:00'})
        cmd_rescan(Args(yes=True))

        data = json.loads((dupes["root"] / "movies.json").read_text(encoding='utf-8'))
        paths = {m.get('path') for m in data['movies']}
        assert paths == {"4K/The.Matrix.1999.mkv", "SD/The.Matrix.1999.mkv"}
        # The unresolvable legacy entry (no path, stale tmdb_id) is gone --
        # both real files now have their own fresh, correct entry.
        assert not any(m.get('tmdb_id') == 603 for m in data['movies'])

    def test_partial_probe_failure_keeps_legacy_entry(self, dupes, monkeypatch):
        """One of the two ambiguous files probes fine, the other fails.
        Transactional: since the replacement set is incomplete, the legacy
        entry must survive rather than being dropped for a half-finished
        replacement.
        """
        (dupes["root"] / "movies.json").write_text(json.dumps({
            "movies": [{"name": "The.Matrix.1999.mkv", "tmdb_id": 603}]
        }), encoding='utf-8')

        def flaky_probe(path):
            if '4K' in path:
                return {'name': 'x', 'width': 3840, 'height': 2160,
                         'extension': '.mkv', 'video_codec': 'h264',
                         'audio_codec': 'aac', 'bitrate': '1 kbps',
                         'has_subtitles': False, 'filesize': 1,
                         'date_added': '2026-01-01 00:00:00'}
            return {'error': 'simulated probe failure'}

        monkeypatch.setattr('media_agent.movies.get_media_info', flaky_probe)
        cmd_rescan(Args(yes=True))

        data = json.loads((dupes["root"] / "movies.json").read_text(encoding='utf-8'))
        assert any(m.get('tmdb_id') == 603 for m in data['movies']), \
            "legacy entry was dropped despite an incomplete replacement set"


class TestRenameSafety:
    def test_normalize_keeps_both_copies_and_flags_them(self, dupes):
        """Each file is its own entry, so each is renamed where it lives.

        Both would clean up to the same name, so normalize's existing duplicate
        detection tags them [DUPE-1] and [DUPE-2] instead of letting one
        overwrite the other. Both survive, in their original folders, marked for
        the user to review.
        """
        cmd_normalize(Args(apply=True))

        four_k = list((dupes["movies"] / "4K").glob("*.mkv"))
        sd     = list((dupes["movies"] / "SD").glob("*.mkv"))
        assert len(four_k) == 1 and len(sd) == 1, "a copy was lost"

        # Each stayed in its own folder with its own contents intact.
        assert four_k[0].read_bytes() == b"the 4K copy"
        assert sd[0].read_bytes() == b"the SD copy"

        # Both were cleaned up, and both were flagged rather than silently merged.
        assert all("The Matrix (1999)" in p.name for p in (four_k[0], sd[0]))
        assert {p.name for p in (four_k[0], sd[0])} == {
            "The Matrix (1999) [DUPE-1].mkv", "The Matrix (1999) [DUPE-2].mkv"}

    def test_tmdb_rename_refuses_an_ambiguous_name(self, dupes, capsys):
        """movies_tmdb.json keys by filename, so a duplicate is unresolvable.

        Renaming whichever copy came first was the old behaviour. Refusing and
        saying which folders collide is the right one.
        """
        (dupes["root"] / "movies_tmdb.json").write_text(json.dumps({
            "movies": [{"filename": "The.Matrix.1999.mkv", "tmdb_id": 603,
                        "tmdb_title": "The Matrix", "tmdb_year": "1999",
                        "match_status": "confident"}]
        }), encoding='utf-8')
        (dupes["root"] / "movies.json").write_text(
            json.dumps({"movies": []}), encoding='utf-8')

        cmd_tmdb_rename(Args(apply=True))

        assert "AMBIGUOUS" in capsys.readouterr().out
        # both originals untouched
        assert (dupes["movies"] / "4K" / "The.Matrix.1999.mkv").exists()
        assert (dupes["movies"] / "SD" / "The.Matrix.1999.mkv").exists()
