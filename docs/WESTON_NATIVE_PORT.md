# Weston native desktop integration prerequisite

Status: Experimental HEADLESS PROTOCOL PREFLIGHT ONLY. 2026-10-22.

Synveil currently boots a native authenticated framebuffer launcher but not
a real multiwindow compositor. The separate Debian Xfce live image has
multiwindow runtime proof but is not the native system.

The test-host-only Weston/Pixman proof can be run without root privileges:

    python3 tests/weston-headless.py
    python3 tools/weston_headless_probe.py --timeout 45

This starts Weston with the headless backend and desktop shell, then
requires two different live Wayland application processes (weston-terminal
and weston-simple-shm) to each negotiate configured xdg_toplevel windows.
It rejects an empty socket, short-lived app or unconfigured window, isolates
the server socket in a private 0700 directory, and cleans all children.

The CI workflow installs Weston on the disposable runner only and records
the package version in its logs. Neither these Debian/Ubuntu packages nor
a Debian root filesystem are added to the native Synveil image.

A passed test proves *protocol-level* multiwindow support by the proposed
compositor. It does NOT prove RGB pixels, native UEFI boot, KMS/DRM,
keyboard/mouse input, graphical authentication, session revoke, or
physical compatibility.

Required next: source-pin and cross-build Weston and all dependency
libraries for the native Synveil sysroot; adapt the single Argon2id
veil-seat authority to revocable, non-root DRM/input access without
relaxing its VT and crash cleanup; then test an actual visible multiwindow
native UEFI/QEMU session with terminal, file manager and settings.
Separate signed A/B installation and physical owner tests follow.
See docs/adr/0013-first-boot-desktop-priority.md. Do not mark M1 complete.

## Source-built rendering prerequisite: Pixman

The separate Pixman-only prototype uses an upstream release pinned in
`build/manifests/native-graphics.json` (0.46.4 with the published SHA-512).
The verified download and target build are separate operations:

```
python3 tools/native_pixman.py --check-lock
python3 tools/native_pixman.py --verify-source
# In a fresh checkout after compiling the native Synveil toolchain:
python3 tools/native_pixman.py --build
```

Each run that creates a directory refuses a prior `out/native-desktop` tree.
The cross-build uses Synveil's pinned GCC/glibc sysroot and Meson to stage a
static Pixman library and headers under `out/native-desktop/stage`, not in
the boot image or host system. Static Python/unit validation, official archive
checksum verification, and the full target cross-build are distinct CI gates.

**Limitations:** Pixman is only one library used by Weston, not a compositor.
Wayland protocol libraries, source-built Weston, audited non-root DRM/seat
handoff and native QEMU multiwindow testing are still required. The initial
owner-install milestone remains unchecked.

The cross-stage also links a disposable **target x86_64 executable** against
its staged Pixman static library, using only Synveil's glibc sysroot, and
inspects the ELF machine and target dynamic-loader path with the cross
`readelf`. This binary is discarded with the isolated build output; its
existence is not evidence of framebuffer or compositor runtime operation.

## Next source-built prerequisite: Wayland host protocol scanner

Wayland 1.26.0 is pinned in `build/manifests/native-wayland.json` to the
SHA-512 from the July 16, 2026 upstream announcement. The independent
`tools/native_wayland.py` verifies this archive, safely extracts in-root
symlinks and (in the CI job) compiles the `wayland-scanner` **host executable**
with the protocol libraries and demos disabled. This scanner is necessary to
generate Wayland protocol C headers/sources during later cross builds.

It does **not** cross-compile libwayland-client/server. That step also needs
source-built target libffi, expat, a disciplined native pkg-config sysroot,
and separate ELF/link validation. Neither the scanner nor a Wayland client
is in the Synveil boot image yet. No auth, UEFI or hardware completion claims
follow from this host build.


## Native Wayland ABI prerequisite: libffi

