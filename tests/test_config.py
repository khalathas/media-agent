"""Tests for config loading, discovery and the runtime singleton."""

import json

import pytest

from media_agent import config as config_mod
from media_agent.config import Config


@pytest.fixture
def library(tmp_path):
    """A minimal but valid library tree."""
    root = tmp_path / "Library"
    for section in ("Movies", "TV Shows", "Music"):
        (root / section).mkdir(parents=True)
    return root


def write_config(path, **fields):
    path.write_text(json.dumps(fields), encoding='utf-8')
    return path


class TestLoad:
    def test_minimal_config_uses_defaults(self, tmp_path, library):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library))
        cfg = Config.load(cfg_path)
        assert cfg.library_root == library.resolve()
        assert cfg.sections['movies'] == (library / "Movies").resolve()
        # indexes default to sitting alongside the library
        assert cfg.indexes_dir == library.resolve()
        assert cfg.indexes['movies'].name == "movies.json"

    def test_missing_library_root_exits(self, tmp_path):
        cfg_path = write_config(tmp_path / "c.json", tmdb_read_access_token="x")
        with pytest.raises(SystemExit):
            Config.load(cfg_path)

    def test_bad_json_exits(self, tmp_path):
        cfg_path = tmp_path / "c.json"
        cfg_path.write_text("{not json", encoding='utf-8')
        with pytest.raises(SystemExit):
            Config.load(cfg_path)

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            Config.load(tmp_path / "nope.json")

    def test_indexes_dir_can_be_separated_from_library(self, tmp_path, library):
        idx = tmp_path / "indexes"
        cfg_path = write_config(tmp_path / "c.json",
                                library_root=str(library), indexes_dir=str(idx))
        cfg = Config.load(cfg_path)
        assert cfg.indexes_dir == idx.resolve()
        assert cfg.indexes['movies'].parent == idx.resolve()
        # ...while the library itself is still read from its own location
        assert cfg.library_root == library.resolve()

    def test_bare_section_name_resolves_under_library_root(self, tmp_path, library,
                                                            monkeypatch):
        """A bare name means "inside the library", not "relative to cwd".

        Resolving against the working directory would make the same config
        scan different folders depending on where the command was run from.
        """
        (library / "Films").mkdir()
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                sections={"movies": "Films"})
        monkeypatch.chdir(tmp_path)          # deliberately not the library root
        assert Config.load(cfg_path).sections['movies'] == (library / "Films").resolve()

    def test_absolute_section_path_is_used_as_given(self, tmp_path, library):
        elsewhere = tmp_path / "OtherDrive" / "Films"
        elsewhere.mkdir(parents=True)
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                sections={"movies": str(elsewhere)})
        assert Config.load(cfg_path).sections['movies'] == elsewhere.resolve()

    def test_custom_section_name(self, tmp_path, library):
        (library / "Films").mkdir()
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                sections={"movies": str(library / "Films")})
        assert Config.load(cfg_path).sections['movies'] == (library / "Films").resolve()


class TestSkipShows:
    def test_defaults_to_empty(self, tmp_path, library):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library))
        assert Config.load(cfg_path).skip_shows == frozenset()

    def test_lowercased_for_case_insensitive_match(self, tmp_path, library):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                skip_shows=["Gundam", "  Some Show  "])
        assert Config.load(cfg_path).skip_shows == frozenset({"gundam", "some show"})


class TestTmdbOverrides:
    def test_absent_by_default(self, tmp_path, library):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library))
        cfg = Config.load(cfg_path)
        assert cfg.tmdb_corrections == {}
        assert cfg.tmdb_misspellings == {}

    def test_loaded_from_file(self, tmp_path, library):
        ov = tmp_path / "ov.json"
        ov.write_text(json.dumps({
            "_comment": "ignored",
            "corrections": {"Highlander (1992).mkv": 8009},
            "misspellings": {"Terrabithia": "terabithia"},
        }), encoding='utf-8')
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                tmdb_overrides_file=str(ov))
        cfg = Config.load(cfg_path)
        assert cfg.tmdb_corrections == {"Highlander (1992).mkv": 8009}
        # misspelling keys are lowercased so matching is case-insensitive
        assert cfg.tmdb_misspellings == {"terrabithia": "terabithia"}

    def test_relative_path_resolves_against_config_dir(self, tmp_path, library):
        (tmp_path / "ov.json").write_text(
            json.dumps({"corrections": {"a.mkv": 1}}), encoding='utf-8')
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                tmdb_overrides_file="ov.json")
        assert Config.load(cfg_path).tmdb_corrections == {"a.mkv": 1}

    def test_missing_overrides_file_warns_but_survives(self, tmp_path, library, capsys):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                tmdb_overrides_file="does-not-exist.json")
        cfg = Config.load(cfg_path)
        assert cfg.tmdb_corrections == {}
        assert "not found" in capsys.readouterr().out


class TestTokenPlaceholders:
    @pytest.mark.parametrize("placeholder", [
        "", "YOUR_TOKEN_HERE", "READ_ACCESS_TOKEN_HERE",
    ])
    def test_placeholders_count_as_unset(self, tmp_path, library, placeholder):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                tmdb_read_access_token=placeholder)
        assert Config.load(cfg_path).tmdb_token == ""

    def test_real_token_is_kept(self, tmp_path, library):
        cfg_path = write_config(tmp_path / "c.json", library_root=str(library),
                                tmdb_read_access_token="eyJhbGciOi.real.token")
        assert Config.load(cfg_path).tmdb_token == "eyJhbGciOi.real.token"


class TestRuntimeSingleton:
    def test_get_before_set_raises_clearly(self, monkeypatch):
        monkeypatch.setattr(config_mod, 'CONFIG', None)
        with pytest.raises(RuntimeError, match="not initialised"):
            config_mod.get_config()

    def test_set_then_get_round_trips(self, tmp_path, library, monkeypatch):
        monkeypatch.setattr(config_mod, 'CONFIG', None)
        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library)))
        config_mod.set_config(cfg)
        assert config_mod.get_config() is cfg
