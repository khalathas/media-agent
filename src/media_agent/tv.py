"""Commands: scan-tvshows, normalize-tv, reassign-season."""

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime

from .config import get_config
from .index import write_json_atomic


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
    if not get_config().ffprobe:
        return {'error': 'ffprobe not found — set ffprobe_path in config or add ffprobe to PATH'}
    try:
        result = subprocess.run(
            [get_config().ffprobe, '-v', 'quiet', '-print_format', 'json',
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


def _episode_path_key(fpath):
    """A stable identity for an episode file: its path below the TV root.

    Filenames alone are not identities. "S01E01.mkv" occurs in most shows in a
    library, so a cache keyed by filename hands one show another show's probe
    data -- and its season, including a season the user set by hand with
    reassign-season. Lowercased because Windows paths are case-insensitive.
    """
    try:
        rel = os.path.relpath(fpath, get_config().sections['tv_shows'])
    except ValueError:
        rel = fpath           # different drive; fall back to the absolute path
    return rel.replace(os.sep, '/').replace('\\', '/').lower()


def _show_scoped_key(show_folder, fname):
    """Fallback identity for indexes written before paths were recorded.

    Weaker than the path key -- it cannot tell 720p/S01E01.mkv from
    1080p/S01E01.mkv inside one show -- but it does prevent the far worse
    cross-show collision, and it lets an existing index keep its probe data
    instead of forcing a full re-probe of the whole library.
    """
    return f"{show_folder}/{fname}".replace('\\', '/').lower()


def _ambiguous_legacy_basenames(tv_root, show_folder):
    """Basenames that appear more than once anywhere under this show's folder.

    The show-scoped fallback key (_show_scoped_key) cannot tell
    720p/S01E01.mkv from 1080p/S01E01.mkv apart -- both hash to
    "showfolder/s01e01.mkv". Used to refuse that fallback for any basename
    that isn't actually unique within the show, so a legacy (pathless)
    cache entry never gets handed to the wrong file.

    Deliberately a full recursive walk, broader than the specific
    Season-dir / flat / other-dir traversal scan_tv_shows performs: over-
    detecting an ambiguity in some edge case scan_tv_shows wouldn't itself
    reach just costs an unnecessary fresh probe, never a wrong one.
    """
    counts = Counter()
    show_path = os.path.join(tv_root, show_folder)
    for root, dirs, fnames in os.walk(show_path):
        dirs[:] = [d for d in dirs if not d.startswith('@') and not d.startswith('.')
                   and d.lower() not in _SKIP_DIRS]
        for fname in fnames:
            if os.path.splitext(fname)[1].lower() in get_config().video_exts:
                counts[fname.lower()] += 1
    return {name for name, n in counts.items() if n > 1}


def _lookup_existing(existing_episodes, fpath, show_folder, fname, ambiguous_basenames=None):
    """Find a cached episode, preferring the precise key.

    The show-scoped fallback (for legacy, pathless index entries) is
    refused when `fname` is one of this show's ambiguous_basenames -- see
    _ambiguous_legacy_basenames for why that key can't be trusted there.
    """
    if not existing_episodes:
        return None
    hit = existing_episodes.get(_episode_path_key(fpath))
    if hit is not None:
        return hit
    if ambiguous_basenames and fname.lower() in ambiguous_basenames:
        return None
    return existing_episodes.get(_show_scoped_key(show_folder, fname))


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
        'path':               _episode_path_key(fpath),
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


def _collect_episodes_from_dir(dir_path, season_hint=None, existing_episodes=None,
                               show_folder='', ambiguous_basenames=None):
    """
    Walk a directory (non-recursively) and return list of (ep_dict, season_bucket)
    for every video file found. Skips non-video files.
    season_hint: if set, used as season_from_folder for all files here.
    existing_episodes: cache from a prior index, keyed by episode path.
    show_folder: the show's top-level folder name, used for the fallback key.
    ambiguous_basenames: basenames that collide elsewhere in this show; see
                        _ambiguous_legacy_basenames.
    """
    results = []
    try:
        for fname in sorted(os.listdir(dir_path)):
            if os.path.splitext(fname)[1].lower() not in get_config().video_exts:
                continue
            fpath = os.path.join(dir_path, fname).replace('\\', '/')
            existing = _lookup_existing(existing_episodes, fpath, show_folder, fname,
                                        ambiguous_basenames=ambiguous_basenames)
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
    tv_root = get_config().sections['tv_shows']
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
            if os.path.splitext(f)[1].lower() in get_config().video_exts
        ]

        # episodes_by_season: season_int -> [ep_dict, ...]
        episodes_by_season = {}

        def add_ep(ep, bucket):
            episodes_by_season.setdefault(bucket, []).append(ep)

        # Only worth the extra walk when there's a legacy cache that could
        # otherwise hand one file another's probe data (see
        # _ambiguous_legacy_basenames).
        ambiguous_basenames = (
            _ambiguous_legacy_basenames(tv_root, folder) if existing_episodes else None)

        # 1. Proper Season N subfolders — folder season number is authoritative
        for season_dir in season_dirs:
            m = re.search(r'\bSeason\s*(\d+)', season_dir, re.IGNORECASE)
            season_num = int(m.group(1)) if m else 0
            season_path = os.path.join(show_path, season_dir)
            for ep, _ in _collect_episodes_from_dir(season_path, season_hint=season_num,
                                                     existing_episodes=existing_episodes,
                       show_folder=folder, ambiguous_basenames=ambiguous_basenames):
                add_ep(ep, season_num)

        # 2. Flat video files directly in show folder — infer season from filename
        for fname in sorted(flat_videos):
            fpath = os.path.join(show_path, fname).replace('\\', '/')
            existing = _lookup_existing(existing_episodes, fpath, folder, fname,
                                        ambiguous_basenames=ambiguous_basenames)
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
                                                             existing_episodes=existing_episodes,
                       show_folder=folder, ambiguous_basenames=ambiguous_basenames):
                        add_ep(ep, season_num)
            elif sub_other_dirs:
                # Deeper nesting (e.g. Doctor Who Classic: show → actor → serial → episodes)
                # Also collect any flat video files directly in sub_path itself
                # (e.g. a pack folder that has episodes + a Subs/ subfolder alongside them)
                for ep, bucket in _collect_episodes_from_dir(sub_path, season_hint=None,
                                                              existing_episodes=existing_episodes,
                       show_folder=folder, ambiguous_basenames=ambiguous_basenames):
                    add_ep(ep, bucket)
                for deep_sub in sorted(sub_other_dirs):
                    deep_path = os.path.join(sub_path, deep_sub)
                    for ep, bucket in _collect_episodes_from_dir(deep_path, season_hint=None,
                                                                  existing_episodes=existing_episodes,
                       show_folder=folder, ambiguous_basenames=ambiguous_basenames):
                        add_ep(ep, bucket)
            else:
                # Flat files in a subdirectory — infer season from filenames
                for ep, bucket in _collect_episodes_from_dir(sub_path, season_hint=None,
                                                              existing_episodes=existing_episodes,
                       show_folder=folder, ambiguous_basenames=ambiguous_basenames):
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
    # Entries can become None -- a retracted claim -- and are filtered out
    # before the plan is returned. See _claim_destination.
    file_moves     = []   # (src_path, dst_path, show_display_name) | None
    conflicts      = []   # (reason, path)
    claimed_dsts   = {}   # normalised destination -> (source, index in file_moves)
    poisoned_dsts  = set()  # destinations with 2+ claimants -- never claimable again

    def _claim_destination(dst_key, src_path, dst_path, show_name, root_for_display):
        """Queue a move, or turn it into a conflict if something else wants it too.

        Two files can compute the same destination -- most often the same
        episode filename sitting in a 720p/ and a 1080p/ folder. shutil.move
        overwrites silently on both platforms, so if both were queued the
        second move would destroy the first file.

        The first claimant to arrive is provisionally queued in file_moves. If
        a second claimant shows up, BOTH the earlier queued entry and this one
        become conflicts instead -- the earlier one is retracted from
        file_moves (set to None; filtered out before the plan is returned) and
        the destination is marked poisoned so a third claimant does not slip
        through and silently reclaim it once the dict entry is gone.
        """
        def _where(pth):
            rel = os.path.relpath(pth, root_for_display)
            parent = os.path.dirname(rel)
            return parent.replace(os.sep, '/') if parent else '(show root)'

        if dst_key in poisoned_dsts:
            conflicts.append(
                ("Two files claim the same target — keeping both, moving "
                 f"neither. '{os.path.basename(src_path)}' in {_where(src_path)} "
                 "also wants this target; move or rename it by hand", dst_path))
            return

        if dst_key in claimed_dsts:
            prev_src, prev_idx = claimed_dsts.pop(dst_key)
            file_moves[prev_idx] = None
            poisoned_dsts.add(dst_key)
            conflicts.append(
                ("Two files claim the same target — keeping both, moving "
                 f"neither. '{os.path.basename(src_path)}' exists in "
                 f"{_where(prev_src)} and {_where(src_path)}; "
                 "move or rename one by hand", dst_path))
            return

        claimed_dsts[dst_key] = (src_path, len(file_moves))
        file_moves.append((src_path, dst_path, show_name))

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
        if folder.lower() in get_config().skip_shows:
            continue

        show_data = show_by_folder.get(folder)

        clean_name, year = clean_show_name(folder)
        expected = f"{clean_name} ({year})" if year else clean_name

        if folder == expected:
            continue  # Already correct

        # Shows flagged as duplicate candidates span multiple folders; renaming
        # one would merge folders that need a human decision first.
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
        if folder.lower() in get_config().skip_shows:
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
        if folder.lower() in get_config().skip_shows:
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
                if ext not in get_config().video_exts:
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

                # Check for conflicts against the disk AND against destinations
                # already claimed earlier in this same plan. Two files sharing a
                # name in different subfolders (720p/ and 1080p/, say) both pass
                # the disk check, because neither has moved yet. Without the
                # claimed set, the second move silently overwrites the first and
                # an episode is gone for good.
                dst_disk = os.path.join(show_path_disk,
                                        target_dir if target_dir not in season_rename_map.values() else target_dir,
                                        fname)
                if os.path.exists(dst_disk) and os.path.normpath(dst_disk) != os.path.normpath(os.path.join(dirpath, fname)):
                    conflicts.append((f"File move conflict: target exists", dst_file))
                    continue
                dst_key = os.path.normcase(os.path.normpath(dst_file))
                _claim_destination(dst_key, src_file, dst_file, effective_folder,
                                   show_path_effective)

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
                        if srt_src == srt_dst:
                            continue
                        if os.path.exists(srt_dst):
                            conflicts.append(
                                ('Subtitle move conflict: target already exists',
                                 srt_dst))
                            continue
                        srt_key = os.path.normcase(os.path.normpath(srt_dst))
                        _claim_destination(srt_key, srt_src, srt_dst, effective_folder,
                                           show_path_effective)

    return {
        'show_renames':   show_renames,
        'season_renames': season_renames,
        'file_moves':     [m for m in file_moves if m is not None],
        'conflicts':      conflicts,
    }


