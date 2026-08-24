"""organize-music must not lose files or write outside the music library.

It is the only command that deletes anything, and it moves every track it
touches, so its failure modes are the most expensive in the tool.
"""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.music import cmd_organize_music

from conftest import make_tagged_mp3


class Args:
    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = True
        self.__dict__.update(kw)


def write_config(tmp_path, root, **music):
    cfg = {"library_root": str(root)}
    if music:
        cfg["music"] = music
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return path


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Library"
    for section in ("Movies", "TV Shows", "Music"):
        (root / section).mkdir(parents=True)
    yield root
    config_mod.CONFIG = None


class TestCollisions:
    def test_three_files_claiming_one_destination(self, tmp_path, library):
        """The regression: three-way collisions produced an invalid plan.

        Rerouting pairs as they were encountered re-queued the first source on
        every later collision, so its file was moved once and then "moved"
        again from a path that no longer existed.
        """
        music = library / "Music"
        # Three loose files in the artist folder, identical in every tag that
        # decides their destination, so all three compute the same target path.
        artist = music / "Pink Floyd"
        artist.mkdir(parents=True)
        for i in range(3):
            make_tagged_mp3(artist / f"rip{i}.mp3", "Pink Floyd",
                            "The Dark Side of the Moon", "Breathe", 2, 1973)

        config_mod.set_config(Config.load(write_config(tmp_path, library)))
        cmd_organize_music(Args(apply=True))

        surviving = list(music.rglob("*.mp3"))
        assert len(surviving) == 3, (
            f"started with 3 files, ended with {len(surviving)} -- a file was lost"
        )
        # All three set aside for the user, none silently overwriting another.
        collisions = list((music / "_NeedsTagging" / "_Collisions").rglob("*.mp3"))
        assert len(collisions) == 3
        assert len({p.name for p in collisions}) == 3, "collision names not unique"

    def test_three_files_sharing_an_original_filename_too(self, tmp_path, library):
        """The counter must be unbounded; a fixed _2 fallback ran out at three."""
        music = library / "Music"
        # Same destination AND the same original filename, so the collision
        # folder itself needs a counter to keep them apart.
        for folder in ("a", "b", "c"):
            d = music / "Pink Floyd" / folder
            d.mkdir(parents=True)
            make_tagged_mp3(d / "track.mp3", "Pink Floyd",
                            "The Dark Side of the Moon", "Breathe", 2, 1973)
        # ...and lift them to depth 1, where organize-music acts on them.
        for folder in ("a", "b", "c"):
            (music / "Pink Floyd" / folder / "track.mp3").rename(
                music / "Pink Floyd" / f"{folder}-track.mp3")
            (music / "Pink Floyd" / folder).rmdir()

        config_mod.set_config(Config.load(write_config(tmp_path, library)))
        cmd_organize_music(Args(apply=True))

        assert len(list(music.rglob("*.mp3"))) == 3


class TestContainment:
    @pytest.mark.parametrize("escape", [
        "../../Escaped",
        "../Outside",
        "C:/Windows/Temp",
        "/tmp/escaped",
    ])
    def test_config_rejects_paths_leaving_the_library(self, tmp_path, library,
                                                      escape, capsys):
        """organize-music moves files into these folders.

        An absolute path or a '..' would relocate media outside the library
        entirely -- somewhere the user is not looking and did not agree to.
        """
        config_mod.set_config(
            Config.load(write_config(tmp_path, library, needs_tagging_dir=escape)))
        cfg = config_mod.get_config()
        assert cfg.music_needs_tagging_dir == "_NeedsTagging", (
            f"accepted an escaping path: {escape}"
        )
        assert "must be a folder inside your music library" in capsys.readouterr().out

    def test_ordinary_relative_subpath_is_accepted(self, tmp_path, library):
        config_mod.set_config(Config.load(
            write_config(tmp_path, library, needs_tagging_dir="_Review/Untagged")))
        assert config_mod.get_config().music_needs_tagging_dir == "_Review/Untagged"

    def test_nothing_is_written_outside_the_music_root(self, tmp_path, library):
        """End to end: after a real run, the library's parent is untouched."""
        music = library / "Music"
        d = music / "Pink Floyd"
        d.mkdir(parents=True)
        make_tagged_mp3(d / "t.mp3", "Pink Floyd", "DSOTM", "Breathe", 2, 1973)
        sibling = tmp_path / "DoNotTouch"
        sibling.mkdir()

        config_mod.set_config(
            Config.load(write_config(tmp_path, library, needs_tagging_dir="../../DoNotTouch")))
        cmd_organize_music(Args(apply=True))

        assert list(sibling.iterdir()) == [], "files were written outside the library"
        assert list(music.rglob("*.mp3")), "the track vanished entirely"
