# media-agent Roadmap

> This roadmap records possible product directions and release improvements. It is separate from code-review findings and does not represent committed scope, dates, or implementation decisions. Security defects and release blockers remain tracked in the applicable code-review documents.

## Near Term — Python CLI Distribution

### Release packaging

- [ ] Publish versioned downloadable ZIP archives for users who do not want to clone the repository.
- [ ] Include clear extraction, installation, launch, upgrade, and uninstall instructions.
- [ ] Consider publishing wheel and source distributions to PyPI.
- [ ] Evaluate `pipx` as the recommended installation method for an isolated, user-friendly CLI install.
- [ ] Publish checksums and provenance for downloadable release artifacts.

### Installation experience

- [ ] Clearly state the supported Python versions.
- [ ] Explain when Python, pip, Git, and FFmpeg/ffprobe are required.
- [ ] Remove Git as a hidden prerequisite for users installing from release assets or PyPI.
- [ ] Provide platform-specific prerequisite checks and troubleshooting.
- [ ] Align `doctor`, setup documentation, and actual command requirements.
- [ ] Distinguish music-only usage from commands that require video inspection tools.

### Dependency and licensing review

- [ ] Inventory all direct, transitive, bundled, and downloaded dependencies.
- [ ] Review the current Mutagen GPL dependency before public distribution and determine its implications for the project's license and distribution model.
- [ ] Generate and maintain third-party license notices.
- [ ] Document the source and version of every bundled binary.
- [ ] Obtain focused legal review before representing the distribution as permissively licensed or suitable for proprietary reuse.

### FFmpeg/ffprobe strategy

Evaluate the following options; no option is selected yet:

1. Require users to install FFmpeg/ffprobe separately.
2. Distribute a controlled, version-pinned LGPL-only build with checksums, corresponding source, notices, build configuration, and a documented compliance process.
3. Replace ffprobe for metadata inspection with a more permissively licensed component.

Do not distribute floating or unverified executable downloads.

## Medium Term — Product and Architecture Hardening

### Stable behavioral contract

- [ ] Define versioned schemas for movie, television, and music indexes.
- [ ] Document schema compatibility and migration policies.
- [ ] Build representative fixtures for messy real-world media libraries.
- [ ] Preserve regression cases for parsing, matching, renaming, conflicts, and migration behavior.
- [ ] Treat fixture outputs as a language-independent behavioral contract.

### Core workflow separation

- [ ] Formalize scanning, metadata interpretation, operation planning, preview, and apply as distinct layers.
- [ ] Ensure all destructive operations can be previewed before execution.
- [ ] Define deterministic conflict handling and recovery behavior.
- [ ] Improve cancellation, atomic writes, logging, and failure recovery.
- [ ] Support reproducible diagnostics without exposing credentials or sensitive paths.

### Alternative metadata libraries

- [ ] Prototype MediaInfoLib for video and container metadata.
- [ ] Compare MediaInfoLib results with ffprobe across representative fixtures.
- [ ] Evaluate ATL.NET for audio-tag reading and writing.
- [ ] Confirm format coverage, performance, redistribution terms, and attribution requirements before adoption.

## Long Term — Exploratory Windows Product

> The items in this section are exploratory and are not commitments to replace the Python CLI.

### C#/.NET core port

- [ ] Evaluate a C# port of the parsing, planning, indexing, and filesystem core.
- [ ] Port pure parsing and planning behavior before building a GUI.
- [ ] Require parity with the Python behavioral fixtures.
- [ ] Preserve versioned index compatibility or provide explicit migrations.
- [ ] Evaluate MediaInfoLib and ATL.NET as alternatives to Python and FFmpeg-dependent components.

### Windows desktop experience

- [ ] Design a GUI around the preview → inspect conflicts → approve → apply workflow.
- [ ] Provide progress, cancellation, recovery, and actionable errors.
- [ ] Treat network shares, long paths, case-only renames, and Windows filesystem behavior as first-class scenarios.
- [ ] Provide user-readable operation history and support bundles.

### Runtime and distribution

- [ ] Publish a self-contained .NET build so users do not need to install the .NET runtime separately.
- [ ] Evaluate single-file distribution where compatible with native dependencies.
- [ ] Create a signed Windows installer and uninstall process.
- [ ] Evaluate a signed, secure updater with rollback support.
- [ ] Establish artifact signing, checksums, provenance, and release-retention policies.

### Commercialization and licensing

- [ ] Decide whether the polished Windows product will be proprietary, open source, dual-licensed, or sold as a supported distribution.
- [ ] Use a commercial EULA if a proprietary product model is selected.
- [ ] Ship complete third-party notices and satisfy all source-distribution or relinking obligations.
- [ ] Review bundled native libraries and codecs for copyright, patent, trademark, and redistribution obligations.
- [ ] Obtain qualified licensing and patent counsel before the first paid release, especially before adding decoding, playback, thumbnail extraction, transcoding, or encoding.

## Decision Gates

- **Public Python release:** Resolve the installation path, Mutagen/GPL assessment, dependency inventory, and FFmpeg policy.
- **C# prototype:** Stabilize schemas and behavioral fixtures sufficiently to measure implementation parity.
- **Windows beta:** Validate the metadata-library choice, migration behavior, and installer/update model.
- **Paid release:** Complete the licensing model, EULA, third-party notices, signing, support policy, and legal/patent review.