def _write_normalize_tv_preview(plan):
    """Write the complete plan to a file.

    The docs tell people to read the preview before applying, and to keep it as
    their record of what moved -- there is no undo. That only works if every
    command in the destructive tier actually writes one.
    """
    path = str(get_config().reports_dir / 'normalize_tv_preview.txt')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write("normalize-tv plan\n")
            f.write("=" * 70 + "\n\n")
            f.write("Nothing below has happened yet. Run with --apply to execute.\n")
            f.write("Episode files are never renamed -- only the folders around\n")
            f.write("them, plus loose episodes moved into the right season.\n\n")

            for title, items in (("SHOW FOLDER RENAMES", plan['show_renames']),
                                 ("SEASON FOLDER RENAMES", plan['season_renames'])):
                if not items:
                    continue
                f.write("=" * 70 + "\n")
                f.write(f"{title} ({len(items)})\n")
                f.write("=" * 70 + "\n\n")
                for entry in items:
                    f.write(f"  FROM: {entry[0]}\n    TO: {entry[1]}\n\n")

            if plan['file_moves']:
                f.write("=" * 70 + "\n")
                f.write(f"EPISODE MOVES ({len(plan['file_moves'])})\n")
                f.write("=" * 70 + "\n\n")
                for src, dst, _show in plan['file_moves']:
                    f.write(f"  FROM: {src}\n    TO: {dst}\n\n")

            if plan['conflicts']:
                f.write("=" * 70 + "\n")
                f.write(f"SKIPPED — needs your attention ({len(plan['conflicts'])})\n")
                f.write("Nothing is overwritten. These are left exactly as they are.\n")
                f.write("=" * 70 + "\n\n")
                for reason, pth in plan['conflicts']:
                    f.write(f"  {reason}\n    {pth}\n\n")

        print(f"\nFull plan saved to: {path}")
    except Exception as exc:
        print(f"\nWARNING: could not write preview file: {exc}")


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
    tv_root = get_config().sections['tv_shows']
    index_path = get_config().indexes['tvshows']

    if not os.path.exists(index_path):
        print("ERROR: tvshows.json not found — run scan-tvshows first.")
        return

    with open(index_path, encoding='utf-8') as f:
        data = json.load(f)

    print("Building normalization plan...")
    plan = _build_normalize_tv_plan(tv_root, data)

    _print_normalize_tv_plan(plan)
    _write_normalize_tv_preview(plan)

    total_ops = (len(plan['show_renames']) + len(plan['season_renames'])
                 + len(plan['file_moves']))

    if total_ops == 0:
        print("Nothing to do — folder structure is already normalized.")
        return

    # --dry-run wins even if --apply is also given: a user who passes both is
    # being cautious, and the cautious reading is "don't touch anything".
    apply_mode = getattr(args, 'apply', False) and not getattr(args, 'dry_run', False)

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
            # shutil.move overwrites silently on both platforms, so re-check
            # right before moving: the plan was built earlier and the disk may
            # have changed since. Never destroy an existing file.
            if os.path.exists(dst_path):
                print(f"  SKIPPED (target exists): {os.path.basename(src_path)}")
                errors += 1
                continue
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

    write_json_atomic(index_path, out)

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
    index_path = get_config().indexes['tvshows']
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
    write_json_atomic(index_path, data)

    print(f"\nDone. {changed} episode(s) reassigned.")
    print(f"Index saved to: {index_path}")


