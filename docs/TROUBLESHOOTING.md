# Troubleshooting

**Start here:**

```bash
media-agent doctor
```

It checks every part of your setup and usually names the problem outright. Most of what follows is just the longer explanation of something `doctor` will already have told you.

---

## Installation

### `media-agent` is not recognized

> `'media-agent' is not recognized as an internal or external command`
> `media-agent: command not found`

The install worked, but your terminal doesn't know where to find the command. Python puts it in a `Scripts` folder that often isn't on your PATH.

**The quick way round it** — this always works:

```bash
python -m media_agent --help
```

Use `python -m media_agent` anywhere the docs say `media-agent`.

**Fixing it properly on Windows.** `pip install` printed a warning naming the folder:

> `WARNING: The script media-agent.exe is installed in 'C:\Users\You\AppData\...\Scripts' which is not on PATH.`

Copy that path, then:

1. Press <kbd>Start</kbd>, type `environment variables`, open **Edit the system environment variables**
2. Click **Environment Variables…**
3. Under **User variables**, select **Path**, click **Edit**
4. Click **New**, paste the folder, click OK on all three windows
5. **Close and reopen your terminal** — this is the step people miss

**macOS / Linux:** add the folder `pip` named to your `~/.zshrc` or `~/.bashrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

### `pip` is not recognized

Python isn't installed, or wasn't added to PATH during installation.

Reinstall from [python.org/downloads](https://www.python.org/downloads/) and **tick "Add python.exe to PATH"** on the first screen. If Python is already installed, re-run the installer and choose **Modify** — you don't need to uninstall first.

Then try `python -m pip install ...` instead of `pip install ...`.

---

### `Python 3.9` or older

media-agent needs **3.10 or newer**. Install a current version from [python.org/downloads](https://www.python.org/downloads/).

If you have several versions installed, you may need `python3.12 -m pip install ...` to install into the right one.

---

## Configuration

### No config file found

> `ERROR: No media_agent_config.json found.`

You haven't created one yet:

```bash
media-agent init
```

The error lists every location that was searched. If you *have* a config but it isn't being found, it's probably not in one of those places — point at it directly:

```bash
media-agent --config C:/path/to/media_agent_config.json status
```

---

### Missing required field `library_root`

Your config exists but doesn't say where your library is. Add it:

```json
{
  "library_root": "D:/Plex Media Library"
}
```

This also happens with an **old TMDB-only config** from an earlier version — one that has a token but no `library_root`. Add the field and you're done.

---

### Could not parse config — JSON error

A syntax error, and the message gives you the line number. Nearly always one of:

- **A missing comma** between entries
- **A trailing comma** after the last entry — JSON forbids this, unlike most languages
- **Unescaped backslashes** in a Windows path

For paths, use forward slashes:

```json
"library_root": "D:/Plex Media Library"      // good
"library_root": "D:\Plex Media Library"      // broken
"library_root": "D:\\Plex Media Library"     // also fine, but fussier
```

Paste your file into [jsonlint.com](https://jsonlint.com/) if you can't spot it.

---

## ffprobe

### ffprobe not found

> `WARNING: ffprobe not found in PATH and not set in config.`

Commands that inspect video (`rescan`, `scan-tvshows`) need it. Everything else works without it.

Install it with your package manager:

```powershell
winget install Gyan.FFmpeg
```

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian / Ubuntu
```

Then **close and reopen your terminal** — a running terminal won't see a newly installed program. Check with `ffprobe -version`.

