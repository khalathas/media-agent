#!/usr/bin/env python3
"""
Media Library Maintenance Agent — local operations, no API required.

Commands:
  rescan          Scan disk and reconcile movies.json + movies_below_720p.csv
  rebuild-lowres  Rebuild movies_below_720p.csv from current movies.json
  normalize       Preview (and optionally apply) filename normalizations
  normalize-tv    Normalize TV show folder structure to Plex standards
  status          Show library stats and index health summary
  scan-tvshows    Scan TV Shows folder and build tvshows.json
  scan-music      Scan Music folder, read ID3/mutagen tags, build music.json
  organize-music  Organize Music folder into Plex Artist/Album/Track structure
  tmdb-enrich        Look up movies via TMDB API, save results to movies_tmdb.json
  tmdb-fix           Apply manual corrections + re-search no-match entries
  tmdb-rename        Add {tmdb-XXXXX} suffix to filenames from movies_tmdb.json
  tmdb-canonicalize  Rename files to canonical TMDB title/year format

Usage:
  python media_agent.py rescan
  python media_agent.py rescan --yes          (skip confirmation prompts)
  python media_agent.py rebuild-lowres
  python media_agent.py normalize --dry-run
  python media_agent.py normalize --apply
  python media_agent.py status
  python media_agent.py scan-tvshows
  python media_agent.py scan-tvshows --yes    (skip confirmation prompts)
  python media_agent.py scan-music
  python media_agent.py organize-music --dry-run
  python media_agent.py organize-music --apply
  python media_agent.py organize-music --apply --yes
  python media_agent.py tmdb-enrich           (requires TMDB_TOKEN env var)
  python media_agent.py tmdb-enrich --reset   (re-lookup all, ignore existing)
  python media_agent.py tmdb-fix
  python media_agent.py tmdb-rename --dry-run
  python media_agent.py tmdb-rename --apply
  python media_agent.py tmdb-rename --apply --include-ambiguous
  python media_agent.py tmdb-canonicalize --dry-run
  python media_agent.py tmdb-canonicalize --apply
  python media_agent.py tmdb-canonicalize --apply --yes
"""

import argparse
import csv
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Force UTF-8 output so filenames with special characters don't crash on Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Constants (non-path) ──────────────────────────────────────────────────────

TMDB_API_BASE  = "https://api.themoviedb.org/3"
TMDB_TOKEN_ENV = "TMDB_TOKEN"

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

# ── Configuration ─────────────────────────────────────────────────────────────



