"""Configuration loading and the runtime Config object.

The active Config is a module-level singleton set once by cli.main() before any
command runs. Other modules reach it via get_config() rather than importing the
value directly -- importing it would capture None at import time.
"""

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TMDB_API_BASE  = "https://api.themoviedb.org/3"
# Values that mean 'no token has been set yet'. More than one spelling has
# shipped in example configs over time, so accept them all rather than
# sending a placeholder to TMDB and getting an unexplained 401 back.
_TOKEN_PLACEHOLDERS = frozenset({
    '', 'YOUR_TOKEN_HERE', 'READ_ACCESS_TOKEN_HERE', '<your token here>',
})

TMDB_TOKEN_ENV = "TMDB_TOKEN"

# Shown wherever ffprobe is missing. Deliberately package-manager first: most
# people who install with pip have no repo checkout, so pointing them at a
# script in scripts/ would name a file they do not have.
FFMPEG_HINT = """  Install ffmpeg, then reopen your terminal:
    Windows : winget install Gyan.FFmpeg
    macOS   : brew install ffmpeg
    Linux   : sudo apt install ffmpeg   (or your distro's package manager)
  Or download from https://ffmpeg.org/download.html and add it to your PATH.
  If it is installed somewhere unusual, set 'ffprobe_path' in your config."""

_DEFAULT_VIDEO_EXTS = frozenset({
    '.mkv', '.mp4', '.avi', '.mov', '.m4v', '.wmv', '.flv',
    '.ts', '.m2ts', '.mpg', '.mpeg', '.vob', '.divx', '.webm', '.ogm',
})

_DEFAULT_MUSIC_EXTS = frozenset({
    '.mp3', '.flac', '.m4a', '.mp2', '.ogg', '.wma', '.aac', '.wav',
})

_DEFAULT_MUSIC_JUNK_NAMES = frozenset({
    'desktop.ini', 'thumbs.db', 'folder.jpg', 'folder.jpeg',
    'albumart.jpg', 'albumart.jpeg',
})

_DEFAULT_MUSIC_JUNK_PATTERNS = (
    re.compile(r'^albumart.*\.(jpg|jpeg|png|bmp)$', re.IGNORECASE),
    re.compile(r'\.nfo$', re.IGNORECASE),
    re.compile(r'\.m3u8?$', re.IGNORECASE),
    re.compile(r'\.lnk$', re.IGNORECASE),
)

# Config search paths (in priority order, after --config flag and env var)
_CONFIG_SEARCH_PATHS = [
    Path('media_agent_config.json'),
    Path.home() / '.config' / 'media-agent' / 'config.json',
    Path.home() / 'media_agent_config.json',
]


