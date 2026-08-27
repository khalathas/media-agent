"""tmdb-rename and tmdb-canonicalize preview reports must show which file.

Found by the ninth-pass reviewer (carried forward from earlier passes as a
minor finding): movies_tmdb.json identifies films by bare filename, and the
preview reports printed that same bare filename back -- "FROM: Movie.mkv"
tells you nothing when two folders each hold a "Movie.mkv". Apply-time
correctly refuses an ambiguous name (see _resolve_single_path in tmdb.py),
but a user deciding whether to trust the preview and pass --apply had no way
to see that ambiguity coming, or to tell which of several same-named files a
non-ambiguous entry actually resolves to.

The fix resolves every proposal against the real disk layout before writing
the report, so the preview shows the actual library-relative path when one
file resolves unambiguously, and an explicit warning when it doesn't.
"""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.tmdb import cmd_tmdb_canonicalize, cmd_tmdb_rename


class Args:
    def __init__(self, **kw):
        self.dry_run = True
        self.apply = False
        self.yes = False
        self.include_ambiguous = False
        self.__dict__.update(kw)


@pytest.fixture
def tmdb_library(tmp_path):
    """A movies library with one unambiguous nested match and one ambiguous
    same-basename-in-two-folders match, wired up for both preview commands.
    """
    root = tmp_path / "Library"
    movies = root / "Movies"
    (movies / "BluRay").mkdir(parents=True)
    (movies / "CopyA").mkdir(parents=True)
    (movies / "CopyB").mkdir(parents=True)

    unique = movies / "BluRay" / "Unique.Movie.2020.mkv"
    unique.write_bytes(b"not really a video")

    dup_a = movies / "CopyA" / "Dup.Movie.2021.mkv"
    dup_a.write_bytes(b"not really a video")
    dup_b = movies / "CopyB" / "Dup.Movie.2021.mkv"
    dup_b.write_bytes(b"not really a video")

    (root / "movies_tmdb.json").write_text(json.dumps({
        "movies": [
            {"filename": unique.name, "tmdb_id": 111,
             "tmdb_title": "Unique Movie", "tmdb_year": "2020",
             "match_status": "confident"},
            {"filename": dup_a.name, "tmdb_id": 222,
             "tmdb_title": "Dup Movie", "tmdb_year": "2021",
             "match_status": "confident"},
        ]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))

    yield {"root": root, "unique": unique, "dup_a": dup_a, "dup_b": dup_b}

    config_mod.CONFIG = None


@pytest.fixture
def tmdb_collision_library(tmp_path):
    """Two differently-named files (a two-disc release) that both match the
    same confident TMDB entry -- cmd_tmdb_canonicalize would generate the
    identical canonical name for both, so it skips them as a collision
    rather than pick one arbitrarily. Nested in a subfolder so a
    library-relative path is distinguishable from a bare filename.
    """
    root = tmp_path / "Library"
    movies = root / "Movies"
    (movies / "ColBox").mkdir(parents=True)

    cd1 = movies / "ColBox" / "Collide.Movie.CD1.mkv"
    cd1.write_bytes(b"not really a video")
    cd2 = movies / "ColBox" / "Collide.Movie.CD2.mkv"
    cd2.write_bytes(b"not really a video")

    (root / "movies_tmdb.json").write_text(json.dumps({
        "movies": [
            {"filename": cd1.name, "tmdb_id": 333,
             "tmdb_title": "Collide Movie", "tmdb_year": "2022",
             "match_status": "confident"},
            {"filename": cd2.name, "tmdb_id": 333,
             "tmdb_title": "Collide Movie", "tmdb_year": "2022",
             "match_status": "confident"},
        ]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))

    yield {"root": root, "cd1": cd1, "cd2": cd2}

    config_mod.CONFIG = None


@pytest.mark.parametrize("command,filename", [
    pytest.param(cmd_tmdb_canonicalize, "tmdb_canonicalize_preview.txt", id="tmdb-canonicalize"),
    pytest.param(cmd_tmdb_rename,       "tmdb_rename_preview.txt",       id="tmdb-rename"),
])
class TestPreviewShowsResolvedPath:
    def test_unambiguous_entry_shows_the_library_relative_path(self, command, filename, tmdb_library):
        command(Args())
        text = (tmdb_library["root"] / filename).read_text(encoding='utf-8')
        assert "FROM: BluRay/Unique.Movie.2020.mkv" in text, (
            "preview should show the resolved library-relative path, not just "
            f"the bare filename -- got:\n{text}"
        )
        assert "FROM: Unique.Movie.2020.mkv\n" not in text, (
            "preview still shows the bare, unresolved filename"
        )

    def test_ambiguous_entry_is_flagged_not_silently_shown_as_bare_filename(self, command, filename, tmdb_library):
        command(Args())
        text = (tmdb_library["root"] / filename).read_text(encoding='utf-8')
        # Whichever line covers the ambiguous entry, it must say so -- not
        # print the bare "Dup.Movie.2021.mkv" as if it were a resolved path
        # indistinguishable from the unambiguous case above.
        dup_lines = [line for line in text.splitlines() if "Dup.Movie.2021.mkv" in line]
        assert dup_lines, f"expected a line mentioning the ambiguous file -- got:\n{text}"
        assert any("AMBIGUOUS" in line or "WARNING" in line for line in dup_lines), (
            f"ambiguous entry isn't flagged as such in the preview -- got:\n{dup_lines}"
        )


class TestCanonicalizeCollisionSectionShowsResolvedPaths:
    """Collision entries (multi-part files sharing one TMDB match) are
    listed separately from the main rename section and are never applied
    either way, but the eleventh-pass reviewer found they still printed
    bare filenames after the main-section fix -- the same
    can't-tell-which-file problem the main fix addressed, just left
    unaddressed in the one section specifically about ambiguity.
    """

    def test_collision_entries_show_the_library_relative_path(self, tmdb_collision_library):
        cmd_tmdb_canonicalize(Args())
        text = (tmdb_collision_library["root"] / "tmdb_canonicalize_preview.txt").read_text(
            encoding='utf-8')
        assert "ColBox/Collide.Movie.CD1.mkv" in text, (
            f"collision section should show the resolved library-relative path, "
            f"not a bare filename -- got:\n{text}"
        )
        assert "ColBox/Collide.Movie.CD2.mkv" in text
        assert "  Collide.Movie.CD1.mkv\n" not in text, (
            "collision section still shows a bare, unresolved filename"
        )
