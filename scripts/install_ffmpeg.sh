#!/usr/bin/env bash
# Installs ffmpeg/ffprobe for media-agent.
#
# PREFER YOUR PACKAGE MANAGER INSTEAD:
#   macOS         brew install ffmpeg
#   Debian/Ubuntu sudo apt install ffmpeg
#   Fedora        sudo dnf install ffmpeg
#
# This script is a fallback for systems without one. Its Linux fallback path
# downloads a static build from johnvansickle.com. Unlike the previous
# version of this script, the download is now:
#   - pinned to a specific, known-good release (not a "latest" alias that
#     can change underneath us),
#   - verified against a checksum hardcoded below before anything is
#     extracted or executed,
#   - checked for path-traversal / absolute-path archive members before
#     extraction.
#
# johnvansickle.com only publishes MD5 checksums (no SHA-256/signature is
# offered for these builds) — see release-readme.txt on that site. MD5 is
# not collision-resistant against a determined adversary, but it does catch
# the realistic threats here: a corrupted download, or the upstream file
# changing out from under a pinned URL. It is verified below; do not treat
# this comment as an invitation to skip verification because "it's only
# MD5" — no verification is strictly worse.
#
# Checks if ffprobe is already on PATH first. If so, no action is needed.
# Otherwise tries the system package manager, then falls back to the pinned
# static build for Linux.
#
# Does NOT modify media_agent_config.json. Runtime discovery handles
# vendor/ffmpeg/ automatically (PATH -> config.ffprobe_path -> vendor/ffmpeg/).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/ffmpeg"

# Pinned release. Bump these together (version, per-arch MD5) after manually
# downloading the new build from https://johnvansickle.com/ffmpeg/ and
# computing its md5sum — do not paste in a hash you haven't verified
# yourself against a real download.
FFMPEG_STATIC_VERSION="7.0.2"
FFMPEG_STATIC_MD5_AMD64="7fa72b652e19bf84c9461e332ea1cdf3"
FFMPEG_STATIC_MD5_ARM64="807afe21601db0a73e426121c7d636ea"
FFMPEG_STATIC_MD5_ARMHF="bd0c3d4821a5ddce1a4339cd00031e38"

# Computes an md5 checksum for $1 using whatever md5 tool is available.
# Prints the checksum to stdout; returns non-zero if no tool is available.
compute_md5() {
    local file="$1"
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$file" | awk '{print $1}'
    elif command -v md5 >/dev/null 2>&1; then
        # BSD/macOS md5
        md5 -q "$file"
    else
        return 1
    fi
}

# Verifies that $1 (a file path) has md5 checksum $2. Prints a diagnostic
# and returns non-zero on any failure, including "no md5 tool available" —
# callers must treat that as "verification failed", not "skip verification".
verify_checksum() {
    local file="$1" expected="$2" actual
    if ! actual="$(compute_md5 "$file")"; then
        echo "ERROR: no md5sum/md5 utility found — cannot verify download integrity." >&2
        echo "Refusing to proceed without checksum verification." >&2
        return 1
    fi
    if [ "$actual" != "$expected" ]; then
        echo "ERROR: checksum mismatch for $file" >&2
        echo "  expected: $expected" >&2
        echo "  actual  : $actual" >&2
        echo "The downloaded file does not match the pinned checksum. It will" >&2
        echo "NOT be extracted or executed. This can mean the download was" >&2
        echo "corrupted, or that the upstream file changed — either way, do not" >&2
        echo "bypass this check." >&2
        return 1
    fi
    return 0
}

# Overridable via environment variable for testing; the defaults are
# generous for a real ffmpeg static build (tens of MB, a few dozen files).
: "${MEDIA_AGENT_MAX_ARCHIVE_MEMBERS:=5000}"
: "${MEDIA_AGENT_MAX_ARCHIVE_BYTES:=2147483648}"   # 2 GiB

