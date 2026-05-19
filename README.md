# media-agent

A command-line tool for maintaining a Plex media library. Scans movies, TV shows, and music; builds searchable JSON indexes; normalises filenames to Plex conventions; enriches movie metadata via the TMDB API; and organises music into Artist/Album/Track structure.

Tested on Windows. Linux and macOS are supported but less battle-tested.

---

## Install

**1. Python dependencies**

```bash
pip install -r requirements.txt
```

**2. ffmpeg / ffprobe**

ffprobe is required for the video scanning commands (`rescan`, `scan-tvshows`). If you already have ffmpeg installed system-wide, media-agent will detect it automatically. Otherwise run the installer for your platform:

```powershell
# Windows
.\scripts\install_ffmpeg.ps1
```

```bash
# macOS / Linux
bash scripts/install_ffmpeg.sh
```

The installer checks your PATH first and does nothing if ffprobe is already present. If not found, it installs to `vendor/ffmpeg/` inside the repo (auto-detected at runtime, no config change needed).

---

## Quick start

```bash
# 1. Create your config file interactively
python media_agent.py init

# 2. Verify everything is working
python media_agent.py doctor

# 3. See your library stats
python media_agent.py status
```

---

## Commands

| Command | Description |
|---|---|
| `init` | Interactive first-run setup — creates `~/.config/media-agent/config.json` |
| `doctor` | Health check: ffprobe, mutagen, library paths, TMDB token |
| `status` | Library stats and index health summary |
| `rescan` | Scan disk and reconcile `movies.json` + `movies_below_720p.csv` |
| `rebuild-lowres` | Rebuild `movies_below_720p.csv` from current `movies.json` |
| `normalize` | Preview or apply filename normalizations for movies |
| `normalize-tv` | Normalize TV show folder structure to Plex standards |
| `scan-tvshows` | Scan TV Shows folder and build `tvshows.json` |
| `scan-music` | Scan Music folder, read ID3/mutagen tags, build `music.json` |
| `organize-music` | Organize Music into Plex `Artist/Album/Track` structure |
| `tmdb-enrich` | Look up movies via TMDB API, save results to `movies_tmdb.json` |
| `tmdb-fix` | Apply manual corrections + re-search no-match entries |
| `tmdb-canonicalize` | Rename files to canonical TMDB `Title (Year)` format |
| `tmdb-rename` | Add `{tmdb-XXXXX}` suffix to filenames from `movies_tmdb.json` |

Most commands that modify files accept `--dry-run` (preview only) and `--apply` (execute). Use `--yes` / `-y` to skip confirmation prompts.

---

## Configuration

Config is loaded from the first location found:

1. `--config PATH` CLI flag
2. `MEDIA_AGENT_CONFIG` environment variable
3. `./media_agent_config.json` (current directory)
4. `~/.config/media-agent/config.json`
5. `~/media_agent_config.json`

Copy `media_agent_config.EXAMPLE.json` to get started, or run `media-agent init`.

### Config fields

| Field | Required | Default | Description |
|---|---|---|---|
| `library_root` | **yes** | — | Root path of your Plex Media Library |
| `sections.movies` | no | `{library_root}/Movies` | Path to your Movies folder |
| `sections.tv_shows` | no | `{library_root}/TV Shows` | Path to your TV Shows folder |
| `sections.music` | no | `{library_root}/Music` | Path to your Music folder |
| `indexes_dir` | no | `library_root` | Where to write `movies.json`, `tvshows.json`, `music.json` |
| `reports_dir` | no | `library_root` | Where to write preview/report text files |
| `ffprobe_path` | no | auto-detected | Explicit path to `ffprobe` binary (only needed if not on PATH and not using the vendor install) |
| `tmdb_read_access_token` | no | — | TMDB v4 Read Access Token for `tmdb-*` commands |
| `music.needs_tagging_dir` | no | `_NeedsTagging` | Subfolder name for tracks missing required tags |
| `music.collisions_dir` | no | `_NeedsTagging/_Collisions` | Subfolder name for filename collisions during organize |

`null` values use their defaults. `library_root` is the only required field.

---

## TMDB token setup

The `tmdb-*` commands require a free TMDB API Read Access Token:

1. Create an account at https://www.themoviedb.org/signup
2. Request an API key at https://www.themoviedb.org/settings/api
   (choose "Developer" tier — free, instant approval for personal use)
3. Copy the **API Read Access Token** (the long v4 token, NOT the short v3 key)
4. Add it to your config as `"tmdb_read_access_token"`, or set the environment variable `TMDB_TOKEN=<token>`

Run `python media_agent.py doctor` to verify the token is valid.

---

## Caveats

- Developed and tested on Windows with a Synology NAS. Linux and macOS are supported via `install_ffmpeg.sh` and cross-platform path handling, but have had less real-world testing.
- The tool operates directly on your filesystem. Always use `--dry-run` before `--apply` on a new installation.
- Indexes (`movies.json`, `tvshows.json`, `music.json`) are generated files and are not committed to source control.

---

## License

MIT — see [LICENSE](LICENSE).
