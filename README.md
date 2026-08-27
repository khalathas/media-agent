# media-agent

**Tidy up your Plex library's files and folders, automatically.**

If Plex shows films with no artwork, episodes in the wrong season, or albums split into a dozen "Unknown Artist" entries, the usual cause isn't Plex — it's how the files are named and arranged on disk. `media-agent` reads what you already have and renames and rearranges it into the layout Plex expects.

**New here? Read [Getting Started](docs/GETTING-STARTED.md).** It walks you through the whole thing from scratch, assuming no prior experience.

---

## ⚠️ Read this first

**This tool renames and moves your real media files.** That is its entire purpose, and it cannot be undone with a single button.

Three things keep you safe, and you should use all three:

1. **Back up first**, or try it on a copy of one folder before you point it at everything.
2. **Every command that changes files previews by default.** They do nothing at all unless you add `--apply`.
3. **Read the preview.** Each preview writes a text file listing every single change it intends to make. Open it. If something looks wrong, don't apply.

Start with `media-agent status`. It only reads, and it changes nothing.

---

## What it does

- **Builds an index** of your movies, TV shows, and music — resolution, codec, bitrate, tags — into JSON files you can search or inspect.
- **Cleans up movie filenames**, turning `The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv` into `The Matrix (1999) [1080p].mkv`.
- **Restructures TV folders** into `Show Name (Year)/Season 01/`, moving stray episodes into the right season folder.
- **Organises music** into `Artist/Album (Year)/01 - Track.mp3` using the tags stored inside each audio file.
- **Looks up your films on TMDB** and can rename them to the official title, year, and Plex `{tmdb-12345}` tag so Plex matches them correctly.
- **Flags low-resolution files**, so you know what's worth re-acquiring.

## What it does *not* do

- It does **not** talk to your Plex server. You don't need Plex running, and it never needs your Plex password or token.
- It does **not** download, stream, or transcode anything.
- It does **not** delete your media. `organize-music` does delete files it treats as junk — `Thumbs.db`, `desktop.ini`, stray playlists, **and cover-art files such as `folder.jpg` and `AlbumArt*.jpg`**. That list is configurable. Your audio and video files are never deleted.

---

## Requirements

**Windows, macOS, or Linux.** Developed and used daily on Windows against a NAS; the others work but have had less real-world testing.

