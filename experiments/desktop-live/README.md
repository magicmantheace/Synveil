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

## Separate real-password test without shipping a password

Once the independent locked LightDM greeter passes, a **third**
VM-only developer experiment checks whether an actual password
authentication can open a non-root graphical session:

```sh
SYNVEIL_DESKTOP_LOGIN_GATE=2 bash tools/desktop_live.sh configure
# Inside a disposable build container only:
bash tools/desktop_live.sh build
```

The `desktop-pam-test.yml` workflow builds that separate ISO, not the
ordinary autologin developer ISO and not the locked/no-password variant.
The ISO contains **no password**. The host QEMU test generates a
**new random 128-bit lowercase-hex value**, injects it only through
a disposable QEMU virtual `fw_cfg` device, and types it through the
private QMP keyboard. The guest's fixed root LightDM prestart validates
and loads it through standard `chpasswd` tools. A deliberate *wrong*
random password must leave the XFCE session absent. Only the correct
test password may cause the guest to report a running XFCE session and
panel belonging to the non-root user, with an actual graphical image.

The virtual `fw_cfg` channel is visible inside the guest and must
**never** be used as production secret storage. This is purely an
integrated test of LightDM/PAM/session startup, not a secure way for
the owner to select a password. Neither the test password, guest
`/etc/shadow`, ISO nor virtual credentials are uploaded as evidence.

A production first-user enrollment implementation still requires
a local human-facing password-creation UI, repeat entry/confirmation,
protecting the credential from telemetry/AI/QMP/serial, verifier
integrity and ownership checks, persistence through cold boots,
denial of invalid login, active-session revocation on logout/crash,
and an independent authenticated recovery path. The owner installer
remains blocked until those are implemented and validated.

## Opt-in locked graphical login gate (not first-user enrollment)

The default developer ISO remains an unauthenticated, disposable XFCE
session solely for automated desktop/window/persistence testing. It
must not be distributed as an owner-ready operating system.

The separate locked-greeter experiment uses a **fresh** output tree:

```sh
SYNVEIL_DESKTOP_LOGIN_GATE=1 bash tools/desktop_live.sh configure
# Explicit privileged developer-VM image build only, never on physical media:
bash tools/desktop_live.sh build
```

Both UEFI live boot entries include Debian's
`live-config.noautologin` shortcut. The LightDM prestart refuses to
proceed unless the disposable `synveil` user exists and is **locked**.
The greeter disables guest and automatic sign-in. The CI-only reporter
requires a real LightDM/GTK greeter process, locked account and **no XFCE
user session**, while the host QMP smoke captures the greeter, presses
a single Return without a password, and refuses a desktop session.

Run the full negative login gate using
[the opt-in CI workflow](../../.github/workflows/desktop-login-gate.yml).
It must pass a complete QEMU test before describing the locked greeter
as runtime-validated. The existing developer ISO must continue passing
its independent four-boot XFCE/home acceptance gate.

This image has **no usable enrolled password**, no first-user setup
wizard and no authorized path to open the desktop. It is deliberately
a negative security regression harness, not a finished login system.
A future production profile needs one-time enrollment, a vetted password
verification path, non-root session authority, logout and cold-reboot
relock, and separate authenticated recovery.

## VM-only /home persistence proof

The [first real multiwindow proof](../../docs/validation/desktop-live-c024225/README.md)
and [two separate cold boots](../../docs/validation/desktop-live-437020d/README.md)
passed before persistence was added.

To test file survival without touching real storage, the developer CI
workflow can now create a **256 MiB file-only** ext4 home volume:

```sh
# Only after 'bash tools/desktop_live.sh configure' in a disposable checkout:
python3 tools/create_desktop_home_image.py --create
# Creates out/desktop-live/home-persistence.img; NEVER a block device.
```

The image has the special label `synvmhome` and a restricted
`persistence.conf` containing just `/home`. The experimental live
kernel permits that specific filesystem label. QEMU attaches the file
using a virtual virtio disk while remaining completely offline.
The first graphical guest writes a benign test file and synchronizes it;
a second independent guest must find and validate the same bytes.
It still checks the real XFCE panel, Settings Manager, Terminal and Thunar.

This test does not enroll a user, encrypt the volume, install a
root filesystem, establish authenticated login, or prove a power-loss
safe A/B update. **The untrusted test home image is never uploaded as a
public artifact**. A VM-only developer account must never store real
personal information or credentials.

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