def cmd_scan_tvshows(args):
    """Scan the TV Shows folder and build tvshows.json."""
    rescan_mode = getattr(args, 'rescan', False)

    # Load existing index for incremental rescan
    existing_episodes = None
    old_filenames     = set()
    if rescan_mode and os.path.exists(get_config().indexes['tvshows']):
        print("Loading existing tvshows.json for incremental rescan...")
        with open(get_config().indexes['tvshows'], encoding='utf-8') as f:
            old_data = json.load(f)
        existing_episodes = {}
        legacy = 0
        for show in old_data.get('shows', []):
            show_folder = show.get('folder', '')
            for season in show.get('seasons', []):
                for ep in season.get('episodes', []):
                    entry = {**ep, '_season': season['season']}
                    # Key by the episode's path. Keying by filename alone made
                    # every "S01E01.mkv" in the library collide, so one show
                    # inherited another's probe data and season.
                    if ep.get('path'):
                        existing_episodes[ep['path']] = entry
                    else:
                        # Written before paths were recorded. Scope to the show
                        # so an old index still gets an incremental rescan
                        # rather than a full re-probe of the whole library.
                        existing_episodes[_show_scoped_key(show_folder,
                                                           ep['filename'])] = entry
                        legacy += 1
                    old_filenames.add(ep['filename'])
        print(f"  {len(existing_episodes)} episodes already indexed — probe data will be reused.")
        if legacy:
            print(f"  {legacy} of them predate per-episode paths; this scan records "
                  "them, so the next rescan is more precise.")
        print()
    else:
        if rescan_mode:
            print("No existing tvshows.json found — performing full scan.\n")
        else:
            print("Full scan (all files will be probed).\n")

    print("Scanning TV Shows folder...")
    print(f"  {get_config().sections['tv_shows']}\n")

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

    index_path = get_config().indexes['tvshows']
    write_json_atomic(index_path, out)

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
    failures_path = str(get_config().reports_dir / 'probe_failures.txt')
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