**Python 3.10 or newer.** Download from [python.org/downloads](https://www.python.org/downloads/). On Windows, **tick the "Add python.exe to PATH" checkbox** on the first screen of the installer — if you miss it, nothing below will work and the fix is annoying. To check it worked:

```bash
python --version
```

**ffmpeg**, which provides a tool called `ffprobe` used to read video properties. Only needed for the commands that scan video.

```powershell
winget install Gyan.FFmpeg
```

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian / Ubuntu
```

Then **close and reopen your terminal**, and check it worked:

```bash
ffprobe -version
```

If you'd rather not use a package manager, download it from [ffmpeg.org/download](https://ffmpeg.org/download.html) and add it to your PATH. If you have a copy of this project's source, `scripts/install_ffmpeg.ps1` (Windows) or `scripts/install_ffmpeg.sh` will fetch it into `vendor/ffmpeg/` for you — they download a pinned, checksum-verified build (SHA-256 on Windows, MD5 on Linux — the only digest the upstream Linux builds publish) and check for unsafe archive contents before extracting. A package manager is still the stronger route: it goes through its own maintained distribution pipeline with verification these scripts don't attempt to replicate, rather than trusting one hardcoded checksum against a single third-party build.

**Git.** The install command below (`pip install git+...`) needs it — pip uses it behind the scenes to fetch the source, even though you never type a `git` command yourself. Check whether you already have it:

```bash
git --version
```

If that fails:

```powershell
winget install Git.Git
```

```bash
brew install git              # macOS
sudo apt install git          # Debian / Ubuntu
```

Then **close and reopen your terminal** and check again with `git --version`.

> **Opening a terminal:** on Windows press <kbd>Start</kbd>, type `powershell`, press <kbd>Enter</kbd>. On macOS press <kbd>Cmd</kbd>+<kbd>Space</kbd>, type `terminal`, press <kbd>Enter</kbd>.

---

## Install

```bash
python -m pip install git+https://github.com/khalathas/media-agent
```

That gives you a `media-agent` command. Check it worked:

```bash
media-agent --help
```

If you get **"'media-agent' is not recognized"**, Python's scripts folder isn't on your PATH. This is common and fixable — see [Troubleshooting](docs/TROUBLESHOOTING.md#media-agent-is-not-recognized). This always works regardless:

```bash
python -m media_agent --help
```

---

## First run

```bash
media-agent init      # creates your config file, asks where your library is
media-agent doctor    # checks everything is set up correctly
media-agent status    # shows what's in your library
```

`doctor` tells you exactly what's wrong and how to fix it if anything isn't working. A healthy result looks like:

```
── media-agent doctor ────────────────────────────────────────────────────────
  [OK] ffprobe     : ffprobe version 7.1
  [OK] mutagen     : importable
  [OK] requests    : importable
  [OK] library_root: D:\Plex Media Library
  [OK] section/movies  : D:\Plex Media Library\Movies
  [OK] section/tv_shows: D:\Plex Media Library\TV Shows
  [OK] section/music   : D:\Plex Media Library\Music
  [--] TMDB token  : not configured (tmdb-* commands will fail)

All checks passed.
```

A TMDB token is optional — you only need one for the four `tmdb-*` commands. See [TMDB setup](#tmdb-setup).

---

## The safety model

Commands fall into three groups, by how much they can change.

| | Group | What it can change |
|---|---|---|
| 🟢 | **Safe** | Nothing. Reads only. |
| 🟡 | **Builds indexes** | Only the generated `.json`/`.csv` index files. **Never your media.** |
| 🔴 | **Changes your files** | Renames and moves real media. Requires `--apply`. |

For every 🔴 command:

- Running it with **no flag at all does nothing** and tells you so.
- `--dry-run` shows you the plan and writes it to a preview file. Nothing is touched.
- `--apply` performs the changes, after asking you to confirm.
- `--yes` skips only the confirmation *question*. **It does not make anything safer** — don't reach for it until you've read a preview and trust the result.

The right habit is always: `--dry-run` → read the preview → `--apply`.

---

## Recommended order

If you're starting from a messy library, do it in this order. Each step is safe to stop after.

```bash
# 1. See what you've got
media-agent status

# 2. Index your movies (writes movies.json — doesn't touch media)
media-agent rescan

# 3. Clean up movie filenames — preview first, then read the preview file
media-agent normalize --dry-run
media-agent normalize --apply

# 4. Index and tidy TV
media-agent scan-tvshows
media-agent normalize-tv --dry-run
media-agent normalize-tv --apply

# 5. Index and tidy music
media-agent scan-music
media-agent organize-music --dry-run
media-agent organize-music --apply
```

Then, if you've set up a TMDB token and want Plex to match your films perfectly:

```bash
media-agent tmdb-enrich                 # look everything up
media-agent tmdb-fix                    # retry the ones that failed
media-agent tmdb-canonicalize --dry-run
media-agent tmdb-canonicalize --apply
```

---

## Commands

Full detail for each, with examples and failure modes, is in **[docs/COMMANDS.md](docs/COMMANDS.md)**.

### 🟢 Safe — reads only

| Command | What it does |
|---|---|
| `status` | Library stats and index health. The best place to start. |
| `doctor` | Checks ffprobe, mutagen, your paths, and your TMDB token. |
| `init` | Creates your config file interactively. |

### 🟡 Builds indexes — never touches your media

| Command | What it does |
|---|---|
| `rescan` | Scans Movies, updates `movies.json` and `movies_below_720p.csv`. |
| `rebuild-lowres` | Rebuilds the low-resolution list from `movies.json`, without re-scanning the disk. |
| `scan-tvshows` | Scans TV Shows, builds `tvshows.json`. Add `--rescan` to reuse existing data and only probe new files — much faster. |
| `scan-music` | Scans Music, reads the tags inside each file, builds `music.json`. |
| `tmdb-enrich` | Looks each movie up on TMDB, recording confident / ambiguous / no-match results. `--reset` re-does everything. |
| `tmdb-fix` | Retries the failures using your overrides file and smarter cleanup. |
| `reassign-season` | Moves episodes wrongly filed as Season 0 into a real season — **in the index only**, not on disk. |

### 🔴 Changes your files — needs `--apply`

All of these accept `--dry-run`, `--apply`, and `--yes`.

| Command | What it does |
|---|---|
| `normalize` | Cleans movie filenames into `Title (Year) [Quality].ext`. |
| `normalize-tv` | Restructures TV into `Show (Year)/Season NN/` and moves loose episodes into the right season, bringing matching `.srt` subtitles along. **It does not rename the episode files themselves** — only the folders they live in. |
| `organize-music` | Moves tracks into `Artist/Album (Year)/01 - Title.ext`, with `Disc N` subfolders for multi-disc sets. Untagged files are set aside in `_NeedsTagging` rather than guessed at. Deletes junk files — including cover art such as `folder.jpg`; see [what it deletes](docs/COMMANDS.md#organize-music). The only deletion this tool performs. |
| `tmdb-canonicalize` | Renames films to TMDB's official title and year: `Highlander (1986) {tmdb-8009}.mkv`. |
| `tmdb-rename` | Adds just the `{tmdb-12345}` tag, leaving your title alone. Confident matches only, unless you add `--include-ambiguous`. |

---

## Configuration

`media-agent init` writes this for you. Full reference: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

The only field you must set is `library_root`:

```json
{
  "library_root": "D:/Plex Media Library"
}
```

Everything else has a sensible default. Useful extras:

| Field | Purpose |
|---|---|
| `sections` | Set these only if your folders aren't called `Movies`, `TV Shows`, `Music`. |
| `indexes_dir` | Where to write the index files. Defaults to alongside your library. |
| `reports_dir` | Where to write the `--dry-run` preview files. |
| `skip_shows` | TV folders to leave completely alone during `normalize-tv`. |
| `tmdb_read_access_token` | Needed only for `tmdb-*` commands. |
| `tmdb_overrides_file` | Manual fixes for films TMDB gets wrong. See [examples/](examples/tmdb-overrides.example.json). |

media-agent looks for your config in the first place it finds one:

1. `--config PATH`
2. the `MEDIA_AGENT_CONFIG` environment variable
3. `./media_agent_config.json` (the folder you're currently in)
4. `~/.config/media-agent/config.json`
5. `~/media_agent_config.json`

---

## TMDB setup

Only needed for the four `tmdb-*` commands. It's free.

1. Create an account at [themoviedb.org/signup](https://www.themoviedb.org/signup).
2. Go to [Settings → API](https://www.themoviedb.org/settings/api) and request a key. Choose the **Developer** tier — free, approved instantly for personal use.
3. Copy the **API Read Access Token**. This is the long one that starts with `eyJ`.
   **It is not the "API Key".** Picking the wrong one is the single most common setup mistake, and it fails with an unhelpful `401`.
4. Paste it into your config as `tmdb_read_access_token`, or set the environment variable `TMDB_TOKEN`.

Run `media-agent doctor` to confirm it's recognised.

---

## FAQ

**Do I need Plex running?** No. media-agent never contacts Plex. It only rearranges files; Plex picks up the tidier layout next time it scans.

**Will it delete my media?** Your audio and video files are never deleted — only renamed and moved. `organize-music` does delete files it treats as junk: `Thumbs.db`, `desktop.ini`, `.nfo`, stray `.m3u` playlists, and **cover-art files** (`folder.jpg`, `AlbumArt*.jpg`). If you rely on local artwork files rather than art embedded in the tracks, change `music.junk_patterns` in your config first. The complete delete list is written to `organize_music_preview.txt` by `--dry-run`.

**Can I undo it?** Not automatically. This is why previews matter — **keep the preview files**, because they record every rename and are your map back.

**It says 0 files found.** Almost always `library_root` points somewhere wrong, or your folders aren't named `Movies`/`TV Shows`/`Music`. Run `media-agent doctor`.

**Do I need a TMDB token?** Only for the `tmdb-*` commands. Everything else works without one.

**Something else broke.** See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Documentation

- **[Getting Started](docs/GETTING-STARTED.md)** — full walkthrough from zero
- **[Commands](docs/COMMANDS.md)** — every command and flag in detail
- **[Configuration](docs/CONFIGURATION.md)** — every config field
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** — when things go wrong
- **[Contributing](CONTRIBUTING.md)** — development setup

## License

media-agent's own code is MIT — see [LICENSE](LICENSE). It depends on a few third-party packages under their own licenses; see [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