Wayland's source-built target server/client libraries depend on libffi, not
just the Wayland protocol scanner. \`build/manifests/native-libffi.json\`
pins upstream libffi **3.5.2** to its release SHA-256 (also independently
recorded in Buildroot's 2026 source lock). The \`tools/native_libffi.py\`
builder checks download integrity and safe archive extraction, requires the
existing Synveil target compiler and glibc marker, builds a static target
library, and links/inspects a disposable x86-64 Synveil ELF that calls
\`ffi_prep_cif\` and \`ffi_call\`.

Run the source-only verification with:

    python3 tools/native_libffi.py --verify-source

Or on a fresh checkout with the pinned compiler already present, run:

    python3 tools/native_libffi.py --build

The target library and headers are staged privately under
\`out/native-libffi/stage\`. They are **not** copied to the native rootfs.
A complete Wayland cross-build must use these artifacts together with a
pinned, correctly built protocol scanner, and provide separate runtime proof.
No physical device is written and this does not complete M1.


## Cross-stage libwayland-client and libwayland-server experiment

The first target Wayland recipe lives in \`tools/native_wayland_target.py\`.
This uses the verified Synveil target GCC/glibc, a source-built target
libffi 3.5.2 archive, the pinned host Wayland 1.26.0 scanner, and upstream
Wayland 1.26.0 source to attempt **static target client/server libraries**.
The staging recipe constrains target pkg-config to the private native libffi
installation and overrides the host scanner pkg-config lookup with an exact
staged executable. It has offline tests and a separate expensive CI gate.

An eventual successful result must show \`libwayland-client.a\` and
\`libwayland-server.a\` under private \`out/native-wayland-target/stage\`,
and an x86_64 Synveil ELF linked against both and libffi. This is
**build-level evidence only**. No Weston compositor, DRM device, window
manager, user session, native rootfs or update pathway changes are made
by this isolated experiment. The CI gate's actual conclusion must be
checked before describing target Wayland libraries as built.

## Pinned xdg-shell window-interface generation

The first native multiwindow desktop needs the Wayland `xdg-shell` interfaces
`xdg_wm_base`, `xdg_surface`, and `xdg_toplevel`. The source-only
`build/manifests/native-xdg-shell.json` pins the official
**wayland-protocols 1.49** release to the June 7, 2026 announcement SHA-512.
The locally compiled Wayland 1.26.0 scanner satisfies that release's
minimum scanner version.

`tools/native_xdg_shell.py` rejects unsafe archives, validates upstream
stable `xdg-shell.xml` and its window configure events, checks scanner
binary/source provenance, and generates separately hashed client/server
headers and C interface code. The exact CI run
[39891808737](https://github.com/magicmantheace/Synveil/actions/runs/39891808737)
completed **SUCCESS** at commit `69c6558`. These files are not yet
cross-compiled into native Synveil or used by Weston on a real display.
No login authority, native rootfs, physical disks, or firmware were changed.

The separate native `libwayland-client` and `libwayland-server`
target-cross-build checks must finish successfully before Weston dependency
integration. Even then, a headless/protocol source proof is **not** evidence
of the required native multiwindow UEFI/QEMU and signed A/B install gates.

## First original Synveil xdg-shell application window — headless runtime evidence

The developer-only C application at src/desktop-wayland/first-window.c
creates a non-root xdg_toplevel window with its own ARGB shared-memory
drawing buffer. It uses a Linux memfd, asks the real compositor to configure
the window, acknowledges the configuration, attaches the buffer and commits
the surface. This is an actual Wayland client, not the native framebuffer
launcher or a simulated in-process window.

At commit e0e4ce6, GitHub Actions run 39972930715 finished SUCCESS.
The workflow compiled the original C source against the independently
verified source-pinned xdg-shell C code, using disposable runner host
libwayland-client, then completed the real configure/ack/attach/commit
handshake under unprivileged headless Weston. A bounded host process and
strict trace gate reject an empty socket, unconfigured surface or absent
buffer attachment.

This is HEADLESS HOST runtime evidence, not a native Synveil guest running
the new window. It does not prove native GCC target linking, RGB display,
seat and input, trusted native owner login, desktop taskbar, physical NVIDIA,
installed system updateability, or first-install readiness. The next gate
cross-links this exact source with the verified native Wayland/libffi
archives, then needs a native compositor and visible QEMU window runtime.
