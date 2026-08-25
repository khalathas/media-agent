"""Episodes must be identified by path, not by filename.

"S01E01.mkv" is not a name, it is a coincidence -- most shows in a library have
one. A cache keyed by filename alone hands one show another show's probe data,
and its season, including a season the user set deliberately with
reassign-season. The damage is silent: the index simply reports wrong numbers.
"""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.tv import (_episode_path_key, _lookup_existing, _show_scoped_key,
                            scan_tv_shows)

SHOWS = [("Breaking Bad (2008)", 1080), ("Firefly (2002)", 480)]


@pytest.fixture
def two_shows(tmp_path):
    """Two shows whose episodes share a filename but differ in every other way."""
    root = tmp_path / "Library"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    for folder, _ in SHOWS:
        season = root / "TV Shows" / folder / "Season 01"
        season.mkdir(parents=True)
        (season / "S01E01.mkv").write_bytes(b"x")

    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield root
    config_mod.CONFIG = None


def cached(path_key, height, season):
    return {"filename": "S01E01.mkv", "path": path_key, "height": height,
            "width": 1920, "video_codec": "h264", "audio_codec": "aac",
            "bitrate": "1 kbps", "has_subtitles": False, "filesize": 1,
            "duration": 1.0, "date_added": "2026-01-01 00:00:00", "_season": season}


def by_folder(shows):
    return {s['folder']: s for s in shows}


class TestPathKey:
    def test_key_is_relative_to_the_tv_root(self, two_shows):
        key = _episode_path_key(
            str(two_shows / "TV Shows" / "Breaking Bad (2008)" / "Season 01" / "S01E01.mkv"))
        assert key == "breaking bad (2008)/season 01/s01e01.mkv"

    def test_same_filename_in_two_shows_gives_different_keys(self, two_shows):
        keys = {
            _episode_path_key(str(two_shows / "TV Shows" / folder / "Season 01" / "S01E01.mkv"))
            for folder, _ in SHOWS
        }
        assert len(keys) == 2

    def test_key_is_case_insensitive(self, two_shows):
        base = two_shows / "TV Shows" / "Breaking Bad (2008)" / "Season 01"
        assert _episode_path_key(str(base / "S01E01.mkv")) == \
               _episode_path_key(str(base / "s01e01.MKV"))


class TestCacheReuse:
    def test_each_show_keeps_its_own_probe_data(self, two_shows):
        """The regression: previously both shows got whichever was cached last."""
        cache = {}
        for folder, height in SHOWS:
            key = _episode_path_key(
                str(two_shows / "TV Shows" / folder / "Season 01" / "S01E01.mkv"))
            cache[key] = cached(key, height, 1)

        shows = by_folder(scan_tv_shows(existing_episodes=cache))
        for folder, height in SHOWS:
            episode = shows[folder]['seasons'][0]['episodes'][0]
            assert episode['height'] == height, (
                f"{folder} got height {episode['height']}, expected {height} -- "
                "probe data leaked between shows"
            )

    def test_scan_records_a_path_on_every_episode(self, two_shows):
        for show in scan_tv_shows():
            for season in show['seasons']:
                for episode in season['episodes']:
                    assert episode.get('path'), "episode written without a path key"

    def test_legacy_index_without_paths_still_matches_within_a_show(self, two_shows):
        """An index written before paths existed must not force a full re-probe.

        Re-probing a large library over a network share takes hours, so the
        fallback key is scoped to the show -- weaker than a path, but it still
        prevents the cross-show collision.
        """
        cache = {}
        for folder, height in SHOWS:
            key = _show_scoped_key(folder, "S01E01.mkv")
            entry = cached(key, height, 1)
            del entry['path']            # as an old index would have it
            cache[key] = entry

        shows = by_folder(scan_tv_shows(existing_episodes=cache))
        for folder, height in SHOWS:
            assert shows[folder]['seasons'][0]['episodes'][0]['height'] == height

    def test_lookup_prefers_the_path_key(self, two_shows):
        fpath = str(two_shows / "TV Shows" / "Breaking Bad (2008)" / "Season 01" / "S01E01.mkv")
        cache = {
            _episode_path_key(fpath): {"marker": "precise"},
            _show_scoped_key("Breaking Bad (2008)", "S01E01.mkv"): {"marker": "fallback"},
        }
        hit = _lookup_existing(cache, fpath, "Breaking Bad (2008)", "S01E01.mkv")
        assert hit["marker"] == "precise"

    def test_lookup_returns_none_when_nothing_matches(self, two_shows):
        fpath = str(two_shows / "TV Shows" / "Firefly (2002)" / "Season 01" / "S01E01.mkv")
        assert _lookup_existing({}, fpath, "Firefly (2002)", "S01E01.mkv") is None
        assert _lookup_existing(None, fpath, "Firefly (2002)", "S01E01.mkv") is None


