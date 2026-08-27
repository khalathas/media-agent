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


def _walk_forcing_show_subdir_order(show_path, order):
    """A drop-in replacement for os.walk that forces a specific traversal
    order for `show_path`'s direct subdirectories, real os.walk everywhere
    else. os.walk's directory order is filesystem/OS-dependent and not
    something a test can otherwise control deterministically -- confirmed
    directly (see the commit that added this) that Windows and Linux visit
    two conflicting quality subfolders in different orders for the exact
    same fixture, which changes observable behavior in _build_normalize_tv_plan.
    """
    real_walk = os.walk

    def fake_walk(top, *a, **kw):
        for dirpath, dirnames, filenames in real_walk(top, *a, **kw):
            if os.path.normpath(dirpath) == os.path.normpath(str(show_path)):
                dirnames.sort(key=lambda d: order.index(d) if d in order else 99)
            yield dirpath, dirnames, filenames

    return fake_walk


class TestSubtitleConflictMessageDoesNotDependOnWalkOrder:
    """Found via a real CI run on ubuntu-latest, not local testing: this
    project's test suite ran exclusively on Windows for 14 review rounds,
    and TestSubtitleStaysWithAConflictedVideo above happened to pass on
    every one of them -- Windows' os.walk order for this exact fixture
    always visits the subtitled video (1080p) before the non-subtitled one
    (720p). The very first time this ran on a real Linux runner, it visited
    them in the opposite order and the subtitle's conflict message vanished
    entirely: a losing video's own claim attempt fails and returns early
    without ever examining its subtitle, so nothing about that subtitle
    ever enters the conflict-reporting system when it happens to lose
    first, versus being explicitly reported when it wins its claim and is
    only retracted later. The underlying safety property (nothing moves)
    held in both orderings; only the diagnostic message was order-dependent.

    These tests force both orderings directly via monkeypatched os.walk,
    so this doesn't require a Linux runner to verify (and won't get
    re-broken and only be re-caught by accident on the next real CI run).
    """

    def test_subtitle_reported_when_its_video_wins_the_claim_first(
            self, conflicted_episode_with_one_subtitle, monkeypatch):
        show = conflicted_episode_with_one_subtitle["show"]
        monkeypatch.setattr(
            'media_agent.tv.os.walk',
            _walk_forcing_show_subdir_order(show, ["1080p", "720p"]))
        data = json.loads(
            (conflicted_episode_with_one_subtitle["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(conflicted_episode_with_one_subtitle["root"] / "TV Shows"), data)

        assert plan['file_moves'] == []
        conflict_text = " ".join(reason for reason, _path in plan['conflicts'])
        assert "S01E01.srt" in conflict_text

    def test_subtitle_reported_when_its_video_loses_the_claim_first(
            self, conflicted_episode_with_one_subtitle, monkeypatch):
        """The exact ordering that silently dropped the subtitle before
        this fix -- the video WITHOUT a subtitle (720p) is visited first
        and wins the claim, so the video WITH the subtitle (1080p) is the
        one that loses. Its subtitle must still be reported.
        """
        show = conflicted_episode_with_one_subtitle["show"]
        monkeypatch.setattr(
            'media_agent.tv.os.walk',
            _walk_forcing_show_subdir_order(show, ["720p", "1080p"]))
        data = json.loads(
            (conflicted_episode_with_one_subtitle["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(conflicted_episode_with_one_subtitle["root"] / "TV Shows"), data)

        assert plan['file_moves'] == [], (
            f"expected no moves at all (both videos conflict), got {plan['file_moves']}"
        )
        conflict_text = " ".join(reason for reason, _path in plan['conflicts'])
        assert "S01E01.srt" in conflict_text, (
            "the subtitle belonging to the LOSING video in this walk order "
            f"was never reported at all -- got conflicts:\n{plan['conflicts']}"
        )

    def test_apply_leaves_the_subtitle_with_its_video_regardless_of_walk_order(
            self, conflicted_episode_with_one_subtitle, monkeypatch):
        """The behavioral guarantee (not just the message) must also hold
        in the losing-video-visited-first order -- belt and suspenders
        alongside the plan-level assertions above."""
        show = conflicted_episode_with_one_subtitle["show"]
        monkeypatch.setattr(
            'media_agent.tv.os.walk',
            _walk_forcing_show_subdir_order(show, ["720p", "1080p"]))

        cmd_normalize_tv(Args(apply=True))

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


@pytest.fixture
def two_different_subtitles_collide(tmp_path):
    """Three videos, two of them sharing one subtitle, the third in a
    different folder with its OWN, genuinely different subtitle -- but all
    subtitles land on the same destination filename regardless of which
    folder or video they came from.

    Found by the fifth-pass reviewer: retracting a video correctly cascaded
    down to its shared children, but nothing cascaded back up from a
    conflicted CHILD to the video(s) depending on it. Reproduced before
    fixing: all three videos moved into Season 01, both subtitles were left
    behind, contradicting the "moving together or not at all" episode-group
    promise this tool makes everywhere else.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Breaking Bad (2008)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    show.mkdir(parents=True)
    (show / "alt").mkdir()

    (show / "S01E01.mkv").write_bytes(b"mkv")
    (show / "S01E01.mp4").write_bytes(b"mp4")
    (show / "S01E01.srt").write_bytes(b"root subs")
    (show / "alt" / "S01E01.avi").write_bytes(b"avi")
    (show / "alt" / "S01E01.srt").write_bytes(b"alt subs -- different content")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Breaking Bad", "year": "2008",
                   "folder": "Breaking Bad (2008)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                       {"filename": "S01E01.mp4", "season": 1, "episode": 1},
                       {"filename": "S01E01.avi", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


class TestConflictingSubtitleCancelsAllDependentVideos:
    """A genuine conflict between two DIFFERENT subtitle files must cancel
    the whole episode group -- every video that depends on either subtitle --
    not just the subtitle claims themselves.
    """

    def test_plan_moves_nothing(self, two_different_subtitles_collide):
        data = json.loads(
            (two_different_subtitles_collide["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(two_different_subtitles_collide["root"] / "TV Shows"), data)

        assert plan['file_moves'] == [], (
            f"expected the whole episode group cancelled, got {plan['file_moves']}"
        )

    def test_apply_leaves_every_file_exactly_where_it_was(
            self, two_different_subtitles_collide):
        cmd_normalize_tv(Args(apply=True))

        show = two_different_subtitles_collide["show"]
        assert not (show / "Season 01").exists(), "a conflict should create nothing"
        assert (show / "S01E01.mkv").exists()
        assert (show / "S01E01.mp4").exists()
        assert (show / "alt" / "S01E01.avi").exists()
        assert (show / "S01E01.srt").exists(), "root subtitle moved despite the conflict"
        assert (show / "alt" / "S01E01.srt").exists(), "alt subtitle moved despite the conflict"


@pytest.fixture
def subtitle_already_at_destination(tmp_path):
    """The subtitle's target already has a file sitting there -- left over
    from an earlier run, most likely -- while the video itself has a clear
    path. Found by the sixth-pass reviewer: this on-disk check predates all
    three planned-collision fixes and never touched the video's own claim,
    so the video moved alone while its subtitle was left behind.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Show (2020)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    (show / "loose").mkdir(parents=True)
    (show / "Season 01").mkdir()

    (show / "loose" / "S01E01.mkv").write_bytes(b"the mkv")
    (show / "loose" / "S01E01.srt").write_bytes(b"loose subs")
    (show / "Season 01" / "S01E01.srt").write_bytes(b"pre-existing, unrelated")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Show", "year": "2020", "folder": "Show (2020)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


class TestVideoRetractedWhenSubtitleDestinationAlreadyExists:
    """A pre-existing file at the subtitle's destination is a conflict, and
    like every other conflict in this planner, it must cancel the whole
    episode group -- not just report itself and let the video move alone.
    """

    def test_plan_moves_nothing(self, subtitle_already_at_destination):
        data = json.loads(
            (subtitle_already_at_destination["root"] / "tvshows.json")
            .read_text(encoding='utf-8'))
        plan = _build_normalize_tv_plan(
            str(subtitle_already_at_destination["root"] / "TV Shows"), data)

        assert plan['file_moves'] == [], (
            f"expected the video to be cancelled too, got {plan['file_moves']}"
        )

    def test_apply_leaves_everything_exactly_where_it_was(
            self, subtitle_already_at_destination):
        cmd_normalize_tv(Args(apply=True))

        show = subtitle_already_at_destination["show"]
        assert not (show / "Season 01" / "S01E01.mkv").exists(), (
            "video moved even though its subtitle's destination was blocked"
        )
        assert (show / "loose" / "S01E01.mkv").exists()
        assert (show / "loose" / "S01E01.srt").exists()
        # The pre-existing file at the destination must be untouched, not
        # overwritten and not deleted.
        pre_existing = show / "Season 01" / "S01E01.srt"
        assert pre_existing.exists()
        assert pre_existing.read_bytes() == b"pre-existing, unrelated"


@pytest.fixture
def video_with_two_subtitles_first_blocked(tmp_path):
    """One video with TWO subtitle matches (plain + language-tagged). The
    first one found (alphabetically, .en.srt before .srt) collides with a
    pre-existing file and retracts the video -- the second must not still
    be claimed on behalf of a parent that no longer exists.
    """
    root = tmp_path / "Library"
    show = root / "TV Shows" / "Show (2020)"
    (root / "Movies").mkdir(parents=True)
    (root / "Music").mkdir()
    (show / "loose").mkdir(parents=True)
    (show / "Season 01").mkdir()

    (show / "loose" / "S01E01.mkv").write_bytes(b"the mkv")
    (show / "loose" / "S01E01.en.srt").write_bytes(b"english subs")
    (show / "loose" / "S01E01.srt").write_bytes(b"plain subs")
    (show / "Season 01" / "S01E01.en.srt").write_bytes(b"pre-existing english")

    (root / "tvshows.json").write_text(json.dumps({
        "shows": [{"name": "Show", "year": "2020", "folder": "Show (2020)",
                   "seasons": [{"season": 1, "episodes": [
                       {"filename": "S01E01.mkv", "season": 1, "episode": 1},
                   ]}]}]
    }), encoding='utf-8')

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"library_root": str(root)}), encoding='utf-8')
    config_mod.set_config(Config.load(cfg_path))
    yield {"root": root, "show": show}
    config_mod.CONFIG = None


class TestSecondSubtitleNotClaimedForAnAlreadyDeadVideo:
    """Once a video is retracted mid-loop, no further subtitle on its behalf
    should be claimed at all -- a child registered against a parent that's
    already gone would never be cleaned up by the normal cascade.
    """

    def test_neither_subtitle_moves_and_video_stays_put(
            self, video_with_two_subtitles_first_blocked):
        cmd_normalize_tv(Args(apply=True))

        show = video_with_two_subtitles_first_blocked["show"]
        assert not (show / "Season 01" / "S01E01.mkv").exists()
        assert (show / "loose" / "S01E01.mkv").exists()
        assert (show / "loose" / "S01E01.en.srt").exists()
        assert (show / "loose" / "S01E01.srt").exists(), (
            "the second subtitle was left dangling -- claimed on behalf of "
            "a video that had already been retracted"
        )
