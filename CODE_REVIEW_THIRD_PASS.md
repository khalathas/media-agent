# Media Agent — Independent Third-Pass Review

**Date:** 2026-08-25  
**Branch:** `feature/package-split`  
**Reviewed commit:** `b57e8f2f30cc10d48f7aae0bbb22c7c2af6d32aa`  
**Second-pass baseline:** `cbe60c5`  
**Input trust:** The coding-agent brief was treated only as a checklist. Claims were independently checked against code, tests, scripts, documentation, packaging, Git state, and live pinned artifacts.  
**Verdict:** **Changes requested — not ready for public release.**

## Executive summary

The third-pass changes resolve most second-pass findings substantively. Movie migration, legacy TV identity, bare-command preview behavior, TMDB token resolution, ordinary language-tagged subtitle moves, loose-root music diagnosis, normalized movie previews, case-only rename handling on the success path, CI coverage, and the core FFmpeg pin/checksum work are real improvements.

The branch still has one licensing release blocker and four major issues. Two are new correctness defects in the fixes themselves:

- A conflicting TV episode can remain in place while its companion subtitle is moved away.
- A failed case-only rename can strand a media file under an internal temporary filename.

The primary installation guide also retains an undocumented Git dependency, and its FFmpeg warning now contradicts the repaired scripts.

### Finding count

| Priority | Count | Meaning |
|---|---:|---|
| P0 | 0 | Critical/immediate |
| P1 | 1 | Release blocker |
| P2 | 4 | Major; fix before public release |
| P3 | 5 | Lower-risk correctness, security, and documentation gaps |

## P1 — Release blocker

### P1-1: Public licensing posture remains unresolved

**Locations:** `pyproject.toml:11`, `:18`, `:29-32`; `LICENSE`; absence of a third-party notices/dependency-license inventory.

The package presents media-agent as MIT while requiring Mutagen, whose upstream distribution is GPL-2.0-or-later. The repository does not yet document an audited dependency-license inventory or the intended licensing treatment of the combined distributable application.

This is especially important before the first public distribution, while the owner can still choose a clean licensing model. Recording the concern in `ROADMAP.md` does not resolve the current release.

**Remediation:** Obtain focused licensing advice, explicitly choose the Python distribution's license, audit direct and transitive dependencies, and add third-party notices. Do not represent the complete distributable application as unambiguously permissive/MIT until this is settled.

## P2 — Major findings

### P2-1: A conflicted TV episode can still have its subtitle moved

**Locations:** `src/media_agent/tv.py:641-680`, `:883-907`.

`_claim_destination` now retracts the first video claimant when a second video targets the same destination. Video and subtitle destinations are claimed independently, however. If only the first duplicate episode has a matching subtitle, the second video retracts/poisons the video move while the first subtitle move remains queued.

Apply can therefore keep both videos in their original locations but move one video's `.srt` into `Season NN`. This contradicts the preview and conflict promise that the conflicting files remain unchanged.

**Remediation:** Plan each episode and all companion files as one operation group. Retracting or poisoning a video claim must retract its associated subtitle claims. Add regressions with duplicate videos where only one has `episode.srt` and `episode.en.srt`, for both preview and apply.

### P2-2: Case-only rename failure can strand media under a temporary name

**Locations:** `src/media_agent/probe.py:128-143`; callers in `src/media_agent/movies.py:261-278` and `src/media_agent/tmdb.py:397-415`, `:516-534`.

`rename_case_only` first renames the media file to `*.case-rename-tmp` and then performs the final rename. If the second operation fails, no rollback restores the original name. Callers catch the exception, but the index still refers to the old path while the real media remains at the temporary path.

Network shares, antivirus, permissions changes, or transient filesystem errors can trigger this partial operation.

**Remediation:** If the final rename fails, make a best-effort rollback from the temporary path to the original path. Report both the original and rollback errors if recovery also fails. Add failure-injection tests for the second move and rollback failure.

