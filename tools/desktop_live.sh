#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Isolated Debian live-build desktop experiment. Does NOT install to a disk.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/experiments/desktop-live"
OUT_DIR="$ROOT_DIR/out/desktop-live"
PROFILE_MARKER=".synveil-desktop-live-prototype"

usage() {
    printf 'Usage: bash tools/desktop_live.sh configure|build\n' >&2
    exit 2
}

mode="${1:-}"
[[ "$#" -eq 1 ]] || usage
case "$mode" in configure|build) ;; *) usage ;; esac

command -v lb >/dev/null 2>&1 || {
    echo "[desktop-live] Debian live-build ('lb') is required" >&2
    exit 1
}

if [[ "$mode" == configure ]]; then
    # No destructive device tools, no overwrite of an existing build tree.
    # A pre-existing symlink (even dangling) is a hard error.
    [[ ! -e "$OUT_DIR" && ! -L "$OUT_DIR" ]] || {
        echo "[desktop-live] refusing existing output; use a fresh checkout" >&2
        exit 1
    }
    mkdir -p "$ROOT_DIR/out"
    mkdir -m 0700 "$OUT_DIR"
    (
        cd "$OUT_DIR"
        lb config \
          --mode debian \
          --distribution trixie \
          --architectures amd64 \
          --binary-images iso-hybrid \
          --bootloaders 'syslinux grub-efi' \
          --debian-installer none \
          --archive-areas 'main contrib non-free-firmware' \
          --checksums sha256 \
          --image-name synveil-desktop-preview \
          --bootappend-live 'boot=live components live-config.username=synveil live-config.hostname=synveil live-config.noroot' \
          --bootappend-live-failsafe 'boot=live components live-config.username=synveil live-config.hostname=synveil live-config.noroot nomodeset'
    )
    install -Dm 0644 "$SOURCE_DIR/packages.list.chroot" \
        "$OUT_DIR/config/package-lists/synveil-desktop.list.chroot"
    install -Dm 0644 "$SOURCE_DIR/live-config.conf" \
        "$OUT_DIR/config/includes.chroot/etc/live/config.conf.d/synveil.conf"
    install -Dm 0644 "$SOURCE_DIR/lightdm.conf" \
        "$OUT_DIR/config/includes.chroot/etc/lightdm/lightdm.conf.d/50-synveil-preview.conf"
    printf '%s\n' 'Experimental desktop with no installer; VM use only.' > "$OUT_DIR/$PROFILE_MARKER"
    echo "[desktop-live] configured disposable live-build source in $OUT_DIR"
    echo "[desktop-live] NOT owner-ready: no authenticated first-user onboarding or A/B updates"
    exit 0
fi

# 'build' is separate, explicit, and may require root for the chroot stages.
# It never accepts block-device paths and never runs dd/mkfs/partition tools.
[[ -d "$OUT_DIR" && ! -L "$OUT_DIR" &&
   -f "$OUT_DIR/$PROFILE_MARKER" && ! -L "$OUT_DIR/$PROFILE_MARKER" ]] || {
    echo "[desktop-live] configure an isolated preview before building" >&2
    exit 1
}
[[ ! -e "$OUT_DIR/live-image-amd64.hybrid.iso" ]] || {
    echo "[desktop-live] refusing to replace an existing ISO" >&2
    exit 1
}
[[ "$(id -u)" -eq 0 ]] || {
    echo "[desktop-live] live-build image assembly needs explicit root privileges" >&2
    exit 1
}
(
    cd "$OUT_DIR"
    lb build
)
echo "[desktop-live] experimental image build finished (not owner-ready)"
