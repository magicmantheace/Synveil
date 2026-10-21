#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Isolated Debian live-build desktop experiment. Does NOT install to a disk.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/experiments/desktop-live"
OUT_DIR="$ROOT_DIR/out/desktop-live"
PROFILE_MARKER=".synveil-desktop-live-prototype"
# Separate opt-in test-only locked LightDM greeter (never enrolled).
LOGIN_GATE="${SYNVEIL_DESKTOP_LOGIN_GATE:-0}"
[[ "$LOGIN_GATE" == 0 || "$LOGIN_GATE" == 1 || "$LOGIN_GATE" == 2 ]] || {
    echo "[desktop-live] invalid explicit VM login gate" >&2
    exit 2
}

usage() {
    printf 'Usage: bash tools/desktop_live.sh configure|build\n' >&2
    exit 2
}

mode="${1:-}"
[[ "$#" -eq 1 ]] || usage
case "$mode" in configure|build) ;; *) usage ;; esac

# Do not follow an alternate output root outside the checkout.
[[ ! -L "$ROOT_DIR/out" ]] || {
    echo "[desktop-live] refusing symlinked output parent" >&2
    exit 1
}

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
    login_flags=""
    if [[ "$LOGIN_GATE" != 0 ]]; then
        login_flags=" live-config.noautologin"
    fi
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
          --bootappend-live "boot=live components live-config.username=synveil live-config.hostname=synveil live-config.noroot${login_flags} persistence persistence-label=synvmhome persistence-storage=filesystem console=tty0 console=ttyS0,115200n8 systemd.show_status=yes loglevel=6" \
          --bootappend-live-failsafe "boot=live components live-config.username=synveil live-config.hostname=synveil live-config.noroot${login_flags} nomodeset persistence persistence-label=synvmhome persistence-storage=filesystem console=tty0 console=ttyS0,115200n8 systemd.show_status=yes loglevel=6"
    )
    install -Dm 0644 "$SOURCE_DIR/packages.list.chroot" \
        "$OUT_DIR/config/package-lists/synveil-desktop.list.chroot"
    install -Dm 0644 "$SOURCE_DIR/live-config.conf" \
        "$OUT_DIR/config/includes.chroot/etc/live/config.conf.d/synveil.conf"
    install -Dm 0644 "$SOURCE_DIR/lightdm.conf" \
        "$OUT_DIR/config/includes.chroot/etc/lightdm/lightdm.conf.d/50-synveil-preview.conf"
    # All of these probes exist solely in the disposable developer ISO.
    # Their output is a single fixed serial marker after Xfwm4 truly moves,
    # maximizes, minimizes and restores two distinct GUI applications.
    install -Dm 0755 "$SOURCE_DIR/guest-proof-session.sh" \
        "$OUT_DIR/config/includes.chroot/usr/local/bin/synveil-guest-proof-session"
    install -Dm 0755 "$SOURCE_DIR/guest-proof-relay.sh" \
        "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-guest-proof-relay"
    install -Dm 0644 "$SOURCE_DIR/guest-proof.desktop" \
        "$OUT_DIR/config/includes.chroot/etc/xdg/autostart/synveil-guest-proof.desktop"
    install -Dm 0644 "$SOURCE_DIR/guest-proof.service" \
        "$OUT_DIR/config/includes.chroot/etc/systemd/system/synveil-guest-proof.service"
    mkdir -p "$OUT_DIR/config/includes.chroot/etc/systemd/system/multi-user.target.wants"
    ln -s ../synveil-guest-proof.service \
        "$OUT_DIR/config/includes.chroot/etc/systemd/system/multi-user.target.wants/synveil-guest-proof.service"
    if [[ "$LOGIN_GATE" == 1 ]]; then
        # Disposable test-only greeter; no credential or auto-login.
        install -Dm 0644 /dev/null \
            "$OUT_DIR/config/includes.chroot/etc/synveil/ci-login-gate"
        install -Dm 0755 "$SOURCE_DIR/login-gate-prestart.sh" \
            "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-login-gate-prestart"
        install -Dm 0755 "$SOURCE_DIR/login-gate-reporter.sh" \
            "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-login-gate-reporter"
        install -Dm 0644 "$SOURCE_DIR/login-gate-lightdm.conf" \
            "$OUT_DIR/config/includes.chroot/etc/lightdm/lightdm.conf.d/90-synveil-ci-login-gate.conf"
        install -Dm 0644 "$SOURCE_DIR/login-gate-reporter.service" \
            "$OUT_DIR/config/includes.chroot/etc/systemd/system/synveil-login-gate-reporter.service"
        mkdir -p "$OUT_DIR/config/includes.chroot/etc/systemd/system/lightdm.service.d"
        cat > "$OUT_DIR/config/includes.chroot/etc/systemd/system/lightdm.service.d/50-synveil-ci-login-gate.conf" <<'UNIT'
