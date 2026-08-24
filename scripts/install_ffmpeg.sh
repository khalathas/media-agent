#!/usr/bin/env bash
# Installs ffmpeg/ffprobe for media-agent.
#
# PREFER YOUR PACKAGE MANAGER INSTEAD:
#   macOS         brew install ffmpeg
#   Debian/Ubuntu sudo apt install ffmpeg
#   Fedora        sudo dnf install ffmpeg
#
# This script is a fallback for systems without one. Be aware that its Linux
# fallback path DOWNLOADS A BINARY OVER THE NETWORK AND RUNS IT, without
# pinning a version or verifying a checksum or signature. That means you are
# trusting the upstream host and your network path at the moment you run it.
# A package manager verifies signatures for you, which is why it is preferred.
#
# Checks if ffprobe is already on PATH first. If so, no action is needed.
# Otherwise tries the system package manager, then falls back to a static
# build for Linux.
#
# Does NOT modify media_agent_config.json. Runtime discovery handles
# vendor/ffmpeg/ automatically (PATH -> config.ffprobe_path -> vendor/ffmpeg/).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/ffmpeg"

# 1. Check PATH first — don't create a duplicate install
if command -v ffprobe >/dev/null 2>&1; then
    echo "ffprobe already available at: $(command -v ffprobe)"
    echo "No install needed — media-agent will detect it automatically."
    exit 0
fi

# 2. Check if vendor install already exists
if [ -f "$VENDOR_DIR/bin/ffprobe" ]; then
    echo "ffprobe already installed in vendor/ffmpeg/: $VENDOR_DIR/bin/ffprobe"
    echo "No install needed."
    exit 0
fi

echo "ffprobe not found on PATH. Attempting install ..."

OS="$(uname -s)"

# 3. Try system package manager
if [ "$OS" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
        echo "macOS detected — installing via Homebrew ..."
        brew install ffmpeg
        echo "Installed. media-agent will detect ffprobe on PATH automatically."
        exit 0
    else
        echo "Homebrew not found. Install Homebrew first: https://brew.sh"
        echo "Then re-run this script."
        exit 1
    fi
fi

# Linux — try package managers in order
if command -v apt-get >/dev/null 2>&1; then
    echo "Debian/Ubuntu detected — installing via apt ..."
    sudo apt-get update -qq && sudo apt-get install -y ffmpeg
    echo "Installed. media-agent will detect ffprobe on PATH automatically."
    exit 0
elif command -v dnf >/dev/null 2>&1; then
    echo "Fedora/RHEL detected — installing via dnf ..."
    sudo dnf install -y ffmpeg
    echo "Installed. media-agent will detect ffprobe on PATH automatically."
    exit 0
elif command -v pacman >/dev/null 2>&1; then
    echo "Arch Linux detected — installing via pacman ..."
    sudo pacman -Sy --noconfirm ffmpeg
    echo "Installed. media-agent will detect ffprobe on PATH automatically."
    exit 0
fi

# 4. Linux fallback: johnvansickle.com static build
echo "No supported package manager found. Falling back to static build ..."

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  ARCH_TAG="amd64" ;;
    aarch64) ARCH_TAG="arm64" ;;
    armv7l)  ARCH_TAG="armhf" ;;
    *)
        echo "Unsupported architecture: $ARCH"
        echo "Download a static build manually from https://johnvansickle.com/ffmpeg/"
        echo "and set ffprobe_path in media_agent_config.json."
        exit 1
        ;;
esac

STATIC_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${ARCH_TAG}-static.tar.xz"
TMP_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "Downloading $STATIC_URL ..."
curl -L --progress-bar -o "$TMP_DIR/ffmpeg.tar.xz" "$STATIC_URL"

echo "Extracting ..."
tar -xJf "$TMP_DIR/ffmpeg.tar.xz" -C "$TMP_DIR"

TOP_LEVEL="$(find "$TMP_DIR" -maxdepth 1 -mindepth 1 -type d | head -1)"
if [ -z "$TOP_LEVEL" ]; then
    echo "Unexpected archive structure — no top-level directory found."
    exit 1
fi

echo "Installing to $VENDOR_DIR ..."
mkdir -p "$VENDOR_DIR/bin"
cp "$TOP_LEVEL/ffprobe" "$VENDOR_DIR/bin/ffprobe"
chmod +x "$VENDOR_DIR/bin/ffprobe"
# Copy ffmpeg too if present (not required by media-agent, but useful to have)
if [ -f "$TOP_LEVEL/ffmpeg" ]; then
    cp "$TOP_LEVEL/ffmpeg" "$VENDOR_DIR/bin/ffmpeg"
    chmod +x "$VENDOR_DIR/bin/ffmpeg"
fi

VERSION="$("$VENDOR_DIR/bin/ffprobe" -version 2>&1 | head -1)"
echo "Installed: $VERSION"
echo "Location : $VENDOR_DIR/bin/ffprobe"
echo ""
echo "media-agent will detect vendor/ffmpeg/ automatically. No config changes needed."