Or download it from [ffmpeg.org/download](https://ffmpeg.org/download.html) and add it to your PATH. If you have the project source, `scripts/install_ffmpeg.ps1` / `scripts/install_ffmpeg.sh` will fetch a copy into `vendor/ffmpeg/`, though they download without verifying a checksum, so a package manager is the safer route.

If it's installed somewhere unusual, name it in your config:

```json
"ffprobe_path": "C:/tools/ffmpeg/bin/ffprobe.exe"
```

---

### ffprobe fails on particular files

Failures are collected in `probe_failures.txt`. A handful is normal and genuinely useful information — **those files are usually corrupt**, which is worth knowing.

Test one by hand:

```bash
ffprobe "D:/Plex Media Library/Movies/Suspect File.mkv"
```

If ffprobe can't read it, your media player probably can't either.

---

### ffprobe times out

Each probe gets 30 seconds. Timeouts usually mean a slow network share rather than a bad file. Try copying the file locally and probing it there; if that's fine, it's the network.

---

## Scanning

### It says 0 files found

Three usual causes, in order of likelihood:

**1. `library_root` points at the wrong place.** Run `media-agent doctor` — it prints each resolved path. Open one in your file manager and check it's what you expect.

**2. Your folders aren't named `Movies` / `TV Shows` / `Music`.** Tell media-agent the real names:

```json
"sections": {
  "movies":   "D:/Plex Media Library/Films",
  "tv_shows": null,
  "music":    null
}
```

**3. Your files use an extension that isn't recognised.** See [`video_exts`](CONFIGURATION.md#file-extensions).

---

### Scanning is extremely slow

The first scan reads every file, and over a network share that's slow. Normal — but:

- Use **`scan-tvshows --rescan`** for routine updates. It reuses existing probe data and only inspects new files, which is dramatically faster.
- Set [`indexes_dir`](CONFIGURATION.md#indexes_dir) to a local folder so the index isn't rewritten across the network.
- If you can, run it on the NAS itself rather than across the wire.

---

### Episodes end up in Season 0

Season 0 is where specials live, and also where episodes land when the season number can't be read from the filename.

If a whole show has gone to season 0 by mistake:

```bash
media-agent reassign-season "Show Name" --to 1
```

That corrects the index. Run `normalize-tv` afterwards to move the folders.

If only some episodes are affected, their filenames probably lack a recognisable marker. media-agent understands `S01E05`, `1x05`, and bare `105`. Rename the odd ones out to one of those forms and rescan.

---

### normalize-tv didn't rename my episode files

**That's correct behaviour.** `normalize-tv` renames show folders and season folders, and moves loose episodes into the right season folder. It deliberately leaves episode *filenames* alone.

See [Commands](COMMANDS.md#normalize-tv).

---

### normalize-tv reports conflicts

Conflicts are reported and **skipped, never guessed at**. Two kinds:

- **"Unknown season for file"** — the filename has no recognisable episode marker. Rename it to `SxxExx` form and rescan.
- **"File move conflict: target exists"** — a file of that name is already in the destination. Usually a genuine duplicate; compare the two and remove the one you don't want.
- **"Two files claim the same target"** — the same episode filename exists in two subfolders of one show, often `720p/` and `1080p/`. Both would land in the same season folder, so neither is moved. The message names both folders. Keep the one you want, or rename them so they differ, then run it again.

Nothing is overwritten.

---

## Music

### mutagen not installed

```bash
pip install mutagen
```

Normally installed automatically. If you see this, the install was incomplete — reinstalling media-agent fixes it.

---

### Everything went to `_NeedsTagging`

Those files are missing the tags — artist, album, or title — needed to file them. media-agent won't guess.

Fix the tags with a tag editor such as [MusicBrainz Picard](https://picard.musicbrainz.org/), which can usually identify and tag a folder automatically. Then:

```bash
media-agent scan-music
media-agent organize-music --dry-run
```

Nothing was lost — the files are intact in `_NeedsTagging`.

---

### Files in `_NeedsTagging/_Collisions`

Two files wanted the same destination path — typically the same track twice, or two versions of one album. Nothing was overwritten; the second file was set aside.

Compare them, keep the one you want, and put it in place manually.

---

## TMDB

### 401 Unauthorized

**You almost certainly used the wrong token.** TMDB gives you two credentials, and this is the single most common setup mistake.

You need the **API Read Access Token** — the long one starting with `eyJ`.
Not the **API Key**, which is a shorter string of letters and numbers.

Get the right one at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api), then:

```bash
media-agent doctor
```

---

### No TMDB token found

Add it to your config as `tmdb_read_access_token`, or set the environment variable:

```powershell
$env:TMDB_TOKEN = "eyJhbGciOi..."
```

```bash
export TMDB_TOKEN="eyJhbGciOi..."
```

The environment variable takes priority over the config file.

---

### Films marked "ambiguous" or "no match"

Working as intended — media-agent refuses to guess. Ambiguous means several plausible matches; no match means it found nothing.

First, try the second pass:

```bash
media-agent tmdb-fix
```

For what remains, fix them by hand with a [`tmdb_overrides_file`](CONFIGURATION.md#tmdb_overrides_file):

```json
{
  "corrections": { "Highlander (1992).mkv": 8009 },
  "misspellings": { "terrabithia": "terabithia" }
}
```

Find the id in the TMDB page URL: `themoviedb.org/movie/8009-highlander` → `8009`.

Then re-run `media-agent tmdb-fix`.

---

### It picked the wrong film

Common with remakes and sequels — *Highlander (1986)* versus a file named `Highlander (1992).mkv`.

Add a correction to your overrides file as above, run `tmdb-fix`, then re-run `tmdb-canonicalize --dry-run` and check the preview.

---

## General

### It renamed something wrongly

**Check the preview file.** Every 🔴 command writes one into your `reports_dir`: `normalize_preview.txt`, `normalize_tv_preview.txt`, `organize_music_preview.txt`, `tmdb_rename_preview.txt`, and `tmdb_canonicalize_preview.txt`. Each lists every intended change, old name to new name, and is your map back.

There is no automatic undo. This is why previews and backups matter.

---

### Permission denied on a network drive

- Confirm the share is mounted and writable — try creating a file in it manually
- On Windows, mapped drives sometimes aren't visible to elevated processes; try the UNC path (`//nas/Public/...`) instead of the drive letter
- Check the share is exported read-write, not read-only

---

### Filenames with accents or non-English characters

media-agent handles these, and forces UTF-8 output on Windows so they display correctly.

If your terminal shows `?` or boxes it's a *display* issue, not a data one — the files are fine. Windows Terminal handles them better than the old console.

---

## Still stuck?

Open an issue at [github.com/khalathas/media-agent/issues](https://github.com/khalathas/media-agent/issues) with:

1. What you ran
2. What happened, including any error message
3. The output of `media-agent doctor`

**Before pasting `doctor` output, remove your TMDB token** if it appears — it's a credential.
