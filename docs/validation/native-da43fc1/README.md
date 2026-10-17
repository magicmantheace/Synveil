# Corrected native archive release: da43fc1

- Revision: `da43fc1b0e7ca87a4cc96304997cddb539517939`.
- [Native workflow 39038885250](https://github.com/magicmantheace/Synveil/actions/runs/39038885250): **success**, October 17, 2026 UTC.
- Every one of the 44 build, guest, packaging and upload steps passed; API evidence is retained in `workflow-result.json`.

This revision includes the active-journal safety correction: release requires a
healthy private active file with a distinct inode and holds/rechecks its locks
and identity. The private storage adapter's real filesystem implementation passed
the complete native pipeline. Its eleven injected-error/partial-write boundaries
and unsafe-active cases passed separately in the 126-test host workspace run;
those fault scenarios were not injected in QEMU.

The native release gate verified both journals and snapshots across abrupt
emulator stop/reboot, released only the copied archive, checked authorization and
completion records, and reverified active and retained-copy digests. The
downloaded guest release-read log contains a standalone
`SYNVEIL_AUDIT_CRASH_READ_OK` marker. All existing audit gates, recovery,
observer, graphical terminal return/session revocation, enrollment, packaged-VM
checks and settings persistence also passed.

The native evidence ZIP (artifact `12103823901`, 15346045 bytes) was downloaded
and its SHA256 verified:
`7576c46771de230c048a37715958c753899916486570a1969ef744f847e0d041`.
The preview artifact is `12104566582`, 23915168 ZIP bytes,
digest `sha256:15d5a6609e6bb625359c289ea893021098e0bd453bb9eab70daab7bcf8cdb06f`,
expiring October 24, 2026 at 14:52:57 UTC. These checksums are not release
signatures. See [FIRST_INSTALL.md](../../FIRST_INSTALL.md) for preview scope.

Later release-process SIGKILL fixtures are outside this revision. This run
does not interrupt release itself, inject real device errors, discard volatile
device caches or establish physical power-loss durability. Independent
authenticated integrity and physical-install readiness remain open. The
durable-audit roadmap item remains unchecked.

The merged production source at `b5b343609a5677b45889fe57fdf272235216889b`
subsequently passed all 44 steps of
[native run 39045646860](https://github.com/magicmantheace/Synveil/actions/runs/39045646860),
job `117433708309`. This is post-merge workflow evidence for the corrected
release implementation; the artifact checksums above belong to `da43fc1`, not
this later run. The test-only process-loss matrix merged afterward.
