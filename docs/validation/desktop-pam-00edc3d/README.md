# First password-authenticated graphical XFCE QEMU session (CI-only)

- **Exact tested source revision:** `00edc3db054111f95da18af4bb1e4e332074ff87`
- **Full CI result:** [successful run 39626829728](https://github.com/magicmantheace/Synveil/actions/runs/39626829728)
- **Test class:** disposable Debian Trixie XFCE ISO, OVMF UEFI, private QEMU runtime
- **Host-profile result:** success
- **Real-guest PAM sign-in result:** success

## Completed test

1. Built a **third, separate** developer ISO from the same XFCE
   integration profile, but with Debian live-config autologin disabled,
   LightDM guest/autologin disabled, and **no preset account password**.
2. QEMU started with a fresh private OVMF variable file and no network,
   attached the ISO read-only, and received a brand-new **128-bit
   random hexadecimal test password** over *virtual QEMU fw_cfg*.
   This host-generated value was never present in the ISO or logged
   in the committed source.
3. Root-only guest LightDM prestart read the test-only fw_cfg value,
   validated 32 lower-case hexadecimal characters and installed it
   using Debian `chpasswd`. The guest observer saw the graphical
   LightDM login screen before a user desktop was active.
4. The host QMP keyboard typed an **independent wrong test password**.
   The guest did not report an authorized XFCE session during the
   separate wrong-password observation period.
5. The host then typed the **correct freshly generated password**.
   The independent guest reporter confirmed a real `xfce4-session`
   and XFCE panel owned by the **non-root temporary user**.
6. Host QMP captured a nonblank 1280x800 graphical screen. It shows
   the real XFCE panel, an XFCE Terminal under the `synveil` account,
   and the Thunar file manager in the panel/task list.

CI log evidence (the test passwords are never logged):

```text
[desktop-qemu] recognized Debian live menu; sent one Return to boot default entry
[desktop-qemu] attempted wrong ephemeral PAM test password (value redacted)
[desktop-qemu] submitted valid ephemeral PAM test password (value redacted)
[desktop-qemu] real non-root XFCE session after valid PAM password: PASS
[desktop-qemu] PASS
```

The CI evidence artifact retains only `test-pam-uefi.json`,
guest serial/stderr logs, and the framebuffer. **Neither the ISO
nor any password is published.** No writable home or physical disk
was attached in this sign-in test.

## Limitations (owner release still blocked)

- This **is not owner-driven first-user enrollment**. The guest did not
  ask a human to create or confirm a password. QEMU's `fw_cfg` test
  channel is accessible inside the disposable guest and **must not**
  be used as production credential storage.
- The account's `/etc/shadow` state was in the disposable live system,
  not an authenticated installed-system state volume. **Password
  persistence across reboot is unverified**.
- This test exercised **wrong then correct sign-in**, but **did not**
  validate normal logout/relock, graphical session crash revocation,
  unattended update/rollback, or owner recovery.
- The native Synveil Rust `veil-seat`/credential system was not
  integrated into this Debian/XFCE VM. This is **not** an owner-ready
  ISO, bootable installed system or native physical NVIDIA validation.
- Do not flash a prototype image or expose a host operating-system disk.
