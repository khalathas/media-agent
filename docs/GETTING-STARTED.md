# Getting Started

This guide takes you from nothing to a tidier Plex library. It assumes you have never used a command line before. Every command is written out in full so you can copy and paste it.

Take it one section at a time. You can stop after any step — nothing is left half-finished.

---

## Before you begin: back up

`media-agent` renames and moves your media files. That's the point of it. But it means a mistake costs you time, so please do one of these first:

- Copy your library to a backup drive, **or**
- Copy a single folder (say, twenty films) somewhere separate and point media-agent at that copy while you get comfortable.

The second option is genuinely worth it. You will understand what the tool does far better after watching it work on files you don't mind breaking.

---

## Step 1 — Open a terminal

A terminal is a window where you type commands instead of clicking.

- **Windows:** press <kbd>Start</kbd>, type `powershell`, press <kbd>Enter</kbd>.
- **macOS:** press <kbd>Cmd</kbd>+<kbd>Space</kbd>, type `terminal`, press <kbd>Enter</kbd>.
- **Linux:** you already know.

You'll see a window with a blinking cursor. Everything below gets typed here, one line at a time, pressing <kbd>Enter</kbd> after each.

---

## Step 2 — Check you have Python

```bash
python --version
```

**If you see something like `Python 3.12.1`** — you're set, skip to Step 3.

**If you see `Python 2.7.x`**, try `python3 --version` instead, and use `python3` everywhere below.

