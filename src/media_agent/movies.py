"""Commands: rescan, rebuild-lowres, normalize, status."""

import csv
import os
import shutil
from collections import Counter

from .config import get_config
from .console import confirm
from .index import (_check_low_res_sync, _rebuild_low_res, load_movies_json,
                    save_movies_json)
from .naming import build_clean_name, parse_movie_filename
from .probe import classify_resolution, get_media_info, scan_video_files


def cmd_rescan(args):
    """Scan the Movies folder and reconcile movies.json + movies_below_720p.csv."""
    print("Scanning Movies folder...")
    disk_files = scan_video_files(get_config().sections['movies'])
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


def cmd_normalize(args):
    """Scan Movies folder and suggest standardized filenames."""
    disk_files = scan_video_files(get_config().sections['movies'])
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
    review_path = str(get_config().reports_dir / 'normalize_preview.txt')
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


def _print_config_block():
    """Print the resolved runtime configuration."""
    print("── Configuration ─────────────────────────────────────────────────────────────")
    print(f"  library_root  : {get_config().library_root}")
    for key in ('movies', 'tv_shows', 'music'):
        path = get_config().sections.get(key)
        if path:
            marker = "" if path.is_dir() else "  [NOT FOUND]"
            print(f"  section/{key:<8}: {path}{marker}")
        else:
            print(f"  section/{key:<8}: (not configured)")
    print(f"  indexes_dir   : {get_config().indexes_dir}")
    print(f"  reports_dir   : {get_config().reports_dir}")
    ffprobe_display = get_config().ffprobe if get_config().ffprobe else "NOT FOUND — run scripts/install_ffmpeg"
    print(f"  ffprobe       : {ffprobe_display}")
    token_display = "set" if get_config().tmdb_token else "not set"
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
    disk_files = scan_video_files(get_config().sections['movies'])
    in_index   = {m['name'] for m in movies}
    on_disk    = set(disk_files)
    new_count   = len(on_disk - in_index)
    stale_count = len(in_index - on_disk)
    print(f"  On disk:       {len(on_disk)}")
    print(f"  Not indexed:   {new_count}" + (" <-- run rescan" if new_count else ""))
    print(f"  Stale entries: {stale_count}" + (" <-- run rescan" if stale_count else ""))

    # Low-res CSV check
    try:
        with open(get_config().indexes['movies_low_res'], newline='', encoding='utf-8') as f:
            csv_count = sum(1 for _ in csv.DictReader(f))
        expected = sum(1 for m in movies
                       if (m.get('height') or 0) < 720 and (m.get('width') or 0) < 1280)
        sync = "OK" if csv_count == expected else f"OUT OF SYNC (has {csv_count}, expected {expected})"
        print(f"\nmovies_below_720p.csv: {csv_count} rows [{sync}]")
    except Exception as e:
        print(f"\nmovies_below_720p.csv: could not read ({e})")
