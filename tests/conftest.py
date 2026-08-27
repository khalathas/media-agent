"""Shared fixtures.

The key one is `library`, which builds a small but *real* library on disk:
actual movie files, a TV show with episodes, and MP3s with genuine ID3 tags.

Real files matter here. Tests that mock the filesystem can pass for the wrong
reason -- a command that bails out early because an index is missing looks
exactly like one that bails out because a safety guard stopped it. Only
asserting on real files afterwards distinguishes the two.
"""

import json

import pytest

# A minimal but genuinely valid MPEG-1 Layer III frame: 128 kbps, 44.1 kHz.
# The frame length of 417 bytes includes the 4-byte header -- get that wrong
# and mutagen cannot sync to the stream, so the file reads as untagged.
_MP3_FRAME = b'\xff\xfb\x90\x00' + b'\x00' * 413
MP3_BYTES = _MP3_FRAME * 20


def make_tagged_mp3(path, artist, album, title, track, year):
    """Write a real, playable-enough MP3 carrying real ID3 tags."""
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    path.write_bytes(MP3_BYTES)
    try:
        tags = EasyID3(str(path))
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(str(path))
        tags = EasyID3(str(path))
    tags['artist'] = [artist]
    tags['album'] = [album]
    tags['title'] = [title]
    tags['tracknumber'] = [str(track)]
    tags['date'] = [str(year)]
    tags.save()
    return path


@pytest.fixture
def library(tmp_path):
    """A real library with pending work for every destructive command.

    Each section is deliberately messy, so no command can pass a safety test
    simply by having nothing to do:

    - Movies:  a release-group filename for `normalize`, and a confident TMDB
               match for `tmdb-rename` / `tmdb-canonicalize`.
    - TV:      an episode sitting loose in the show root for `normalize-tv`.
    - Music:   a fully tagged MP3 in the wrong place for `organize-music`.
    """
    from media_agent import config as config_mod
    from media_agent.config import Config

    root = tmp_path / "Library"
    movies = root / "Movies"
    music = root / "Music"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    for d in (movies, music, show):
        d.mkdir(parents=True)

    movie = movies / "The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv"
    movie.write_bytes(b"not really a video")

    episode = show / "S01E01.mkv"
    episode.write_bytes(b"not really an episode")

    # The track must sit one level down, inside an artist folder. organize-music
    # iterates directories under the music root, so a file loose at the top
    # level is never seen at all.
    loose_artist = music / "Pink Floyd"
    loose_artist.mkdir()
    track = make_tagged_mp3(loose_artist / "01 track.mp3", "Pink Floyd",
                            "The Dark Side of the Moon", "Breathe", 2, 1973)

    (root / "movies.json").write_text(json.dumps({
        "movies": [{"name": movie.name, "width": 1920, "height": 1080,
                    "resolution_class": "1080p", "extension": ".mkv",
                    "video_codec": "h264", "audio_codec": "aac",
                    "bitrate": "5000 kbps", "filesize": movie.stat().st_size,
                    "date_added": "2026-01-01 00:00:00"}]
    }), encoding='utf-8')

    (root / "movies_tmdb.json").write_text(json.dumps({
        "movies": [{"filename": movie.name, "tmdb_id": 603,
                    "tmdb_title": "The Matrix", "tmdb_year": "1999",
                    "match_status": "confident"}]
    }), encoding='utf-8')

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": episode.name, "season": 1, "episode": 1}]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))

    yield {"root": root, "movie": movie, "episode": episode, "track": track}

    config_mod.CONFIG = None


def snapshot(root):
    """Every file under root, as {relative path: contents}.

    Compared before and after a command to prove nothing moved, was renamed,
    or was deleted. Index and report files are excluded -- commands are allowed
    to rewrite those; it is the media that must not move.
    """
    skip = {'.json', '.csv', '.txt', '.tmp'}
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob('*'))
        if p.is_file() and p.suffix.lower() not in skip
    }