# Lists the members of tar archive $1 and rejects it if any of the following
# hold, or if the archive can't be listed at all:
#   - a member is an absolute path or contains a ".." path segment (path
#     traversal -- could write outside the extraction directory)
#   - a member is a symlink or hardlink. A symlink's target isn't validated
#     by this check, and a later member extracted "through" it could still
#     escape the destination even though its own name looks safe -- refusing
#     link members outright is simpler and safer than trying to validate
#     every possible target.
#   - the archive has more than $MEDIA_AGENT_MAX_ARCHIVE_MEMBERS members, or
#     more than $MEDIA_AGENT_MAX_ARCHIVE_BYTES of total declared
#     uncompressed size (a decompression-bomb guard)
check_archive_paths() {
    local archive="$1" member seg unsafe=0
    local plain_listing verbose_listing
    # --force-local: a colon in $archive (e.g. a Windows-style "C:/..." path,
    # as can appear when this is exercised under Git Bash) would otherwise
    # be parsed by GNU tar as a "host:path" remote archive spec.
    if ! plain_listing="$(tar --force-local -tf "$archive" 2>/dev/null)"; then
        echo "ERROR: could not list archive contents: $archive" >&2
        return 1
    fi
    # -tv adds a leading type/permission column (e.g. "-rw-r--r--" for a
    # regular file, "l..." for a symlink, "h..." for a hardlink) that plain
    # -t doesn't provide. Listed a second time rather than parsed out of one
    # combined pass, since the verbose format's exact column widths aren't
    # guaranteed portable enough to reliably split out just the name --
    # pairing the two listings by line order avoids needing to.
    if ! verbose_listing="$(tar --force-local -tvf "$archive" 2>/dev/null)"; then
        echo "ERROR: could not list archive contents (verbose): $archive" >&2
        return 1
    fi

    # The plain and verbose listings are paired up by line order below, on
    # the assumption that both invocations enumerate the same archive's
    # members in the same order. That assumption is normally safe (it's the
    # same tar binary listing the same file twice), but the read loop below
    # stops as soon as *either* stream runs out -- if the two ever disagree
    # on member count for any reason, any member past the shorter stream's
    # end is silently never visited by either the type check or the
    # path-traversal check at all. Verified directly: a plain listing with
    # one more entry than its paired verbose listing lets that extra entry
    # (independent of what it is -- includes ".." traversal) through
    # completely unchecked. Reject outright on a mismatch rather than trust
    # a partially-paired validation.
    local plain_count verbose_count
    plain_count=$(printf '%s\n' "$plain_listing" | grep -c '^')
    verbose_count=$(printf '%s\n' "$verbose_listing" | grep -c '^')
    if [ "$plain_count" -ne "$verbose_count" ]; then
        echo "ERROR: archive listing mismatch -- plain listing has $plain_count member(s), verbose listing has $verbose_count; refusing to trust a partially-paired validation" >&2
        return 1
    fi

    local member_count=0 total_size=0 vline type_char size
    while IFS= read -r member <&3 && IFS= read -r vline <&4; do
        [ -z "$member" ] && continue
        member_count=$((member_count + 1))

        type_char="${vline:0:1}"
        if [ "$type_char" != "-" ] && [ "$type_char" != "d" ]; then
            echo "ERROR: archive member is a symlink, hardlink, or other special type ('$type_char'), refusing: $member" >&2
            unsafe=1
            continue
        fi

        size="$(awk '{print $3; exit}' <<<"$vline")"
        case "$size" in
            ''|*[!0-9]*)
                # A field this check can't parse is exactly the case the
                # size limit exists to catch -- treating it as 0 would let
                # that member's real size (however large) pass uncounted,
                # silently weakening the decompression-bomb guard instead
                # of enforcing it. Fail closed: reject rather than guess.
                echo "ERROR: could not parse a numeric size for archive member: $member (raw listing: '$vline')" >&2
                unsafe=1
                continue
                ;;
        esac
        # Bash arithmetic is signed 64-bit and wraps silently past ~9.2e18
        # rather than erroring -- a declared size at or beyond that range
        # would sum to a negative total and slip under any limit
        # completely undetected. Verified directly: a fake size of
        # 2^63 wraps total_size negative, which then passes even a tiny
        # limit. Comparing an out-of-range value directly (`[ "$size" -gt
        # ... ]`) isn't a safe guard either -- bash reports "integer
        # expression expected" for it, and that error is swallowed as a
        # false condition inside `if`, the wrong direction for a safety
        # check. Reject on digit-string LENGTH instead, before any
        # arithmetic touches the value at all: 15 digits (under a
        # petabyte) is far beyond any real archive's legitimate size and
        # comfortably clear of where summing many such values could itself
        # approach overflow.
        if [ "${#size}" -gt 15 ]; then
            echo "ERROR: archive member declares an implausibly large size ($size bytes), refusing: $member" >&2
            unsafe=1
            continue
        fi
        total_size=$((total_size + size))

        case "$member" in
            /*)
                echo "ERROR: archive member has an absolute path: $member" >&2
                unsafe=1
                continue
                ;;
        esac
        local saved_ifs="$IFS"
        IFS='/'
        # shellcheck disable=SC2086
        set -- $member
        IFS="$saved_ifs"
        for seg in "$@"; do
            if [ "$seg" = ".." ]; then
                echo "ERROR: archive member escapes the target directory: $member" >&2
                unsafe=1
            fi
        done
    done 3<<EOF3 4<<EOF4
$plain_listing
EOF3
$verbose_listing
EOF4

    if [ "$member_count" -gt "$MEDIA_AGENT_MAX_ARCHIVE_MEMBERS" ]; then
        echo "ERROR: archive has $member_count members, exceeding the limit of $MEDIA_AGENT_MAX_ARCHIVE_MEMBERS" >&2
        unsafe=1
    fi
    if [ "$total_size" -gt "$MEDIA_AGENT_MAX_ARCHIVE_BYTES" ]; then
        echo "ERROR: archive's declared uncompressed size ($total_size bytes) exceeds the limit of $MEDIA_AGENT_MAX_ARCHIVE_BYTES bytes" >&2
        unsafe=1
    fi

    [ "$unsafe" -eq 0 ]
}

main() {
    # 1. Check PATH first — don't create a duplicate install
    if command -v ffprobe >/dev/null 2>&1; then
        echo "ffprobe already available at: $(command -v ffprobe)"
        echo "No install needed — media-agent will detect it automatically."
        return 0
    fi

    # 2. Check if vendor install already exists
    if [ -f "$VENDOR_DIR/bin/ffprobe" ]; then
        echo "ffprobe already installed in vendor/ffmpeg/: $VENDOR_DIR/bin/ffprobe"
        echo "No install needed."
        return 0
    fi

    echo "ffprobe not found on PATH. Attempting install ..."

    local os
    os="$(uname -s)"

    # 3. Try system package manager
    if [ "$os" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            echo "macOS detected — installing via Homebrew ..."
            brew install ffmpeg
            echo "Installed. media-agent will detect ffprobe on PATH automatically."
            return 0
        else
            echo "Homebrew not found. Install Homebrew first: https://brew.sh"
            echo "Then re-run this script."
            return 1
        fi
    fi

    # Linux — try package managers in order
    if command -v apt-get >/dev/null 2>&1; then
        echo "Debian/Ubuntu detected — installing via apt ..."
        sudo apt-get update -qq && sudo apt-get install -y ffmpeg
        echo "Installed. media-agent will detect ffprobe on PATH automatically."
        return 0
    elif command -v dnf >/dev/null 2>&1; then
        echo "Fedora/RHEL detected — installing via dnf ..."
        sudo dnf install -y ffmpeg
        echo "Installed. media-agent will detect ffprobe on PATH automatically."
        return 0
    elif command -v pacman >/dev/null 2>&1; then
        echo "Arch Linux detected — installing via pacman ..."
        sudo pacman -Sy --noconfirm ffmpeg
        echo "Installed. media-agent will detect ffprobe on PATH automatically."
        return 0
    fi

    # 4. Linux fallback: pinned johnvansickle.com static build
    echo "No supported package manager found. Falling back to static build ..."

    local arch arch_tag expected_md5
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch_tag="amd64"; expected_md5="$FFMPEG_STATIC_MD5_AMD64" ;;
        aarch64) arch_tag="arm64"; expected_md5="$FFMPEG_STATIC_MD5_ARM64" ;;
        armv7l)  arch_tag="armhf"; expected_md5="$FFMPEG_STATIC_MD5_ARMHF" ;;
        *)
            echo "Unsupported architecture: $arch"
            echo "Download a static build manually from https://johnvansickle.com/ffmpeg/"
            echo "and set ffprobe_path in media_agent_config.json."
            return 1
            ;;
    esac

    local static_url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-${FFMPEG_STATIC_VERSION}-${arch_tag}-static.tar.xz"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp_dir'" EXIT

    echo "Downloading $static_url ..."
    curl -L --progress-bar -o "$tmp_dir/ffmpeg.tar.xz" "$static_url"

    echo "Verifying checksum ..."
    if ! verify_checksum "$tmp_dir/ffmpeg.tar.xz" "$expected_md5"; then
        return 1
    fi
    echo "Checksum OK."

    echo "Checking archive contents for unsafe paths ..."
    if ! check_archive_paths "$tmp_dir/ffmpeg.tar.xz"; then
        echo "Refusing to extract an archive with unsafe member paths." >&2
        return 1
    fi

    echo "Extracting ..."
    tar --force-local -xJf "$tmp_dir/ffmpeg.tar.xz" -C "$tmp_dir"

    local top_level
    top_level="$(find "$tmp_dir" -maxdepth 1 -mindepth 1 -type d | head -1)"
    if [ -z "$top_level" ]; then
        echo "Unexpected archive structure — no top-level directory found."
        return 1
    fi

    echo "Installing to $VENDOR_DIR ..."
    mkdir -p "$VENDOR_DIR/bin"
    cp "$top_level/ffprobe" "$VENDOR_DIR/bin/ffprobe"
    chmod +x "$VENDOR_DIR/bin/ffprobe"
    # Copy ffmpeg too if present (not required by media-agent, but useful to have)
    if [ -f "$top_level/ffmpeg" ]; then
        cp "$top_level/ffmpeg" "$VENDOR_DIR/bin/ffmpeg"
        chmod +x "$VENDOR_DIR/bin/ffmpeg"
    fi

    local version
    version="$("$VENDOR_DIR/bin/ffprobe" -version 2>&1 | head -1)"
    echo "Installed: $version"
    echo "Location : $VENDOR_DIR/bin/ffprobe"
    echo ""
    echo "media-agent will detect vendor/ffmpeg/ automatically. No config changes needed."
}

# Allow this script to be sourced (e.g. by tests) without running main —
# only run it when executed directly.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
