"""Command-line entry point."""

import argparse
from pathlib import Path

from . import console
from .config import Config, set_config
from .doctor import cmd_doctor, cmd_init
from .movies import cmd_normalize, cmd_rebuild_lowres, cmd_rescan, cmd_status
from .music import cmd_organize_music, cmd_scan_music
from .tmdb import (cmd_tmdb_canonicalize, cmd_tmdb_enrich, cmd_tmdb_fix,
                   cmd_tmdb_rename)
from .tv import cmd_normalize_tv, cmd_reassign_season, cmd_scan_tvshows

EPILOG = """First time here?  Run these three, in order:

  media-agent init        create your config file (asks where your library is)
  media-agent doctor      check that everything is set up correctly
  media-agent status      show what is in your library

Commands are grouped by how much they can change:

SAFE - reads only, changes nothing
  status              library stats and index health
  doctor              check ffprobe, mutagen, paths and TMDB token

BUILDS INDEXES - writes the .json/.csv index files, never touches your media
  rescan              scan Movies, update movies.json + movies_below_720p.csv
  rebuild-lowres      rebuild movies_below_720p.csv from movies.json
  scan-tvshows        scan TV Shows, build tvshows.json  (--rescan = faster)
  scan-music          scan Music, read tags, build music.json
  tmdb-enrich         look up movies on TMDB, write movies_tmdb.json
  tmdb-fix            retry failed TMDB lookups, apply your overrides
  reassign-season     move Season 0 episodes to a real season, in the index only

CHANGES YOUR FILES - renames and moves real media. Requires --apply.
  normalize           clean up movie filenames
  normalize-tv        restructure TV folders to 'Show (Year)/Season NN/'
  organize-music      move tracks into 'Artist/Album (Year)/## - Title.ext'
  tmdb-canonicalize   rename movies to TMDB's official title and year
  tmdb-rename         append the {tmdb-12345} tag to movie filenames

Every command in the last group previews by default. Run it with --dry-run,
read the preview file it writes, and only then run it again with --apply.
Passing neither flag does nothing. --yes skips the confirmation prompt; it
does not make the operation any safer.

Examples:
  media-agent normalize --dry-run
  media-agent normalize --apply
  media-agent scan-tvshows --rescan
  media-agent tmdb-rename --apply --include-ambiguous
  media-agent reassign-season "Doctor Who" --to 1
"""


def main():
    parser = argparse.ArgumentParser(
        prog='media-agent',
        description="Media Library Maintenance Agent - organise a Plex library on disk.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
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

    console.init_stdout()

    # init creates the config - run it before Config.load so a missing config isn't fatal
    if args.command == 'init':
        cmd_init(args)
        return

    set_config(Config.load(Path(args.config) if args.config else None))

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
