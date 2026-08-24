"""Commands: tmdb-enrich, tmdb-fix, tmdb-canonicalize, tmdb-rename."""

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

from .config import TMDB_API_BASE, TMDB_TOKEN_ENV, get_config
from .console import confirm
from .index import load_movies_json, save_movies_json, write_json_atomic
from .naming import _STRIP_RE, _YEAR_RE, _canonical_filename
from .probe import group_by_basename, scan_video_files, video_path_key


_TMDB_ID_RE = re.compile(r'\{tmdb-(\d+)\}', re.IGNORECASE)


def _get_tmdb_headers():
    # Environment variable takes priority over config file
    token = os.environ.get(TMDB_TOKEN_ENV, '').strip() or get_config().tmdb_token

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
    tmdb_path = get_config().indexes['movies_tmdb']
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
    write_json_atomic(tmdb_path, output)

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



def _resolve_single_path(disk_files, by_name, filename):
    """Map a filename to the one file on disk that bears it.

    Returns (abs_path, path_key) or (None, reason). movies_tmdb.json identifies
    films by filename, so when two folders both hold that name there is no way
    to tell which one the entry meant. Renaming whichever came first was the old
    behaviour; refusing and saying so is the right one.
    """
    keys = by_name.get(filename, [])
    if not keys:
        return None, "NOT FOUND on disk"
    if len(keys) > 1:
        where = ", ".join(sorted(os.path.dirname(k) or "(root)" for k in keys))
        return None, f"AMBIGUOUS — {len(keys)} files share this name ({where})"
    return disk_files[keys[0]], keys[0]


def cmd_tmdb_canonicalize(args):
    """
    Rename movie files to canonical Plex format using TMDB title/year data:
        Title (Year) {tmdb-XXXXX}.ext

    Only processes 'confident' matches. Skips files already in canonical form.
    Writes a preview report before applying any changes.
    """
    tmdb_path = get_config().indexes['movies_tmdb']
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
    report_path = str(get_config().reports_dir / 'tmdb_canonicalize_preview.txt')
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

    # Renaming requires an explicit --apply. Passing no flag at all is a
    # preview, never a mutation -- see the safety contract in cli.py.
    if args.dry_run or not args.apply:
        print("(No changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    disk_files    = scan_video_files(get_config().sections['movies'])
    by_name       = group_by_basename(disk_files)
    data          = load_movies_json()
    index_by_path = {m.get('path'): m for m in data['movies'] if m.get('path')}
    index_by_name = {m['name']: m for m in data['movies']}
    tmdb_by_name  = {e['filename']: e for e in tmdb_data.get('movies', [])}
    movies_root   = get_config().sections['movies']

    renamed, failed = 0, 0
    for p in proposals:
        old_path, key = _resolve_single_path(disk_files, by_name, p['old'])
        if old_path is None:
            print(f"  {key}: {p['old']}")
            failed += 1
            continue
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (already exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            entry = index_by_path.get(key) or index_by_name.get(p['old'])
            if entry is not None:
                entry['name'] = p['new']
                entry['path'] = video_path_key(new_path, movies_root)
            if p['old'] in tmdb_by_name:
                tmdb_by_name[p['old']]['filename'] = p['new']
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['old']} → {e}")
            failed += 1

    if renamed:
        data['movies'].sort(key=lambda m: (m.get('path') or m['name']).lower())
        save_movies_json(data)
        tmdb_data['movies'] = list(tmdb_by_name.values())
        write_json_atomic(tmdb_path, tmdb_data)
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
    tmdb_path = get_config().indexes['movies_tmdb']
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
    report_path = str(get_config().reports_dir / 'tmdb_rename_preview.txt')
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

    # Renaming requires an explicit --apply. Passing no flag at all is a
    # preview, never a mutation -- see the safety contract in cli.py.
    if args.dry_run or not args.apply:
        print("(No changes made. Use --apply to rename.)")
        return

    if not args.yes and not confirm(f"Rename {len(proposals)} files and update indexes?"):
        print("Aborted.")
        return

    disk_files    = scan_video_files(get_config().sections['movies'])
    by_name       = group_by_basename(disk_files)
    data          = load_movies_json()
    index_by_path = {m.get('path'): m for m in data['movies'] if m.get('path')}
    index_by_name = {m['name']: m for m in data['movies']}
    tmdb_by_name  = {e['filename']: e for e in tmdb_data.get('movies', [])}
    movies_root   = get_config().sections['movies']

    renamed, failed = 0, 0
    for p in proposals:
        old_path, key = _resolve_single_path(disk_files, by_name, p['old'])
        if old_path is None:
            print(f"  {key}: {p['old']}")
            failed += 1
            continue
        new_path = os.path.join(os.path.dirname(old_path), p['new'])
        if os.path.exists(new_path):
            print(f"  SKIP (already exists): {p['new']}")
            continue
        try:
            shutil.move(old_path, new_path)
            entry = index_by_path.get(key) or index_by_name.get(p['old'])
            if entry is not None:
                entry['name'] = p['new']
                entry['path'] = video_path_key(new_path, movies_root)
            if p['old'] in tmdb_by_name:
                tmdb_by_name[p['old']]['filename'] = p['new']
            renamed += 1
            print(f"  OK: {p['new']}")
        except Exception as e:
            print(f"  FAIL: {p['old']} → {e}")
            failed += 1

    if renamed:
        data['movies'].sort(key=lambda m: (m.get('path') or m['name']).lower())
        save_movies_json(data)
        tmdb_data['movies'] = list(tmdb_by_name.values())
        write_json_atomic(tmdb_path, tmdb_data)
        print(f"\nRenamed {renamed} files.")
        print(f"Updated: movies.json, movies_tmdb.json")
        if failed:
            print(f"{failed} failed — check output above.")


# Per-library TMDB overrides come from the config's "tmdb_overrides_file".
# See examples/tmdb-overrides.example.json for the format.

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
    for wrong, right in get_config().tmdb_misspellings.items():
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
    tmdb_path = get_config().indexes['movies_tmdb']
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
    for filename, tmdb_id in get_config().tmdb_corrections.items():
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
    write_json_atomic(tmdb_path, tmdb_data)

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
