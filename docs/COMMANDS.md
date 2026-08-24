# Commands

Every command, what it reads, what it writes, and what can go wrong.

Commands are grouped by how much they can change. If you only remember one thing: **🔴 commands rename and move your real files, and need `--apply`.**

- [🟢 Safe](#-safe--reads-only) — `status`, `doctor`, `init`
- [🟡 Builds indexes](#-builds-indexes--never-touches-your-media) — `rescan`, `rebuild-lowres`, `scan-tvshows`, `scan-music`, `tmdb-enrich`, `tmdb-fix`, `reassign-season`
- [🔴 Changes your files](#-changes-your-files--requires---apply) — `normalize`, `normalize-tv`, `organize-music`, `tmdb-canonicalize`, `tmdb-rename`

## Flags used across commands

| Flag | Meaning |
|---|---|
| `--config PATH` | Use a specific config file instead of searching the usual places. Works with every command. |
| `--dry-run` | Show what would happen. Change nothing. Writes a preview file you can read. |
| `--apply` | Actually do it. Asks for confirmation first. |
| `--yes` / `-y` | Skip the confirmation question. **Does not make the operation safer.** |

Running a 🔴 command with neither `--dry-run` nor `--apply` does nothing and tells you so. That's deliberate — a bare command name can't damage anything.

---

# 🟢 Safe — reads only

## `status`

Prints a summary of your library and the health of your index files.

```bash
media-agent status
```

Reads `movies.json` and your Movies folder. Writes nothing.

```
── Configuration ─────────────────────────────────────────────────────────────
  library_root  : D:\Plex Media Library
  indexes_dir   : D:\Plex Media Library
  ffprobe       : C:\ffmpeg\bin\ffprobe.exe
  TMDB token    : not set

movies.json:  797 entries

By resolution:
  4K      : 23
  1080p   : 291
  720p    : 163
```

It also warns if the index has drifted from what's on disk — files added or removed since the last `rescan`.

**`movies.json: 0 entries`** just means you haven't run `rescan` yet.

---

## `doctor`

Checks your whole setup and reports what's wrong plus how to fix it.

```bash
media-agent doctor
```

Verifies: ffprobe is present and runnable, the `mutagen` and `requests` libraries are importable, your library root and each section folder exists, and whether a TMDB token is configured.

`[OK]` is good, `[--]` is optional-and-unset, anything else comes with instructions.

**Run this first whenever something misbehaves.** It's faster than guessing.

---

## `init`

Creates your config file by asking you where things are.

```bash
media-agent init
```

Writes `media_agent_config.json`. Safe to run again — it won't silently overwrite an existing config.

This is the only command that works without a config already existing.

---

# 🟡 Builds indexes — never touches your media

These write `.json` and `.csv` index files. They never rename, move, or delete media.

## `rescan`

Scans your Movies folder and brings `movies.json` into line with what's actually on disk.

```bash
media-agent rescan
media-agent rescan --yes     # don't ask for confirmation
```

| | |
|---|---|
| **Reads** | Your Movies folder; `movies.json` |
| **Writes** | `movies.json`, `movies_below_720p.csv` |
| **Needs** | ffprobe |

For each new file it runs ffprobe to record resolution, codecs, bitrate, and size. It prompts before removing entries whose files are gone, and before probing newly found files.

**The first run is slow** — it opens every video file. Over a network drive, a large library can take a long while. Later runs only probe files it hasn't seen.

**If ffprobe fails on some files** they're listed in `probe_failures.txt`. Usually those files are genuinely corrupt, which is useful to know.

---

## `rebuild-lowres`

Regenerates the low-resolution list from `movies.json` without touching the disk.

```bash
media-agent rebuild-lowres
```

| | |
|---|---|
| **Reads** | `movies.json` |
| **Writes** | `movies_below_720p.csv` |

Instant, because it re-uses data already gathered. Use it when you want a fresh list of re-acquisition candidates but nothing has changed on disk.

---

## `scan-tvshows`

Walks your TV Shows folder and builds `tvshows.json` — every show, season, and episode, with the season and episode numbers parsed out of the filenames.

```bash
media-agent scan-tvshows
media-agent scan-tvshows --rescan     # much faster: only probe new files
media-agent scan-tvshows --yes
```

| | |
|---|---|
| **Reads** | Your TV Shows folder; existing `tvshows.json` (with `--rescan`) |
| **Writes** | `tvshows.json` |
| **Needs** | ffprobe |

**`--rescan` reuses the probe data already in `tvshows.json`** and only inspects files it hasn't seen. On a large library this is the difference between minutes and hours. Use it for routine updates; use a plain `scan-tvshows` when you want everything rebuilt from scratch.

It understands the common episode naming styles — `S01E05`, `1x05`, and bare `105` — plus multi-episode files like `S01E01E02`, and specials, which go to season 0.

**Episodes it can't parse** are reported. They usually need renaming by hand before `normalize-tv` can file them.

---

## `scan-music`

Reads the tags inside every audio file and builds `music.json`.

```bash
media-agent scan-music
```

| | |
|---|---|
| **Reads** | Your Music folder |
| **Writes** | `music.json` |
| **Needs** | the `mutagen` library (installed automatically) |

"Tags" are the artist/album/track details stored inside the file itself — what your music player shows. This command reads them, it doesn't change them.

It reports how many files are missing the tags needed to file them properly. Those are the ones `organize-music` will set aside in `_NeedsTagging`.

---

## `tmdb-enrich`

Looks up every film in your index on TMDB and records what it found.

```bash
media-agent tmdb-enrich
media-agent tmdb-enrich --reset     # start over, re-look-up everything
```

| | |
|---|---|
| **Reads** | `movies.json`, `movies_tmdb.json` |
| **Writes** | `movies_tmdb.json` |
| **Needs** | a TMDB Read Access Token |

Every film is graded:

| Result | Meaning |
|---|---|
| **confident** | Title and year both match well. Safe to rename automatically. |
| **ambiguous** | Several plausible matches, or an imperfect one. Needs your judgement. |
| **no match** | Nothing found. Usually a mangled filename, or a very obscure film. |

Only confident matches get renamed by default. That's why the grading exists.

Without `--reset` it skips films it has already resolved, so re-running is cheap after adding new media.

**Getting `401 Unauthorized`?** You almost certainly used the "API Key" instead of the "API Read Access Token". See [TMDB setup](../README.md#tmdb-setup).

---

## `tmdb-fix`

Second pass over the films `tmdb-enrich` couldn't resolve.

```bash
media-agent tmdb-fix
```

| | |
|---|---|
| **Reads** | `movies_tmdb.json`; your overrides file, if configured |
| **Writes** | `movies_tmdb.json` |
| **Needs** | a TMDB Read Access Token |

Three things happen:

1. **Your manual corrections are applied** — exact filename to exact TMDB id, from your [overrides file](CONFIGURATION.md#tmdb_overrides_file).
2. **Misspellings are repaired** before searching again, also from your overrides file.
3. **Failed lookups are retried** with more aggressive filename cleaning.

Trailers, teasers, and behind-the-scenes clips are recognised and skipped rather than being searched for pointlessly.

Run this after `tmdb-enrich`, and again whenever you add corrections to your overrides file.

---

## `reassign-season`

Moves episodes filed as Season 0 into a real season — **in the index only**.

```bash
media-agent reassign-season "Doctor Who"
media-agent reassign-season "Show A" "Show B" --to 2
media-agent reassign-season "Firefly" --to 1 --yes
```

| | |
|---|---|
| **Reads** | `tvshows.json` |
| **Writes** | `tvshows.json` |

| Argument | Meaning |
|---|---|
| `<show>...` | One or more show names. Case-insensitive. Quote names containing spaces. |
| `--to N` | Target season number. Default `1`. |

Season 0 is where specials live, and it's also where episodes end up when the season number can't be determined from the filename. When a whole show has landed in season 0 by mistake, this fixes the index in one go.

**No files are moved.** It corrects the index. Run `normalize-tv` afterwards if you want the folders on disk to follow.

---

# 🔴 Changes your files — requires `--apply`

These rename and move real media. Each writes a preview file listing every intended change. **Read it before applying.**

## `normalize`

Cleans up movie filenames.

```bash
media-agent normalize --dry-run
media-agent normalize --apply
media-agent normalize --apply --yes
```

| | |
|---|---|
| **Reads** | Your Movies folder; `movies.json` |
| **Writes** | Renamed movie files; `movies.json`; `normalize_preview.txt` |

Strips release-group tags, source markers, and codec noise; converts dots and underscores to spaces; fixes capitalisation; and produces `Title (Year) [Quality].ext`.

```
The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv  ->  The Matrix (1999) [1080p].mkv
alien.1979.dvdrip.avi                        ->  Alien (1979).avi
```

Notes:

- **The quality tag is kept.** `[1080p]` is useful and Plex reads it fine.
- **Already-clean filenames are left alone**, so re-running is harmless.
- **Words already capitalised are not touched**, so acronyms and stylised titles survive.
- Titles beginning with a number (`2001: A Space Odyssey`, `300`, `1984`) are handled — the leading number isn't mistaken for a year.

---

## `normalize-tv`

Restructures TV folders into the layout Plex expects.

```bash
media-agent normalize-tv --dry-run
media-agent normalize-tv --apply
```

| | |
|---|---|
| **Reads** | Your TV Shows folder; `tvshows.json` |
| **Writes** | Renamed/moved folders and episode files |

Requires `scan-tvshows` to have been run first — it uses the index to know which season each episode belongs to.

Three things happen:

1. **Show folders** are renamed to `Show Name (Year)`.
2. **Season folders** are renamed to `Season 01`, `Season 02` — zero-padded, as Plex prefers.
3. **Loose episodes** sitting in the show's root are moved into the correct season folder, and any matching `.srt` subtitle goes with them.

```
Breaking.Bad\                    Breaking Bad (2008)\
├── Season 1\           ->       ├── Season 01\
└── s02e01.mkv                   └── Season 02\
                                     └── s02e01.mkv
```

> ### It does not rename your episode files
>
> Only the folders change. `s02e01.mkv` stays `s02e01.mkv` — it just ends up in the right place. If your episodes are already sensibly named, they're untouched.
>
> Unchanged episode filenames after running this is **correct behaviour**, not a failure.

**Conflicts are reported and skipped, never guessed at** — an episode whose season can't be determined, or a target file that already exists. Those need your attention; nothing is overwritten.

To exclude a show entirely, add its folder name to [`skip_shows`](CONFIGURATION.md#skip_shows).

---

## `organize-music`

Moves audio files into a clean `Artist/Album/Track` structure using their tags.

```bash
media-agent organize-music --dry-run
media-agent organize-music --apply
```

| | |
|---|---|
| **Reads** | Your Music folder; `music.json` |
| **Writes** | Moved audio files; deletes junk files |
| **Needs** | the `mutagen` library |

```
Music\
└── Pink Floyd\
    └── The Dark Side of the Moon (1973)\
        ├── 01 - Speak to Me.mp3
        └── 02 - Breathe.mp3
```

Multi-disc albums get `Disc 2`, `Disc 3` subfolders. Disc 1 doesn't, since that's the common case.

**Files with missing tags are never guessed at.** Anything lacking an artist, album, or title goes to `_NeedsTagging`, preserved and untouched, for you to fix with a tag editor such as [MusicBrainz Picard](https://picard.musicbrainz.org/). If two files would land on the same path, the loser goes to `_NeedsTagging/_Collisions` rather than overwriting anything.

> ### This is the only command that deletes anything
>
> It removes junk that accumulates in music folders: `Thumbs.db`, `desktop.ini`, `folder.jpg`, `AlbumArt*.jpg`, stray `.m3u` playlists, `.nfo` files, and Windows `.lnk` shortcuts.
>
> **Audio files are never deleted** — only moved. The junk list is [configurable](CONFIGURATION.md#music).

---

## `tmdb-canonicalize`

Renames films to TMDB's official title and year.

```bash
media-agent tmdb-canonicalize --dry-run
media-agent tmdb-canonicalize --apply
```

| | |
|---|---|
| **Reads** | `movies.json`, `movies_tmdb.json`; your Movies folder |
| **Writes** | Renamed movie files; `movies.json`; `tmdb_canonicalize_preview.txt` |

```
Highlander (1992).mkv  ->  Highlander (1986) {tmdb-8009}.mkv
```

Note the year was wrong and got corrected — that's the point. This is the strongest fix for Plex mismatching your films, because the `{tmdb-8009}` tag removes all ambiguity.

Only **confident** matches are renamed. Characters Windows forbids in filenames are replaced; a colon becomes ` - `, so `Alien: Resurrection` becomes `Alien - Resurrection`.

Run `tmdb-enrich` (and ideally `tmdb-fix`) first.

---

## `tmdb-rename`

Adds the `{tmdb-12345}` tag without changing your title.

```bash
media-agent tmdb-rename --dry-run
media-agent tmdb-rename --apply
media-agent tmdb-rename --apply --include-ambiguous
```

| | |
|---|---|
| **Reads** | `movies.json`, `movies_tmdb.json`; your Movies folder |
| **Writes** | Renamed movie files; `movies.json`; `tmdb_rename_preview.txt` |

```
The Matrix (1999) [1080p].mkv  ->  The Matrix (1999) [1080p] {tmdb-603}.mkv
```

The gentler alternative to `tmdb-canonicalize`: Plex gets the unambiguous ID it needs, but your filenames keep whatever form you prefer.

| Flag | Effect |
|---|---|
| `--include-ambiguous` | Also tag films whose match wasn't confident. **Check the preview carefully** — these are the ones most likely to be wrong. |

Files that already carry a `{tmdb-...}` tag are skipped, so re-running is safe.

---

## See also

- [Getting Started](GETTING-STARTED.md) — the guided walkthrough
- [Configuration](CONFIGURATION.md) — every setting
- [Troubleshooting](TROUBLESHOOTING.md) — when things go wrong
