# Supervision guest validation status

Revision: `fc781954dda9bbc0b55ed0c1b066af83910f812a`.

[Native run 37880421088](https://github.com/magicmantheace/Synveil/actions/runs/37880421088),
job `113658605150`, failed on 2026-10-09 at 04:21:17 UTC. Native compilation,
ELF/link audits, rootfs/kernel/image construction, boot/status, and core-crash
recovery passed. Supervision recovery timed out between 04:20:13 and 04:21:13;
the protocol and absent-core guests were skipped. This is not supervision
validation. Host Rust tests, formatting, and strict clippy passed separately
in run `37880421080`.

The original supervision command exceeded 1024 bytes on one console input
line. The pinned BusyBox `FEATURE_EDITING_MAX_LEN` defaults to 1024 bytes;
this can truncate the compound test command. The harness now sends shell
statements on separate lines below that limit, with a fixture assertion.
A real guest rerun is still needed to confirm that this resolves the timeout.

The revised test also distinguishes zombie processes from living supervisors rather
than using `kill -0` alone. This addresses a harness limitation; it is not a
confirmed root cause of the timeout. Independent guest cases now run whenever
the native image build succeeded, even if a preceding guest check fails.
Actual supervision, protocol, and absent-core guest results remain pending.
