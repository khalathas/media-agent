# Media Agent — Independent Second-Pass Review

**Date:** 2026-08-24  
**Branch reviewed:** `feature/package-split` at `cbe60c5`  
**Baseline:** `main` at `cbd3f3d`  
**Input trust:** The coding-agent brief was treated only as a checklist of claims. Every disposition below was independently checked against the current code, documentation, tests, and Git state.  
**Verdict:** **Changes requested — not ready for public release.**

> **Continuation note:** This remains the second-pass review. The previously concurrent `doctor.py` work is now committed as `cbe60c5`, the coding agent stopped changing the tree, and that commit plus its new tests were included in the resumed review.

## Executive summary

The branch is substantially safer than the first-pass version. Path-based movie and episode identities, atomic JSON writes, filesystem containment checks, packaging, CI, and the expanded test suite are real improvements. All 132 tests pass, byte-compilation succeeds, and both wheel and source distributions build.

However, the supplied brief overstates completion. One original P1 release blocker remains open: the FFmpeg fallback scripts still execute unverified floating downloads. Six major correctness, safety, and documentation-alignment issues also remain. Most notably, TV conflict handling promises to move neither file but actually leaves the first move queued, and ambiguous movie-index migration can delete the only legacy index record without successfully replacing it.

## Verification performed

- Reviewed all current source modules, tests, scripts, packaging metadata, configuration examples, README, and files under `docs/`.
- Compared `main..feature/package-split` and inspected branch/commit state.
- Verified that the intended package, tests, examples, and metadata are tracked.
- Ran the complete suite in an independent runtime after the continuation update: **132 passed in 4.01 seconds**.
- Successfully byte-compiled `src/` and `tests/` under Python 3.12.
- Successfully built `media_agent-0.2.0.tar.gz` and `media_agent-0.2.0-py3-none-any.whl`.

## Release blocker

### P1: FFmpeg fallback installers still execute unverified downloads

**Locations:** `scripts/install_ffmpeg.ps1:45-72`; `scripts/install_ffmpeg.sh:93-121`.

Both scripts download mutable third-party archives without a pinned version, checksum, or signature, extract them, and execute `ffprobe`. The updated documentation discloses the risk and recommends package managers, but disclosure does not mitigate the executable supply-chain exposure for users who run the included scripts.

**Remediation:** Pin known versions and verify authoritative hashes or signatures before extraction or execution. Prefer removing these scripts if trustworthy artifact verification cannot be maintained.

## Major findings

### P2-1: TV conflicts move one file despite promising to move neither

**Locations:** `src/media_agent/tv.py:785-801`; `src/media_agent/tv.py:865-869`; `docs/COMMANDS.md:336`; `docs/TROUBLESHOOTING.md:235`.

When a second source claims an already-claimed destination, the code records a conflict but leaves the first source in `file_moves`. Apply therefore moves the first claimant. Runtime preview text and documentation explicitly promise that neither file is moved and conflicts remain exactly as-is.

The apply-time `os.path.exists` recheck is valuable and prevents overwriting an existing target, but it does not fulfill the stated two-claimant behavior.

**Remediation:** Group candidates by destination before queueing, as the music planner does, or remove the earlier claimant and all associated subtitle moves when a collision is detected. Add a regression test proving both sources and companions remain at their original paths.

### P2-2: Ambiguous movie migration can discard the legacy record without replacement

**Locations:** `src/media_agent/movies.py:53-66`, `:85`, `:105-129`.

Pathless legacy records with ambiguous basenames are dropped before probing is approved or succeeds. If the user declines probing, or if all probes fail, the modified index can still be persisted without the old record or replacement records. The brief's statement that ambiguous entries “are re-indexed per file” is conditional and therefore too strong.

**Remediation:** Preserve each legacy entry until all intended replacements have successfully probed. Make migration transactional, or explicitly require confirmation before removing legacy data. Add tests for declined probing, partial failure, and complete failure.

### P2-3: The default-preview documentation is false for `organize-music`

**Locations:** `src/media_agent/music.py:304-309`; `README.md:18`, `:133-140`; `docs/COMMANDS.md:20`; `tests/test_safety_contract.py:143-154`.

