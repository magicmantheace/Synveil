# Native first-user state mount identity hardening

- **Validated revision:** `0bad15cdf8a16bae19c27771b3d64beb36f8e6c5`
- **Credential CI:** [39645331949](https://github.com/magicmantheace/Synveil/actions/runs/39645331949) — completed successfully
- **Bootstrap CI:** [39645338606](https://github.com/magicmantheace/Synveil/actions/runs/39645338606) — completed successfully
- **Scope:** Existing native Argon2id verifier/enroller and C graphical first-user gate
- **Evidence type:** Genuine host Rust/C security unit tests and source consistency checks, **not** a new QEMU first-boot or owner installation

## What changed

The original native `src/veil-core/src/credential.rs` only checked
that **some** ext4 filesystem was mounted at `/var`. That was
insufficient to prove the password record originated from Synveil's
declared persistent developer-VM state drive.

The verifier and one-shot enroller now require **exactly one writable**
`/dev/vdb /var ext4` mount, matching the pinned device in
`build/rootfs/mount-state`. A read-only filesystem, wrong block device,
tmpfs, unexpected filesystem, absent `rw` flag, or duplicate/ambiguous
`/var` entries fails closed. The Rust check applies to both
credential creation and verification.

The graphical `src/veil-display/enroll.c` gate already restricted
`/var` to that specific block source and ext4. It now additionally
requires writable mount flags and **exactly one** matching `/var`
mount entry before offering native graphical first-user setup.
A separate `synveil_enroll_mount_options_writable()` fixture rejects
`ro`, `defaults`, `xrw`, mixed `rw,ro`, and missing options.

## Actual passing regression evidence

- Rust `cargo test --locked -p veil-core --lib credential::tests::`:
  **7 passed, 0 failed**, including the explicit device/duplicate/flag
  negative fixtures.
- Rust `cargo test --locked -p veil-core --test enrollment`:
  **3 passed, 0 failed**, checking protected enrollment interfaces.
- Graphical C enrollment gate compiled with
  `gcc -O2 -std=c11 -Wall -Wextra -Werror` and its executable exited 0.
- `Cargo.lock` was not modified.
- Repository bootstrap lint also passed.

The test workflow is `.github/workflows/credential-state-mount.yml`,
which runs when either Rust credentials or graphical C enrollment
gate changes on the development branch.

## Remaining limitations

This deliberately pins **only the isolated QEMU image's /dev/vdb**.
A physical Synveil installer must instead use an explicitly verified
stable partition identity (e.g. PARTUUID, expected parent device, owner
consent and mounted-state provenance), with an independently reviewed
policy. It must **not** silently accept arbitrary external disks.

Native credential storage remains separate from the disposable Debian
LightDM/PAM demonstration. This test does **not** establish owner-created
password persistence across a physical reboot, native compositor/desktop
integration, device-safe installation, A/B slot activation, signed
updates or independent recovery.

No owner-ready ISO, physical disk formatting, or Windows modification
was performed by this test.
