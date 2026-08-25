# Third-party licenses

media-agent's own source code is licensed MIT — see [LICENSE](LICENSE). That license covers only the code in this repository.

Installing media-agent also installs its runtime dependencies, each under its own license, unmodified and used as an ordinary Python import:

| Package | License | Project |
|---|---|---|
| [mutagen](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later | Reads and writes audio file tags |
| [requests](https://github.com/psf/requests) | Apache-2.0 | HTTP calls to the TMDB API |

This file is a disclosure of what's installed, not a legal opinion on how the licenses interact. If you plan to redistribute media-agent bundled with its dependencies (rather than having users `pip install` them separately), or to build a derivative product from this project, get your own advice on what that requires — in particular, `mutagen`'s GPL terms may impose obligations on a bundled, combined distribution that don't apply to media-agent's own MIT-licensed code in isolation.

## FFmpeg

media-agent does not bundle, vendor, or redistribute FFmpeg or ffprobe — this repository and its installable package contain no FFmpeg binary. media-agent looks for `ffprobe` already on your system, and `scripts/install_ffmpeg.*` can optionally fetch a build directly onto **your own machine** at your request, from the same official sources you'd reach via a package manager:

| Platform | Source | Build | License |
|---|---|---|---|
| Windows | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) | `essentials_build`, pinned version | **GPLv3** |
| Linux | [johnvansickle.com](https://johnvansickle.com/ffmpeg/) | static builds, pinned version | **GPLv3** |

Both confirmed directly from each site's own licensing statement, not assumed. The installer script headers (`scripts/install_ffmpeg.ps1`, `scripts/install_ffmpeg.sh`) document the pinned version, source URL, and checksum-verification method — not the license; that's here instead.

Because the binary is fetched onto your machine at your own request rather than shipped inside media-agent's package, this is the same posture as installing ffmpeg via `winget`/`brew`/`apt` yourself — media-agent is not the one distributing it. If you build something that *does* bundle or redistribute one of these binaries (rather than fetching it per-install), GPLv3's terms apply to that distribution and are a different situation than running the installer script locally.
