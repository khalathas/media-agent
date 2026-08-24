"""The safety contract for commands that modify files.

Every command in the "changes your files" group must refuse to touch anything
unless given an explicit --apply. Passing no flag at all is a preview, never a
mutation.

This is the single most important guarantee the tool makes, it is stated
plainly in the README and in `media-agent --help`, and it has been broken
before: tmdb-canonicalize and tmdb-rename once checked only `--dry-run`, so a
bare `media-agent tmdb-rename` renamed the whole library, and adding --yes did
it with no prompt at all.

These tests build a real library on disk and assert the files are still there
afterwards. An earlier version of this file mocked the filesystem instead and
passed even with the bug reintroduced -- the commands bailed out early for
unrelated reasons and never reached the guard. Only end-to-end assertions on
real files actually prove the contract holds.
"""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.tmdb import cmd_tmdb_canonicalize, cmd_tmdb_rename

ORIGINAL = "The Matrix (1999).mkv"


class Args:
    """Stand-in for the argparse namespace."""

    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = False
        self.include_ambiguous = False
        self.__dict__.update(kw)


@pytest.fixture
def library(tmp_path):
    """A real, minimal library with one movie that TMDB wants to rename.

    Yields the path to the movie file, which must still exist under its
    original name unless --apply was given.
    """
    root = tmp_path / "Library"
    movies = root / "Movies"
    movies.mkdir(parents=True)
    (root / "TV Shows").mkdir()
    (root / "Music").mkdir()

    movie = movies / ORIGINAL
    movie.write_bytes(b"not really a video")

    (root / "movies.json").write_text(json.dumps({
        "movies": [{"name": ORIGINAL, "width": 1920, "height": 1080,
                    "resolution_class": "1080p", "extension": ".mkv",
                    "video_codec": "h264", "audio_codec": "aac",
                    "bitrate": "5000 kbps", "filesize": 18,
                    "date_added": "2026-01-01 00:00:00"}]
    }), encoding='utf-8')

    # A confident match, so every command has real work queued up. If the
    # guard fails, there is definitely something to rename.
    (root / "movies_tmdb.json").write_text(json.dumps({
        "movies": [{"filename": ORIGINAL, "tmdb_id": 603,
                    "tmdb_title": "The Matrix", "tmdb_year": "1999",
                    "match_status": "confident"}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield movie
    config_mod.CONFIG = None


@pytest.fixture
def no_input(monkeypatch):
    """Answering the confirmation prompt is not an option.

    If a command reaches the prompt without --apply it has already failed the
    contract, so make any attempt to read stdin an outright error rather than
    letting it block or silently take a default.
    """
    def boom(*a, **kw):
        raise AssertionError(
            "command asked for confirmation without --apply -- it should have "
            "returned before reaching the prompt"
        )
    monkeypatch.setattr('builtins.input', boom)


COMMANDS = [
    pytest.param(cmd_tmdb_rename,       id="tmdb-rename"),
    pytest.param(cmd_tmdb_canonicalize, id="tmdb-canonicalize"),
]


@pytest.mark.parametrize("command", COMMANDS)
def test_no_flags_does_not_rename(command, library, no_input):
    """The headline guarantee: a bare command name is harmless."""
    command(Args())
    assert library.exists(), (
        f"{library.name} was renamed with no --apply flag -- "
        "the safety contract is broken"
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_yes_without_apply_does_not_rename(command, library, no_input):
    """--yes skips the prompt; it must not stand in for --apply.

    This is the genuinely dangerous combination -- a whole library renamed
    with no preview and no confirmation.
    """
    command(Args(yes=True))
    assert library.exists(), (
        f"{library.name} was renamed by --yes alone -- "
        "--yes must only skip the prompt, never authorise the operation"
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_dry_run_does_not_rename(command, library, no_input):
    command(Args(dry_run=True))
    assert library.exists()


@pytest.mark.parametrize("command", COMMANDS)
def test_dry_run_with_yes_does_not_rename(command, library, no_input):
    command(Args(dry_run=True, yes=True))
    assert library.exists()


@pytest.mark.parametrize("command", COMMANDS)
def test_apply_does_rename(command, library):
    """The other half of the contract: --apply must actually work.

    Without this, a command that refused to do anything at all would pass
    every test above.
    """
    command(Args(apply=True, yes=True))
    assert not library.exists(), "--apply did not rename the file"
    renamed = list(library.parent.glob("*.mkv"))
    assert len(renamed) == 1
    assert "tmdb-603" in renamed[0].name