**If you see an error** like "not recognized" or "command not found", install Python from [python.org/downloads](https://www.python.org/downloads/).

> **Windows users, this bit matters:** on the very first screen of the installer there is a checkbox at the bottom reading **"Add python.exe to PATH"**. Tick it. It is not ticked by default, and if you miss it Python installs fine but the terminal can't find it. If you already installed without ticking it, just run the installer again and choose Modify.

You need version **3.10 or newer**.

---

## Step 3 — Check you have Git

The install command in the next step (`pip install git+...`) uses Git behind the scenes to fetch the source, even though you never type a `git` command yourself.

```bash
git --version
```

**If you see a version number** — you're set, skip to Step 4.

**If you see an error**, install it:

```powershell
# Windows
winget install Git.Git
```

```bash
# macOS
brew install git

# Debian / Ubuntu
sudo apt install git
```

Then **close your terminal and open a new one**, and check again with `git --version`.

---

## Step 4 — Install media-agent

```bash
python -m pip install git+https://github.com/khalathas/media-agent
```

This downloads and installs the tool. It takes a few seconds. Some text scrolls past; as long as the last line says something like `Successfully installed media-agent`, you're fine.

Check it worked:

```bash
media-agent --help
```

You should see a list of commands.

**If you get "'media-agent' is not recognized"**, the install worked but your terminal can't find the command. Don't worry — this always works instead:

```bash
python -m media_agent --help
```

If you use that form, just write `python -m media_agent` everywhere this guide says `media-agent`. To fix it properly, see [Troubleshooting](TROUBLESHOOTING.md#media-agent-is-not-recognized).

---

## Step 5 — Install ffmpeg

media-agent uses a program called `ffprobe` (part of ffmpeg) to look inside video files and find out their resolution and codec. Without it, the video scanning commands won't run.

First check whether you already have it:

```bash
ffprobe -version
```

If that prints version information, you're done — skip ahead.

If not, install it with your package manager:

```powershell
# Windows
winget install Gyan.FFmpeg
```

```bash
# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

Then **close your terminal and open a new one** — a terminal that's already running won't notice a newly installed program. Check it worked:

```bash
ffprobe -version
```

> No package manager? Download it from [ffmpeg.org/download](https://ffmpeg.org/download.html) and add the folder containing `ffprobe` to your PATH — the same PATH idea as in [Troubleshooting](TROUBLESHOOTING.md#media-agent-is-not-recognized). Failing that, tell media-agent exactly where it is by setting `ffprobe_path` in your config.

---

## Step 6 — Tell media-agent where your library is

```bash
media-agent init
```

This asks you a few questions and writes a small settings file. The important one is the **library root** — the single folder that contains your `Movies`, `TV Shows`, and `Music` folders.

For example, if your library looks like this:

```
D:\Plex Media Library\
├── Movies\
├── TV Shows\
└── Music\
```

then your library root is `D:\Plex Media Library`.

> **Finding the path on Windows:** open the folder in File Explorer, click the address bar, and the full path appears. Copy it. You can paste it with a right-click in PowerShell.

---

## Step 7 — Check everything works

```bash
media-agent doctor
```

This is a health check. It looks at every part of the setup and tells you what's wrong and how to fix it. A good result looks like:

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

`[OK]` means good. `[--]` means "not set up, but optional" — the TMDB token is only needed later, so ignore it for now. Anything marked as a failure will come with instructions.

**Come back and run `doctor` any time something behaves oddly.** It's the fastest way to find the cause.

---

## Step 8 — Look at your library

```bash
media-agent status
```

This reads your library and prints a summary — how many films, what resolutions, which codecs. **It changes nothing at all.** Run it as often as you like.

You'll see something like:

```
movies.json:  797 entries

By resolution:
  4K      : 23
  1080p   : 291
  720p    : 163
  480p    : 31
```

If it says `0 entries`, that's expected the first time — you haven't built the index yet. That's next.

---

## Step 9 — Build your movie index

```bash
media-agent rescan
```

This walks through your Movies folder, inspects every video file, and records what it finds in a file called `movies.json`.

**This does not touch your media files.** It only writes that one index file.

The first run is slow — it opens every single video. A large library can take a while, especially over a network drive. Later runs are much faster because it only looks at files it hasn't seen.

It will ask you to confirm before adding new files or removing entries for files you've deleted. Read the numbers, then type `yes`.

---

## Step 10 — Your first real change: cleaning up filenames

This is the first step that modifies actual files, so we do it carefully, in two stages.

### Stage one: look, don't touch

```bash
media-agent normalize --dry-run
```

`--dry-run` means **"show me what you would do, but don't do it."** Nothing on disk changes.

You'll see proposed renames:

```
The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv
  -> The Matrix (1999) [1080p].mkv

alien.1979.dvdrip.avi
  -> Alien (1979).avi
```

It also writes the full list to a file called `normalize_preview.txt` next to your library, so you can open it in Notepad and read through the whole thing properly.

### Stage two: read the preview

**Actually open that file and read it.** This is the most important habit with this tool. You're looking for anything that seems wrong — a film whose title got mangled, a year that looks incorrect.

If you spot problems, don't apply. See [Troubleshooting](TROUBLESHOOTING.md).

### Stage three: apply it

Happy? Then:

```bash
media-agent normalize --apply
```

It shows the plan once more and asks you to confirm. Type `yes`.

**Keep `normalize_preview.txt`.** If you ever need to work out what a file used to be called, that file is your map back. There's no undo button.

---

## Step 11 — TV shows

Same two-stage pattern. First build the index:

```bash
media-agent scan-tvshows
```

Then preview the reorganisation:

```bash
media-agent normalize-tv --dry-run
```

This restructures your TV folders into the shape Plex expects:

```
TV Shows\
└── Breaking Bad (2008)\
    ├── Season 01\
    └── Season 02\
```

It moves loose episode files into the right season folder and brings matching `.srt` subtitle files along with them.

> **It does not rename the episode files themselves** — only the folders around them. If your episodes are already named sensibly, they stay exactly as they are. This surprises people, so: unchanged episode filenames after running this is correct behaviour, not a failure.

Read the preview, then:

```bash
media-agent normalize-tv --apply
```

---

## Step 12 — Music

```bash
media-agent scan-music
media-agent organize-music --dry-run
```

Music is organised using the **tags** inside each file — the artist, album, and track information embedded in the MP3 or FLAC itself, which is what your music player displays. Files get moved into:

```
Music\
└── Pink Floyd\
    └── The Dark Side of the Moon (1973)\
        ├── 01 - Speak to Me.mp3
        └── 02 - Breathe.mp3
```

Two things to know:

- **Files with missing tags are not guessed at.** They're moved to a folder called `_NeedsTagging` so you can fix them yourself with a tag editor like [MusicBrainz Picard](https://picard.musicbrainz.org/). Nothing is lost.
- **This command deletes junk files.** By default that means: files literally named `desktop.ini`, `thumbs.db`, `folder.jpg`, `folder.jpeg`, `albumart.jpg`, or `albumart.jpeg`; any other file starting with `albumart` **and** ending in `.jpg`, `.jpeg`, `.png`, or `.bmp`; and any file ending in `.nfo`, `.m3u`, `.m3u8`, or `.lnk` — regardless of what it actually contains. The `albumart*`/`folder.*` names are often auto-generated filler, but they're also the exact names many people save their own hand-picked, high-resolution cover art under — media-agent can't tell the two apart by filename alone. If you've deliberately curated art under one of those names, either rename it first or check `dry-run`'s preview report before running `--apply`. The full, exact list (and how to change it) is in [Configuration → music](CONFIGURATION.md#music). This is the only place media-agent deletes anything, and your actual music files are never deleted.

Then:

```bash
media-agent organize-music --apply
```

---

## Step 13 (optional) — Perfect movie matching with TMDB

If Plex still mismatches some films, this fixes it properly by tagging each file with its official database ID.

You need a free TMDB account:

1. Sign up at [themoviedb.org/signup](https://www.themoviedb.org/signup).
2. Go to [Settings → API](https://www.themoviedb.org/settings/api), request a key, choose the **Developer** tier. It's free and instant.
3. Copy the **API Read Access Token** — the long one starting with `eyJ`. **Not** the shorter "API Key". Getting these two mixed up is the most common mistake here, and it fails with a confusing `401` error.
4. Add it to your config file as `tmdb_read_access_token`.

Check it took:

```bash
media-agent doctor
```

Then:

```bash
media-agent tmdb-enrich            # look up every film
media-agent tmdb-fix               # retry the ones that failed
media-agent tmdb-canonicalize --dry-run
media-agent tmdb-canonicalize --apply
```

You'll end up with filenames like `Highlander (1986) {tmdb-8009}.mkv`. That `{tmdb-8009}` tag tells Plex exactly which film this is, and it will never mismatch it again.

Some films won't match automatically — remakes, foreign titles, films with numbers in the name. `tmdb-enrich` marks those as *ambiguous* or *no match* rather than guessing. You can correct them by hand; see [Configuration](CONFIGURATION.md#tmdb_overrides_file).

---

## You're done

Finally, tell Plex to rescan its libraries, and it'll pick up the tidier layout.

From here:

- **[Commands](COMMANDS.md)** — every command in detail
- **[Configuration](CONFIGURATION.md)** — all the settings
- **[Troubleshooting](TROUBLESHOOTING.md)** — when things go wrong

### The habit worth keeping

Whenever you add new media, run the scan for that type, then preview, then apply:

```bash
media-agent rescan
media-agent normalize --dry-run     # read it
media-agent normalize --apply
```

Preview, read, apply. Every time.
