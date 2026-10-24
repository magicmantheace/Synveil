# First complete PAM sign-in, user logout, LightDM relock and relogin

- **Exact tested commit:** `ad7b2b76726b250b784594667ae7b805d515cc9e`
- **Completed green Actions run:** [39639758799](https://github.com/magicmantheace/Synveil/actions/runs/39639758799)
- **Scope:** disposable **Debian Trixie XFCE** live ISO, offline OVMF/QEMU; no Windows/physical disks
- **Host static/profile checks:** PASS
- **Full guest authentication and session lifecycle:** PASS

## Observed guest sequence

1. The ISO booted in OVMF/QEMU from the actual UEFI DVD entry. It disabled
   automatic and guest login. There was no hardcoded password in the image.
2. A fresh, randomly generated 128-bit test secret was supplied by the
   **disposable QEMU virtual fw_cfg test channel**; Debian `chpasswd`
   initialized the temporary live user's PAM credential.
3. The host entered a different **wrong** test password with allowlisted
   QMP keyboard events and observed no authenticated XFCE session.
4. The **correct** random password opened a real non-root XFCE session
   and panel. QMP captured a nonblank first authenticated desktop screen.
5. The isolated XDG-autostart logout helper waited for successful
   terminal, Thunar, window-manager and Settings GUI checks, then
   invoked `xfce4-session-logout --logout --fast` **as the non-root
   user**, not as a privileged host shell.
6. An independent guest system observer required the prior XFCE
   session and panel to exit, and the LightDM/GTK greeter to return,
   before reporting `SYNVEIL_QEMU_TEST_AUTH_RELOCKED`. The host
   captured a separate nonblank re-locked graphical screen.
7. A **new incorrect** random password was submitted after logout.
   The observer never reported a second logged-in desktop during
   the negative interval.
8. Submitting the valid ephemeral password a second time launched
   another real XFCE session and panel under the non-root user.
   Host QMP captured its graphical screen, and the final result passed.

The exact CI log includes:

```text
[desktop-qemu] attempted wrong ephemeral PAM test password (value redacted)
[desktop-qemu] submitted valid ephemeral PAM test password (value redacted)
[desktop-qemu] PAM password opened non-root GUI; now requiring real logout and return to LightDM
[desktop-qemu] guest logged out to LightDM; wrong second password submitted (redacted)
[desktop-qemu] valid second PAM password submitted (value redacted)
[desktop-qemu] confirmed logout/relock, denied wrong password and accepted second non-root XFCE login: PASS
[desktop-qemu] PASS
```

The [run artifact](https://github.com/magicmantheace/Synveil/actions/runs/39639758799)
contains an UEFI validation record, guest serial/stderr logs, and the
three real framebuffer captures (`first-authenticated-session.ppm`,
`relocked-greeter.ppm`, `desktop.ppm`). It **does not** contain the ISO,
password, guest credential database or any writable user disk.

## Remaining security and release gaps

The test account password was synthesized and inserted into a disposable
VM, **not entered and confirmed by the owner**. QEMU `fw_cfg` is
guest-accessible and **not a secure storage or onboarding mechanism**.
The whole OS image remains a live Debian/XFCE integration prototype:
passwords were **not carried across a cold reboot**, the native
Synveil Argon2id `veil-seat` authority was not connected to LightDM,
and none of the logged-in user state was encrypted on disk.

This PASS does **not** prove power-loss-safe credential persistence,
lock/recovery after compositor crash, deletion of leftover background
programs, tamper-resistant session revocation, real hardware GPU/input,
an installed A/B rootfs or signed updates. No owner-ready installer or
flashable release exists. Windows and physical disks were never accessed.
