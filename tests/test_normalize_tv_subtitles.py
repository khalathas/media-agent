"""P3-1: normalize-tv must move language-tagged subtitles along with their
episode, not just exact-stem ones.

find_external_subtitles (used to populate the index) already matches
'episode.en.srt' style names, stripping an optional 2-3 char language code.
The move logic in _build_normalize_tv_plan used to compare stems exactly
instead, so a loose episode.en.srt was silently left behind when its video
moved into a Season NN folder.
"""

import json
import os

import pytest

from media_agent import config as config_mod
from media_agent.config import Config
from media_agent.tv import (_build_normalize_tv_plan, _subtitle_matches_video,
                            cmd_normalize_tv, find_external_subtitles)


class Args:
    def __init__(self, **kw):
        self.dry_run = False
        self.apply = False
        self.yes = True
        self.__dict__.update(kw)


@pytest.fixture
def show_with_tagged_subtitle(tmp_path):
    """One episode loose in the show root, with an .en.srt sibling, plus a
    plain (untagged) subtitle sibling for a second episode -- so the fix is
    checked against both the previously-working and previously-broken case
    at once.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    show.mkdir(parents=True)

    (show / "S01E01.mkv").write_bytes(b"episode one")
    (show / "S01E01.en.srt").write_bytes(b"english subs")
    (show / "S01E02.mkv").write_bytes(b"episode two")
    (show / "S01E02.srt").write_bytes(b"plain subs")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                       {"filename": "S01E02.mkv", "season": 1, "episode": 2},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


class TestSubtitleMatcher:
    def test_matches_plain_subtitle(self):
        assert _subtitle_matches_video("S01E01.srt", "S01E01")

    def test_matches_language_tagged_subtitle(self):
        assert _subtitle_matches_video("S01E01.en.srt", "S01E01")
        assert _subtitle_matches_video("S01E01.fr.srt", "S01E01")

    def test_rejects_a_different_episode(self):
        assert not _subtitle_matches_video("S01E02.en.srt", "S01E01")

    def test_rejects_non_srt_files(self):
        assert not _subtitle_matches_video("S01E01.nfo", "S01E01")


class TestNormalizeTvMovesTaggedSubtitles:
    def test_plan_includes_language_tagged_subtitle(self, show_with_tagged_subtitle):
        data = json.loads(
            (show_with_tagged_subtitle["root"] / "tvshows.json").read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(show_with_tagged_subtitle["root"] / "TV Shows"), data)

        moved_basenames = {os.path.basename(dst) for _src, dst, _show in plan['file_moves']}
        assert "S01E01.en.srt" in moved_basenames, (
            "language-tagged subtitle missing from the move plan -- "
            f"plan moved: {sorted(moved_basenames)}"
        )
        assert "S01E02.srt" in moved_basenames, "plain subtitle should still move too"

    def test_apply_actually_moves_the_tagged_subtitle_to_disk(
            self, show_with_tagged_subtitle):
        cmd_normalize_tv(Args(apply=True))

        show = show_with_tagged_subtitle["show"]
        season_dir = show / "Season 01"
        assert (season_dir / "S01E01.mkv").exists()
        assert (season_dir / "S01E01.en.srt").exists(), (
            "S01E01.en.srt was left behind in the show root instead of "
            "moving with its episode into Season 01/"
        )
        assert (season_dir / "S01E02.mkv").exists()
        assert (season_dir / "S01E02.srt").exists()
        # Nothing left loose in the show root.
        assert not (show / "S01E01.en.srt").exists()
        assert not (show / "S01E01.mkv").exists()
