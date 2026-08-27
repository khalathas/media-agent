# Configuration

`media-agent init` writes a config file for you, so you may never need this page. It's here for when you want to change something.

The config is a JSON file. **The only field you must set is `library_root`.** Everything else has a sensible default, and `null` means "use the default".

---

## Where the config lives

media-agent uses the first one of these it finds:

1. `--config PATH` — works with every command
2. the `MEDIA_AGENT_CONFIG` environment variable
3. `./media_agent_config.json` — the folder your terminal is currently in
4. `~/.config/media-agent/config.json`
5. `~/media_agent_config.json`

`~` means your home folder (`C:\Users\YourName` on Windows).

`media-agent status` prints which config is in use, under `── Configuration ──`.

---

## The smallest possible config

```json
{
  "library_root": "D:/Plex Media Library"
}
```

That's genuinely enough, provided your library looks like this:

```
D:\Plex Media Library\
├── Movies\
├── TV Shows\
└── Music\
```

> **Windows paths:** use forward slashes (`D:/Plex Media Library`). Backslashes have a special meaning in JSON and need doubling (`D:\\Plex Media Library`), which is easy to get wrong. Forward slashes work fine on Windows.

---

## Every field

### `library_root`

**Required.** The one folder containing your section folders.

```json
"library_root": "D:/Plex Media Library"
```

Network paths work: `//nas/Public/Plex Media Library`, or a mapped drive like `Y:/Plex Media Library`.

---

### `sections`

Only needed if your folders **aren't** called `Movies`, `TV Shows`, and `Music`.

```json
"sections": {
  "movies":   null,
  "tv_shows": null,
  "music":    null
}
```

`null` means "the default name inside `library_root`". A full path is used exactly as given, which lets a section live on a different drive entirely:

```json
"sections": {
  "movies":   "E:/Films",
  "tv_shows": null,
  "music":    "//nas/Music"
}
```

---

### `indexes_dir`

Where the generated index files (`movies.json`, `tvshows.json`, `music.json`, and the low-resolution CSVs) are written.

```json
"indexes_dir": null
```

`null` puts them alongside your library. Set it if your library is on a slow network share and you'd rather keep the indexes local, or if the library is read-only.

Setting this **does not** change where your media is read from — only where the index files go.

---

### `reports_dir`

Where the `--dry-run` preview files are written (`normalize_preview.txt`, `tmdb_rename_preview.txt`, `probe_failures.txt`, and so on).

```json
"reports_dir": null
```

`null` means alongside your library. Note this follows `library_root`, **not** `indexes_dir` — so if you move your indexes elsewhere and want the previews to follow them, set this as well.

These files are your record of what changed. Keep them somewhere you'll find them.

---

### `ffprobe_path`

```json
"ffprobe_path": null
```

`null` means find it automatically. media-agent looks, in order:

1. `ffprobe` on your system PATH
2. this `ffprobe_path` value
3. `vendor/ffmpeg/bin/` relative to a source checkout or your current folder — where `scripts/install_ffmpeg.*` puts it. This only applies if you have the project source; a `pip install` has no such folder.

Only set this if `media-agent doctor` reports it can't find ffprobe.

```json
"ffprobe_path": "C:/ffmpeg/bin/ffprobe.exe"
```

---

### `tmdb_read_access_token`

Needed only for the four `tmdb-*` commands.

```json
"tmdb_read_access_token": ""
```

This is TMDB's **API Read Access Token** — the long one starting with `eyJ`, not the shorter "API Key". See [TMDB setup](../README.md#tmdb-setup).

The `TMDB_TOKEN` environment variable takes priority over this field, which is handy if you'd rather not keep a token in a file.

Placeholder values (`""`, `YOUR_TOKEN_HERE`, `READ_ACCESS_TOKEN_HERE`) are all treated as "not set", so you get a clear message rather than a confusing `401` from TMDB.

> **Keep your token out of git.** The default `.gitignore` already excludes `media_agent_config.json` for exactly this reason.

---

### `skip_shows`

TV show folders that `normalize-tv` should leave completely alone.

```json
"skip_shows": ["gundam", "some weird folder"]
```

Case-insensitive, matched against the folder name. Empty by default.

