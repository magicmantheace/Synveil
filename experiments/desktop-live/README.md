# Synveil multiwindow desktop proof (developer CI only)

The Synveil native EFI kernel, core and framebuffer login are preserved.
This tree is an **isolated Debian Trixie/Xfce experiment**, not a replacement
for the source-built core or an installed Synveil distribution.

It uses the Xfwm4 window manager, LightDM, Xfce panel, Thunar, terminal
and settings to shorten the route to actual everyday desktop behavior.

## Safe development sequence

1. On an isolated Debian 13 builder, `bash tools/desktop_live.sh configure`
   prepares `out/desktop-live` without writing a disk.
2. A separate explicit privileged `bash tools/desktop_live.sh build`
   assembles the ISO inside that folder. It does **not** install the system.
3. `python3 tests/smoke-desktop-live.py --iso PATH --evidence NEW_DIRECTORY`
   boots only a virtual optical drive in QEMU with OVMF. It does not access
   attached Windows drives or write any block devices.
4. The live Xfce session's **VM-only** probe launches a terminal and file
   manager, verifies Xfwm4 window positioning, maximizing, minimizing and
   restoring, and sends an allowlisted marker over the emulated serial port.
5. The host checks the marker and captures a nonblank QMP screenshot. Both
   observations are required. Source/ISO validation alone cannot pass.

The automated workflow retains only build/proof evidence, not the ISO.
It cannot establish actual NVIDIA RTX 3060 support, user authentication,
boot persistence, signed release delivery, A/B updates, or recovery.

**Do not flash this image or offer it as the owner's first hands-on
installation.** Debian live-config's developer autologin is untrusted for
that purpose, and the output lacks independent owner recovery. The separate
Windows installation is never an allowed target.

Track outstanding milestones in
[issue #39](https://github.com/magicmantheace/Synveil/issues/39) and
[ADR 0014](../../docs/adr/0014-isolated-desktop-proof.md).