@dataclass(frozen=True)
class Config:
    library_root:           Path
    sections:               dict   # {'movies': Path, 'tv_shows': Path, 'music': Path}
    indexes:                dict   # {'movies': Path, 'movies_low_res': Path, ...}
    indexes_dir:            Path
    reports_dir:            Path
    ffprobe:                Optional[str]
    tmdb_token:             str
    video_exts:             frozenset
    music_exts:             frozenset
    music_junk_names:       frozenset
    music_junk_patterns:    tuple
    music_needs_tagging_dir: str
    music_collisions_dir:   str
    skip_shows:             frozenset   # show folder names to leave alone (lowercased)
    tmdb_corrections:       dict        # {filename: tmdb_id}
    tmdb_misspellings:      dict        # {wrong_fragment: right_fragment}

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'Config':
        path = cls._find_config_file(config_path)
        if path is None:
            searched = '\n  '.join(str(p.resolve()) for p in _CONFIG_SEARCH_PATHS)
            print(
                "ERROR: No media_agent_config.json found.\n"
                f"Searched:\n  {searched}\n\n"
                "Run 'media-agent init' to create one.",
                file=sys.stderr,
            )
            sys.exit(2)

        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Could not parse {path}: {e}", file=sys.stderr)
            sys.exit(2)

        # ── Required field ────────────────────────────────────────────────────
        lr = raw.get('library_root')
        if not lr:
            print(
                f"ERROR: {path} is missing required field 'library_root'.\n"
                "If this is an old TMDB-only config, copy media_agent_config.EXAMPLE.json\n"
                "and merge your 'tmdb_read_access_token' into the new format.",
                file=sys.stderr,
            )
            sys.exit(2)

        library_root = Path(lr).expanduser().resolve()
        if not library_root.is_dir():
            print(
                f"ERROR: library_root '{library_root}' does not exist or is not a directory.",
                file=sys.stderr,
            )
            sys.exit(2)

        # ── Sections ──────────────────────────────────────────────────────────
        sec_overrides = raw.get('sections') or {}
        sections = {
            'movies':   cls._resolve_section(
                sec_overrides.get('movies'),   library_root / 'Movies',   library_root),
            'tv_shows': cls._resolve_section(
                sec_overrides.get('tv_shows'), library_root / 'TV Shows', library_root),
            'music':    cls._resolve_section(
                sec_overrides.get('music'),    library_root / 'Music',    library_root),
        }
        for name, spath in sections.items():
            if not spath.is_dir():
                print(f"WARNING: section '{name}' path does not exist: {spath}")

        # ── Indexes and reports dirs ──────────────────────────────────────────
        indexes_dir  = cls._resolve_dir(raw.get('indexes_dir'),  library_root)
        reports_dir  = cls._resolve_dir(raw.get('reports_dir'),  library_root)

        indexes = {
            'movies':         indexes_dir / 'movies.json',
            'movies_low_res': indexes_dir / 'movies_below_720p.csv',
            'movies_tmdb':    indexes_dir / 'movies_tmdb.json',
            'tvshows':        indexes_dir / 'tvshows.json',
            'music':          indexes_dir / 'music.json',
        }

        # ── ffprobe ───────────────────────────────────────────────────────────
        ffprobe = cls._resolve_ffprobe(raw.get('ffprobe_path'))
        if ffprobe is None:
            print("WARNING: ffprobe not found in PATH and not set in config. "
                  "Commands that probe media files will fail.")

        # ── TMDB token ────────────────────────────────────────────────────────
        tmdb_token = raw.get('tmdb_read_access_token', '')
        if tmdb_token in _TOKEN_PLACEHOLDERS:
            tmdb_token = ''

        # ── Music settings ────────────────────────────────────────────────────
        music_cfg = raw.get('music') or {}

        # ── Extension overrides ───────────────────────────────────────────────
        video_exts = frozenset(raw['video_exts']) if 'video_exts' in raw else _DEFAULT_VIDEO_EXTS
        music_exts = frozenset(raw['music_exts']) if 'music_exts' in raw else _DEFAULT_MUSIC_EXTS
        junk_names = (
            frozenset(n.lower() for n in raw['music']['junk_names'])
            if 'music' in raw and 'junk_names' in raw['music']
            else _DEFAULT_MUSIC_JUNK_NAMES
        )
        junk_patterns = (
            tuple(re.compile(p, re.IGNORECASE) for p in raw['music']['junk_patterns'])
            if 'music' in raw and 'junk_patterns' in raw['music']
            else _DEFAULT_MUSIC_JUNK_PATTERNS
        )

        overrides = cls._load_tmdb_overrides(raw.get('tmdb_overrides_file'), path)

        return cls(
            library_root           = library_root,
            sections               = sections,
            indexes                = indexes,
            indexes_dir            = indexes_dir,
            reports_dir            = reports_dir,
            ffprobe                = ffprobe,
            tmdb_token             = tmdb_token,
            video_exts             = video_exts,
            music_exts             = music_exts,
            music_junk_names       = junk_names,
            music_junk_patterns    = junk_patterns,
            music_needs_tagging_dir = cls._safe_subdir(
                music_cfg.get('needs_tagging_dir'), '_NeedsTagging',
                'music.needs_tagging_dir'),
            music_collisions_dir    = cls._safe_subdir(
                music_cfg.get('collisions_dir'), '_NeedsTagging/_Collisions',
                'music.collisions_dir'),
            skip_shows              = frozenset(
                s.strip().lower() for s in raw.get('skip_shows', []) if s.strip()),
            tmdb_corrections        = overrides.get('corrections', {}),
            tmdb_misspellings       = {k.lower(): v for k, v
                                       in overrides.get('misspellings', {}).items()},
        )

    @staticmethod
    def _find_config_file(explicit: Optional[Path]) -> Optional[Path]:
        if explicit is not None:
            p = Path(explicit).expanduser().resolve()
            if p.is_file():
                return p
            print(f"ERROR: Config file not found: {p}", file=sys.stderr)
            sys.exit(2)
        env = os.environ.get('MEDIA_AGENT_CONFIG', '').strip()
        if env:
            p = Path(env).expanduser().resolve()
            if p.is_file():
                return p
        for candidate in _CONFIG_SEARCH_PATHS:
            if candidate.expanduser().resolve().is_file():
                return candidate.expanduser().resolve()
        return None

    @staticmethod
    def _resolve_section(override, default: Path, library_root: Path) -> Path:
        """Resolve a section path.

        A bare name like "Films" means a folder inside library_root, not one
        relative to whatever directory the user happened to run the command
        from -- otherwise the same config would scan different folders
        depending on the current directory.
        """
        if not override:
            return default
        p = Path(override).expanduser()
        if not p.is_absolute():
            p = library_root / p
        return p.resolve()

    @staticmethod
    def _resolve_dir(raw_val, default: Path) -> Path:
        if raw_val:
            p = Path(raw_val).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
        return default

    @staticmethod
    def _safe_subdir(raw_val, default: str, field: str) -> str:
        """Validate a folder name that lives *inside* a media section.

        organize-music moves files into these, so an absolute path or a '..'
        would move media out of the library entirely -- somewhere the user is
        not looking and did not agree to. Only a plain relative subpath is
        accepted; anything else falls back to the default with a warning rather
        than silently relocating someone's music.
        """
        if not raw_val:
            return default
        value = str(raw_val).strip().replace('\\', '/')
        bad = (
            os.path.isabs(value)
            or (len(value) > 1 and value[1] == ':')        # Windows drive letter
            or value.startswith('/')
            or any(part == '..' for part in value.split('/'))
        )
        if bad:
            print(f"WARNING: {field} must be a folder inside your music library, "
                  f"not '{raw_val}'. Using '{default}' instead.")
            return default
        return value.strip('/')

    @staticmethod
    def _load_tmdb_overrides(raw_val, config_path: Path) -> dict:
        """Load optional TMDB corrections/misspellings.

        A relative path is resolved against the config file's own directory, so a
        config and its overrides file can travel together.
        """
        if not raw_val:
            return {}
        p = Path(raw_val).expanduser()
        if not p.is_absolute():
            p = config_path.parent / p
        if not p.is_file():
            print(f"WARNING: tmdb_overrides_file not found: {p}")
            return {}
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"WARNING: could not parse {p}: {e}")
            return {}
        return {k: v for k, v in data.items() if not k.startswith('_')}

    @staticmethod
    def _resolve_ffprobe(config_val: Optional[str]) -> Optional[str]:
        # 1. System PATH — honours any existing install, avoids duplicates
        p = shutil.which('ffprobe')
        if p:
            return p
        if os.name == 'nt':
            p = shutil.which('ffprobe.exe')
            if p:
                return p
        # 2. Explicit config override
        if config_val:
            cv = Path(config_val).expanduser()
            if cv.is_file():
                return str(cv)
            print(f"WARNING: ffprobe_path '{config_val}' not found.")
        # 3. vendor/ffmpeg/ installed by scripts/install_ffmpeg.*
        # This module lives at <root>/src/media_agent/config.py, so the repo root
        # is two levels up. Also check the working directory for a plain checkout.
        names = ('ffprobe.exe',) if os.name == 'nt' else ('ffprobe',)
        roots = (Path(__file__).resolve().parents[2], Path.cwd())
        for root in roots:
            for name in names:
                candidate = root / 'vendor' / 'ffmpeg' / 'bin' / name
                if candidate.is_file():
                    return str(candidate)
        return None


# ── Runtime singleton ─────────────────────────────────────────────────────────

CONFIG: Optional[Config] = None


def set_config(cfg: Config) -> None:
    """Install the active Config. Called once by cli.main()."""
    global CONFIG
    CONFIG = cfg


def get_config() -> Config:
    """Return the active Config, or fail loudly if one was never installed."""
    if CONFIG is None:
        raise RuntimeError(
            "Config not initialised. media_agent.cli.main() installs it before "
            "dispatching a command; call set_config() first if you are using "
            "this package as a library."
        )
    return CONFIG