### P2-3: Clean-machine installation still has an undocumented Git prerequisite

**Locations:** `README.md:44-78`; `docs/GETTING-STARTED.md:32-53`.

The primary command remains `pip install git+https://...`, which requires a working Git executable. The prerequisites explain Python and FFmpeg but do not install or verify Git. A non-technical user can follow every documented prerequisite and still fail at the main installation step.

**Remediation:** Until PyPI, wheel, or ZIP distribution is available, document platform-specific Git installation and a `git --version` check. Prefer interpreter-bound `python -m pip`/`python3 -m pip` commands over bare `pip`.

### P2-4: Documentation falsely says the FFmpeg scripts lack checksum verification

**Locations:** `README.md:69`; `docs/TROUBLESHOOTING.md:143`; implementation in `scripts/install_ffmpeg.ps1:37-173` and `scripts/install_ffmpeg.sh:39-216`.

The user-facing text still warns that the fallback scripts download builds without verifying a checksum. The code now pins versions and checks hashes before extraction. This is a direct code/documentation contradiction at a security-sensitive step.

**Remediation:** Explain the actual distinction: the scripts now use pinned checksum verification, but package-manager signatures remain preferable. Disclose that the Unix fallback uses upstream MD5 rather than a signature or modern digest.

## P3 — Lower-priority findings

### P3-1: TMDB previews do not satisfy the global “map back” promise

**Locations:** `src/media_agent/tmdb.py:354-367`, `:475-485`; `README.md:272`; `docs/TROUBLESHOOTING.md:346`.

The ordinary movie-normalization preview now records relative path identities, but the TMDB preview files still record bare filenames. Duplicate basenames remain ambiguous in documents described as a reliable map back.

**Remediation:** Record library-relative source and destination paths in TMDB previews, or narrow the global documentation promise.

### P3-2: Secure POSIX config creation has a permissions exposure window

**Location:** `src/media_agent/doctor.py:263-278`.

`init` writes the token-bearing file using ordinary `open()` and applies `chmod(0600)` afterward. Under a permissive umask, the token can briefly exist with broader permissions.

**Remediation:** Create the file owner-only from the outset on POSIX, then retain post-write chmod as defense in depth.

### P3-3: Getting Started understates cover-art deletion

**Location:** `docs/GETTING-STARTED.md:303`; clearer descriptions in `README.md:38`, `:270`.

Calling the files “leftover album art” suggests they are disposable, although intentionally maintained `folder.jpg` and `AlbumArt*.jpg` files are deleted by default.

**Remediation:** Reuse the README's explicit warning, direct the user to `organize_music_preview.txt`, and explain how to change `music.junk_patterns` before applying.

### P3-4: Archive hardening is not comprehensive

**Locations:** `scripts/install_ffmpeg.sh:85-123`, `:209-216`; `scripts/install_ffmpeg.ps1:57-129`.

Traversal and absolute paths are rejected, and pinned hashes substantially reduce practical exposure. The Unix archive inspection does not reject symbolic/hard links or impose member/expanded-size limits; neither implementation validates a strict expected manifest or expansion limits.

**Remediation:** Validate member types and expected top-level structure before extraction, reject unsafe links, and impose reasonable file-count and expanded-size limits.

### P3-5: Installer tests mistake an unusable `bash.exe` for a working shell

**Location:** `tests/test_ffmpeg_installer_safety.py` bash availability detection and bash-marked tests.

The tests use executable discovery to decide whether bash is available. In the independent Windows sandbox, `bash.exe` resolved to a WSL launcher that could not execute and returned `E_ACCESSDENIED`; six tests failed during shell startup rather than being skipped. GitHub-hosted Windows runners may provide a functional Git Bash, so this is not evidence that the installer is broken, but shell capability detection is fragile.

