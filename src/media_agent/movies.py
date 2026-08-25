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
from .probe import (classify_resolution, get_media_info, group_by_basename,
                    scan_video_files, video_path_key)


def migrate_index_paths(movies, disk_files):
    """Give every index entry a 'path', matching it to a real file on disk.

    Indexes written before paths were recorded identify a movie by filename
    alone. Re-probing the whole library to rebuild them would take hours over a
    network share, so instead match each entry to disk by basename. Where the
    basename is unique -- the overwhelming majority -- the match is certain and
    the entry keeps all its probe data.

    Returns (migrated_count, ambiguous_names). An ambiguous name is one where
    two or more files share the basename, so which one the entry described
    cannot be recovered. Those are reported rather than guessed at.
    """
    by_name = group_by_basename(disk_files)
    migrated, ambiguous = 0, []
    for m in movies:
        if m.get('path'):
            continue
        candidates = by_name.get(m['name'], [])
        if len(candidates) == 1:
            m['path'] = candidates[0]
            migrated += 1
        elif len(candidates) > 1:
            ambiguous.append(m['name'])
    return migrated, sorted(set(ambiguous))


def cmd_rescan(args):
    """Scan the Movies folder and reconcile movies.json + movies_below_720p.csv."""
    print("Scanning Movies folder...")
    disk_files = scan_video_files(get_config().sections['movies'])
    print(f"  {len(disk_files)} video files found on disk")

    data = load_movies_json()
    print(f"  {len(data['movies'])} entries in movies.json")

    migrated, ambiguous = migrate_index_paths(data['movies'], disk_files)
    by_name = group_by_basename(disk_files)
    if migrated:
        print(f"  {migrated} existing entries matched to their file on disk.")
    if ambiguous:
        print(f"\n  {len(ambiguous)} indexed name(s) match more than one file on disk.")
        print("  Which file each entry described cannot be recovered, so they will be")
        print("  re-indexed per file. Review these — they are often true duplicates:")
        for name in ambiguous:
            for key in by_name[name]:
                print(f"    {key}")
        print("  The legacy entry for each is kept until every file sharing its name")
        print("  has been freshly probed and added -- if you decline, or a probe")
        print("  fails, nothing is lost.")

    # Entries that never got a path are the ambiguous ones. Set them aside
    # rather than dropping them outright: each is only removed once every
    # file sharing its name has actually been re-probed and added below, so
    # a decline or a failed probe leaves the old entry in place instead of
    # discarding the user's classification/resolution history for nothing.
    ambiguous_names = set(ambiguous)
    pending_legacy = [m for m in data['movies']
                      if not m.get('path') and m['name'] in ambiguous_names]
    data['movies'] = [m for m in data['movies'] if m.get('path')]
    index_by_path = {m['path']: m for m in data['movies']}

    on_disk    = set(disk_files)
    in_index   = set(index_by_path)
    new_files  = sorted(on_disk - in_index)
    stale      = sorted(in_index - on_disk)

    print(f"\n  New (on disk, not indexed): {len(new_files)}")
    print(f"  Stale (in index, not on disk): {len(stale)}")

    if not new_files and not stale and not pending_legacy:
        print("\nIndex is already up to date.")
        if migrated:
            save_movies_json(data)
            print("movies.json saved (file paths recorded).")
        _check_low_res_sync(data['movies'])
        return

    changed = bool(migrated or ambiguous)

    # ── Remove stale entries ──────────────────────────────────────────────────
    if stale:
        print("\nStale index entries (file no longer on disk):")
        for key in stale:
            print(f"  - {key}")
        if args.yes or confirm(f"\nRemove {len(stale)} stale entries from index?"):
            data['movies'] = [m for m in data['movies'] if m['path'] not in set(stale)]
            print(f"  Removed {len(stale)} entries.")
            changed = True
        else:
            print("  Skipped.")

    # ── Add new files ─────────────────────────────────────────────────────────
    if new_files:
        print(f"\nNew files to index ({len(new_files)}):")
        for key in new_files:
            print(f"  + {key}")

        if args.yes or confirm(f"\nProbe and add {len(new_files)} new files to index?"):
            added, failed = 0, 0
            for key in new_files:
                fpath = disk_files[key]
                print(f"  Probing: {key} ... ", end='', flush=True)
                info = get_media_info(fpath)
                if 'error' in info:
                    print(f"FAILED ({info['error']})")
                    failed += 1
                else:
                    info['path'] = key
                    res = classify_resolution(info['width'], info['height'])
                    print(f"{info['width']}x{info['height']} {res}")
                    data['movies'].append(info)
                    added += 1
            print(f"\n  Added {added} entries." + (f" {failed} failed." if failed else ""))
            changed = True
        else:
            print("  Skipped.")

    # ── Reconcile ambiguous legacy entries ────────────────────────────────────
    # Only now do we know whether every file sharing an ambiguous name got a
    # fresh per-path entry above. Where it did, the old unresolvable entry is
    # superseded. Where it didn't (declined, or a probe failed), put it back
    # rather than let it stay dropped with nothing to replace it.
    if pending_legacy:
        current_paths = {m['path'] for m in data['movies'] if m.get('path')}
        by_pending_name = {}
        for m in pending_legacy:
            by_pending_name.setdefault(m['name'], []).append(m)
        restored = 0
        for name, entries in by_pending_name.items():
            candidates = set(by_name.get(name, []))
            if candidates and candidates.issubset(current_paths):
                continue  # fully replaced -- old entry not needed
            data['movies'].extend(entries)
            restored += len(entries)
        if restored:
            print(f"\n  Kept {restored} legacy entr{'y' if restored == 1 else 'ies'} "
                  f"whose replacement could not be completed.")
        changed = True

    # ── Save movies.json ──────────────────────────────────────────────────────
    if changed:
        # A restored legacy entry has no 'path' -- fall back to 'name' so it
        # still sorts instead of crashing.
        data['movies'].sort(key=lambda m: (m.get('path') or m['name']).lower())
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

    for key, fpath in sorted(disk_files.items()):
        fname  = os.path.basename(key)
        parsed = parse_movie_filename(fname)
        if parsed is None:
            continue
        new_name = build_clean_name(parsed)
        if new_name == fname:
            continue
        # 'path' is the absolute path to act on; 'key' identifies the file in
        # the index. Two files can share 'old' and still be distinct entries.
        proposals.append({'old': fname, 'new': new_name, 'path': fpath, 'key': key})

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
            # The full path key, not just the bare filename -- two files in
            # different folders can share a basename (the DUPLICATE case
            # right below is exactly that), and a preview that can't tell
            # them apart isn't a real "map back" to what happened.
            rf.write(f"FROM: {p['key']}\n")
            rf.write(f"  TO: {p['new']}{tag}\n\n")

    print(f"{len(proposals)} files could be renamed "
          f"({dupe_count} flagged as duplicates).")
    print(f"Full list saved to: {review_path}\n")

    # ── Console output ────────────────────────────────────────────────────────
    for p in proposals:
        tag = "  ** DUPLICATE **" if p.get('is_dupe') else ""
        print(f"  FROM: {p['key']}")
        print(f"    TO: {p['new']}{tag}")
        print()

    if args.dry_run or not args.apply:
        print(f"(Dry run — no changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    data = load_movies_json()
    index_by_path = {m.get('path'): m for m in data['movies'] if m.get('path')}

    renamed, failed = 0, 0
    for p in proposals:
        old_path = p['path']
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            # Update the index entry for this exact file, and move its key with
            # it -- the path is the identity, so a rename changes it.
            entry = index_by_path.get(p['key'])
            if entry is not None:
                entry['name'] = p['new']
                entry['path'] = video_path_key(new_path, get_config().sections['movies'])
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['key']} -> {e}")
            failed += 1

    if renamed:
        # Rebuild index from updated dict
        data['movies'] = sorted(data['movies'],
                                key=lambda m: (m.get('path') or m['name']).lower())
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
    ffprobe_display = (get_config().ffprobe or
                       "NOT FOUND — run 'media-agent doctor' to see how to install it")
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
    # Compare by path where the index has one; fall back to basenames for an
    # index that predates paths, so status still reports something sensible.
    if movies and all(m.get('path') for m in movies):
        in_index = {m['path'] for m in movies}
        on_disk  = set(disk_files)
    else:
        in_index = {m['name'] for m in movies}
        on_disk  = {os.path.basename(k) for k in disk_files}
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
    except FileNotFoundError:
        # Normal on a brand new library -- not something to alarm anyone with.
        print("\nmovies_below_720p.csv: not built yet — run 'media-agent rescan'")
    except Exception as e:
        print(f"\nmovies_below_720p.csv: could not read ({e})")