[Service]
ExecStartPre=/usr/local/libexec/synveil-login-gate-prestart
UNIT
        mkdir -p "$OUT_DIR/config/includes.chroot/etc/systemd/system/graphical.target.wants"
        ln -s ../synveil-login-gate-reporter.service \
            "$OUT_DIR/config/includes.chroot/etc/systemd/system/graphical.target.wants/synveil-login-gate-reporter.service"
        printf '%s\n' 'VM-ONLY LOCK GATE: NO OWNER ENROLLMENT' \
            > "$OUT_DIR/.synveil-desktop-login-gate"
    fi
    if [[ "$LOGIN_GATE" == 2 ]]; then
        # Separate deliberately test-only PAM path. chpasswd consumes an
        # ephemeral host secret from the QEMU fw_cfg virtual hardware,
        # never a password baked into a downloadable image.
        install -Dm 0644 /dev/null \
            "$OUT_DIR/config/includes.chroot/etc/synveil/ci-login-enroll"
        install -Dm 0755 "$SOURCE_DIR/login-enroll-prestart.sh" \
            "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-login-enroll-prestart"
        install -Dm 0755 "$SOURCE_DIR/login-enroll-reporter.sh" \
            "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-login-enroll-reporter"
        install -Dm 0755 "$SOURCE_DIR/login-enroll-session-logout.sh" \
            "$OUT_DIR/config/includes.chroot/usr/local/libexec/synveil-login-enroll-session-logout"
        install -Dm 0644 "$SOURCE_DIR/login-enroll-session-logout.desktop" \
            "$OUT_DIR/config/includes.chroot/etc/xdg/autostart/synveil-login-enroll-session-logout.desktop"
        install -Dm 0644 "$SOURCE_DIR/login-enroll-lightdm.conf" \
            "$OUT_DIR/config/includes.chroot/etc/lightdm/lightdm.conf.d/90-synveil-ci-login-enroll.conf"
        install -Dm 0644 "$SOURCE_DIR/login-enroll-reporter.service" \
            "$OUT_DIR/config/includes.chroot/etc/systemd/system/synveil-login-enroll-reporter.service"
        mkdir -p "$OUT_DIR/config/includes.chroot/etc/systemd/system/lightdm.service.d"
        cat > "$OUT_DIR/config/includes.chroot/etc/systemd/system/lightdm.service.d/50-synveil-ci-login-enroll.conf" <<'UNIT'
[Service]
ExecStartPre=/usr/local/libexec/synveil-login-enroll-prestart
UNIT
        mkdir -p "$OUT_DIR/config/includes.chroot/etc/systemd/system/graphical.target.wants"
        ln -s ../synveil-login-enroll-reporter.service \
            "$OUT_DIR/config/includes.chroot/etc/systemd/system/graphical.target.wants/synveil-login-enroll-reporter.service"
        printf '%s\n' 'VM-ONLY EPHEMERAL PAM TEST: NOT OWNER ENROLLMENT' \
            > "$OUT_DIR/.synveil-desktop-login-enroll"
    fi
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
if compgen -G "$OUT_DIR/*.iso" >/dev/null; then
    echo "[desktop-live] refusing to replace an existing ISO" >&2
    exit 1
fi
[[ "$(id -u)" -eq 0 ]] || {
    echo "[desktop-live] live-build image assembly needs explicit root privileges" >&2
    exit 1
}
(
    cd "$OUT_DIR"
    lb build
)
echo "[desktop-live] experimental image build finished (not owner-ready)"
