"""Commands: scan-music, organize-music."""

import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime

from .config import get_config
from .console import confirm
from .naming import _sanitize_path_component


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
    if fname.lower() in get_config().music_junk_names:
        return True
    for pat in get_config().music_junk_patterns:
        if pat.search(fname):
            return True
    return False


def cmd_scan_music(args):
    """Scan the Music folder, read tags, and build music.json."""
    try:
        from mutagen import File as MutagenFile  # noqa: F401
    except ImportError:
        print("ERROR: mutagen not installed. Run: pip install mutagen")
        sys.exit(1)

    music_root = get_config().sections['music']
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
                if ext in get_config().music_exts:
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

    index_path = get_config().indexes['music']
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

    music_root = get_config().sections['music']
    needs_dir  = os.path.join(music_root, get_config().music_needs_tagging_dir)

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

                if ext not in get_config().music_exts:
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
    collisions_root = os.path.join(music_root, get_config().music_collisions_dir)
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
        if get_config().music_needs_tagging_dir in dst:
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
    well_moves      = [(s, d) for s, d in moves if get_config().music_needs_tagging_dir not in d]
    tagging_moves   = [(s, d) for s, d in moves if get_config().music_needs_tagging_dir in d
                       and get_config().music_collisions_dir not in d]
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
        if rel.startswith(get_config().music_needs_tagging_dir):
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
