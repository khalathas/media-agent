"""Tests for the pure filename/title parsing helpers.

Nothing here touches the filesystem or the config, so these run anywhere.
"""

import pytest

from media_agent.naming import (_canonical_filename, _sanitize_path_component,
                                build_clean_name, parse_movie_filename,
                                smart_title_case)


class TestSmartTitleCase:
    @pytest.mark.parametrize("raw,expected", [
        ("the matrix",                 "The Matrix"),
        ("lord of the rings",          "Lord of the Rings"),
        ("a tale of two cities",       "A Tale of Two Cities"),
        ("gone with the wind",         "Gone with the Wind"),
    ])
    def test_casing(self, raw, expected):
        assert smart_title_case(raw) == expected

    def test_existing_capitals_are_left_alone(self):
        # Deliberate: an already-capitalised word is not touched, so acronyms
        # and stylised titles survive.
        assert smart_title_case("THE MATRIX") == "THE MATRIX"

    def test_first_word_always_capitalised(self):
        assert smart_title_case("the thing").startswith("The")

    def test_last_word_always_capitalised(self):
        # a trailing minor word still gets capitalised
        assert smart_title_case("what its all about for").endswith("For")


class TestParseMovieFilename:
    def test_extracts_title_and_year(self):
        parsed = parse_movie_filename("The.Matrix.1999.1080p.BluRay.x264.mkv")
        assert parsed is not None
        assert parsed['year'] == '1999'
        assert 'matrix' in parsed['title'].lower()

    def test_underscores_and_dots_become_spaces(self):
        parsed = parse_movie_filename("Blade_Runner.1982.mp4")
        assert parsed is not None
        assert 'blade runner' in parsed['title'].lower()

    def test_already_clean_name_returns_none(self):
        # parse_movie_filename signals "nothing to do" with None
        assert parse_movie_filename("The Matrix (1999).mkv") is None


class TestBuildCleanName:
    def test_round_trip_produces_plex_shape(self):
        parsed = parse_movie_filename("Alien.1979.DVDRip.avi")
        assert build_clean_name(parsed) == "Alien (1979).avi"

    def test_quality_tag_is_preserved(self):
        # Plex reads [1080p] fine, and it is useful information, so normalize
        # keeps it rather than discarding it.
        parsed = parse_movie_filename("The.Matrix.1999.1080p.BluRay.x264.mkv")
        assert build_clean_name(parsed) == "The Matrix (1999) [1080p].mkv"

    def test_preserves_extension(self):
        parsed = parse_movie_filename("Blade_Runner.1982.mp4")
        assert build_clean_name(parsed).endswith(".mp4")


class TestSanitizePathComponent:
    @pytest.mark.parametrize("bad", list(r'\/:*?"<>|'))
    def test_strips_every_windows_illegal_char(self, bad):
        assert bad not in _sanitize_path_component(f"AC{bad}DC")

    def test_strips_trailing_dot_and_space(self):
        # Windows silently drops these, which breaks path round-tripping
        assert _sanitize_path_component("Album. ") == "Album"

    def test_never_returns_empty(self):
        assert _sanitize_path_component("...") == "_"
        assert _sanitize_path_component("") == "_"


class TestCanonicalFilename:
    def test_basic_shape(self):
        assert _canonical_filename("Highlander", "1986", 8009, ".mkv") == \
            "Highlander (1986) {tmdb-8009}.mkv"

    def test_colon_becomes_dash(self):
        out = _canonical_filename("Alien: Resurrection", "1997", 8078, ".mp4")
        assert ':' not in out
        assert "Alien - Resurrection" in out

    def test_missing_year_omits_parens(self):
        out = _canonical_filename("Untitled", None, 42, ".mkv")
        assert out == "Untitled {tmdb-42}.mkv"

    def test_no_windows_illegal_chars_survive(self):
        out = _canonical_filename('What?! <Really>', "2000", 1, ".mkv")
        assert not any(c in out for c in r'\/:*?"<>|')
