# Phase 1 validation evidence

Validated revision: `4c40d41b979090cc72b370a8dad540e7f0a6f735`.

The [Phase 1 full boot run](https://github.com/magicmantheace/Synveil/actions/runs/37650261963)
completed successfully on 2026-10-07 at 16:45:58 UTC. Job `112892000446`
ran on `ubuntu-24.04` with four build jobs, forced QEMU TCG, and the exact-commit
Git source transport. The recorded build checkout was clean.

## Validation contract

The job passed bootstrap static checks, then `bash build.sh all` built the
isolated toolchain, installed glibc, linked dynamic and static sysroot probes,
built BusyBox, generated the rootfs and Synveil `/init`, built Linux, inspected
the GPT/EFI image, and wrote the build manifest. `bash build.sh smoke` booted
that image through OVMF/QEMU and required `SYNVEIL_BOOT_OK`.

The job logs contain these results:

```text
16:33:53 UTC  bootstrap toolchain ready: x86_64-synveil-linux-gnu
16:34:30 UTC  rootfs ready
16:45:21 UTC  EFI-stub kernel ready
16:45:25 UTC  image structure verified: GPT + EFI System Partition + BOOTX64.EFI
16:45:55 UTC  QEMU smoke boot passed
16:45:55 UTC  SYNVEIL_BOOT_OK
```

All build, boot, identity, and evidence-upload steps succeeded. This satisfies
the Phase 1 contract in [VALIDATION.md](../../VALIDATION.md).

## Retained evidence

[build.json](build.json) is the exact manifest printed by the successful CI job,
preserved here as an intentional validation record. It includes the validated
commit, clean-checkout flag, source versions and pins, target, and generated
artifact sizes and SHA-256 hashes. These are CI artifact hashes, not hashes from
the separate incremental developer build.

The run uploaded logs and manifests as artifact
`phase1-validation-4c40d41b979090cc72b370a8dad540e7f0a6f735`, ID `11497098519`.
Its archive digest is
`sha256:cef6585ffa6aa1d6b8edbcc76214a71401c968ec67906791041150cf059da4ff`.
GitHub reports expiry on 2026-10-14 at 16:45:56 UTC; the manifest and this
summary remain in Git after the downloadable artifact expires.

## Scope

This validates the source-built bootstrap console in QEMU. It does not establish
bit-for-bit reproducibility across builds, physical-hardware support, or native
core image integration. Phase 2 remains incomplete.
