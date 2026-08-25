"""The safety contract for commands that modify files.

Every command in the "changes your files" group must refuse to touch anything
unless given an explicit --apply. Passing no flag at all is a preview, never a
mutation, and --yes only skips the confirmation prompt -- it never authorises
the operation.

This is the single most important guarantee the tool makes, it is stated
plainly in the README and in `media-agent --help`, and it has been broken twice:

- tmdb-canonicalize and tmdb-rename once checked only --dry-run, so a bare
  `media-agent tmdb-rename` renamed the whole library and --yes did it with no
  prompt at all.
- normalize-tv once checked only --apply and ignored --dry-run entirely, so
  `normalize-tv --dry-run --apply` executed the full plan. A cautious user
  passing both flags got the worst outcome.

Two lessons are baked into how these tests are written:

1. They build a real library on disk and compare a full before/after snapshot.
   An earlier version mocked the filesystem and passed even with the bug
   reintroduced, because the commands bailed out early for unrelated reasons
   and never reached the guard.
2. Every destructive command is covered. An earlier version claimed a
   five-command contract in its docstring but parametrised only two -- and the
   normalize-tv bug above lived in one of the three it skipped.
"""

import pytest

from media_agent.movies import cmd_normalize
from media_agent.music import cmd_organize_music
from media_agent.tmdb import cmd_tmdb_canonicalize, cmd_tmdb_rename
from media_agent.tv import cmd_normalize_tv

from conftest import snapshot

# Every command in the "changes your files" tier. If you add one, add it here.
DESTRUCTIVE = [
    pytest.param(cmd_normalize,         id="normalize"),
    pytest.param(cmd_normalize_tv,      id="normalize-tv"),
    pytest.param(cmd_organize_music,    id="organize-music"),
    pytest.param(cmd_tmdb_rename,       id="tmdb-rename"),
    pytest.param(cmd_tmdb_canonicalize, id="tmdb-canonicalize"),
]


class Args:
    """Stand-in for the argparse namespace."""

    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = False
        self.include_ambiguous = False
        self.__dict__.update(kw)


@pytest.fixture
def no_input(monkeypatch):
    """Reaching the confirmation prompt without --apply is itself a failure."""
    def boom(*a, **kw):
        raise AssertionError(
            "command asked for confirmation without --apply -- it should have "
            "returned before reaching the prompt"
        )
    monkeypatch.setattr('builtins.input', boom)


def run(command, args, library):
    """Run a command and return (before, after) snapshots of the media tree."""
    before = snapshot(library["root"])
    try:
        command(args)
    except SystemExit:
        pass          # refusing loudly is a valid way to be safe
    return before, snapshot(library["root"])


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_no_flags_changes_nothing(command, library, no_input):
    """The headline guarantee: a bare command name is harmless."""
    before, after = run(command, Args(), library)
    assert after == before, "files changed with no --apply flag"


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_yes_alone_changes_nothing(command, library, no_input):
    """--yes must not stand in for --apply.

    The genuinely dangerous combination: a whole library rewritten with no
    preview and no confirmation.
    """
    before, after = run(command, Args(yes=True), library)
    assert after == before, "--yes alone changed files; it must only skip the prompt"


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_dry_run_changes_nothing(command, library, no_input):
    before, after = run(command, Args(dry_run=True), library)
    assert after == before, "--dry-run changed files"


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_dry_run_wins_over_apply(command, library, no_input):
    """Both flags at once must be the safe reading.

    Someone who passes --dry-run and --apply together is being careful. They
    must not get the destructive interpretation. This is the exact case that
    normalize-tv failed.
    """
    before, after = run(command, Args(dry_run=True, apply=True, yes=True), library)
    assert after == before, "--dry-run --apply together changed files"


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_apply_actually_does_something(command, library):
    """The other half of the contract.

    Without this, a command that refused to do anything at all would sail
    through every test above, and the fixture having no pending work would go
    unnoticed -- making all the tests vacuous.
    """
    before, after = run(command, Args(apply=True, yes=True), library)
    assert after != before, (
        "--apply made no change; either the command is broken or the fixture "
        "has no pending work, which would make the other tests meaningless"
    )


# The docs make a specific promise: every command in this tier writes a preview
# file listing every intended change, and tells you to read it before applying.
# With no undo, that file is the user's only record of what happened.
PREVIEW_FILES = [
    pytest.param(cmd_normalize,         "normalize_preview.txt",         id="normalize"),
    pytest.param(cmd_normalize_tv,      "normalize_tv_preview.txt",      id="normalize-tv"),
    pytest.param(cmd_organize_music,    "organize_music_preview.txt",    id="organize-music"),
    pytest.param(cmd_tmdb_rename,       "tmdb_rename_preview.txt",       id="tmdb-rename"),
    pytest.param(cmd_tmdb_canonicalize, "tmdb_canonicalize_preview.txt", id="tmdb-canonicalize"),
]


@pytest.mark.parametrize("command,filename", PREVIEW_FILES)
def test_dry_run_writes_a_preview_file(command, filename, library, no_input):
    try:
        command(Args(dry_run=True))
    except SystemExit:
        pass
    preview = library["root"] / filename
    assert preview.exists(), (
        f"--dry-run wrote no {filename}; the docs promise a preview file for "
        "every command in this tier"
    )
    assert preview.read_text(encoding='utf-8').strip(), f"{filename} is empty"


# P2-3: README.md states plainly that "Every command that changes files
# previews by default" -- i.e. a bare invocation (no flags at all) behaves
# like --dry-run, not like an error. organize-music alone violated this: it
# printed "Specify --dry-run ... or --apply ..." and sys.exit(1)'d without
# ever writing a preview. The test above only ever exercised --dry-run
# explicitly, so it never caught this.
@pytest.mark.parametrize("command,filename", PREVIEW_FILES)
def test_bare_invocation_writes_a_preview_file(command, filename, library, no_input):
    try:
        command(Args())
    except SystemExit:
        pass
    preview = library["root"] / filename
    assert preview.exists(), (
        f"a bare invocation (no --dry-run, no --apply) wrote no {filename} -- "
        "README.md promises every destructive command previews by default"
    )
    assert preview.read_text(encoding='utf-8').strip(), f"{filename} is empty"