@dataclass(frozen=True)
class Config:
    library_root:           Path
    sections:               dict   # {'movies': Path, 'tv_shows': Path, 'music': Path}
    indexes:                dict   # {'movies': Path, 'movies_low_res': Path, ...}
    reports_dir:            Path
    ffprobe:                Optional[str]
    tmdb_token:             str
    video_exts:             frozenset
    music_exts:             frozenset
    music_junk_names:       frozenset
    music_junk_patterns:    tuple
    music_needs_tagging_dir: str
    music_collisions_dir:   str

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'Config':
        path = cls._find_config_file(config_path)
        if path is None:
            searched = '\n  '.join(str(p.resolve()) for p in _CONFIG_SEARCH_PATHS)
            print(
                "ERROR: No media_agent_config.json found.\n"
                f"Searched:\n  {searched}\n\n"
                "Copy media_agent_config.EXAMPLE.json to one of the above paths "
                "and set 'library_root'.",
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
            'movies':   cls._resolve_section(sec_overrides.get('movies'),   library_root / 'Movies'),
            'tv_shows': cls._resolve_section(sec_overrides.get('tv_shows'), library_root / 'TV Shows'),
            'music':    cls._resolve_section(sec_overrides.get('music'),    library_root / 'Music'),
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
        if tmdb_token in ('', 'YOUR_TOKEN_HERE'):
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

        return cls(
            library_root           = library_root,
            sections               = sections,
            indexes                = indexes,
            reports_dir            = reports_dir,
            ffprobe                = ffprobe,
            tmdb_token             = tmdb_token,
            video_exts             = video_exts,
            music_exts             = music_exts,
            music_junk_names       = junk_names,
            music_junk_patterns    = junk_patterns,
            music_needs_tagging_dir = music_cfg.get('needs_tagging_dir', '_NeedsTagging'),
            music_collisions_dir    = music_cfg.get('collisions_dir', '_NeedsTagging/_Collisions'),
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
    def _resolve_section(override, default: Path) -> Path:
        if override:
            return Path(override).expanduser().resolve()
        return default

    @staticmethod
    def _resolve_dir(raw_val, default: Path) -> Path:
        if raw_val:
            p = Path(raw_val).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
        return default

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
        vendor_bin = Path(__file__).parent / 'vendor' / 'ffmpeg' / 'bin'
        names = ('ffprobe.exe',) if os.name == 'nt' else ('ffprobe',)
        for name in names:
            candidate = vendor_bin / name
            if candidate.is_file():
                return str(candidate)
        return None


# Module-level singleton — populated in main() after argparse
CONFIG: Optional[Config] = None

LOW_RES_COLS = ['name', 'width', 'height', 'resolution_class',
                'extension', 'audio_codec', 'video_codec',
                'bitrate', 'filesize', 'date_added']

# ── Helpers ───────────────────────────────────────────────────────────────────

def classify_resolution(width, height):
    if width is None or height is None:
        return 'unknown'
    if height >= 2160 or width >= 3840:
        return '4K'
    if height >= 1080 or width >= 1920:
        return '1080p'
    if height >= 720 or width >= 1280:
        return '720p'
    if height >= 480 or width >= 854:
        return '480p'
    if height >= 360 or width >= 640:
        return '360p'
    return 'SD'


def get_media_info(file_path):
    """Run ffprobe and return a dict of media properties, or {'error': ...}."""
    if not CONFIG.ffprobe:
        return {'error': 'ffprobe not found — set ffprobe_path in config or add ffprobe to PATH'}
    try:
        result = subprocess.run(
            [CONFIG.ffprobe, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', file_path],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
        if result.returncode != 0:
            return {'error': result.stderr.strip() or 'ffprobe failed'}
        data    = json.loads(result.stdout)
        fmt     = data.get('format', {})
        streams = data.get('streams', [])
        video   = next((s for s in streams if s.get('codec_type') == 'video'), {})
        audio   = next((s for s in streams if s.get('codec_type') == 'audio'), {})
        has_sub = any(s.get('codec_type') == 'subtitle' for s in streams)
        bitrate = int(fmt.get('bit_rate', 0)) // 1000
        mtime   = datetime.fromtimestamp(
            os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')
        return {
            'name':          os.path.basename(file_path),
            'extension':     os.path.splitext(file_path)[1].lower(),
            'width':         video.get('width'),
            'height':        video.get('height'),
            'video_codec':   video.get('codec_name'),
            'audio_codec':   audio.get('codec_name'),
            'bitrate':       f'{bitrate} kbps',
            'has_subtitles': has_sub,
            'filesize':      int(fmt.get('size', 0)),
            'date_added':    mtime,
        }
    except subprocess.TimeoutExpired:
        return {'error': 'ffprobe timed out'}
    except Exception as e:
        return {'error': str(e)}


def scan_video_files(section_path):
    """Return dict of {filename: full_path} for all video files under path."""
    files = {}
    for root, dirs, fnames in os.walk(section_path):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith('@') and not d.startswith('.'))
        for fname in fnames:
            if os.path.splitext(fname)[1].lower() in CONFIG.video_exts:
                fpath = os.path.join(root, fname).replace('\\', '/')
                if fname in files:
                    # Duplicate filename in different subdirs — keep both with paths
                    existing = files[fname]
                    if isinstance(existing, str):
                        files[fname] = [existing, fpath]
                    else:
                        existing.append(fpath)
                else:
                    files[fname] = fpath
    return files


def load_movies_json():
    path = CONFIG.indexes['movies']
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_movies_json(data):
    path = CONFIG.indexes['movies']
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def confirm(prompt, default='no'):
    """Ask yes/no. Returns True if yes."""
    hint = ' [yes/no]'
    while True:
        try:
            answer = input(prompt + hint + ': ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer in ('yes', 'y'):
            return True
        if answer in ('no', 'n', ''):
            return False
        print("Please enter yes or no.")


# ── Command: rescan ────────────────────────────────────────────────────────────

def cmd_rescan(args):
    """Scan the Movies folder and reconcile movies.json + movies_below_720p.csv."""
    print("Scanning Movies folder...")
    disk_files = scan_video_files(CONFIG.sections['movies'])
    print(f"  {len(disk_files)} video files found on disk")

    data = load_movies_json()
    index_by_name = {m['name']: m for m in data['movies']}
    print(f"  {len(index_by_name)} entries in movies.json")

    on_disk    = set(disk_files)
    in_index   = set(index_by_name)
    new_files  = sorted(on_disk - in_index)
    stale      = sorted(in_index - on_disk)

    print(f"\n  New (on disk, not indexed): {len(new_files)}")
    print(f"  Stale (in index, not on disk): {len(stale)}")

    if not new_files and not stale:
        print("\nIndex is already up to date.")
        _check_low_res_sync(data['movies'])
        return

    changed = False

    # ── Remove stale entries ──────────────────────────────────────────────────
    if stale:
        print("\nStale index entries (file no longer on disk):")
        for name in stale:
            print(f"  - {name}")
        if args.yes or confirm(f"\nRemove {len(stale)} stale entries from index?"):
            data['movies'] = [m for m in data['movies'] if m['name'] not in set(stale)]
            print(f"  Removed {len(stale)} entries.")
            changed = True
        else:
            print("  Skipped.")

    # ── Add new files ─────────────────────────────────────────────────────────
    if new_files:
        print(f"\nNew files to index ({len(new_files)}):")
        for name in new_files:
            p = disk_files[name]
            print(f"  + {name}")

        if args.yes or confirm(f"\nProbe and add {len(new_files)} new files to index?"):
            added, failed = 0, 0
            for name in new_files:
                fpath = disk_files[name]
                if isinstance(fpath, list):
                    fpath = fpath[0]  # pick first if duplicates
                print(f"  Probing: {name} ... ", end='', flush=True)
                info = get_media_info(fpath)
                if 'error' in info:
                    print(f"FAILED ({info['error']})")
                    failed += 1
                else:
                    res = classify_resolution(info['width'], info['height'])
                    print(f"{info['width']}x{info['height']} {res}")
                    data['movies'].append(info)
                    added += 1
            print(f"\n  Added {added} entries." + (f" {failed} failed." if failed else ""))
            changed = True
        else:
            print("  Skipped.")

    # ── Save movies.json ──────────────────────────────────────────────────────
    if changed:
        data['movies'].sort(key=lambda m: m['name'].lower())
        save_movies_json(data)
        print(f"\nmovies.json saved: {len(data['movies'])} entries total.")

    # ── Rebuild low-res CSV ───────────────────────────────────────────────────
    _rebuild_low_res(data['movies'], verbose=True)


def _check_low_res_sync(movies):
    """Warn if movies_below_720p.csv is out of sync with movies.json."""
    try:
        with open(CONFIG.indexes['movies_low_res'], newline='', encoding='utf-8') as f:
            csv_names = {r['name'] for r in csv.DictReader(f)}
        expected = {m['name'] for m in movies
                    if (m.get('height') or 0) < 720 and (m.get('width') or 0) < 1280}
        if csv_names != expected:
            print(f"\nWARNING: movies_below_720p.csv is out of sync "
                  f"({len(csv_names)} rows vs {len(expected)} expected).")
            print("Run:  python media_agent.py rebuild-lowres")
        else:
            print(f"\nmovies_below_720p.csv is in sync ({len(csv_names)} entries).")
    except Exception as e:
        print(f"\nCould not verify movies_below_720p.csv: {e}")


# ── Command: rebuild-lowres ────────────────────────────────────────────────────

def _rebuild_low_res(movies, verbose=False):
    below = []
    for m in movies:
        w, h = m.get('width'), m.get('height')
        if w is not None and h is not None and h < 720 and w < 1280:
            res = classify_resolution(w, h)
            below.append({
                'name':             m['name'],
                'width':            w,
                'height':           h,
                'resolution_class': res,
                'extension':        m.get('extension', ''),
                'audio_codec':      m.get('audio_codec', ''),
                'video_codec':      m.get('video_codec', ''),
                'bitrate':          m.get('bitrate', ''),
                'filesize':         m.get('filesize', ''),
                'date_added':       m.get('date_added', ''),
            })
    below.sort(key=lambda r: (r['height'], r['width']))
    path = CONFIG.indexes['movies_low_res']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=LOW_RES_COLS)
        writer.writeheader()
        writer.writerows(below)
    if verbose:
        print(f"\nmovies_below_720p.csv rebuilt: {len(below)} entries.")
    return len(below)


def cmd_rebuild_lowres(args):
    """Rebuild movies_below_720p.csv from the current movies.json."""
    data = load_movies_json()
    count = _rebuild_low_res(data['movies'], verbose=True)
    res_counts = Counter(
        classify_resolution(m.get('width'), m.get('height'))
        for m in data['movies']
        if (m.get('height') or 0) < 720 and (m.get('width') or 0) < 1280
    )
    for label in ['SD', '360p', '480p']:
        if res_counts[label]:
            print(f"  {label}: {res_counts[label]}")


# ── Command: normalize ─────────────────────────────────────────────────────────

# ── Title casing ──────────────────────────────────────────────────────────────

# Small words that stay lowercase unless they're the first or last word
_LOWER_WORDS = {
    'a', 'an', 'the',
    'and', 'but', 'or', 'nor', 'for', 'so', 'yet',
    'at', 'by', 'in', 'of', 'on', 'to', 'up', 'as', 'from', 'into', 'with',
}


def _case_word(word, is_first, is_last):
    """Case a single word: preserve existing uppercase, capitalise lowercase starts."""
    if not word:
        return word
    # Hyphenated compound — case each part (e.g. x-men → X-Men, web-dl stays)
    if '-' in word:
        parts = word.split('-')
        cased = [_case_word(p, i == 0 and is_first, i == len(parts) - 1 and is_last)
                 for i, p in enumerate(parts)]
        return '-'.join(cased)
    lower = word.lower()
    # Small word in the middle: only lowercase it if it's already all-lowercase
    if not is_first and not is_last and lower in _LOWER_WORDS and word == lower:
        return lower
    # Word starts lowercase → capitalise first letter, preserve the rest
    if word[0].islower():
        return word[0].upper() + word[1:]
    # Already starts with uppercase (includes acronyms like BBC, RSC) → keep as-is
    return word


def smart_title_case(s):
    """
    Title-case a string while:
    - Preserving existing uppercase (acronyms, release tags already capitalised)
    - Only capitalising words that start lowercase
    - Lowercasing small words (articles, short prepositions) in the middle
    - Handling hyphenated compounds word-by-word
    """
    words = s.split()
    return ' '.join(
        _case_word(w, i == 0, i == len(words) - 1)
        for i, w in enumerate(words)
    )


# Tags to strip when building a clean title
_STRIP_RE = re.compile(
    r'[\s._-]*\b('
    r'2160p|1080p|720p|480p|360p|4k|uhd'
    r'|bluray|blu-ray|bdrip|brrip|webrip|web[-\s]?dl|webdl|hdtv'
    r'|dvdrip|dvdscr|hdcam|hdrip|hdzip'
    r'|x264|x265|h264|h265|hevc|xvid|divx|avc'
    r'|aac|ac3|dts|mp3|dd5\.1|truehd|atmos|flac|eac3'
    r'|remux|repack|extended|unrated|theatrical|directors\.cut'
    r'|yify|yts|rarbg|ettv|etrg|galaxyrg|galleryrg'
    r')[\s._-]*.*$',
    re.IGNORECASE
)

_YEAR_RE  = re.compile(r'[\[(]?(19[5-9]\d|20[0-2]\d)[\])]?')
_QUAL_RE  = re.compile(r'\b(2160p|1080p|720p|480p|4k)\b', re.IGNORECASE)

# Filenames that are clearly extras/trailers — skip normalization
_SKIP_RE  = re.compile(
    r'\b(trailer|teaser|featurette|behind.the.scenes|deleted.scene|'
    r'interview|short|sample|extra|bonus|special)\b', re.IGNORECASE)


def parse_movie_filename(filename):
    """
    Attempt to extract (title, year, quality) from a raw filename.
    Returns None if the name already looks clean or can't be parsed.
    """
    stem = os.path.splitext(filename)[0]
    ext  = os.path.splitext(filename)[1].lower()

    # Skip extras and trailers
    if _SKIP_RE.search(stem):
        return None

    # Already in clean format "Title (Year)" or "Title (Year) [Quality]"
    if re.match(r'^[^[]+\(\d{4}\)', stem):
        return None

    # Find year — allow [YYYY] or (YYYY) or bare YYYY
    year_m = _YEAR_RE.search(stem)
    year   = year_m.group(1) if year_m else None

    # Find quality tag
    qual_m = _QUAL_RE.search(stem)
    qual   = qual_m.group(1).lower() if qual_m else None

    # Extract title: everything before the year (or quality tag)
    if year_m:
        raw_title = stem[:year_m.start()]
    elif qual_m:
        raw_title = stem[:qual_m.start()]
    else:
        raw_title = _STRIP_RE.sub('', stem)

    # Clean up separators (dots, underscores → spaces)
    title = re.sub(r'[._]+', ' ', raw_title)
    # Remove stray brackets/parens left at the end (e.g. from "[2003]" parsing)
    title = re.sub(r'[\[(]+\s*$', '', title)
    # Remove non-year parentheticals at the end like "(Unrated)", "(UnCut)", "(ReQ)"
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    title = re.sub(r'\s{2,}', ' ', title).strip().rstrip(' -').strip()

    if not title:
        return None

    return {'title': title, 'year': year, 'quality': qual, 'ext': ext}


def build_clean_name(parsed):
    """Build canonical filename from parsed components."""
    name = smart_title_case(parsed['title'])
    if parsed['year']:
        name += f" ({parsed['year']})"
    if parsed['quality']:
        name += f" [{parsed['quality']}]"
    name += parsed['ext']
    return name


def cmd_normalize(args):
    """Scan Movies folder and suggest standardized filenames."""
    disk_files = scan_video_files(CONFIG.sections['movies'])
    proposals  = []

    for fname, fpath in sorted(disk_files.items()):
        parsed = parse_movie_filename(fname)
        if parsed is None:
            continue
        new_name = build_clean_name(parsed)
        if new_name == fname:
            continue
        proposals.append({'old': fname, 'new': new_name, 'path': fpath})

    if not proposals:
        print("All filenames already look clean.")
        return

    # ── Duplicate detection ───────────────────────────────────────────────────
    # Group by proposed base name (no extension) — catches both same-ext
    # collisions and different-format copies of the same movie.
    from collections import defaultdict
    groups = defaultdict(list)
    for i, p in enumerate(proposals):
        base = os.path.splitext(p['new'])[0]
        groups[base].append(i)

    dupe_count = 0
    for base, indices in groups.items():
        if len(indices) > 1:
            for n, idx in enumerate(indices, 1):
                stem, ext = os.path.splitext(proposals[idx]['new'])
                proposals[idx]['new']      = f"{stem} [DUPE-{n}]{ext}"
                proposals[idx]['is_dupe']  = True
            dupe_count += len(indices)

    # ── Write review file ─────────────────────────────────────────────────────
    review_path = str(CONFIG.reports_dir / 'normalize_preview.txt')
    with open(review_path, 'w', encoding='utf-8') as rf:
        rf.write(f"Normalize preview -- {len(proposals)} proposals"
                 f" ({dupe_count} flagged as duplicates)\n")
        rf.write("=" * 70 + "\n\n")
        for p in proposals:
            tag = "  [DUPLICATE]" if p.get('is_dupe') else ""
            rf.write(f"FROM: {p['old']}\n")
            rf.write(f"  TO: {p['new']}{tag}\n\n")

    print(f"{len(proposals)} files could be renamed "
          f"({dupe_count} flagged as duplicates).")
    print(f"Full list saved to: {review_path}\n")

    # ── Console output ────────────────────────────────────────────────────────
    for p in proposals:
        tag = "  ** DUPLICATE **" if p.get('is_dupe') else ""
        print(f"  FROM: {p['old']}")
        print(f"    TO: {p['new']}{tag}")
        print()

    if args.dry_run or not args.apply:
        print(f"(Dry run — no changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    data = load_movies_json()
    index_by_name = {m['name']: m for m in data['movies']}

    renamed, failed = 0, 0
    for p in proposals:
        old_path = p['path'] if isinstance(p['path'], str) else p['path'][0]
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            # Update index entry
            if p['old'] in index_by_name:
                index_by_name[p['old']]['name'] = p['new']
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['old']} -> {e}")
            failed += 1

    if renamed:
        # Rebuild index from updated dict
        data['movies'] = sorted(index_by_name.values(), key=lambda m: m['name'].lower())
        save_movies_json(data)
        _rebuild_low_res(data['movies'])
        print(f"\nRenamed {renamed} files. Indexes updated.")
    if failed:
        print(f"{failed} renames failed.")


# ── Command: status ────────────────────────────────────────────────────────────

def _print_config_block():
    """Print the resolved runtime configuration."""
    print("── Configuration ─────────────────────────────────────────────────────────────")
    print(f"  library_root  : {CONFIG.library_root}")
    for key in ('movies', 'tv_shows', 'music'):
        path = CONFIG.sections.get(key)
        if path:
            marker = "" if path.is_dir() else "  [NOT FOUND]"
            print(f"  section/{key:<8}: {path}{marker}")
        else:
            print(f"  section/{key:<8}: (not configured)")
    print(f"  indexes_dir   : {CONFIG.indexes_dir}")
    print(f"  reports_dir   : {CONFIG.reports_dir}")
    ffprobe_display = CONFIG.ffprobe if CONFIG.ffprobe else "NOT FOUND — run scripts/install_ffmpeg"
    print(f"  ffprobe       : {ffprobe_display}")
    token_display = "set" if CONFIG.tmdb_token else "not set"
    print(f"  TMDB token    : {token_display}")
    print()


def cmd_status(args):
    """Show library stats and index health."""
    _print_config_block()

    # Index stats
    data = load_movies_json()
    movies = data['movies']
    print(f"movies.json:  {len(movies)} entries")

    res_counts = Counter(
        classify_resolution(m.get('width'), m.get('height')) for m in movies)
    ext_counts = Counter(m.get('extension', '?') for m in movies)
    vc_counts  = Counter(m.get('video_codec', '?') for m in movies)

    print("\nBy resolution:")
    for label in ['4K', '1080p', '720p', '480p', '360p', 'SD', 'unknown']:
        if res_counts[label]:
            print(f"  {label:8s}: {res_counts[label]}")

    print("\nBy extension:")
    for ext, count in ext_counts.most_common():
        print(f"  {ext:6s}: {count}")

    print("\nBy video codec:")
    for codec, count in vc_counts.most_common():
        print(f"  {codec:10s}: {count}")

    # Disk check (quick — just count, don't walk)
    print("\nDisk check...")
    disk_files = scan_video_files(CONFIG.sections['movies'])
    in_index   = {m['name'] for m in movies}
    on_disk    = set(disk_files)
    new_count   = len(on_disk - in_index)
    stale_count = len(in_index - on_disk)
    print(f"  On disk:       {len(on_disk)}")
    print(f"  Not indexed:   {new_count}" + (" <-- run rescan" if new_count else ""))
    print(f"  Stale entries: {stale_count}" + (" <-- run rescan" if stale_count else ""))

    # Low-res CSV check
    try:
        with open(CONFIG.indexes['movies_low_res'], newline='', encoding='utf-8') as f:
            csv_count = sum(1 for _ in csv.DictReader(f))
        expected = sum(1 for m in movies
                       if (m.get('height') or 0) < 720 and (m.get('width') or 0) < 1280)
        sync = "OK" if csv_count == expected else f"OUT OF SYNC (has {csv_count}, expected {expected})"
        print(f"\nmovies_below_720p.csv: {csv_count} rows [{sync}]")
    except Exception as e:
        print(f"\nmovies_below_720p.csv: could not read ({e})")


# ── TV Show helpers ────────────────────────────────────────────────────────────

# Patterns that mark the end of a clean show name in a folder name.
# We strip from the first match of any of these rightward.
_TV_STRIP_RE = re.compile(
    r'\b(?:'
    r'S\d{2,3}(?:E\d{2,3})?'          # S01, S01E01
    r'|Complete[\s._-]+Series'
    r'|Season[\s._-]\d+'
    r'|2160p|1080p|720p|480p|4k|uhd'
    r'|bluray|blu-ray|bdrip|brrip|webrip|web[-\s]?dl|webdl|hdtv'
    r'|nf|amzn|hbo|hulu|dsnp|atvp'    # streaming source tags
    r'|x264|x265|h264|h265|hevc|xvid|divx|avc'
    r'|aac|ac3|dts|mp3|dd\+?5\.1|truehd|atmos|flac|eac3|ddp'
    r'|remux|repack|proper|extended|unrated|uncensored'
    r'|dual[\s._-]?audio|multi'
    r'|batch'
    r')\b'
    r'|\[.*',                          # anything from [ onwards
    re.IGNORECASE,
)

_TV_YEAR_RE = re.compile(r'\(?(19[5-9]\d|20[0-2]\d)\)?')

# Episode number patterns — checked in order (most specific first)
_MULTI_EP_RE = re.compile(r'[Ss](\d{1,2})[-_]?[Ee](\d{1,3})[-_]?[Ee](\d{1,3})')
_SXEX_RE     = re.compile(r'[Ss](\d{1,2})[-_]?[Ee](\d{1,3})')
_NXNN_RE     = re.compile(r'(?<!\d)(\d{1,2})[xX](\d{1,3})(?!\d)')
# Compact NxMM: 101 → S1E01, 0201 → S2E01
# Requires a word-boundary separator (space/dash/dot/underscore) or start/end of stem
# so that resolution specs (720p, 1080p) and hex hashes don't false-match.
_COMPACT_NMM_RE = re.compile(r'(?:^|(?<=[ \-_.]))(0?[1-9])(\d{2})(?=[ \-_.]|$)')

# Tags to strip from the title portion (everything after the SxxExx match)
_EP_TAG_STRIP_RE = re.compile(
    r'\b(?:2160p|1080p|720p|480p|4k|uhd'
    r'|bluray|blu-ray|bdrip|webrip|web[-\s]?dl|webdl|hdtv'
    r'|x264|x265|h264|h265|hevc|xvid|avc'
    r'|aac|ac3|dts|atmos|flac|eac3|ddp|dd\+?5\.1'
    r'|remux|repack|proper)\b'
    r'|\[.*',
    re.IGNORECASE,
)

# Folders to skip when walking show directories
_SKIP_DIRS = {'sample', 'extras', 'bonus', 'featurettes', 'behind the scenes',
              'deleted scenes', 'interviews', 'scenes', 'shorts', 'trailers',
              'corrupt'}

# Known specials keywords — routes episode to season 0
_SPECIAL_RE = re.compile(
    r'\b(special|ova|oad|oav|extra|bonus|christmas|xmas|holiday|new[\s._-]?year)\b',
    re.IGNORECASE,
)


def clean_show_name(folder_name):
    """
    Extract a clean show name and year from a messy folder name.
    Returns (clean_name, year_str_or_None).

    e.g. 'Arcane (2021) S01 (1080p Bluray DDP 5.1 x265) [AnoZu]'
         -> ('Arcane', '2021')
    """
    s = folder_name

    # Strip leading fansub/release-group tag, e.g. '[Judas] Show Name ...'
    s = re.sub(r'^\[[^\]]*\]\s*', '', s)

    # Find year before stripping so we can preserve it
    year_m = _TV_YEAR_RE.search(s)
    year   = year_m.group(1) if year_m else None

    # Strip from the first quality/codec/season indicator rightward
    m = _TV_STRIP_RE.search(s)
    if m:
        s = s[:m.start()]

    # Remove year from what's left — we'll re-attach it canonically
    if year:
        s = re.sub(r'\(?' + year + r'\)?', '', s)

    # Normalise separators
    s = re.sub(r'[._]+', ' ', s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = s.strip(' -.,_')

    # Strip any orphaned opening brackets left behind by partial stripping
    # e.g. "(Season 1)" stripped from the middle leaves a trailing "("
    s = re.sub(r'\s*[\[(]+\s*$', '', s).strip()

    return (s or folder_name), year


def parse_episode_info(filename):
    """
    Parse season number, episode number(s), and title from an episode filename.
    Returns dict: {season, episode, multi_episode, title}
      - season: int or None (None means unknown → caller should default to 0)
      - episode: int or None
      - multi_episode: list of ints (e.g. [1,2]) or None
      - title: str or None
    """
    stem = os.path.splitext(filename)[0]
    season = episode = multi_episode = title = None
    tag_end = 0  # position in stem where the SxxExx tag ends

    # 1. Multi-episode: S01E01E02 or S01E01-E02
    m = _MULTI_EP_RE.search(stem)
    if m:
        season        = int(m.group(1))
        ep_start      = int(m.group(2))
        ep_end        = int(m.group(3))
        episode       = ep_start
        multi_episode = list(range(ep_start, ep_end + 1))
        tag_end       = m.end()
    else:
        # 2. Standard S01E01
        m = _SXEX_RE.search(stem)
        if m:
            season  = int(m.group(1))
            episode = int(m.group(2))
            tag_end = m.end()
        else:
            # 3. 01x01 style
            m = _NXNN_RE.search(stem)
            if m:
                season  = int(m.group(1))
                episode = int(m.group(2))
                tag_end = m.end()
            else:
                # 4. Compact NxMM: 101 → S1E01, 0101 → S1E01
                m = _COMPACT_NMM_RE.search(stem)
                if m:
                    season  = int(m.group(1))
                    episode = int(m.group(2))
                    tag_end = m.end()

    # Extract title from everything after the episode tag
    title_raw = stem[tag_end:]
    title_raw = _EP_TAG_STRIP_RE.sub('', title_raw)
    title_raw = re.sub(r'[._]+', ' ', title_raw)
    title_raw = re.sub(r'[\[(][^\]]*[\])]', '', title_raw)  # strip [...] and (...)
    title_raw = re.sub(r'\s{2,}', ' ', title_raw)
    title_raw = title_raw.strip(' -.,_')
    if title_raw:
        title = title_raw

    # Route specials to season 0 (only if no season was parsed from filename)
    if season is None and _SPECIAL_RE.search(stem):
        season = 0

    return {
        'season':        season,
        'episode':       episode,
        'multi_episode': multi_episode,
        'title':         title,
    }


def get_episode_info(file_path):
    """Run ffprobe on an episode file and return media properties."""
    if not CONFIG.ffprobe:
        return {'error': 'ffprobe not found — set ffprobe_path in config or add ffprobe to PATH'}
    try:
        result = subprocess.run(
            [CONFIG.ffprobe, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', file_path],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=60,
        )
        if result.returncode != 0:
            return {'error': result.stderr.strip() or 'ffprobe failed'}
        data    = json.loads(result.stdout)
        fmt     = data.get('format', {})
        streams = data.get('streams', [])
        video   = next((s for s in streams if s.get('codec_type') == 'video'), {})
        audio   = next((s for s in streams if s.get('codec_type') == 'audio'), {})
        has_sub = any(s.get('codec_type') == 'subtitle' for s in streams)
        bitrate  = int(fmt.get('bit_rate', 0)) // 1000
        duration = float(fmt.get('duration') or 0)
        mtime    = datetime.fromtimestamp(
            os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')
        return {
            'extension':     os.path.splitext(file_path)[1].lower(),
            'width':         video.get('width'),
            'height':        video.get('height'),
            'video_codec':   video.get('codec_name'),
            'audio_codec':   audio.get('codec_name'),
            'bitrate':       f'{bitrate} kbps',
            'has_subtitles': has_sub,
            'filesize':      int(fmt.get('size', 0)),
            'duration':      round(duration, 3),
            'date_added':    mtime,
        }
    except subprocess.TimeoutExpired:
        return {'error': 'ffprobe timed out'}
    except Exception as e:
        return {'error': str(e)}


def find_external_subtitles(video_path):
    """
    Find .srt files in the same directory that belong to this video.
    Matches 'episode.srt', 'episode.en.srt', 'episode.fr.srt', etc.
    """
    video_dir  = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0].lower()
    srts = []
    try:
        for fname in os.listdir(video_dir):
            if not fname.lower().endswith('.srt'):
                continue
            # Strip .srt, then optionally strip a 2-3 char language code
            base = fname.lower()[:-4]
            if re.match(r'.*\.[a-z]{2,3}$', base):
                base = base.rsplit('.', 1)[0]
            if base == video_stem:
                srts.append(fname)
    except Exception:
        pass
    return sorted(srts)


def _build_episode(fname, fpath, season_from_folder=None, existing=None):
    """
    Build a single episode dict.
    season_from_folder: if the file lives in a 'Season N' subfolder, pass that
                        season number as authoritative. Otherwise None → infer.
    existing: a previously-indexed episode dict for this filename. If provided
              and has no probe_error, its media properties are reused instead of
              calling ffprobe again (incremental rescan optimisation).
    """
    ep_info  = parse_episode_info(fname)
    ext_srts = find_external_subtitles(fpath)  # always re-check; SRTs may have been added

    ep = {
        'filename':           fname,
        'episode':            ep_info['episode'],
        'multi_episode':      ep_info['multi_episode'],
        'title':              ep_info['title'],
        'extension':          os.path.splitext(fname)[1].lower(),
        'external_subtitles': ext_srts,
    }

    _PROBE_KEYS = ('width', 'height', 'video_codec', 'audio_codec',
                   'bitrate', 'has_subtitles', 'filesize', 'duration', 'date_added')

    if existing and not existing.get('probe_error'):
        # Reuse prior probe data — no ffprobe call needed
        for k in _PROBE_KEYS:
            ep[k] = existing.get(k)
    else:
        media = get_episode_info(fpath)
        if 'error' in media:
            ep['probe_error'] = media['error']
            for k in _PROBE_KEYS:
                ep[k] = None
        else:
            ep.update({
                'width':         media['width'],
                'height':        media['height'],
                'video_codec':   media['video_codec'],
                'audio_codec':   media['audio_codec'],
                'bitrate':       media['bitrate'],
                'has_subtitles': media['has_subtitles'],
                'filesize':      media['filesize'],
                'duration':      media['duration'],
                'date_added':    media['date_added'],
            })

    # Determine which season bucket this episode belongs to.
    # Priority: season_from_folder > parsed > previously-assigned > 0
    if season_from_folder is not None:
        bucket = season_from_folder
    elif ep_info['season'] is not None:
        bucket = ep_info['season']
    elif existing and existing.get('_season', 0) > 0:
        # Preserve a manually-assigned non-zero season from a prior reassign-season
        # so that incremental rescans don't undo Phase 2 Season 0 resolution work.
        bucket = existing['_season']
    else:
        bucket = 0

    return ep, bucket


def _collect_episodes_from_dir(dir_path, season_hint=None, existing_episodes=None):
    """
    Walk a directory (non-recursively) and return list of (ep_dict, season_bucket)
    for every video file found. Skips non-video files.
    season_hint: if set, used as season_from_folder for all files here.
    existing_episodes: {filename: episode_dict} from prior index for probe reuse.
    """
    results = []
    try:
        for fname in sorted(os.listdir(dir_path)):
            if os.path.splitext(fname)[1].lower() not in CONFIG.video_exts:
                continue
            fpath = os.path.join(dir_path, fname).replace('\\', '/')
            existing = existing_episodes.get(fname) if existing_episodes else None
            ep, bucket = _build_episode(fname, fpath, season_from_folder=season_hint,
                                        existing=existing)
            results.append((ep, bucket))
    except Exception as e:
        print(f"    WARNING: could not read {dir_path}: {e}")
    return results


def scan_tv_shows(existing_episodes=None):
    """
    Walk the TV Shows root and build the full show/season/episode structure.
    Returns list of show dicts ready for JSON serialisation.
    existing_episodes: {filename: episode_dict} from a prior index. When provided,
                       probe data is reused for known files (incremental rescan).
    """
    tv_root = CONFIG.sections['tv_shows']
    shows   = []

    try:
        top_level = sorted(
            d for d in os.listdir(tv_root)
            if os.path.isdir(os.path.join(tv_root, d))
            and not d.startswith('@') and not d.startswith('.')
            and d.lower() not in _SKIP_DIRS
        )
    except Exception as e:
        print(f"ERROR: cannot list TV Shows root: {e}")
        return shows

    for folder in top_level:
        show_path  = os.path.join(tv_root, folder)
        clean_name, year = clean_show_name(folder)

        show = {
            'name':    clean_name,
            'year':    year,
            'folder':  folder,
            'seasons': [],
        }

        # Inventory immediate children
        try:
            children = os.listdir(show_path)
        except Exception as e:
            print(f"  WARNING: cannot read {folder}: {e}")
            continue

        season_dirs = sorted(
            d for d in children
            if os.path.isdir(os.path.join(show_path, d))
            and re.search(r'\bSeason\s*\d+', d, re.IGNORECASE)
        )
        other_dirs = [
            d for d in children
            if os.path.isdir(os.path.join(show_path, d))
            and d not in season_dirs
            and d.lower() not in _SKIP_DIRS
            and not d.startswith('@') and not d.startswith('.')
        ]
        flat_videos = [
            f for f in children
            if os.path.splitext(f)[1].lower() in CONFIG.video_exts
        ]

        # episodes_by_season: season_int -> [ep_dict, ...]
        episodes_by_season = {}

        def add_ep(ep, bucket):
            episodes_by_season.setdefault(bucket, []).append(ep)

        # 1. Proper Season N subfolders — folder season number is authoritative
        for season_dir in season_dirs:
            m = re.search(r'\bSeason\s*(\d+)', season_dir, re.IGNORECASE)
            season_num = int(m.group(1)) if m else 0
            season_path = os.path.join(show_path, season_dir)
            for ep, _ in _collect_episodes_from_dir(season_path, season_hint=season_num,
                                                     existing_episodes=existing_episodes):
                add_ep(ep, season_num)

        # 2. Flat video files directly in show folder — infer season from filename
        for fname in sorted(flat_videos):
            fpath = os.path.join(show_path, fname).replace('\\', '/')
            existing = existing_episodes.get(fname) if existing_episodes else None
            ep, bucket = _build_episode(fname, fpath, season_from_folder=None,
                                        existing=existing)
            add_ep(ep, bucket)

        # 3. Other subdirectories (e.g. messy pack folders without Season naming)
        #    Recurse up to two levels; infer season from filenames
        for sub in sorted(other_dirs):
            sub_path = os.path.join(show_path, sub)
            # Check if this sub looks like it contains season subfolders itself
            try:
                sub_children = os.listdir(sub_path)
            except Exception:
                continue
            sub_season_dirs = [
                d for d in sub_children
                if os.path.isdir(os.path.join(sub_path, d))
                and re.search(r'\bSeason\s*\d+', d, re.IGNORECASE)
            ]
            sub_other_dirs = [
                d for d in sub_children
                if os.path.isdir(os.path.join(sub_path, d))
                and d not in sub_season_dirs
                and d.lower() not in _SKIP_DIRS
                and not d.startswith('@') and not d.startswith('.')
            ]
            if sub_season_dirs:
                # Nested season structure — treat like top-level season dirs
                for season_dir in sorted(sub_season_dirs):
                    m = re.search(r'\d+', season_dir)
                    season_num  = int(m.group()) if m else 0
                    season_path = os.path.join(sub_path, season_dir)
                    for ep, _ in _collect_episodes_from_dir(season_path, season_hint=season_num,
                                                             existing_episodes=existing_episodes):
                        add_ep(ep, season_num)
            elif sub_other_dirs:
                # Deeper nesting (e.g. Doctor Who Classic: show → actor → serial → episodes)
                # Also collect any flat video files directly in sub_path itself
                # (e.g. a pack folder that has episodes + a Subs/ subfolder alongside them)
                for ep, bucket in _collect_episodes_from_dir(sub_path, season_hint=None,
                                                              existing_episodes=existing_episodes):
                    add_ep(ep, bucket)
                for deep_sub in sorted(sub_other_dirs):
                    deep_path = os.path.join(sub_path, deep_sub)
                    for ep, bucket in _collect_episodes_from_dir(deep_path, season_hint=None,
                                                                  existing_episodes=existing_episodes):
                        add_ep(ep, bucket)
            else:
                # Flat files in a subdirectory — infer season from filenames
                for ep, bucket in _collect_episodes_from_dir(sub_path, season_hint=None,
                                                              existing_episodes=existing_episodes):
                    add_ep(ep, bucket)

        # Build seasons list: sort seasons numerically, season 0 always last
        for season_num in sorted(episodes_by_season, key=lambda n: (n == 0, n)):
            episodes = sorted(
                episodes_by_season[season_num],
                key=lambda e: (e['episode'] or 0, e['filename']),
            )
            show['seasons'].append({'season': season_num, 'episodes': episodes})

        if show['seasons']:
            total_eps = sum(len(s['episodes']) for s in show['seasons'])
            has_s0    = any(s['season'] == 0 for s in show['seasons'])
            flag      = '  [has Season 0]' if has_s0 else ''
            print(f"  {clean_name}: {len(show['seasons'])} season(s), "
                  f"{total_eps} ep(s){flag}")
            shows.append(show)
        else:
            print(f"  {folder}: no video files found, skipping")

    return shows


def _merge_duplicate_shows(shows):
    """
    Post-process the shows list to merge entries that share the same cleaned name
    (i.e. multiple folders for the same show).

    For each duplicate group:
    - The merged entry gets duplicate_candidate=True and a duplicate_folders list.
    - Every season gets a source_folder field so future tooling knows its origin.
    - Seasons that share the same season number across folders get
      duplicate_candidate=True and a duplicate_index (1, 2, ...) for ordering.
    """
    from collections import OrderedDict, defaultdict

    by_name = OrderedDict()
    for show in shows:
        by_name.setdefault(show['name'], []).append(show)

    merged = []
    for name, group in by_name.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Tag every season with its source folder
        for show_entry in group:
            for season in show_entry['seasons']:
                season['source_folder'] = show_entry['folder']

        # Group seasons by season number across all folders
        seasons_by_num = defaultdict(list)
        for show_entry in group:
            for season in show_entry['seasons']:
                seasons_by_num[season['season']].append(season)

        merged_seasons = []
        for season_num in sorted(seasons_by_num, key=lambda n: (n == 0, n)):
            entries = seasons_by_num[season_num]
            if len(entries) > 1:
                for i, s in enumerate(entries, 1):
                    s['duplicate_candidate'] = True
                    s['duplicate_index'] = i
            merged_seasons.extend(entries)

        base = group[0].copy()
        base['seasons']           = merged_seasons
        base['duplicate_candidate'] = True
        base['duplicate_folders'] = [s['folder'] for s in group]
        # Prefer a year if any entry has one
        years = [s['year'] for s in group if s.get('year')]
        if years:
            base['year'] = years[0]
        merged.append(base)

    return merged


# ── Command: normalize-tv ──────────────────────────────────────────────────────

_SEASON_DIR_RE = re.compile(r'\bSeason\s*(\d+)', re.IGNORECASE)
_PROPER_SEASON_RE = re.compile(r'^Season \d{2}$')


def _build_normalize_tv_plan(tv_root, data):
    """
    Build the full normalization plan (renames + moves) without touching disk.
    Returns dict with keys: show_renames, season_renames, file_moves, conflicts.
    """
    show_renames   = []   # (old_path, new_path)
    season_renames = []   # (old_path, new_path, show_display_name)
    file_moves     = []   # (src_path, dst_path, show_display_name)
    conflicts      = []   # (reason, path)

    # Build filename -> season lookup from tvshows.json
    # Key: (show_folder, filename) -> season_num
    filename_to_season = {}
    for show in data.get('shows', []):
        folders = show.get('duplicate_folders', [show.get('folder', '')])
        for season in show.get('seasons', []):
            season_num = season['season']
            source_folder = season.get('source_folder')
            for ep in season.get('episodes', []):
                fname = ep['filename']
                if source_folder:
                    filename_to_season[(source_folder, fname)] = season_num
                else:
                    for fld in folders:
                        filename_to_season[(fld, fname)] = season_num

    # Identify all show folders on disk
    try:
        top_level = sorted(
            d for d in os.listdir(tv_root)
            if os.path.isdir(os.path.join(tv_root, d))
            and not d.startswith('@') and not d.startswith('.')
        )
    except Exception as exc:
        print(f"ERROR: cannot list TV Shows root: {exc}")
        return {'show_renames': [], 'season_renames': [], 'file_moves': [], 'conflicts': []}

    # Build a lookup of show data by folder name
    show_by_folder = {}
    for show in data.get('shows', []):
        if show.get('duplicate_folders'):
            for fld in show['duplicate_folders']:
                show_by_folder[fld] = show
        else:
            show_by_folder[show.get('folder', '')] = show

    # Track which expected names are already claimed (for conflict detection)
    existing_folders = set(top_level)

    # Map old folder -> new folder for shows being renamed
    folder_rename_map = {}

    # ── TIER 1a: Show folder renames ──────────────────────────────────────────
    for folder in top_level:
        if folder.lower() == 'gundam':
            continue

        show_data = show_by_folder.get(folder)

        clean_name, year = clean_show_name(folder)
        expected = f"{clean_name} ({year})" if year else clean_name

        if folder == expected:
            continue  # Already correct

        # Skip duplicate_candidate shows for rename? No — spec says only skip
        # file moves for duplicate_candidate, renames are OK.
        # But wait — duplicate shows have multiple folders. We should only rename
        # folders that aren't duplicate_candidate at the SHOW level... actually
        # the spec says "Skip duplicate-candidate shows (they have
        # duplicate_candidate: True at the show level in tvshows.json)" for
        # show folder renames. Let me re-read... yes, skip for show folder renames.
        if show_data and show_data.get('duplicate_candidate'):
            continue

        old_path = os.path.join(tv_root, folder)
        new_path = os.path.join(tv_root, expected)

        # Check for conflict: another folder already has this name on disk
        # (and it's not the folder itself)
        if expected in existing_folders and expected != folder:
            conflicts.append((f"Show rename conflict: '{expected}' already exists", old_path))
            continue

        show_renames.append((old_path, new_path))
        folder_rename_map[folder] = expected
        # Update the existing_folders set
        existing_folders.discard(folder)
        existing_folders.add(expected)

    # ── TIER 1b: Season folder renames ────────────────────────────────────────
    for folder in top_level:
        if folder.lower() == 'gundam':
            continue

        # Use the renamed folder path if applicable
        effective_folder = folder_rename_map.get(folder, folder)
        show_path = os.path.join(tv_root, effective_folder)

        try:
            children = os.listdir(os.path.join(tv_root, folder))
        except Exception:
            continue

        for child in sorted(children):
            child_path_original = os.path.join(tv_root, folder, child)
            if not os.path.isdir(child_path_original):
                continue

            m = _SEASON_DIR_RE.search(child)
            if not m:
                continue

            season_num = int(m.group(1))
            expected_season = f"Season {season_num:02d}"

            if child == expected_season:
                continue

            old_season_path = os.path.join(show_path, child)
            new_season_path = os.path.join(show_path, expected_season)

            # Check conflict
            if os.path.exists(os.path.join(tv_root, folder, expected_season)) and child != expected_season:
                conflicts.append((f"Season rename conflict: '{expected_season}' already exists",
                                  old_season_path))
                continue

            season_renames.append((old_season_path, new_season_path, effective_folder))

    # ── TIER 2: File moves ────────────────────────────────────────────────────
    for folder in top_level:
        if folder.lower() == 'gundam':
            continue

        show_data = show_by_folder.get(folder)

        # Skip duplicate_candidate shows for file moves
        if show_data and show_data.get('duplicate_candidate'):
            continue

        effective_folder = folder_rename_map.get(folder, folder)
        show_path_effective = os.path.join(tv_root, effective_folder)
        # For walking, we need the current on-disk path (before renames apply)
        show_path_disk = os.path.join(tv_root, folder)

        # Build a set of season-renamed dirs so we can compute effective paths
        season_rename_map = {}
        for old_sp, new_sp, _ in season_renames:
            old_basename = os.path.basename(old_sp)
            new_basename = os.path.basename(new_sp)
            if os.path.dirname(old_sp) == show_path_effective:
                season_rename_map[old_basename] = new_basename

        # Walk the show folder recursively for video files
        for dirpath, dirnames, filenames in os.walk(show_path_disk):
            # Filter out skip dirs
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIRS
                           and not d.startswith('@') and not d.startswith('.')]

            for fname in sorted(filenames):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in CONFIG.video_exts:
                    continue

                # Compute relative path from show root
                rel_dir = os.path.relpath(dirpath, show_path_disk).replace('\\', '/')
                if rel_dir == '.':
                    rel_dir = ''

                # Check if the file is already in a proper Season NN dir
                # (the immediate parent relative to show root)
                parts = rel_dir.split('/') if rel_dir else []
                immediate_parent = parts[0] if parts else ''

                # Account for season renames: if the parent will be renamed
                effective_parent = season_rename_map.get(immediate_parent, immediate_parent)

                if _PROPER_SEASON_RE.match(effective_parent):
                    continue  # Already in a proper Season NN folder

                # Look up season from tvshows.json
                season_num = filename_to_season.get((folder, fname))
                if season_num is None:
                    # Try without folder specificity (shouldn't happen for non-dup)
                    season_num = filename_to_season.get((effective_folder, fname))
                if season_num is None:
                    conflicts.append((f"Unknown season for file (not in index)", fname))
                    continue

                target_dir = f"Season {season_num:02d}"
                src_file = os.path.join(show_path_effective,
                                        rel_dir if rel_dir else '',
                                        fname).replace('\\', '/')
                if not rel_dir:
                    src_file = os.path.join(show_path_effective, fname).replace('\\', '/')

                dst_file = os.path.join(show_path_effective, target_dir, fname).replace('\\', '/')

                if src_file == dst_file:
                    continue

                # Check conflict
                dst_disk = os.path.join(show_path_disk,
                                        target_dir if target_dir not in season_rename_map.values() else target_dir,
                                        fname)
                if os.path.exists(dst_disk) and os.path.normpath(dst_disk) != os.path.normpath(os.path.join(dirpath, fname)):
                    conflicts.append((f"File move conflict: target exists", dst_file))
                    continue

                file_moves.append((src_file, dst_file, effective_folder))

                # Also move matching .srt subtitle files
                stem = os.path.splitext(fname)[0]
                for other_file in filenames:
                    if other_file.lower().endswith('.srt') and os.path.splitext(other_file)[0] == stem:
                        srt_src = os.path.join(show_path_effective,
                                               rel_dir if rel_dir else '',
                                               other_file).replace('\\', '/')
                        if not rel_dir:
                            srt_src = os.path.join(show_path_effective, other_file).replace('\\', '/')
                        srt_dst = os.path.join(show_path_effective, target_dir, other_file).replace('\\', '/')
                        if srt_src != srt_dst:
                            file_moves.append((srt_src, srt_dst, effective_folder))

    return {
        'show_renames':   show_renames,
        'season_renames': season_renames,
        'file_moves':     file_moves,
        'conflicts':      conflicts,
    }


def _print_normalize_tv_plan(plan):
    """Print the normalization plan in the specified format."""
    show_renames   = plan['show_renames']
    season_renames = plan['season_renames']
    file_moves     = plan['file_moves']
    conflicts      = plan['conflicts']

    tier1_count = len(show_renames) + len(season_renames)
    tier2_count = len(file_moves)

    print(f"\n{'=' * 60}")
    print(f"=== NORMALIZE-TV PLAN ===")
    print(f"{'=' * 60}")

    print(f"\nTIER 1 — RENAMES ({tier1_count} operations)")
    if tier1_count == 0:
        print("  (none)")
    for old_path, new_path in show_renames:
        old_name = os.path.basename(old_path)
        new_name = os.path.basename(new_path)
        print(f"  [SHOW RENAME]   {old_name}  ->  {new_name}")
    for old_path, new_path, show_name in season_renames:
        old_name = os.path.basename(old_path)
        new_name = os.path.basename(new_path)
        print(f"  [SEASON RENAME] {show_name} / {old_name}  ->  {new_name}")

    print(f"\nTIER 2 — FILE MOVES ({tier2_count} operations)")
    if tier2_count == 0:
        print("  (none)")
    for src, dst, show_name in file_moves:
        fname = os.path.basename(src)
        target_dir = os.path.basename(os.path.dirname(dst))
        print(f"  [MOVE] {show_name} / {fname}  ->  {target_dir}/")

    print(f"\nCONFLICTS ({len(conflicts)} skipped):")
    if not conflicts:
        print("  (none)")
    for reason, path in conflicts:
        print(f"  [CONFLICT] {reason}: {path}")

    print()


def cmd_normalize_tv(args):
    """Normalize the TV Shows folder structure to Plex standards."""
    tv_root = CONFIG.sections['tv_shows']
    index_path = CONFIG.indexes['tvshows']

    if not os.path.exists(index_path):
        print("ERROR: tvshows.json not found — run scan-tvshows first.")
        return

    with open(index_path, encoding='utf-8') as f:
        data = json.load(f)

    print("Building normalization plan...")
    plan = _build_normalize_tv_plan(tv_root, data)

    _print_normalize_tv_plan(plan)

    total_ops = (len(plan['show_renames']) + len(plan['season_renames'])
                 + len(plan['file_moves']))

    if total_ops == 0:
        print("Nothing to do — folder structure is already normalized.")
        return

    apply_mode = getattr(args, 'apply', False)

    if not apply_mode:
        print("Dry-run mode — no changes made.")
        print("Use --apply to execute.")
        return

    # ── Apply the plan ────────────────────────────────────────────────────────
    if not args.yes:
        try:
            ans = input(f"Apply {total_ops} operations? [y/N] ").strip().lower()
        except EOFError:
            ans = ''
        if ans != 'y':
            print("Aborted.")
            return

    applied  = 0
    errors   = 0

    # Tier 1a: Show folder renames
    for old_path, new_path in plan['show_renames']:
        try:
            os.rename(old_path, new_path)
            print(f"  RENAMED show: {os.path.basename(old_path)} -> {os.path.basename(new_path)}")
            applied += 1
        except Exception as exc:
            print(f"  ERROR renaming show {old_path}: {exc}")
            errors += 1

    # Tier 1b: Season folder renames (paths already use new show names)
    for old_path, new_path, show_name in plan['season_renames']:
        try:
            os.rename(old_path, new_path)
            print(f"  RENAMED season: {show_name} / {os.path.basename(old_path)}"
                  f" -> {os.path.basename(new_path)}")
            applied += 1
        except Exception as exc:
            print(f"  ERROR renaming season {old_path}: {exc}")
            errors += 1

    # Tier 2: File moves
    for src_path, dst_path, show_name in plan['file_moves']:
        try:
            dst_dir = os.path.dirname(dst_path)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(src_path, dst_path)
            target_season = os.path.basename(dst_dir)
            print(f"  MOVED: {show_name} / {os.path.basename(src_path)} -> {target_season}/")
            applied += 1
        except Exception as exc:
            print(f"  ERROR moving {src_path}: {exc}")
            errors += 1

    print(f"\nApplied: {applied}  Errors: {errors}")

    # ── Rescan tvshows.json ───────────────────────────────────────────────────
    print(f"\nRunning full rescan of TV Shows...")
    shows = scan_tv_shows()
    shows = _merge_duplicate_shows(shows)

    total_shows   = len(shows)
    total_seasons = sum(len(s['seasons']) for s in shows)
    total_eps     = sum(len(season['episodes'])
                        for show in shows for season in show['seasons'])

    out = {
        'type':      'tvshows',
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'shows':     shows,
    }

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\nRescan complete.")
    print(f"  Shows:    {total_shows}")
    print(f"  Seasons:  {total_seasons}")
    print(f"  Episodes: {total_eps}")
    print(f"Index saved to: {index_path}")


# ── Command: scan-tvshows ──────────────────────────────────────────────────────

def cmd_reassign_season(args):
    """
    Reassign all Season 0 episodes of one or more shows to a target season.
    Modifies tvshows.json directly — no disk rescan needed.
    """
    index_path = CONFIG.indexes['tvshows']
    if not os.path.exists(index_path):
        print("ERROR: tvshows.json not found — run scan-tvshows first.")
        return

    with open(index_path, encoding='utf-8') as f:
        data = json.load(f)

    target_season = args.to
    show_names    = [n.lower() for n in args.show]

    # Find matching shows (case-insensitive on cleaned name or folder)
    matches = []
    for show in data['shows']:
        key = show['name'].lower()
        folder_key = show.get('folder', '').lower()
        if any(k in (key, folder_key) for k in show_names):
            matches.append(show)

    if not matches:
        print(f"No shows found matching: {', '.join(args.show)}")
        return

    # Preview what will happen
    print(f"\nReassign Season 0 → Season {target_season} for:\n")
    total_eps = 0
    for show in matches:
        s0 = [s for s in show['seasons'] if s['season'] == 0]
        n = sum(len(s['episodes']) for s in s0)
        existing = [s for s in show['seasons'] if s['season'] == target_season]
        existing_n = sum(len(s['episodes']) for s in existing)
        if n == 0:
            print(f"  {show['name']}: no Season 0 episodes — skipping")
        else:
            note = f" (merges with existing {existing_n} eps in S{target_season})" if existing_n else ""
            print(f"  {show['name']}: {n} ep(s){note}")
            total_eps += n

    if total_eps == 0:
        print("\nNothing to do.")
        return

    if not args.yes:
        try:
            ans = input("\nProceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ''
        if ans != 'y':
            print("Aborted.")
            return

    # Apply
    changed = 0
    for show in matches:
        s0_seasons = [s for s in show['seasons'] if s['season'] == 0]
        if not s0_seasons:
            continue

        s0_eps = []
        for s in s0_seasons:
            s0_eps.extend(s['episodes'])

        # Remove S0 season entries
        show['seasons'] = [s for s in show['seasons'] if s['season'] != 0]

        # Find or create target season entry
        target_entry = next((s for s in show['seasons'] if s['season'] == target_season), None)
        if target_entry is None:
            target_entry = {'season': target_season, 'episodes': []}
            show['seasons'].append(target_entry)

        target_entry['episodes'].extend(s0_eps)
        target_entry['episodes'].sort(key=lambda e: (e['episode'] or 0, e['filename']))

        # Re-sort seasons (season 0 always last)
        show['seasons'].sort(key=lambda s: (s['season'] == 0, s['season']))

        print(f"  {show['name']}: moved {len(s0_eps)} ep(s) → Season {target_season}")
        changed += len(s0_eps)

    # Save
    data['generated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone. {changed} episode(s) reassigned.")
    print(f"Index saved to: {index_path}")


def cmd_scan_tvshows(args):
    """Scan the TV Shows folder and build tvshows.json."""
    rescan_mode = getattr(args, 'rescan', False)

    # Load existing index for incremental rescan
    existing_episodes = None
    old_filenames     = set()
    if rescan_mode and os.path.exists(CONFIG.indexes['tvshows']):
        print("Loading existing tvshows.json for incremental rescan...")
        with open(CONFIG.indexes['tvshows'], encoding='utf-8') as f:
            old_data = json.load(f)
        existing_episodes = {}
        for show in old_data.get('shows', []):
            for season in show.get('seasons', []):
                for ep in season.get('episodes', []):
                    existing_episodes[ep['filename']] = {**ep, '_season': season['season']}
                    old_filenames.add(ep['filename'])
        print(f"  {len(existing_episodes)} episodes already indexed — probe data will be reused.\n")
    else:
        if rescan_mode:
            print("No existing tvshows.json found — performing full scan.\n")
        else:
            print("Full scan (all files will be probed).\n")

    print("Scanning TV Shows folder...")
    print(f"  {CONFIG.sections['tv_shows']}\n")

    shows = scan_tv_shows(existing_episodes=existing_episodes)
    shows = _merge_duplicate_shows(shows)

    # Compute new / reused / stale counts for rescan reporting
    new_filenames = {ep['filename']
                     for show in shows
                     for season in show['seasons']
                     for ep in season['episodes']}
    newly_probed = len(new_filenames - old_filenames)
    reused       = len(new_filenames & old_filenames)
    stale        = len(old_filenames - new_filenames)

    total_shows   = len(shows)
    total_seasons = sum(len(s['seasons']) for s in shows)
    total_eps     = sum(len(season['episodes'])
                        for show in shows for season in show['seasons'])
    duplicate_shows = [s for s in shows if s.get('duplicate_candidate')]
    probe_errors  = [
        (show['name'], ep['filename'], ep.get('probe_error'))
        for show in shows
        for season in show['seasons']
        for ep in season['episodes']
        if ep.get('probe_error')
    ]
    season0_shows = [
        (show['name'],
         sum(len(s['episodes']) for s in show['seasons'] if s['season'] == 0))
        for show in shows
        if any(s['season'] == 0 for s in show['seasons'])
    ]

    out = {
        'type':      'tvshows',
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'shows':     shows,
    }

    index_path = CONFIG.indexes['tvshows']
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"TV Shows scan complete.")
    print(f"  Shows:    {total_shows}")
    print(f"  Seasons:  {total_seasons}")
    print(f"  Episodes: {total_eps}")
    print(f"  Duplicate groups: {len(duplicate_shows)}")
    if rescan_mode:
        print(f"\n  Probe data reused: {reused}")
        print(f"  Newly probed:      {newly_probed}")
        print(f"  Stale (removed):   {stale}")
    print(f"\nIndex saved to: {index_path}")

    if duplicate_shows:
        print(f"\nDuplicate show groups flagged for evaluation ({len(duplicate_shows)}):")
        for show in sorted(duplicate_shows, key=lambda s: s['name']):
            dup_seasons = sum(1 for s in show['seasons'] if s.get('duplicate_candidate'))
            print(f"  {show['name']}: {len(show['duplicate_folders'])} folders"
                  f", {dup_seasons} overlapping season(s)")
            for folder in show['duplicate_folders']:
                print(f"    - {folder}")

    if season0_shows:
        print(f"\nShows with Season 0 (unresolved specials / unknown season) "
              f"— {len(season0_shows)} show(s):")
        for name, count in sorted(season0_shows):
            print(f"  {name}: {count} episode(s)")

    # ── Write probe_failures.txt ───────────────────────────────────────────────
    failures_path = str(CONFIG.reports_dir / 'probe_failures.txt')
    if probe_errors:
        with open(failures_path, 'w', encoding='utf-8') as f:
            f.write(f"ffprobe failures — {len(probe_errors)} file(s)\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("These files may be corrupt and need reacquisition.\n")
            f.write("=" * 70 + "\n\n")
            current_show = None
            for show_name, fname, err in sorted(probe_errors, key=lambda x: (x[0], x[1])):
                if show_name != current_show:
                    f.write(f"[{show_name}]\n")
                    current_show = show_name
                f.write(f"  {fname}\n")
                if err:
                    f.write(f"    Error: {err}\n")
        print(f"\nProbe failures ({len(probe_errors)} files) written to:")
        print(f"  {failures_path}")
    else:
        if os.path.exists(failures_path):
            os.remove(failures_path)
        print(f"\nNo ffprobe errors.")


# ── Music helpers ─────────────────────────────────────────────────────────────

def _read_music_tags(file_path):
    """
    Read tags from a music file using mutagen (easy interface).
    Returns a dict with keys: artist, albumartist, album, tracknumber,
    discnumber, title, year, extension, filesize, date_added.
    On error, returns dict with 'tag_error' key.
    """
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {'tag_error': 'mutagen not installed (pip install mutagen)'}

    ext = os.path.splitext(file_path)[1].lower()
    try:
        f = MutagenFile(file_path, easy=True)
        if f is None:
            return {'tag_error': 'mutagen could not parse file'}

        def _get(key):
            v = f.get(key)
            return str(v[0]).strip() if v else ''

        artist      = _get('albumartist') or _get('artist')
        album       = _get('album')
        tracknumber = _get('tracknumber')
        discnumber  = _get('discnumber')
        title       = _get('title')
        raw_date    = _get('date')
        year        = raw_date[:4] if len(raw_date) >= 4 and raw_date[:4].isdigit() else ''

        # Normalize track/disc: "3/12" → "3"
        track_num = tracknumber.split('/')[0].strip() if tracknumber else ''
        disc_num  = discnumber.split('/')[0].strip() if discnumber else '1'

        mtime = datetime.fromtimestamp(
            os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')

        return {
            'artist':      artist,
            'album':       album,
            'tracknumber': track_num,
            'discnumber':  disc_num,
            'title':       title,
            'year':        year,
            'extension':   ext,
            'filesize':    os.path.getsize(file_path),
            'date_added':  mtime,
        }
    except Exception as e:
        return {'tag_error': str(e)}


def _is_well_tagged(tags):
    """
    Return True if a file has enough tags for Plex organization.
    Requires: artist, album (non-empty, not 'Unknown', not a URL),
              tracknumber (non-empty, non-zero), title.
    """
    if tags.get('tag_error'):
        return False
    artist = tags.get('artist', '')
    album  = tags.get('album', '')
    track  = tags.get('tracknumber', '')
    title  = tags.get('title', '')

    if not artist:
        return False
    if not album or album.lower() == 'unknown':
        return False
    if album.startswith('http') or album.startswith('ftp'):
        return False
    if not track or track == '0' or track == '00':
        return False
    if not title:
        return False
    return True


def _sanitize_path_component(name):
    """Strip characters illegal in Windows file/folder names."""
    # Replace illegal chars with similar safe alternatives or spaces
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    # Strip leading/trailing dots and spaces (Windows quirk)
    name = name.strip('. ')
    return name or '_'


def _make_plex_music_path(tags):
    """
    Return (artist_folder, album_folder, filename) for a well-tagged file.
    Layout: Artist/Album (Year)/TrackNum - Title.ext
    Multi-disc: Artist/Album (Year)/Disc N/TrackNum - Title.ext  (if disc > 1)
    """
    artist = _sanitize_path_component(tags['artist'])
    album  = _sanitize_path_component(tags['album'])
    year   = tags.get('year', '')
    track  = tags.get('tracknumber', '0').zfill(2)
    disc   = tags.get('discnumber', '1')
    title  = _sanitize_path_component(tags.get('title', 'Unknown'))
    ext    = tags.get('extension', '')

    album_folder = f"{album} ({year})" if year else album

    filename = f"{track} - {title}{ext}"

    if disc and disc not in ('', '0', '1'):
        return artist, album_folder, f"Disc {disc}", filename
    return artist, album_folder, None, filename


def _is_junk_file(fname):
    """Return True if this file should be deleted as library junk."""
    if fname.lower() in CONFIG.music_junk_names:
        return True
    for pat in CONFIG.music_junk_patterns:
        if pat.search(fname):
            return True
    return False


# ── Command: scan-music ────────────────────────────────────────────────────────

def cmd_scan_music(args):
    """Scan the Music folder, read tags, and build music.json."""
    try:
        from mutagen import File as MutagenFile  # noqa: F401
    except ImportError:
        print("ERROR: mutagen not installed. Run: pip install mutagen")
        sys.exit(1)

    music_root = CONFIG.sections['music']
    print(f"Scanning Music folder...")
    print(f"  {music_root}\n")

    artists = []
    total_files = 0
    well_tagged  = 0
    needs_tagging = 0
    tag_errors   = 0
    junk_files   = []

    for artist_dir in sorted(os.listdir(music_root)):
        artist_path = os.path.join(music_root, artist_dir)
        if not os.path.isdir(artist_path):
            continue
        if artist_dir.startswith('_') or artist_dir.startswith('.'):
            continue

        tracks = []
        # Walk recursively: handles both flat (Artist/Track) and
        # organized (Artist/Album/Track) structures.
        for root, dirs, fnames in os.walk(artist_path):
            dirs[:] = sorted(d for d in dirs
                             if not d.startswith('.') and not d.startswith('@'))
            rel_dir = os.path.relpath(root, music_root).replace('\\', '/')
            for fname in sorted(fnames):
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in CONFIG.music_exts:
                    total_files += 1
                    tags = _read_music_tags(fpath)
                    tags['filename'] = fname
                    tags['folder']   = rel_dir
                    tracks.append(tags)
                    if tags.get('tag_error'):
                        tag_errors += 1
                    elif _is_well_tagged(tags):
                        well_tagged += 1
                    else:
                        needs_tagging += 1
                elif _is_junk_file(fname):
                    junk_files.append(os.path.join(rel_dir, fname))

        if tracks:
            artists.append({'folder': artist_dir, 'tracks': tracks})

    out = {
        'type':      'music',
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'artists':   artists,
    }

    index_path = CONFIG.indexes['music']
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"{'=' * 60}")
    print(f"Music scan complete.")
    print(f"  Artist folders: {len(artists)}")
    print(f"  Total tracks:   {total_files}")
    print(f"  Well-tagged:    {well_tagged}")
    print(f"  Needs tagging:  {needs_tagging}")
    if tag_errors:
        print(f"  Tag errors:     {tag_errors}")
    if junk_files:
        print(f"  Junk files:     {len(junk_files)}")
    print(f"\nIndex saved to: {index_path}")

    if needs_tagging > 0:
        # Show a breakdown of why files need tagging
        reasons = Counter()
        for artist in artists:
            for t in artist['tracks']:
                if not t.get('tag_error') and not _is_well_tagged(t):
                    if not t.get('artist'):
                        reasons['missing artist'] += 1
                    elif not t.get('album') or t['album'].lower() == 'unknown':
                        reasons['missing/unknown album'] += 1
                    elif t.get('album', '').startswith('http') or t.get('album', '').startswith('ftp'):
                        reasons['URL in album field'] += 1
                    elif not t.get('tracknumber') or t['tracknumber'] in ('0', '00'):
                        reasons['missing/zero track number'] += 1
                    elif not t.get('title'):
                        reasons['missing title'] += 1
                    else:
                        reasons['other'] += 1
        print(f"\nNeeds-tagging breakdown:")
        for reason, count in reasons.most_common():
            print(f"  {reason}: {count}")

    if junk_files:
        print(f"\nJunk files found (use organize-music to clean up):")
        for j in junk_files[:10]:
            print(f"  {j}")
        if len(junk_files) > 10:
            print(f"  ... and {len(junk_files) - 10} more")


# ── Command: organize-music ────────────────────────────────────────────────────

def cmd_organize_music(args):
    """
    Organize the Music folder into Plex structure.
    Well-tagged → Artist/Album (Year)/TrackNum - Title.ext
    Incomplete tags → _NeedsTagging/OriginalArtistFolder/filename
    Junk files → deleted
    Empty source folders → removed after moves
    """
    try:
        from mutagen import File as MutagenFile  # noqa: F401
    except ImportError:
        print("ERROR: mutagen not installed. Run: pip install mutagen")
        sys.exit(1)

    dry_run = args.dry_run
    apply   = args.apply

    if not dry_run and not apply:
        print("Specify --dry-run to preview or --apply to execute.")
        sys.exit(1)

    music_root = CONFIG.sections['music']
    needs_dir  = os.path.join(music_root, CONFIG.music_needs_tagging_dir)

    print(f"{'[DRY RUN] ' if dry_run else ''}Organizing Music folder...")
    print(f"  {music_root}\n")

    moves      = []   # (src, dst)
    junk_dels  = []   # paths to delete
    conflicts  = []   # (src, dst) where dst already exists

    music_root_norm = str(music_root).replace('\\', '/')

    for artist_dir in sorted(os.listdir(music_root)):
        if artist_dir.startswith('_') or artist_dir.startswith('.'):
            continue
        artist_path = os.path.join(music_root, artist_dir)
        if not os.path.isdir(artist_path):
            continue

        # Walk entire artist subtree
        for root, dirs, fnames in os.walk(artist_path):
            dirs[:] = sorted(d for d in dirs
                             if not d.startswith('.') and not d.startswith('@'))
            root_norm = root.replace('\\', '/')
            # Relative path of this directory from music_root (e.g. "ACDC/Back in Black")
            rel_dir = root_norm[len(music_root_norm):].lstrip('/')
            # depth: 1 = Artist/, 2 = Artist/Album/, etc.
            depth = len(rel_dir.split('/'))

            for fname in sorted(fnames):
                fpath = os.path.join(root, fname).replace('\\', '/')
                ext = os.path.splitext(fname)[1].lower()

                if _is_junk_file(fname):
                    junk_dels.append(fpath)
                    continue

                if ext not in CONFIG.music_exts:
                    continue

                tags = _read_music_tags(fpath)
                tags['filename'] = fname
                tags['folder']   = rel_dir

                if _is_well_tagged(tags):
                    if depth == 1:
                        # Flat file directly in artist folder — move to Plex structure
                        artist_f, album_f, disc_f, new_fname = _make_plex_music_path(tags)
                        if disc_f:
                            dst = os.path.join(music_root, artist_f, album_f, disc_f, new_fname)
                        else:
                            dst = os.path.join(music_root, artist_f, album_f, new_fname)
                        dst = dst.replace('\\', '/')
                    else:
                        # Already in a subdirectory structure — leave it alone
                        continue
                else:
                    # Needs tagging: move to _NeedsTagging/ preserving full relative path
                    rel_path = os.path.join(rel_dir, fname).replace('\\', '/')
                    dst = os.path.join(needs_dir, rel_path).replace('\\', '/')

                src = fpath

                if src == dst:
                    continue

                if os.path.exists(dst):
                    conflicts.append((src, dst))
                else:
                    moves.append((src, dst))

    # Detect destination collisions among well-tagged flat moves.
    # Both copies go to _NeedsTagging/_Collisions/Artist/Album/ using their
    # ORIGINAL filenames so the user can compare and decide.
    collisions_root = os.path.join(music_root, CONFIG.music_collisions_dir)
    dst_seen    = {}   # norm_dst → (src, index_in_deduped)
    deduped_moves   = []
    collision_moves = []

    def _collision_dst(src, shared_dst):
        """Return destination path inside _Collisions, keeping original filename."""
        rel_album = os.path.dirname(
            shared_dst.replace(music_root_norm + '/', ''))
        col_dir  = os.path.join(collisions_root, rel_album).replace('\\', '/')
        col_name = os.path.basename(src)
        col_path = os.path.join(col_dir, col_name).replace('\\', '/')
        # If two sources share even their original filename, append a counter
        seen_col = [d for _, d in collision_moves if os.path.dirname(d) == col_dir]
        if col_path in seen_col:
            base, ext = os.path.splitext(col_name)
            col_path  = os.path.join(col_dir, f"{base}_2{ext}").replace('\\', '/')
        return col_path

    for src, dst in moves:
        if CONFIG.music_needs_tagging_dir in dst:
            deduped_moves.append((src, dst))
            continue
        norm_dst = dst.lower()
        if norm_dst in dst_seen:
            winner_src, winner_idx = dst_seen[norm_dst]
            # Reroute the winner out of deduped_moves and into collisions
            deduped_moves[winner_idx] = None   # mark for removal
            collision_moves.append((winner_src, _collision_dst(winner_src, dst)))
            collision_moves.append((src,        _collision_dst(src, dst)))
        else:
            dst_seen[norm_dst] = (src, len(deduped_moves))
            deduped_moves.append((src, dst))

    deduped_moves = [(s, d) for s, d in deduped_moves if s is not None]
    moves = deduped_moves + collision_moves

    # Report plan
    well_moves      = [(s, d) for s, d in moves if CONFIG.music_needs_tagging_dir not in d]
    tagging_moves   = [(s, d) for s, d in moves if CONFIG.music_needs_tagging_dir in d
                       and CONFIG.music_collisions_dir not in d]
    collision_pairs = len(collision_moves) // 2  # each collision = 2 files

    print(f"Plan:")
    print(f"  Files to organize (well-tagged flat):  {len(well_moves)}")
    print(f"  Files to hold for tagging:             {len(tagging_moves)}")
    if collision_moves:
        print(f"  Collision pairs → _Collisions/:        {collision_pairs} pairs ({len(collision_moves)} files)")
    print(f"  Junk files to delete:                  {len(junk_dels)}")
    print(f"  Conflicts (dst exists, skipped):        {len(conflicts)}")

    if conflicts:
        print(f"\nConflicts (will be skipped):")
        for src, dst in conflicts[:10]:
            print(f"  {os.path.basename(src)} → {dst}")
        if len(conflicts) > 10:
            print(f"  ... and {len(conflicts) - 10} more")

    if dry_run:
        if well_moves:
            print(f"\nSample moves (well-tagged flat, first 10):")
            for src, dst in well_moves[:10]:
                rel_src = src.replace(music_root_norm + '/', '')
                rel_dst = dst.replace(music_root_norm + '/', '')
                print(f"  {rel_src}")
                print(f"    → {rel_dst}")
            if len(well_moves) > 10:
                print(f"  ... and {len(well_moves) - 10} more")

        print(f"\nSample needs-tagging moves (first 15):")
        for src, dst in tagging_moves[:15]:
            rel_src = src.replace(music_root_norm + '/', '')
            rel_dst = dst.replace(music_root_norm + '/', '')
            print(f"  {rel_src}")
            print(f"    → {rel_dst}")
        if len(tagging_moves) > 15:
            print(f"  ... and {len(tagging_moves) - 15} more")

        print(f"\nSample junk deletes (first 10):")
        for fp in junk_dels[:10]:
            print(f"  {fp.replace(music_root_norm + '/', '')}")
        if len(junk_dels) > 10:
            print(f"  ... and {len(junk_dels) - 10} more")

        print(f"\nRun with --apply to execute.")
        return

    # Execute
    if not args.yes:
        total_ops = len(moves) + len(junk_dels)
        if not confirm(f"\nExecute {total_ops} operations "
                       f"({len(moves)} moves + {len(junk_dels)} deletes)?"):
            print("Aborted.")
            return

    ok = deleted = errors = 0

    for src, dst in moves:
        dst_dir = os.path.dirname(dst)
        try:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(src, dst)
            ok += 1
        except Exception as e:
            print(f"  ERROR moving {os.path.basename(src)}: {e}")
            errors += 1

    for fpath in junk_dels:
        try:
            # Remove read-only flag if needed before deleting
            if not os.access(fpath, os.W_OK):
                os.chmod(fpath, 0o644)
            os.remove(fpath)
            deleted += 1
        except Exception as e:
            print(f"  ERROR deleting {os.path.basename(fpath)}: {e}")
            errors += 1

    print(f"\nDone: {ok} moved, {deleted} junk deleted, "
          f"{len(conflicts)} skipped (conflicts), {errors} errors.")

    # Remove empty directories (bottom-up walk to catch nested empties)
    removed_dirs = 0
    for root, dirs, fnames in os.walk(music_root, topdown=False):
        root_norm = root.replace('\\', '/')
        rel = root_norm[len(music_root_norm):].lstrip('/')
        if not rel:
            continue  # don't remove music_root itself
        # Skip _NeedsTagging tree
        if rel.startswith(CONFIG.music_needs_tagging_dir):
            continue
        try:
            contents = [f for f in os.listdir(root) if not f.startswith('.')]
            if not contents:
                os.rmdir(root)
                removed_dirs += 1
        except Exception:
            pass

    if removed_dirs:
        print(f"Removed {removed_dirs} empty folders.")

    print(f"\nRun 'scan-music' to rebuild music.json.")


# ── TMDB helpers ──────────────────────────────────────────────────────────────

_TMDB_ID_RE = re.compile(r'\{tmdb-(\d+)\}', re.IGNORECASE)


def _get_tmdb_headers():
    # Environment variable takes priority over config file
    token = os.environ.get(TMDB_TOKEN_ENV, '').strip() or CONFIG.tmdb_token

    if not token:
        print("ERROR: No TMDB Read Access Token found.")
        print(f"  Option 1 — set 'tmdb_read_access_token' in your media_agent_config.json")
        print(f"  Option 2 — set env var {TMDB_TOKEN_ENV}=your_token")
        sys.exit(1)

    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _tmdb_get(path, headers, params=None):
    """GET from TMDB API, return parsed JSON. Raises on HTTP error."""
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' is not installed. Run: pip install requests")
        sys.exit(1)
    resp = requests.get(f"{TMDB_API_BASE}{path}", headers=headers,
                        params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _tmdb_search_movie(title, year, headers):
    """Search TMDB for a movie. Returns list of result dicts."""
    params = {"query": title, "language": "en-US", "page": 1}
    if year:
        params["primary_release_year"] = year
    results = _tmdb_get("/search/movie", headers, params).get("results", [])
    # If no results with year constraint, retry without it
    if not results and year:
        params.pop("primary_release_year")
        results = _tmdb_get("/search/movie", headers, params).get("results", [])
    return results


def _parse_for_tmdb(filename):
    """
    Extract (title, year) from any movie filename for TMDB lookup.
    Returns (None, None) if the filename can't yield a usable title.
    Does NOT return None for already-clean filenames — unlike parse_movie_filename.
    """
    stem = os.path.splitext(filename)[0]
    # Strip existing {tmdb-...} tag if present
    stem = _TMDB_ID_RE.sub('', stem).strip()

    year_m = _YEAR_RE.search(stem)
    # If the year match is at position 0, the number is part of the title
    # (e.g. "2001 A Space Odyssey", "1984", "300") — don't treat it as a year.
    if year_m and year_m.start() == 0:
        year_m = None
    year = year_m.group(1) if year_m else None

    if year_m:
        raw_title = stem[:year_m.start()]
    else:
        raw_title = _STRIP_RE.sub('', stem)

    title = re.sub(r'[._]+', ' ', raw_title)
    title = re.sub(r'[\[(]+\s*$', '', title)
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    title = re.sub(r'\{[^}]*\}\s*$', '', title)  # strip any {tag} remnants
    title = re.sub(r'\s{2,}', ' ', title).strip().rstrip(' -').strip()

    return (title, year) if title else (None, None)


def _evaluate_confidence(results, title, year):
    """
    Score TMDB results and return (confidence, best_result).
    confidence: 'confident' | 'ambiguous' | 'no_match'
    """
    if not results:
        return "no_match", None

    def norm(s):
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    title_norm = norm(title)

    def score(r):
        s = 0
        if norm(r.get('title', '')) == title_norm or \
           norm(r.get('original_title', '')) == title_norm:
            s += 10  # exact title match
        elif title_norm in norm(r.get('title', '')) or \
             norm(r.get('title', '')) in title_norm:
            s += 4   # partial title match
        r_year = (r.get('release_date') or '')[:4]
        if year and r_year == str(year):
            s += 6   # year match
        return s

    scored = sorted(results, key=score, reverse=True)
    best   = scored[0]
    bs     = score(best)
    second = score(scored[1]) if len(scored) > 1 else 0

    # Exact title + year match → confident
    if bs >= 16:
        return "confident", best
    # Exact title, no year in filename or only one result → confident
    if bs >= 10 and (not year or len(results) == 1):
        return "confident", best
    # Exact title, year mismatch but clearly the best result → confident
    if bs >= 10 and bs - second >= 6:
        return "confident", best
    # Everything else → ambiguous
    return "ambiguous", best


# ── Command: tmdb-enrich ───────────────────────────────────────────────────────

def cmd_tmdb_enrich(args):
    """Look up each movie via TMDB API and save results to movies_tmdb.json."""
    headers = _get_tmdb_headers()
    data    = load_movies_json()
    movies  = data['movies']

    # Load existing tmdb index for incremental mode
    tmdb_path = CONFIG.indexes['movies_tmdb']
    existing  = {}
    if os.path.exists(tmdb_path) and not args.reset:
        with open(tmdb_path, encoding='utf-8') as f:
            tmdb_data = json.load(f)
        existing = {e['filename']: e for e in tmdb_data.get('movies', [])}
        print(f"Resuming: {len(existing)} entries already in movies_tmdb.json "
              f"(use --reset to start fresh).\n")

    results_list = []
    stats = {"confident": 0, "ambiguous": 0, "no_match": 0,
             "already_enriched": 0, "skipped": 0, "error": 0}
    total = len(movies)

    for i, movie in enumerate(movies, 1):
        filename = movie['name']

        # Carry forward existing entry in incremental mode
        if filename in existing and not args.reset:
            entry = existing[filename]
            results_list.append(entry)
            stats[entry.get('match_status', 'skipped')] += 1
            continue

        # Already has a {tmdb-...} tag in the filename
        if _TMDB_ID_RE.search(filename):
            m = _TMDB_ID_RE.search(filename)
            results_list.append({
                "filename":         filename,
                "match_status":     "already_enriched",
                "tmdb_id":          int(m.group(1)),
                "imdb_id":          None,
                "tmdb_title":       None,
                "tmdb_year":        None,
                "parsed_title":     None,
                "parsed_year":      None,
            })
            stats["already_enriched"] += 1
            print(f"[{i:4}/{total}] SKIP (already enriched): {filename}")
            continue

        title, year = _parse_for_tmdb(filename)
        if not title:
            results_list.append({
                "filename":     filename,
                "match_status": "skipped",
                "tmdb_id":      None, "imdb_id": None,
                "tmdb_title":   None, "tmdb_year": None,
                "parsed_title": None, "parsed_year": None,
                "note":         "Could not parse title from filename",
            })
            stats["skipped"] += 1
            print(f"[{i:4}/{total}] SKIP (no title parsed): {filename}")
            continue

        try:
            search_results              = _tmdb_search_movie(title, year, headers)
            time.sleep(0.15)
            confidence, best            = _evaluate_confidence(search_results, title, year)

            if best:
                tmdb_id    = best['id']
                tmdb_title = best.get('title', '')
                tmdb_year  = (best.get('release_date') or '')[:4]
                ext_ids    = _tmdb_get(f"/movie/{tmdb_id}/external_ids", headers)
                time.sleep(0.15)
                imdb_id    = ext_ids.get('imdb_id')
                entry = {
                    "filename":     filename,
                    "match_status": confidence,
                    "tmdb_id":      tmdb_id,
                    "imdb_id":      imdb_id,
                    "tmdb_title":   tmdb_title,
                    "tmdb_year":    tmdb_year,
                    "parsed_title": title,
                    "parsed_year":  year,
                }
                tag = "OK " if confidence == "confident" else "???"
                print(f"[{i:4}/{total}] {tag} {confidence.upper()}: {filename}")
                print(f"           → {tmdb_title} ({tmdb_year}) [tmdb-{tmdb_id}]")
            else:
                entry = {
                    "filename":     filename,
                    "match_status": "no_match",
                    "tmdb_id":      None, "imdb_id": None,
                    "tmdb_title":   None, "tmdb_year": None,
                    "parsed_title": title, "parsed_year": year,
                }
                print(f"[{i:4}/{total}] NO MATCH: {filename}  (searched: '{title}' {year or ''})")

            stats[confidence] += 1

        except Exception as e:
            entry = {
                "filename":     filename,
                "match_status": "error",
                "tmdb_id":      None, "imdb_id": None,
                "tmdb_title":   None, "tmdb_year": None,
                "parsed_title": title, "parsed_year": year,
                "note":         str(e),
            }
            stats["error"] += 1
            print(f"[{i:4}/{total}] ERROR: {filename} — {e}")
            time.sleep(2)  # back off on errors

        results_list.append(entry)

    # Save
    output = {
        "type":      "movies_tmdb",
        "generated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "movies":    results_list,
    }
    with open(tmdb_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"TMDB enrichment complete — {total} movies processed")
    print(f"  Confident:        {stats['confident']}")
    print(f"  Ambiguous:        {stats['ambiguous']}")
    print(f"  No match:         {stats['no_match']}")
    print(f"  Already enriched: {stats['already_enriched']}")
    print(f"  Skipped/Error:    {stats['skipped'] + stats['error']}")
    print(f"\nSaved: {tmdb_path}")
    if stats['ambiguous'] or stats['no_match']:
        print(f"\nReview ambiguous/no-match entries in movies_tmdb.json,")
        print(f"then run:  python media_agent.py tmdb-rename --dry-run")


# ── Command: tmdb-canonicalize ─────────────────────────────────────────────────

# Characters not allowed in Windows filenames
_WIN_INVALID_RE = re.compile(r'[\\/:*?"<>|]')


def _canonical_filename(tmdb_title, tmdb_year, tmdb_id, ext):
    """
    Build a Plex-canonical filename:
        Title (Year) {tmdb-XXXXX}.ext
    Replaces Windows-invalid characters in the title with safe equivalents.
    """
    # Replace colon-space and standalone colon with " -" (common subtitle separator)
    safe_title = re.sub(r':\s*', ' - ', tmdb_title)
    # Remove any remaining Windows-invalid chars
    safe_title = _WIN_INVALID_RE.sub('', safe_title)
    # Collapse multiple spaces
    safe_title = re.sub(r'  +', ' ', safe_title).strip()
    if tmdb_year:
        stem = f"{safe_title} ({tmdb_year}) {{tmdb-{tmdb_id}}}"
    else:
        stem = f"{safe_title} {{tmdb-{tmdb_id}}}"
    return stem + ext


def cmd_tmdb_canonicalize(args):
    """
    Rename movie files to canonical Plex format using TMDB title/year data:
        Title (Year) {tmdb-XXXXX}.ext

    Only processes 'confident' matches. Skips files already in canonical form.
    Writes a preview report before applying any changes.
    """
    tmdb_path = CONFIG.indexes['movies_tmdb']
    if not os.path.exists(tmdb_path):
        print("ERROR: movies_tmdb.json not found. Run tmdb-enrich first.")
        sys.exit(1)

    with open(tmdb_path, encoding='utf-8') as f:
        tmdb_data = json.load(f)

    proposals = []
    already_canonical = 0

    for entry in tmdb_data.get('movies', []):
        if entry.get('match_status') != 'confident':
            continue
        tmdb_id    = entry.get('tmdb_id')
        tmdb_title = entry.get('tmdb_title', '').strip()
        tmdb_year  = str(entry.get('tmdb_year', '')).strip()
        filename   = entry.get('filename', '')
        if not tmdb_id or not tmdb_title or not filename:
            continue

        ext      = os.path.splitext(filename)[1]
        new_name = _canonical_filename(tmdb_title, tmdb_year, tmdb_id, ext)

        if filename == new_name:
            already_canonical += 1
            continue

        proposals.append({
            "old":        filename,
            "new":        new_name,
            "tmdb_id":    tmdb_id,
            "tmdb_title": tmdb_title,
            "tmdb_year":  tmdb_year,
        })

    # Detect new_name collisions (multiple old files → same canonical name)
    # These are multi-part files (CD1/CD2, Part I/II) that share one TMDB entry.
    # Skip them — they cannot be canonicalized without losing the part distinction.
    from collections import Counter as _Counter
    new_name_counts = _Counter(p['new'] for p in proposals)
    collisions      = {name for name, count in new_name_counts.items() if count > 1}
    collision_items = [p for p in proposals if p['new'] in collisions]
    proposals       = [p for p in proposals if p['new'] not in collisions]

    print(f"Already canonical:  {already_canonical}")
    print(f"Renames proposed:   {len(proposals)}")
    if collision_items:
        print(f"Skipped (collision): {len(collision_items)} "
              f"— multi-part files sharing one TMDB entry")

    if not proposals:
        print("Nothing to do.")
        return

    # ── Write report file ─────────────────────────────────────────────────────
    report_path = str(CONFIG.reports_dir / 'tmdb_canonicalize_preview.txt')
    with open(report_path, 'w', encoding='utf-8') as rf:
        rf.write(f"TMDB Canonicalize Preview — {len(proposals)} files\n")
        rf.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        rf.write("=" * 70 + "\n\n")
        for p in proposals:
            rf.write(f"FROM: {p['old']}\n")
            rf.write(f"  TO: {p['new']}\n\n")
        if collision_items:
            rf.write("\n" + "=" * 70 + "\n")
            rf.write(f"SKIPPED — collisions ({len(collision_items)} files, multi-part sharing one TMDB entry):\n\n")
            for p in sorted(collision_items, key=lambda x: x['new']):
                rf.write(f"  {p['old']}\n")
                rf.write(f"    → would collide with: {p['new']}\n\n")

    print(f"Report saved to: {report_path}\n")

    if args.dry_run:
        print("(Dry run — no changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    disk_files    = scan_video_files(CONFIG.sections['movies'])
    data          = load_movies_json()
    index_by_name = {m['name']: m for m in data['movies']}
    tmdb_by_name  = {e['filename']: e for e in tmdb_data.get('movies', [])}

    renamed, failed = 0, 0
    for p in proposals:
        if p['old'] not in disk_files:
            print(f"  NOT FOUND on disk: {p['old']}")
            failed += 1
            continue
        old_path = disk_files[p['old']]
        if isinstance(old_path, list):
            old_path = old_path[0]
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (already exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            if p['old'] in index_by_name:
                index_by_name[p['old']]['name'] = p['new']
            if p['old'] in tmdb_by_name:
                tmdb_by_name[p['old']]['filename'] = p['new']
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['old']} → {e}")
            failed += 1

    if renamed:
        data['movies'].sort(key=lambda m: m['name'].lower())
        save_movies_json(data)
        tmdb_data['movies'] = list(tmdb_by_name.values())
        with open(tmdb_path, 'w', encoding='utf-8') as f:
            json.dump(tmdb_data, f, indent=2, ensure_ascii=False)
        print(f"\nRenamed {renamed} files.")
        print(f"Updated: movies.json, movies_tmdb.json")
        if failed:
            print(f"{failed} failed — check output above.")


# ── Command: tmdb-rename ───────────────────────────────────────────────────────

def cmd_tmdb_rename(args):
    """
    Add {tmdb-XXXXX} suffix to movie filenames based on movies_tmdb.json.
    Only processes 'confident' matches by default; --include-ambiguous adds those too.
    """
    tmdb_path = CONFIG.indexes['movies_tmdb']
    if not os.path.exists(tmdb_path):
        print("ERROR: movies_tmdb.json not found. Run tmdb-enrich first.")
        sys.exit(1)

    with open(tmdb_path, encoding='utf-8') as f:
        tmdb_data = json.load(f)

    include_statuses = {"confident"}
    if args.include_ambiguous:
        include_statuses.add("ambiguous")
        print("Including ambiguous matches.\n")

    proposals = []
    for entry in tmdb_data.get('movies', []):
        if entry.get('match_status') not in include_statuses:
            continue
        tmdb_id  = entry.get('tmdb_id')
        filename = entry.get('filename', '')
        if not tmdb_id or not filename:
            continue
        # Already tagged
        if _TMDB_ID_RE.search(filename):
            continue
        stem, ext = os.path.splitext(filename)
        new_name  = f"{stem} {{tmdb-{tmdb_id}}}{ext}"
        proposals.append({
            "old":        filename,
            "new":        new_name,
            "tmdb_id":    tmdb_id,
            "tmdb_title": entry.get('tmdb_title', ''),
            "tmdb_year":  entry.get('tmdb_year', ''),
        })

    if not proposals:
        print("No renames to apply (all confident matches already tagged, "
              "or no confident matches found).")
        return

    # ── Write report file ─────────────────────────────────────────────────────
    label       = "confident+ambiguous" if args.include_ambiguous else "confident"
    report_path = str(CONFIG.reports_dir / 'tmdb_rename_preview.txt')
    with open(report_path, 'w', encoding='utf-8') as rf:
        rf.write(f"TMDB Rename Preview — {len(proposals)} files ({label} matches)\n")
        rf.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        rf.write("=" * 70 + "\n\n")
        for p in proposals:
            rf.write(f"FROM: {p['old']}\n")
            rf.write(f"  TO: {p['new']}\n")
            rf.write(f"      TMDB: {p['tmdb_title']} ({p['tmdb_year']}) [tmdb-{p['tmdb_id']}]\n\n")

    print(f"{len(proposals)} files to rename ({label} matches).")
    print(f"Report saved to: {report_path}\n")

    if args.dry_run:
        print("(Dry run — no changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    disk_files    = scan_video_files(CONFIG.sections['movies'])
    data          = load_movies_json()
    index_by_name = {m['name']: m for m in data['movies']}
    tmdb_by_name  = {e['filename']: e for e in tmdb_data.get('movies', [])}

    renamed, failed = 0, 0
    for p in proposals:
        if p['old'] not in disk_files:
            print(f"  NOT FOUND on disk: {p['old']}")
            failed += 1
            continue
        old_path = disk_files[p['old']]
        if isinstance(old_path, list):
            old_path = old_path[0]
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (already exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            if p['old'] in index_by_name:
                index_by_name[p['old']]['name'] = p['new']
            if p['old'] in tmdb_by_name:
                tmdb_by_name[p['old']]['filename'] = p['new']
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['old']} → {e}")
            failed += 1

    if renamed:
        data['movies'].sort(key=lambda m: m['name'].lower())
        save_movies_json(data)
        tmdb_data['movies'] = list(tmdb_by_name.values())
        with open(tmdb_path, 'w', encoding='utf-8') as f:
            json.dump(tmdb_data, f, indent=2, ensure_ascii=False)
        print(f"\nRenamed {renamed} files.")
        print(f"Updated: movies.json, movies_tmdb.json")
        if failed:
            print(f"{failed} failed — check output above.")


# ── TMDB fix helpers ───────────────────────────────────────────────────────────

# Known-wrong ambiguous entries: filename → correct tmdb_id
_TMDB_CORRECTIONS = {
    "Back To The Future - 2.avi":          165,    # Back to the Future Part II (1989)
    "Endhiran.avi":                        43619,  # Endhiran (2010)
    "Evil Dead 1.avi":                     764,    # The Evil Dead (1981)
    "God Bless America (2011) [720p].mp4": 87236,  # God Bless America (2011)
    "Gremlins 1.mp4":                      927,    # Gremlins (1984)
    "Highlander (1992).mkv":               8009,   # Highlander (1986)
    "Wonder Woman (1984) [1080p].mp4":     464052, # Wonder Woman 1984 (2020)
    # Go Kids.avi — title too ambiguous, left for manual lookup
}

_TMDB_MISSPELLINGS = {
    'terrabithia':                    'terabithia',
    'lightning theif':                'lightning thief',
    'tenacious d and the pick':       'tenacious d in the pick',
}

_TRAILER_RE = re.compile(
    r'\b(trailer|teaser|interview|behind[- ]the[- ]scenes)\b', re.IGNORECASE)


def _clean_for_retry(filename):
    """
    Apply smarter cleaning to a no-match filename.
    Returns (title, year) for a TMDB retry, or (None, None) to mark as skip.
    """
    stem = os.path.splitext(filename)[0]
    stem = _TMDB_ID_RE.sub('', stem).strip()

    # Trailers / extras → skip
    if _TRAILER_RE.search(stem):
        return None, None

    # Normalize Unicode punctuation to ASCII equivalents
    stem = stem.replace('\u2044', '/').replace('\u2013', '-').replace('\u2014', '-')

    # Strip bracketed tags that are not 4-digit years: [BD], [Dual Audio], [720p] …
    stem = re.sub(r'\s*\[(?!\d{4}\])[^\]]*\]\s*', ' ', stem).strip()

    # Strip "Terry Pratchett(')s " prefix
    stem = re.sub(r"^Terry Pratchett'?s\s+", '', stem, flags=re.IGNORECASE)

    # Strip "Joss Whedon - " style author/handle prefix
    stem = re.sub(r'^[A-Z][a-z]+\s+[A-Z][a-z]+\s+-\s+', '', stem)

    # Strip "CrazyHandle Com - " / "Crazy4TV Com - " garbage release-group prefixes
    stem = re.sub(r'^[A-Za-z0-9]+(TV|Com|Net|Tv)\s*\w*\s*[-–]\s*', '',
                  stem, flags=re.IGNORECASE)

    # "Series Trilogy - N - Real Title" → "Real Title"
    m = re.match(r'^.+?(?:Trilogy|Collection)\s*[-–]\s*\d+\s*[-–]\s*(.+)$',
                 stem, re.IGNORECASE)
    if m:
        stem = m.group(1).strip()

    # "Urusei Yatsura Movie NN - Title" / "OAV NN - Title" → "Urusei Yatsura: Title"
    m = re.match(r'^([\w\s]+?)\s+(?:Movie|OAV|OVA)\s+\d+\s*[-–]\s*(.+)$',
                 stem, re.IGNORECASE)
    if m:
        stem = f"{m.group(1).strip()}: {m.group(2).strip()}"

    # Strip episode-range brackets: [01-30], [31-63]
    stem = re.sub(r'\s*\[\d+[-–]\d+\]', '', stem).strip()

    # Strip UNCUT, EXTENDED, IMAX, THEATRICAL, REMASTERED
    stem = re.sub(r'\s*\b(UNCUT|EXTENDED|IMAX|THEATRICAL|REMASTERED)\b\s*',
                  ' ', stem, flags=re.IGNORECASE).strip()

    # Strip CD1/CD2 split-file markers
    stem = re.sub(r'\s*\bCD\s*\d+\b\s*', ' ', stem, flags=re.IGNORECASE).strip()

    # Strip "- Part N" / "Part N" / "PartN" at end (Arabic and Roman numerals)
    stem = re.sub(r'\s*[-–]?\s*Parts?\s*\d+\s*$', '', stem, flags=re.IGNORECASE).strip()
    stem = re.sub(r'\s*[-–]?\s*Part\s*[IVX]+\s*$', '', stem, flags=re.IGNORECASE).strip()

    # "Title, The" → "The Title"
    m = re.match(r'^(.+),\s+The\s*$', stem, re.IGNORECASE)
    if m:
        stem = f"The {m.group(1).strip()}"

    # Clean up trailing " - " left by earlier stripping
    stem = re.sub(r'\s*[-–]\s*$', '', stem).strip()

    # Fix known misspellings
    for wrong, right in _TMDB_MISSPELLINGS.items():
        stem = re.sub(re.escape(wrong), right, stem, flags=re.IGNORECASE)

    # Extract year — ignore if at position 0 (it's part of the title, e.g. "1984")
    year_m = _YEAR_RE.search(stem)
    if year_m and year_m.start() > 0:
        year      = year_m.group(1)
        raw_title = stem[:year_m.start()]
    else:
        year      = None
        raw_title = _STRIP_RE.sub('', stem)

    title = re.sub(r'[._]+', ' ', raw_title)
    title = re.sub(r'[\[(]+\s*$', '', title)
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    title = re.sub(r'\{[^}]*\}', '', title)
    title = re.sub(r'\s{2,}', ' ', title).strip().rstrip(' -').strip()

    return (title, year) if title else (None, None)


# ── Command: tmdb-fix ──────────────────────────────────────────────────────────

def cmd_tmdb_fix(args):
    """
    Fix movies_tmdb.json in two passes:
    1. Apply manual corrections for known-wrong ambiguous entries.
    2. Re-search all no_match (and parse-failed skipped) entries with
       smarter filename cleaning.
    """
    tmdb_path = CONFIG.indexes['movies_tmdb']
    if not os.path.exists(tmdb_path):
        print("ERROR: movies_tmdb.json not found. Run tmdb-enrich first.")
        sys.exit(1)

    headers = _get_tmdb_headers()

    with open(tmdb_path, encoding='utf-8') as f:
        tmdb_data = json.load(f)

    entries = {e['filename']: e for e in tmdb_data['movies']}

    fixed          = 0
    retry_found    = 0
    retry_missing  = 0
    trailers       = 0

    # ── Pass 1: manual corrections ────────────────────────────────────────────
    print("=== Pass 1: manual corrections ===\n")
    for filename, tmdb_id in _TMDB_CORRECTIONS.items():
        if filename not in entries:
            print(f"  SKIP (not in index): {filename}")
            continue
        entry      = entries[filename]
        old_status = entry.get('match_status')
        try:
            details = _tmdb_get(f"/movie/{tmdb_id}", headers, {"language": "en-US"})
            time.sleep(0.15)
            ext_ids = _tmdb_get(f"/movie/{tmdb_id}/external_ids", headers)
            time.sleep(0.15)
        except Exception as e:
            print(f"  ERROR fetching tmdb-{tmdb_id} for {filename}: {e}")
            continue
        entry.update({
            "match_status": "confident",
            "tmdb_id":      tmdb_id,
            "imdb_id":      ext_ids.get("imdb_id"),
            "tmdb_title":   details.get("title", ""),
            "tmdb_year":    (details.get("release_date") or "")[:4],
        })
        print(f"  FIXED ({old_status} → confident): {filename}")
        print(f"        → {entry['tmdb_title']} ({entry['tmdb_year']}) [tmdb-{tmdb_id}]")
        fixed += 1

    # ── Pass 2: re-search no_match + parse-failed skipped ─────────────────────
    retry_entries = [
        e for e in tmdb_data['movies']
        if e.get('match_status') == 'no_match'
        or (e.get('match_status') == 'skipped'
            and 'parse' in e.get('note', '').lower())
    ]
    total = len(retry_entries)
    print(f"\n=== Pass 2: re-searching {total} entries ===\n")

    for i, entry in enumerate(retry_entries, 1):
        filename     = entry['filename']
        title, year  = _clean_for_retry(filename)

        if title is None:
            entry['match_status'] = 'skipped'
            entry['note']         = 'trailer/extra — excluded from library'
            print(f"[{i:3}/{total}] SKIP (trailer): {filename}")
            trailers += 1
            continue

        # No improvement from cleaning — don't waste an API call
        if (title == entry.get('parsed_title')
                and str(year or '') == str(entry.get('parsed_year') or '')):
            print(f"[{i:3}/{total}] UNCHANGED: {filename}")
            print(f"             no new search term ('{title}' {year or ''})")
            retry_missing += 1
            continue

        try:
            results              = _tmdb_search_movie(title, year, headers)
            time.sleep(0.15)
            confidence, best     = _evaluate_confidence(results, title, year)
        except Exception as e:
            print(f"[{i:3}/{total}] ERROR: {filename} — {e}")
            time.sleep(2)
            continue

        if best:
            tmdb_id = best['id']
            ext_ids = _tmdb_get(f"/movie/{tmdb_id}/external_ids", headers)
            time.sleep(0.15)
            entry.update({
                "match_status": confidence,
                "tmdb_id":      tmdb_id,
                "imdb_id":      ext_ids.get("imdb_id"),
                "tmdb_title":   best.get("title", ""),
                "tmdb_year":    (best.get("release_date") or "")[:4],
                "parsed_title": title,
                "parsed_year":  year,
            })
            tag = "OK " if confidence == "confident" else "???"
            print(f"[{i:3}/{total}] {tag} {confidence.upper()}: {filename}")
            print(f"             → {entry['tmdb_title']} ({entry['tmdb_year']}) [tmdb-{tmdb_id}]")
            retry_found += 1
        else:
            entry.update({"parsed_title": title, "parsed_year": year,
                          "note": f"retry searched '{title}' {year or ''} — still no match"})
            print(f"[{i:3}/{total}] STILL NO MATCH: {filename}")
            print(f"             searched: '{title}' {year or ''}")
            retry_missing += 1

    # Save
    with open(tmdb_path, 'w', encoding='utf-8') as f:
        json.dump(tmdb_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"tmdb-fix complete:")
    print(f"  Manual corrections applied:  {fixed}")
    print(f"  No-match entries resolved:   {retry_found}")
    print(f"  Still unresolved:            {retry_missing}")
    print(f"  Trailers/extras skipped:     {trailers}")
    print(f"\nSaved: {tmdb_path}")
    if retry_missing:
        print(f"\nFor remaining unresolved entries, manually look up the TMDB ID")
        print(f"and edit movies_tmdb.json: set match_status='confident', tmdb_id=NNNNN")


_TMDB_TOKEN_HINT = """\
  TMDB token: not set
    The tmdb-* commands require a free TMDB API Read Access Token.
    1. Create an account at https://www.themoviedb.org/signup
    2. Request an API key at https://www.themoviedb.org/settings/api
       (choose "Developer" tier — free, instant approval for personal use)
    3. Copy the "API Read Access Token" (the long v4 token, NOT the v3 key)
    4. Add it to media_agent_config.json as "tmdb_read_access_token"
       or set the environment variable: TMDB_TOKEN=<token>"""


def cmd_doctor(args):
    """Health check: ffprobe, mutagen, library paths, TMDB token."""
    ok = True

    print("── media-agent doctor ────────────────────────────────────────────────────────")

    # ffprobe
    if CONFIG.ffprobe:
        try:
            result = subprocess.run(
                [CONFIG.ffprobe, '-version'],
                capture_output=True, text=True, timeout=10,
            )
            version_line = result.stdout.splitlines()[0] if result.stdout else '(no output)'
            print(f"  [OK] ffprobe     : {version_line}")
        except Exception as e:
            print(f"  [!!] ffprobe     : found at {CONFIG.ffprobe} but failed to run: {e}")
            ok = False
    else:
        print("  [!!] ffprobe     : NOT FOUND")
        print("       Run scripts/install_ffmpeg.ps1 (Windows) or scripts/install_ffmpeg.sh")
        ok = False

    # mutagen
    try:
        importlib.import_module('mutagen')
        print("  [OK] mutagen     : importable")
    except ImportError:
        print("  [!!] mutagen     : not installed — run: pip install mutagen")
        ok = False

    # requests
    try:
        importlib.import_module('requests')
        print("  [OK] requests    : importable")
    except ImportError:
        print("  [!!] requests    : not installed — run: pip install requests")
        ok = False

    # library root
    if CONFIG.library_root.is_dir():
        print(f"  [OK] library_root: {CONFIG.library_root}")
    else:
        print(f"  [!!] library_root: NOT FOUND: {CONFIG.library_root}")
        ok = False

    # sections — missing dirs are warnings, not hard failures (fresh installs may not have them yet)
    for key in ('movies', 'tv_shows', 'music'):
        path = CONFIG.sections.get(key)
        if path is None:
            print(f"  [--] section/{key:<8}: not configured")
        elif path.is_dir():
            print(f"  [OK] section/{key:<8}: {path}")
        else:
            print(f"  [!!] section/{key:<8}: NOT FOUND: {path}")

    # TMDB token
    token = CONFIG.tmdb_token or os.environ.get('TMDB_TOKEN', '').strip()
    if not token:
        print("  [--] TMDB token  : not configured (tmdb-* commands will fail)")
        print()
        print(_TMDB_TOKEN_HINT)
    else:
        try:
            req = urllib.request.Request(
                'https://api.themoviedb.org/3/configuration',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    print("  [OK] TMDB token  : valid")
                else:
                    print(f"  [!!] TMDB token  : unexpected status {resp.status}")
                    ok = False
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("  [!!] TMDB token  : invalid (401 Unauthorized) — check your token")
            else:
                print(f"  [??] TMDB token  : HTTP {e.code} — could not verify")
            ok = False
        except urllib.error.URLError as e:
            print(f"  [??] TMDB token  : network error — could not verify ({e.reason})")
        except Exception:
            print("  [??] TMDB token  : could not verify (unexpected error)")

    print()
    if ok:
        print("All checks passed.")
    else:
        print("One or more checks failed — see above.")
        raise SystemExit(1)


def cmd_init(args):
    """Interactive first-run configuration bootstrap."""
    config_path = Path.home() / '.config' / 'media-agent' / 'config.json'

    print("── media-agent init ──────────────────────────────────────────────────────────")
    print("This will create a config file at:")
    print(f"  {config_path}")
    print()

    if config_path.exists():
        answer = input("Config file already exists. Overwrite? [y/N] ").strip().lower()
        if answer != 'y':
            print("Aborted. Existing config unchanged.")
            return

    # Prompt for library_root
    while True:
        raw = input("Path to your Plex Media Library folder: ").strip()
        if not raw:
            print("  library_root is required.")
            continue
        library_root = Path(raw).expanduser().resolve()
        if library_root.is_dir():
            break
        print(f"  Directory not found: {library_root}")
        print("  Please enter a valid path.")

    # Prompt for TMDB token
    print()
    print("TMDB Read Access Token (optional — required for tmdb-* commands).")
    print("Leave blank to skip; add it later via the config file or TMDB_TOKEN env var.")
    print("Get one free at: https://www.themoviedb.org/settings/api")
    tmdb_token = input("TMDB Read Access Token: ").strip()

    config = {
        "library_root": str(library_root),
        "sections":     {"movies": None, "tv_shows": None, "music": None},
        "indexes_dir":  None,
        "reports_dir":  None,
        "ffprobe_path": None,
    }
    if tmdb_token:
        config["tmdb_read_access_token"] = tmdb_token

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
        f.write('\n')

    print()
    print(f"Config written to: {config_path}")
    print()
    print("Next steps:")
    print("  1. Run: python media_agent.py doctor   (verify everything is working)")
    print("  2. Run: python media_agent.py status   (see your library stats)")
    print()
    print("To customise section paths, indexes location, or other options,")
    print(f"edit {config_path}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    global CONFIG

    parser = argparse.ArgumentParser(
        description="Media Library Maintenance Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--config', metavar='PATH', default=None,
        help='Path to media_agent_config.json (overrides env var and default search paths)',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # rescan
    p_rescan = sub.add_parser('rescan',
        help='Scan disk and reconcile movies.json + movies_below_720p.csv')
    p_rescan.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompts')

    # rebuild-lowres
    sub.add_parser('rebuild-lowres',
        help='Rebuild movies_below_720p.csv from current movies.json')

    # normalize
    p_norm = sub.add_parser('normalize',
        help='Suggest or apply filename normalizations')
    p_norm.add_argument('--dry-run', action='store_true', default=False,
        help='Preview proposed renames without making changes')
    p_norm.add_argument('--apply', action='store_true',
        help='Actually rename files and update indexes')
    p_norm.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompts when using --apply')

    # status
    sub.add_parser('status', help='Show library stats and index health')

    # doctor
    sub.add_parser('doctor', help='Health check: ffprobe, mutagen, library paths, TMDB token')

    # init
    sub.add_parser('init', help='Interactive first-run configuration bootstrap')

    # scan-tvshows
    p_tv = sub.add_parser('scan-tvshows',
        help='Scan TV Shows folder and build tvshows.json')
    p_tv.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompts')
    p_tv.add_argument('--rescan', action='store_true',
        help='Incremental rescan: reuse probe data for known files, only probe new ones')

    # normalize-tv
    p_ntv = sub.add_parser('normalize-tv',
        help='Normalize TV show folder structure to Plex standards')
    p_ntv.add_argument('--dry-run', action='store_true',
        help='Preview plan without making changes')
    p_ntv.add_argument('--apply', action='store_true',
        help='Execute the normalization plan')
    p_ntv.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompt')

    # reassign-season
    p_rs = sub.add_parser('reassign-season',
        help='Reassign Season 0 episodes to a target season in tvshows.json')
    p_rs.add_argument('show', nargs='+',
        help='Show name(s) to reassign (case-insensitive)')
    p_rs.add_argument('--to', type=int, default=1, metavar='N',
        help='Target season number (default: 1)')
    p_rs.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompt')

    # scan-music
    sub.add_parser('scan-music',
        help='Scan Music folder, read ID3/mutagen tags, build music.json')

    # organize-music
    p_om = sub.add_parser('organize-music',
        help='Organize Music into Plex Artist/Album/Track structure')
    p_om.add_argument('--dry-run', action='store_true',
        help='Preview plan without making changes')
    p_om.add_argument('--apply', action='store_true',
        help='Execute the organization plan')
    p_om.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompt')

    # tmdb-enrich
    p_te = sub.add_parser('tmdb-enrich',
        help='Look up movies via TMDB API, save results to movies_tmdb.json')
    p_te.add_argument('--reset', action='store_true',
        help='Re-lookup all movies, ignoring any existing movies_tmdb.json entries')

    # tmdb-fix
    sub.add_parser('tmdb-fix',
        help='Apply manual corrections + re-search no-match entries with smarter cleaning')

    # tmdb-canonicalize
    p_tc = sub.add_parser('tmdb-canonicalize',
        help='Rename files to canonical TMDB title/year format')
    p_tc.add_argument('--dry-run', action='store_true',
        help='Preview proposed renames without making changes')
    p_tc.add_argument('--apply', action='store_true',
        help='Actually rename files and update indexes')
    p_tc.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompt')

    # tmdb-rename
    p_tr = sub.add_parser('tmdb-rename',
        help='Add {tmdb-XXXXX} suffix to filenames based on movies_tmdb.json')
    p_tr.add_argument('--dry-run', action='store_true',
        help='Preview proposed renames without making changes')
    p_tr.add_argument('--apply', action='store_true',
        help='Actually rename files and update indexes')
    p_tr.add_argument('--include-ambiguous', action='store_true',
        help='Also rename ambiguous matches (default: confident only)')
    p_tr.add_argument('--yes', '-y', action='store_true',
        help='Skip confirmation prompt')

    args = parser.parse_args()

    # init creates the config — run it before Config.load so a missing config isn't fatal
    if args.command == 'init':
        cmd_init(args)
        return

    CONFIG = Config.load(Path(args.config) if args.config else None)

    dispatch = {
        'rescan':            cmd_rescan,
        'rebuild-lowres':    cmd_rebuild_lowres,
        'normalize':         cmd_normalize,
        'status':            cmd_status,
        'doctor':            cmd_doctor,
        'scan-tvshows':      cmd_scan_tvshows,
        'normalize-tv':      cmd_normalize_tv,
        'reassign-season':   cmd_reassign_season,
        'scan-music':        cmd_scan_music,
        'organize-music':    cmd_organize_music,
        'tmdb-enrich':       cmd_tmdb_enrich,
        'tmdb-fix':          cmd_tmdb_fix,
        'tmdb-canonicalize': cmd_tmdb_canonicalize,
        'tmdb-rename':       cmd_tmdb_rename,
    }
    dispatch[args.command](args)


if __name__ == '__main__':
    main()
