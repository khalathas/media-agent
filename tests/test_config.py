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


class TestResolveTmdbToken:
    """P2-4: doctor and the tmdb-* commands used to resolve env-var-vs-config
    precedence independently, and disagreed -- so `doctor` could validate a
    token that wasn't the one actually used to call TMDB. Both now call
    config.resolve_tmdb_token(), the single place that decides this.
    """

    def test_env_var_wins_over_config(self, tmp_path, library, monkeypatch):
        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library),
                                       tmdb_read_access_token="config-token"))
        monkeypatch.setenv("TMDB_TOKEN", "env-token")
        assert config_mod.resolve_tmdb_token(cfg) == "env-token"

    def test_config_used_when_no_env_var(self, tmp_path, library, monkeypatch):
        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library),
                                       tmdb_read_access_token="config-token"))
        monkeypatch.delenv("TMDB_TOKEN", raising=False)
        assert config_mod.resolve_tmdb_token(cfg) == "config-token"

    def test_blank_env_var_falls_back_to_config(self, tmp_path, library, monkeypatch):
        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library),
                                       tmdb_read_access_token="config-token"))
        monkeypatch.setenv("TMDB_TOKEN", "   ")
        assert config_mod.resolve_tmdb_token(cfg) == "config-token"

    def test_uses_active_singleton_when_no_cfg_given(self, tmp_path, library, monkeypatch):
        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library),
                                       tmdb_read_access_token="config-token"))
        monkeypatch.delenv("TMDB_TOKEN", raising=False)
        config_mod.set_config(cfg)
        try:
            assert config_mod.resolve_tmdb_token() == "config-token"
        finally:
            config_mod.CONFIG = None


class TestDoctorAndTmdbAgreeOnToken:
    """The concrete reproduction of P2-4: set the config file and the env
    var to two different tokens and confirm `doctor` validates the exact
    same one the tmdb-* commands would actually send to the API.
    """

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_doctor_validates_the_token_tmdb_commands_actually_use(
            self, tmp_path, library, monkeypatch):
        from media_agent import doctor as doctor_mod
        from media_agent import tmdb as tmdb_mod

        cfg = Config.load(write_config(tmp_path / "c.json", library_root=str(library),
                                       tmdb_read_access_token="config-token"))
        config_mod.set_config(cfg)
        monkeypatch.setenv("TMDB_TOKEN", "env-token")

        captured = {}

        def fake_urlopen(req, timeout=10):
            captured['header'] = req.get_header('Authorization')
            return self._FakeResponse()

        monkeypatch.setattr('media_agent.doctor.urllib.request.urlopen', fake_urlopen)

        class Args:
            pass

        try:
            doctor_mod.cmd_doctor(Args())
        except SystemExit:
            pass  # other checks (ffprobe, mutagen) may fail in a bare test env

        try:
            assert 'header' in captured, "doctor never attempted to validate a TMDB token"
            doctor_used_token = captured['header']
            tmdb_used_token = tmdb_mod._get_tmdb_headers()['Authorization']
            assert doctor_used_token == tmdb_used_token == "Bearer env-token", (
                "doctor and the tmdb-* commands resolved different tokens -- "
                f"doctor used {doctor_used_token!r}, tmdb used {tmdb_used_token!r}"
            )
        finally:
            config_mod.CONFIG = None


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
