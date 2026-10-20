# Linux powercap energy observation: bca81e5

Revision `bca81e5866c6797efcd029796f506737222e0a01` passed all 45 steps of
[native run 39358243578](https://github.com/magicmantheace/Synveil/actions/runs/39358243578),
job `118418011641`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12237763364`, 16877245 bytes) was downloaded
and matched SHA256
`060a158c2e899eb610495cf99176c1732996914bee8b91faef7ad6f80df29a38`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required the `energy` object from the real command response.

Exact-revision [host run 39358243724](https://github.com/magicmantheace/Synveil/actions/runs/39358243724),
job `118418012027`, passed all 149 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39358249419](https://github.com/magicmantheace/Synveil/actions/runs/39358249419),
job `118418029160`, also passed. Local validation included all 31 recovery
control-flow tests, and automated review returned no findings.

Host fixtures exercise nested control-type hierarchy, symlink traversal,
deduplication, numeric ordering, partial availability, absence, malformed values,
and bounded traversal. QEMU exposed no powercap zones, so this native evidence
establishes the bounded unavailable schema and its integration into the real
guest command. It does not establish positive physical energy readings,
instantaneous watts, power-limit behavior, counter-wrap behavior, or
physical-hardware accuracy.
