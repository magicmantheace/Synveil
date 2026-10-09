# Native guest validation failure

Revision: `a136fb5a9c4088688a8677db17487dda9e5df570`.

[Run 37728769635](https://github.com/magicmantheace/Synveil/actions/runs/37728769635),
job `113152883873`, completed with failure at 2026-10-08 05:22:27 UTC.
The tooling tests (including host Unix-socket probe fixtures), Rust source
verification, C toolchain, native binary audit, image build, and native status
smoke all passed. The recovery command failed immediately at 05:22:22 UTC;
protocol and absent-core guest steps were consequently skipped.

The existing successful crash-recovery evidence at `248775a` remains valid for
that revision. This later failure does not validate either new guest case.

The job-log API could not return this large log (response exceeded its 8 MiB
limit), and the returned artifact download was inaccessible in this workspace.
The precise console error was therefore not retrieved. An inherited build-lock
handle is a suspected cause of the immediate command failure, not a confirmed
root cause. Launchers now close descriptor 9 in QEMU children and smoke timeout
children, add forced timeout cleanup, and print console tails on failure.
The new lock-inheritance fixture passes; actual guest rerun remains required.
