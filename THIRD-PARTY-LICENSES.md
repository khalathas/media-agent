# Third-party licenses

media-agent's own source code is licensed MIT — see [LICENSE](LICENSE). That license covers only the code in this repository.

Installing media-agent also installs its runtime dependencies, each under its own license, unmodified and used as an ordinary Python import:

| Package | License | Project |
|---|---|---|
| [mutagen](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later | Reads and writes audio file tags |
| [requests](https://github.com/psf/requests) | Apache-2.0 | HTTP calls to the TMDB API |

This file is a disclosure of what's installed, not a legal opinion on how the licenses interact. If you plan to redistribute media-agent bundled with its dependencies (rather than having users `pip install` them separately), or to build a derivative product from this project, get your own advice on what that requires — in particular, `mutagen`'s GPL terms may impose obligations on a bundled, combined distribution that don't apply to media-agent's own MIT-licensed code in isolation.

## FFmpeg

media-agent does not bundle FFmpeg or ffprobe. It looks for them on your system, and `scripts/install_ffmpeg.*` can optionally fetch a build for you — see those scripts' own headers for the pinned version, source, and license of the specific build they download.