**Remediation:** Probe bash with a harmless command during test setup and skip with a clear reason when it cannot actually execute. Keep Linux/macOS CI as the authoritative shell-script execution environments.

## Claim-verification matrix

| Third-pass brief claim | Independent disposition |
|---|---|
| FFmpeg floating/unverified download fixed | **Core fix verified.** All four live pinned artifacts independently downloaded and matched their hardcoded hashes. Broader archive hardening remains P3-4. |
| TV conflict keeps both claimants unchanged | **Video-only behavior fixed; incomplete for companion subtitles.** P2-1 remains. |
| Ambiguous movie migration fixed | **Verified fixed.** Legacy entries survive unless pathful replacements are produced. |
| Bare `organize-music` previews | **Verified fixed.** |
| TMDB token resolution unified | **Verified fixed.** Doctor and runtime use `resolve_tmdb_token`; environment precedence matches documentation. Hidden TTY input and POSIX chmod are present. |
| Archive traversal fixed | **Verified for absolute/`..` paths.** Link/type/expansion defenses remain incomplete. |
| Ambiguous legacy TV fallback fixed | **Verified by mechanism and tests.** Ambiguous basename groups force fresh probes. |
| Language-tagged subtitles move | **Fixed on the ordinary path.** Conflict grouping remains P2-1. |
| Loose music-root files no longer silent | **Verified fixed as diagnosis.** They are reported, previewed, and preserved. |
| Movie preview identity fixed | **Verified for `normalize`.** TMDB previews remain ambiguous. |
| Case-only renames fixed | **Success path fixed.** Failure recovery remains P2-2. |
| CI coverage and wheel smoke fixed | **Verified configured.** Matrix includes Windows/Linux/macOS and Python 3.10/3.11/3.12; wheel build/install/smoke steps exist. |
| Non-atomic low-resolution CSV accepted | **Still open and accepted as low-risk.** |
| Duplicated TV ffprobe logic accepted | **Still open as a maintenance issue.** |

## Verification results

- Git tree was clean at review start.
- Reviewed `feature/package-split` at `b57e8f2`; the brief named `c36f2ec`, followed only by a chore commit ignoring the brief.
- Diff since `cbe60c5`: 23 files, 1,844 insertions, 238 deletions.
- Python byte-compilation succeeded.
- Wheel and source distributions built successfully.
- Test run: **180 passed, 6 failed**. All six failures were shell-launch failures caused by an unusable Windows/WSL `bash.exe`, not application assertions. The remaining installer safety tests, including the PowerShell helpers, passed.
- Independently downloaded and verified every pinned artifact:
  - Windows FFmpeg 9.0.1 essentials ZIP: SHA-256 matched `fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9`.
  - Linux FFmpeg 7.0.2 amd64: MD5 matched `7fa72b652e19bf84c9461e332ea1cdf3`.
  - Linux FFmpeg 7.0.2 arm64: MD5 matched `807afe21601db0a73e426121c7d636ea`.
  - Linux FFmpeg 7.0.2 armhf: MD5 matched `bd0c3d4821a5ddce1a4339cd00031e38`.

The successful hashes prove that the committed pins currently match the live artifacts. They do not replace an ongoing provenance, update, and licensing process.

## Positive observations

- Most second-pass fixes are substantive and integrated into real call paths.
- FFmpeg downloads now fail closed when hashes mismatch.
- TV video-destination poisoning correctly handles third and later video claimants.
- Migration fixes preserve legacy data instead of favoring a superficially clean schema.
- Token resolution is centralized and token entry is hidden on a real terminal.
- CI now matches the advertised Python versions and operating systems and tests a built wheel.
- New regression tests generally exercise behavior rather than merely checking helper existence.

## Release recommendation

Do not publish this snapshot. Resolve the licensing posture, TV companion-conflict grouping, case-only rollback, and the two installation-documentation problems. Then rerun the complete suite and artifact build on the exact release commit and perform a narrow fourth pass over those changes.
