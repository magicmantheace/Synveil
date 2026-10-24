# Experimental XFCE desktop: first complete QEMU multiwindow pass

- **Source revision:** `c0242259f69a6ba4888421e9ce671644c67d9a5d`
- **CI runtime evidence:** [desktop-live run 39541711454](https://github.com/magicmantheace/Synveil/actions/runs/39541711454)
- **Exact-image build class:** Debian Trixie AMD64 hybrid ISO, VM/developer only
- **Guest/host result:** `preview-config=success`, `iso-build=success`
- **Boot firmware:** OVMF UEFI, disposable firmware-variable file; no physical disk
- **Guest network:** disabled by QEMU (`-net none`)

## What actually passed

1. The Debian-based ISO was built from the Xfce live-build profile.
2. `xorriso` found bootable UEFI El Torito entry 2 and BIOS entry 1.
3. The guest presented the Debian live menu. The private host QMP tool
   recognized the developer-only menu framebuffer and sent one fixed Return.
4. Linux userspace started the serial diagnostic reporter and an Xfce session.
5. The guest launched genuine *Thunar* and *XFCE Terminal* windows as a
   **non-root temporary user** and confirmed the Xfwm4 window manager was active.
6. The guest moved/resized, maximized, minimized and restored a window, then
   checked that both application windows still existed.
7. The host received `SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS`, captured an
   actual 1280×800 desktop QMP framebuffer, and verified nonblank pixels.
8. The required [CI job logs](https://github.com/magicmantheace/Synveil/actions/runs/39541711454)
   contain the separate live-menu-selection and real-guest-interaction markers.

The successful CI artifact retains the serial transcript, desktop screenshot
and image SHA-256 for seven days; it deliberately does **not** publish the
prototype ISO.

## What has *not* passed

This is **not a native, owner-ready Synveil installation**. The current
desktop is a Debian/Xfce integration experiment. Owner authentication,
persistent desktop home, independent on-device recovery, verified target disk
identity, immutable A/B slots, signed whole-image updates, automatic rollback,
RTX 3060 driver behavior and HP Pavilion physical boot are all unproven.
The disposable live user should not be mistaken for a protected personal login.

The native source-built Synveil kernel/EFI profile has a *separate* successful
hardware-target image-build result:
[run 39521077016](https://github.com/magicmantheace/Synveil/actions/runs/39521077016).
Its kernel was **not** the kernel in the above Debian desktop ISO.

Do not flash, release, or recommend this experimental ISO for owner use. No
Windows partition or boot configuration was accessed.
