# VM-only persistent XFCE home: four complete QEMU boots

- **Exact source/image build revision:** `0579de0a237e823e60843e00e9e7521ff6d53dd4`
- **Completed successful end-to-end CI:** [39597968232](https://github.com/magicmantheace/Synveil/actions/runs/39597968232)
- **Separate actual ext4 image validation:** [39598691537](https://github.com/magicmantheace/Synveil/actions/runs/39598691537)
- **Scope:** Disposable **Debian Trixie/XFCE** development ISO + one file-only 256 MiB ext4 home volume, no host disk access
- **Result:** Desktop and persistence gate passed, **not** an authenticated owner installation

## Four guest boots on one built ISO

1. OVMF/QEMU **normal graphical boot**: real Xfwm4 window controls,
   Thunar, XFCE Terminal, active panel and Settings Manager, QMP
   framebuffer; `[desktop-qemu] PASS`.
2. **Independent GUI cold boot** with new UEFI variable file and no
   persistent disk: same checks passed.
3. A newly created regular file `out/desktop-live/home-persistence.img`,
   ext4 label `synvmhome`, contains a `persistence.conf` allowing
   **only `/home`**. The third UEFI boot used this virtual disk.
   The non-root XFCE session created a fixed harmless test file under
   its home directory and flushed it; CI required the **guest-emitted**
   serial marker `SYNVEIL_QEMU_HOME_WRITTEN` plus the real desktop pass.
4. The fourth **new QEMU process** reattached the unchanged writable
   virtual disk and ISO with separate private UEFI variables. It found
   the existing file, verified the expected bytes and that its owner
   was the non-root session UID, and reported
   `SYNVEIL_QEMU_HOME_RECOVERED` **plus** a second graphical pass.

All four boots used `-net none`. Only the third and fourth had the
newly created home image; the first two used no writable disk. The
host runner refused arbitrary disk paths and symlinks. The screenshot,
serial logs and build checksum are retained in the CI evidence artifact;
the developer ISO and private home disk were **not published**.

## Supporting exact log excerpts

```text
[desktop-qemu] verified separate home marker: SYNVEIL_QEMU_HOME_WRITTEN
[desktop-qemu] guest interactions passed; real scanout passed
[desktop-qemu] PASS

[desktop-qemu] verified separate home marker: SYNVEIL_QEMU_HOME_RECOVERED
[desktop-qemu] guest interactions passed; real scanout passed
[desktop-qemu] PASS
```

## Explicit gaps remaining

The experimental live account may autologin and uses no trusted first
user credentials. The home data are **unencrypted**, and files can be
read by a party with access to the virtual disk. This does **not**
establish crash/power-loss safety, recovery behavior, actual installed
rootfs persistence, native Synveil core/desktop integration, encrypted
user storage or owner authentication.

The file-only A/B GPT image prototype remains **unformatted/unbootable**
and separate from this Debian live system. Signed releases, inactive-slot
update/activation, trial boot health, failover, independent recovery, any
removable-SSD installer and real HP Pavilion/RTX 3060 boot are not proven.

**Never flash this developer ISO, provide this image as a secure user
installer, or target any Windows physical disk.**
