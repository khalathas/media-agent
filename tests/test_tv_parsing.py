"""Tests for TV show folder and episode filename parsing."""

import pytest

from media_agent.tv import clean_show_name, parse_episode_info


class TestCleanShowName:
    @pytest.mark.parametrize("folder,name,year", [
        ("Breaking Bad (2008)",  "Breaking Bad",  "2008"),
        ("The.Office.US.2005",   "The Office US", "2005"),
        ("Firefly",              "Firefly",       None),
    ])
    def test_splits_name_and_year(self, folder, name, year):
        assert clean_show_name(folder) == (name, year)


class TestParseEpisodeInfo:
    @pytest.mark.parametrize("filename,season,episode", [
        ("Show.S01E05.Title.mkv",   1, 5),   # standard SxxExx
        ("Show 2x07 Title.avi",     2, 7),   # NxNN
        ("Show 105 Title.mkv",      1, 5),   # compact NMM
        ("Show.S00E03.Special.mkv", 0, 3),   # specials live in season 0
    ])
    def test_season_and_episode(self, filename, season, episode):
        info = parse_episode_info(filename)
        assert info['season'] == season
        assert info['episode'] == episode

    def test_multi_episode_is_captured(self):
        info = parse_episode_info("Show.S02E01E02.mkv")
        assert info['season'] == 2
        assert info['multi_episode'] == [1, 2]
        # the first episode number is still reported, for indexing
        assert info['episode'] == 1

    def test_single_episode_has_no_multi_marker(self):
        assert parse_episode_info("Show.S01E05.Title.mkv")['multi_episode'] is None

    def test_episode_title_is_extracted(self):
        assert parse_episode_info("Show.S01E05.Title.mkv")['title'] == 'Title'

    def test_unparseable_returns_none_season(self):
        # A file with no episode marker must not be silently assigned a season;
        # normalize-tv relies on this to flag it as a conflict instead of moving it.
        info = parse_episode_info("random.mkv")
        assert info['season'] is None
        assert info['episode'] is None