Useful for a show with an unusual structure you've arranged deliberately and don't want reorganised — a franchise with multiple series sharing one folder, for instance.

---

### `tmdb_overrides_file`

Manual fixes for films TMDB gets wrong.

```json
"tmdb_overrides_file": "my-tmdb-fixes.json"
```

A relative path is resolved **against the config file's own folder**, so a config and its overrides can travel together. Absolute paths work too. If the file is missing you get a warning, not a crash.

The file looks like this — see [`examples/tmdb-overrides.example.json`](../examples/tmdb-overrides.example.json):

```json
{
  "corrections": {
    "Highlander (1992).mkv": 8009,
    "Gremlins 1.mp4": 927
  },
  "misspellings": {
    "terrabithia": "terabithia",
    "lightning theif": "lightning thief"
  }
}
```

**`corrections`** map an exact filename (as it appears on disk) to the correct TMDB id. Use this when the lookup confidently picked the *wrong* film. Find the id in the TMDB page URL — `themoviedb.org/movie/8009-highlander` means `8009`.

**`misspellings`** repair typos in your filenames before searching. Lowercase fragment to replacement. Use this when a lookup finds nothing because the filename is misspelled.

Apply them with `media-agent tmdb-fix`.

Any key starting with `_` is ignored, so you can leave yourself comments — JSON doesn't otherwise allow them.

---

### `music`

Where `organize-music` puts files it can't file confidently.

```json
"music": {
  "needs_tagging_dir": "_NeedsTagging",
  "collisions_dir":    "_NeedsTagging/_Collisions"
}
```

| Key | Purpose |
|---|---|
| `needs_tagging_dir` | Files missing the artist/album/title tags needed to file them. Never guessed at. |
| `collisions_dir` | Files that would have overwritten another file. Nothing is overwritten. |

You can also override which files count as junk (and therefore get deleted):

```json
"music": {
  "junk_names":    ["desktop.ini", "thumbs.db", "folder.jpg"],
  "junk_patterns": ["\\.nfo$", "\\.m3u8?$"]
}
```

Defaults are `desktop.ini`, `thumbs.db`, `folder.jpg`, `folder.jpeg`, `albumart.jpg`, `albumart.jpeg`, plus patterns matching `albumart*.{jpg,jpeg,png,bmp}`, `.nfo`, `.m3u`/`.m3u8`, and `.lnk`.

`junk_patterns` are regular expressions, matched case-insensitively. **If you're not sure, leave these alone** — this is the one list that controls deletion.

---

### File extensions

Which files count as video or audio.

```json
"video_exts": [".mkv", ".mp4", ".avi"],
"music_exts": [".mp3", ".flac", ".m4a"]
```

Defaults cover the usual formats: video `.mkv .mp4 .avi .mov .m4v .wmv .flv .ts .m2ts .mpg .mpeg .vob .divx .webm .ogm`, audio `.mp3 .flac .m4a .mp2 .ogg .wma .aac .wav`.

Setting these **replaces** the defaults rather than adding to them, so list everything you want.

---

## A worked example

A library with non-standard folder names, indexes kept on a local disk for speed, one show excluded, and TMDB configured:

```json
{
  "library_root": "//nas/Public/Media",

  "sections": {
    "movies":   "//nas/Public/Media/Films",
    "tv_shows": "//nas/Public/Media/Series",
    "music":    null
  },

  "indexes_dir": "C:/Users/me/media-indexes",
  "reports_dir": "C:/Users/me/media-indexes/reports",

  "tmdb_read_access_token": "eyJhbGciOiJIUzI1NiJ9...",
  "tmdb_overrides_file": "my-tmdb-fixes.json",

  "skip_shows": ["gundam"]
}
```

Here `music` is `null`, so it defaults to `//nas/Public/Media/Music`.

---

## Checking your config

```bash
media-agent doctor
```

Confirms every path exists and everything resolves. Run it after any edit.

If your JSON has a syntax error — a missing comma is the usual culprit — media-agent tells you the line number rather than crashing.

---

## See also

- [Getting Started](GETTING-STARTED.md)
- [Commands](COMMANDS.md)
- [Troubleshooting](TROUBLESHOOTING.md)
