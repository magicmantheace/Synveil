# Native image status validation

Validated revision: `79a41c291884c40c881a89a2fc031bd33e9034de`.

[Native target build run 37718996064](https://github.com/magicmantheace/Synveil/actions/runs/37718996064)
completed successfully on 2026-10-08 at 03:29:37 UTC. Job `113124737278`
passed every build and smoke step on Ubuntu 24.04.

The committed pipeline authenticated pinned Rust sources, rebuilt the isolated
C toolchain and Rust std, and audited the static native binaries. It packaged
those binaries into the opt-in rootfs, built the kernel and GPT boot image,
and wrote the image identity manifest. `bash build.sh native-smoke` booted the
image through OVMF/QEMU and required both `SYNVEIL_CORE_READY` and
`SYNVEIL_BOOT_OK`, rejecting `SYNVEIL_CORE_UNAVAILABLE`.

The ready marker requires a successful `synctl --json status` request to the
running `veil-core` through its local Unix socket. This validates native boot
startup and the versioned status request without AI.

The successful evidence-upload step retained native binaries, logs (including
`qemu-native-smoke.log`), linker maps, and image manifests in the
`native-target-79a41c291884c40c881a89a2fc031bd33e9034de` artifact for seven days.
This permanent record identifies the run and tested contract; it is not a
replacement for the archived manifests and logs.

The run did not test core crashes, absent-core boot, a persistent rootfs,
a desktop, or installation on hardware. Phase 2 as a whole remains incomplete.