Project-level documentation says every mutating command previews by default and that a bare command is a harmless preview. A bare `organize-music` instead exits and writes no preview. The safety tests verify preview creation only with explicit `--dry-run`, so they do not enforce the headline contract.

**Remediation:** Prefer making a bare invocation generate the same preview as `--dry-run`. Otherwise revise README, command reference, CLI help, and tests consistently to state that `organize-music` requires an explicit mode.

### P2-4: TMDB credential handling remains inconsistent and insecure

**Locations:** `src/media_agent/doctor.py:81-83`, `:222-241`; `src/media_agent/tmdb.py:21-24`; `docs/TROUBLESHOOTING.md:305`.

Runtime TMDB calls and documentation give `TMDB_TOKEN` precedence, while `doctor` prefers the config token. Doctor can therefore validate a different credential from the one commands use. `init` also collects the token with visible `input()` and creates the config without explicitly restricting permissions.

**Remediation:** Centralize token resolution, use it everywhere, collect secrets with `getpass.getpass`, and create/chmod credential-bearing configuration to owner-only permissions where supported.

### P2-5: Archive extraction remains unhardened

**Locations:** `scripts/install_ffmpeg.ps1:55-66`; `scripts/install_ffmpeg.sh:102-118`.

In addition to lacking authenticity verification, the scripts extract untrusted archives without validating absolute paths, traversal components, links, member size, or expected structure.

**Remediation:** After cryptographic verification, inspect every archive member, reject unsafe paths and links, impose sensible limits, extract into an isolated temporary directory, and copy only expected binaries and license files.

### P2-6: Legacy TV fallback remains ambiguous within one show

**Locations:** `src/media_agent/tv.py:249-270`, `:1155-1159`.

New indexes correctly use path identity. Old pathless entries fall back to show plus basename, which still cannot distinguish `Show/720p/S01E01.mkv` from `Show/1080p/S01E01.mkv`. One cached record can supply incorrect probe data or `_season` to both new entries.

**Remediation:** Reuse legacy metadata only where show-plus-basename is unique. Re-probe ambiguous groups. This preserves the fast migration for ordinary libraries without silently accepting known ambiguity.

## Minor findings and documentation mismatches

### P3-1: Language-tagged subtitles are still not moved

**Locations:** `src/media_agent/tv.py:211-231`, `:803-806`; `README.md:212`; `docs/GETTING-STARTED.md:271`.

Scanning recognizes names such as `episode.en.srt`, but normalization uses exact video-stem equality and moves only `episode.srt`. Documentation broadly promises matching `.srt` companions are moved.

**Remediation:** Reuse the subtitle-discovery matcher in normalization and test both plain and language-tagged companions.

### P3-2: Loose audio files at the music root are silently ignored

**Locations:** `src/media_agent/music.py:323-328`; `tests/conftest.py:72-74`; broad behavior descriptions in `README.md:30` and `docs/COMMANDS.md:161`.

The organizer iterates only root-level directories. Audio files directly inside the configured music root are neither organized nor explicitly reported. The test fixture works around this by placing tracks under a directory.

**Remediation:** Process root-level audio files, or clearly document and diagnose this limitation.

### P3-3: Movie previews are not a reliable map back for duplicate basenames

**Locations:** `src/media_agent/movies.py:194-197`; claims in `README.md:272` and `docs/TROUBLESHOOTING.md:346`.

The preview records basenames rather than source-relative paths. With duplicate basenames, it cannot unambiguously identify which file moved, contrary to the “map back” language.

**Remediation:** Record source and destination paths relative to the library root in every preview.

### P3-4: Case-only renames remain skipped or misleading on Windows

**Locations:** `src/media_agent/movies.py:224-227` and analogous TMDB rename paths.

On a case-insensitive filesystem, `os.path.exists(destination)` is true for a case-only rename, so the operation is reported as an existing-target skip.

**Remediation:** Detect same-file/case-only targets and perform a two-step temporary rename where necessary.

### P3-5: CI does not cover all advertised platforms and versions

**Locations:** `.github/workflows/ci.yml:14-15`; classifiers in `pyproject.toml`.