class TestAmbiguousLegacyFallbackWithinAShow:
    """P2-6: the legacy (pathless) fallback is scoped to show+basename, so
    it still cannot tell 720p/S01E01.mkv from 1080p/S01E01.mkv apart within
    one show. Both hash to the same "showfolder/s01e01.mkv" key, so one
    file's cached probe data could leak into the other. Once a legacy key
    is known to be ambiguous (2+ real files share it), neither file should
    get a cache hit -- both must be freshly probed.
    """

    @pytest.fixture
    def one_show_two_qualities(self, tmp_path):
        """One show, two subfolders, same basename in each."""
        root = tmp_path / "Library"
        (root / "Movies").mkdir(parents=True)
        (root / "Music").mkdir()
        show = root / "TV Shows" / "Breaking Bad (2008)"
        (show / "720p").mkdir(parents=True)
        (show / "1080p").mkdir(parents=True)
        (show / "720p" / "S01E01.mkv").write_bytes(b"low-res copy")
        (show / "1080p" / "S01E01.mkv").write_bytes(b"high-res copy")

        cfg_path = tmp_path / "c.json"
        cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
        config_mod.set_config(Config.load(cfg_path))
        yield root
        config_mod.CONFIG = None

    def test_ambiguous_basename_is_detected(self, one_show_two_qualities):
        from media_agent.tv import _ambiguous_legacy_basenames
        tv_root = one_show_two_qualities / "TV Shows"
        ambiguous = _ambiguous_legacy_basenames(str(tv_root), "Breaking Bad (2008)")
        assert ambiguous == {"s01e01.mkv"}

    def test_lookup_refuses_the_shared_fallback_key_when_ambiguous(
            self, one_show_two_qualities):
        """Direct reproduction at the _lookup_existing level: same show,
        same fname, cache has one entry under the shared fallback key --
        before the fix this always hands it back regardless of which of
        the two real files is asking.
        """
        from media_agent.tv import _ambiguous_legacy_basenames
        show = one_show_two_qualities / "TV Shows" / "Breaking Bad (2008)"
        cache = {
            _show_scoped_key("Breaking Bad (2008)", "S01E01.mkv"):
                cached(None, height=999, season=1),
        }
        ambiguous = _ambiguous_legacy_basenames(
            str(one_show_two_qualities / "TV Shows"), "Breaking Bad (2008)")

        for sub in ("720p", "1080p"):
            fpath = str(show / sub / "S01E01.mkv")
            hit = _lookup_existing(cache, fpath, "Breaking Bad (2008)", "S01E01.mkv",
                                   ambiguous_basenames=ambiguous)
            assert hit is None, (
                f"{sub}/S01E01.mkv got a cache hit from an ambiguous shared key "
                "-- it could be the other quality's stale data"
            )

    def test_scan_forces_a_fresh_probe_for_both_instead_of_leaking(
            self, one_show_two_qualities, monkeypatch):
        """End-to-end reproduction through scan_tv_shows: a legacy cache
        entry under the shared key must not leak into either real file.
        Confirmed by making a fresh probe distinguishable (real height per
        path) from the stale cached value (999) and asserting neither
        episode ends up with the stale one.
        """
        import media_agent.tv as tv_mod

        def fake_probe(fpath):
            height = 720 if '720p' in fpath else 1080
            return {'width': 1280, 'height': height, 'video_codec': 'h264',
                    'audio_codec': 'aac', 'bitrate': '1 kbps', 'has_subtitles': False,
                    'filesize': 1, 'duration': 1.0, 'date_added': '2026-01-01 00:00:00'}
        monkeypatch.setattr(tv_mod, 'get_episode_info', fake_probe)

        stale_cache = {
            _show_scoped_key("Breaking Bad (2008)", "S01E01.mkv"):
                cached(None, height=999, season=1),
        }

        shows = by_folder(scan_tv_shows(existing_episodes=stale_cache))
        episodes = shows["Breaking Bad (2008)"]['seasons'][0]['episodes']
        heights = sorted(ep['height'] for ep in episodes)
        assert 999 not in heights, (
            "a real file inherited the stale legacy cache entry from the "
            "ambiguous shared show+basename key instead of being re-probed"
        )
        assert heights == [720, 1080], (
            "both files should have been freshly (and correctly) probed"
        )
