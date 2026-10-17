# Native release receipt inspection: 9059bf6

Revision `9059bf66bcb1dd533936a6101183cd3054120480` passed all 44 steps of
[native run 39059244894](https://github.com/magicmantheace/Synveil/actions/runs/39059244894),
job `117473621586`. The API step results and artifact metadata are retained in
`workflow-result.json`. This record does not claim a separate artifact download
or checksum verification.

The post-reboot release gate ran the native `inspect-release` command after
releasing a copied archive. It required `completion-recorded` and
`archive_state:"not-inspected"`, then verified the active and retained-copy
checksums again. All existing audit, recovery, observer, graphical authentication,
packaged-VM and persistence gates also passed.

Host source `39d2627` passed all 136 Rust tests, formatting and clippy, including
receipt inspection at nine actual userspace SIGKILL cuts. PR #26 merged as
`4cfd054` after this native run passed. This revision does not interrupt release
inside the guest. Later syscall-tracing fixture work is outside this evidence.
Device/cache/physical power-loss durability and independent authenticated
integrity remain open; the durable-audit roadmap item remains unchecked.