CI covers Windows/Linux and Python 3.10/3.12, while metadata advertises macOS and Python 3.11. CI also does not build artifacts or test installation from the wheel.

**Remediation:** Add macOS and Python 3.11 coverage plus build, artifact inspection, wheel installation, and CLI smoke tests.

### P3-6: Remaining maintenance items

- `_rebuild_low_res` still writes its CSV non-atomically.
- TV probing still duplicates ffprobe logic rather than sharing `probe.py`.

These are lower risk than the findings above but should remain visible in the backlog.

## Claim-verification matrix

| Original/brief claim | Independent disposition |
|---|---|
| P1-1 explicit `--apply` guards fixed | **Verified fixed.** TMDB guards now prevent bare/`--yes` mutation. |
| P1-2 movie path identity fixed | **Core fix verified.** Duplicate identities no longer collapse; ambiguous migration has the new P2-2 failure mode. |
| P1-3 TV path identity fixed | **Core fix verified for pathful indexes.** Legacy ambiguous fallback remains P2-6. |
| P1-4 FFmpeg risk mitigated | **Still open as a release blocker.** Documentation warns; scripts remain unsafe. |
| P1-5 package represented in Git | **Verified fixed.** Intended files are committed on the feature branch. |
| P2-1 JSON writes atomic | **Verified fixed for JSON.** Low-resolution CSV is an acknowledged exception. |
| P2-2 bare section resolution fixed | **Verified fixed.** Relative values anchor under `library_root`. |
| P2-3 music containment fixed | **Verified fixed.** Config validation and apply-time containment both exist. |
| P2-4 three-way music collisions fixed | **Verified fixed.** Grouping, reservation, and unbounded suffixing are present. |
| P2-5 secure token entry | **Not fixed.** Visible input and ordinary file permissions remain. |
| P2-6 archive hardening | **Not fixed.** |
| P2-7 destructive-operation coverage | **Materially improved.** Mutation guards are meaningful; gaps identified above remain. |
| P3-1 obsolete entry-point guidance fixed | **Mostly fixed.** Installed-user docs are corrected. |
| P3-2 language-tagged subtitle move fixed | **Not fixed.** |
| P3-3 token precedence fixed | **Not fixed.** |
| P3-4 public scaffolding fixed | **Partially fixed.** CONTRIBUTING and CI exist; platform/build/security/release coverage is incomplete. |
| TV collision fix makes both claimants stay put | **Disproven.** First claimant remains queued. |
| `normalize-tv --dry-run` fixed | **Verified fixed.** |
| Safety tests cover all five commands | **Verified.** They are non-vacuous for mutation guards. |
| Fresh-install configuration failures fixed | **Verified by code/tests; suite passes.** |
| Every destructive command writes a preview | **True with explicit `--dry-run`; false for bare `organize-music` under the documented default-preview contract.** |
| Pip guidance no longer assumes source scripts | **Verified improved.** Fallback scripts are correctly framed as source-checkout-only. |
| Music delete documentation corrected | **Verified improved.** Cover-art and junk deletion are now disclosed. |

## Positive observations

- Path-based movie and episode identities are substantial correctness improvements.
- Apply-time destination rechecks now protect movie, TV, TMDB, and music operations from overwriting an already-existing target.
- Atomic JSON replacement is implemented cleanly.
- Music path containment is checked at configuration time and again immediately before mutation.
- The expanded safety tests exercise real temporary libraries and include a non-vacuity assertion that apply actually changes something.
- Packaging now produces both wheel and source distributions successfully.
- Documentation is much more accessible for non-technical users and now honestly describes music-junk deletion and the FFmpeg fallback risk.
- The `cbe60c5` first-run update correctly catches likely section-folder mistakes, handles quoted Windows paths and interrupted input, surfaces missing ffprobe earlier, removes obsolete entry-point guidance from `init`, and confines its new tests to a redirected home directory.

## Release recommendation

Do not publish this branch yet. Resolve the FFmpeg installer exposure and all six major findings, add focused regression tests for TV two-claimant conflicts and failed/declined movie migration, then repeat a narrow third-pass review against the exact release commit. Minor documentation mismatches should also be corrected before presenting the tool as safe for non-technical users.
