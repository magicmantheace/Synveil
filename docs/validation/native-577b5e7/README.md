# First native target build validation

Validated revision: `577b5e7eb5d19f5e8fbc8b8c4ce3c6aaf86613f1` (clean checkout).

[GitHub Actions run 37716668863](https://github.com/magicmantheace/Synveil/actions/runs/37716668863)
completed successfully on 2026-10-08 at 02:34:22 UTC, job `113114584303`.
The Ubuntu runner authenticated pinned Rust sources, built the isolated C
toolchain, rebuilt Rust std, compiled both binaries, and passed ELF and linker
input audits. The native-build schema was `synveil.native-build/v1`.

Identity printed by the successful job:

| Binary | Bytes | SHA-256 |
| --- | ---: | --- |
| veil-core | 1407168 | e470545d5d69f33829581b90f675e7a54fd79c95d966d75ba9d36290ad0a9404 |
| synctl | 1367944 | 71238657278c23ded6de609e010e66ad22c8e04662337ecf7d164a9eb192f4d4 |

The workflow uploaded `native-target-577b5e7eb5d19f5e8fbc8b8c4ce3c6aaf86613f1`
(artifact ID `11524857012`) containing binaries, build identity, logs, and maps
with seven-day retention. This permanent summary records the observed job
result and identities; it does not replace the complete manifest or link maps.

This run did not package or boot a native image. It establishes target build
validation only; Phase 2 and ADR 0004 remain incomplete pending guest evidence.
