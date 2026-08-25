# Third-party licenses

media-agent's own source code is licensed MIT — see [LICENSE](LICENSE). That license covers only the code in this repository.

Installing media-agent also installs its dependencies, each under its own license, unmodified and used as an ordinary Python import. This is the complete runtime install — the two direct dependencies media-agent declares, plus every package pip pulls in transitively along with them (confirmed with `pip show`, not assumed):

| Package | License | Direct or transitive |
|---|---|---|
| [mutagen](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later | Direct — reads and writes audio file tags |
| [requests](https://github.com/psf/requests) | Apache-2.0 | Direct — HTTP calls to the TMDB API |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | Transitive (via requests) |
| [charset-normalizer](https://github.com/Ousret/charset_normalizer) | MIT | Transitive (via requests) |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause | Transitive (via requests) |
| [urllib3](https://github.com/urllib3/urllib3) | MIT | Transitive (via requests) |

`mutagen` has no further dependencies of its own. None of the transitive packages carry copyleft terms — mutagen's GPL-2.0-or-later is the only one that does.

This file is a disclosure of what's installed, not a legal opinion on how the licenses interact. If you plan to redistribute media-agent bundled with its dependencies (rather than having users `pip install` them separately), or to build a derivative product from this project, get your own advice on what that requires — in particular, `mutagen`'s GPL terms may impose obligations on a bundled, combined distribution that don't apply to media-agent's own MIT-licensed code in isolation.

## FFmpeg

media-agent does not bundle, vendor, or redistribute FFmpeg or ffprobe — this repository and its installable package contain no FFmpeg binary. media-agent looks for `ffprobe` already on your system, and `scripts/install_ffmpeg.*` can optionally fetch a build directly onto **your own machine**, at your request, when you choose to run the script:

| Platform | Source | Build | License |
|---|---|---|---|
| Windows | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) | `essentials_build`, pinned version | **GPLv3** |
| Linux | [johnvansickle.com](https://johnvansickle.com/ffmpeg/) | static builds, pinned version | **GPLv3** |

Both licenses confirmed directly from each site's own licensing statement, not assumed. Gyan Doshi and John Van Sickle are independent third-party build providers, not the official FFmpeg project — the installer scripts name them explicitly (`scripts/install_ffmpeg.ps1`, `scripts/install_ffmpeg.sh`) so you know exactly where the binary is coming from. **These are not necessarily the same builds a package manager gives you** — `winget`, `brew`, and `apt` each have their own build and distribution pipeline, which may differ from either of the sources above; do not assume they're interchangeable.

Like the dependency table further up, this is a disclosure of what the scripts fetch and from where, not a legal opinion on what running them means for you or for anything you build using media-agent.
