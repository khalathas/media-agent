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
def conflicted_episode_with_one_subtitle(tmp_path):
    """Two videos with the same basename in different quality subfolders --
    a genuine conflict, so normalize-tv must move neither. Only one of the two
    has a matching subtitle.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    (show / "1080p").mkdir(parents=True)
    (show / "720p").mkdir(parents=True)

    (show / "1080p" / "S01E01.mkv").write_bytes(b"1080p copy")
    (show / "1080p" / "S01E01.srt").write_bytes(b"subs")
    (show / "720p" / "S01E01.mkv").write_bytes(b"720p copy")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


@pytest.fixture
def shared_subtitle_two_video_variants(tmp_path):
    """One episode, two video variants (.mkv and .mp4), one shared subtitle.

    Not a conflict -- the two videos have different destinations (different
    extensions), but both reference the same physical .srt via the
    subtitle-matching loop that runs once per video.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    show.mkdir(parents=True)

    (show / "S01E01.mkv").write_bytes(b"mkv copy")
    (show / "S01E01.mp4").write_bytes(b"mp4 copy")
    (show / "S01E01.srt").write_bytes(b"the one shared subtitle")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                       {"filename": "S01E01.mp4", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


@pytest.fixture
def shared_subtitle_one_parent_conflicts(tmp_path):
    """Same as above, but the .mkv variant ALSO conflicts with an unrelated
    third file (a duplicate S01E01.mkv in a quality subfolder). The .mp4
    variant is unaffected. The subtitle has two parents; only one is cancelled,
    so it must still move -- with its surviving parent, not be stranded.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    show.mkdir(parents=True)
    (show / "1080p").mkdir()

    (show / "S01E01.mkv").write_bytes(b"root mkv")
    (show / "S01E01.mp4").write_bytes(b"root mp4")
    (show / "S01E01.srt").write_bytes(b"shared subs")
    (show / "1080p" / "S01E01.mkv").write_bytes(b"unrelated duplicate mkv")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                       {"filename": "S01E01.mp4", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


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


class TestSubtitleStaysWithAConflictedVideo:
    """A conflict must retract every claim it invalidates, not just the video's.

    _claim_destination links a subtitle's claim to its video via
    parent_video_key. When a later-arriving video retroactively poisons the
    first video's destination, the first video's already-claimed subtitle
    must be retracted along with it -- otherwise the subtitle moves alone,
    orphaned from a video that the tool just promised to leave untouched.
    """

    def test_plan_does_not_move_the_subtitle(self, conflicted_episode_with_one_subtitle):
        data = json.loads(
            (conflicted_episode_with_one_subtitle["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(conflicted_episode_with_one_subtitle["root"] / "TV Shows"), data)

        assert plan['file_moves'] == [], (
            f"expected no moves at all (both videos conflict), got {plan['file_moves']}"
        )
        # The subtitle's cancellation must be visible as its own conflict,
        # not merely absent -- a silent drop from the plan is as confusing
        # as a silent move.
        conflict_text = " ".join(reason for reason, _path in plan['conflicts'])
        assert "S01E01.srt" in conflict_text

    def test_apply_leaves_the_subtitle_with_its_video(
            self, conflicted_episode_with_one_subtitle):
        cmd_normalize_tv(Args(apply=True))

        show = conflicted_episode_with_one_subtitle["show"]
        assert not (show / "Season 01").exists(), "a conflict should create nothing"
        assert (show / "1080p" / "S01E01.mkv").exists()
        assert (show / "1080p" / "S01E01.srt").exists(), (
            "the subtitle moved away from its video even though the video "
            "itself correctly stayed put"
        )
        assert (show / "720p" / "S01E01.mkv").exists()


class TestSubtitleSharedByTwoVideoVariants:
    """A subtitle claimed twice for two DIFFERENT videos of the same episode
    (S01E01.mkv and S01E01.mp4, both valid, no conflict between them) must
    not be treated as two files fighting over one destination -- it's the
    same physical file, matched once per video by the per-video subtitle
    loop. Both videos should move, and the subtitle should move with them.

    Found by the fourth-pass reviewer: the earlier conflict-grouping fix
    (TestSubtitleStaysWithAConflictedVideo) made a second claim for the same
    destination always a conflict, without checking whether it was actually
    the same source file being claimed again. Reproduced before fixing: both
    videos moved into Season 01, the shared subtitle was falsely reported as
    "two files claim the same target" and left behind.
    """

    def test_plan_moves_all_three_with_no_conflict(
            self, shared_subtitle_two_video_variants):
        data = json.loads(
            (shared_subtitle_two_video_variants["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(shared_subtitle_two_video_variants["root"] / "TV Shows"), data)

        assert plan['conflicts'] == [], f"expected no conflicts, got {plan['conflicts']}"
        moved = {os.path.basename(dst) for _src, dst, _show in plan['file_moves']}
        assert moved == {"S01E01.mkv", "S01E01.mp4", "S01E01.srt"}

    def test_apply_moves_both_videos_and_the_shared_subtitle(
            self, shared_subtitle_two_video_variants):
        cmd_normalize_tv(Args(apply=True))

        season = shared_subtitle_two_video_variants["show"] / "Season 01"
        assert (season / "S01E01.mkv").exists()
        assert (season / "S01E01.mp4").exists()
        assert (season / "S01E01.srt").exists(), (
            "the shared subtitle was left behind, falsely treated as "
            "conflicting with itself"
        )


class TestSharedSubtitleSurvivesOnePartnerConflicting:
    """A subtitle shared by two videos, where only ONE of those videos
    separately conflicts with an unrelated third file. The surviving video
    still needs its subtitle -- retracting the cancelled parent must not
    strand a claim that another, unaffected parent still needs.
    """

    def test_subtitle_moves_with_its_surviving_parent(
            self, shared_subtitle_one_parent_conflicts):
        cmd_normalize_tv(Args(apply=True))

        show = shared_subtitle_one_parent_conflicts["show"]
        season = show / "Season 01"
        # Both .mkv copies conflict and must stay exactly where they were.
        assert (show / "S01E01.mkv").exists()
        assert (show / "1080p" / "S01E01.mkv").exists()
        assert not (season / "S01E01.mkv").exists()
        # The unaffected .mp4 parent moves, and the subtitle goes with it
        # rather than being stranded by its cancelled sibling parent.
        assert (season / "S01E01.mp4").exists()
        assert (season / "S01E01.srt").exists(), (
            "the shared subtitle was stranded even though one of its two "
            "parent videos was never in conflict and moved successfully"
        )
