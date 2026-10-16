# Native released-key login and audit adapter: 4390a8c

- Revision: `4390a8c7b4110917993f5c55b04763f2ad396848`.
- [Native workflow 38892323779](https://github.com/magicmantheace/Synveil/actions/runs/38892323779): **success**, October 16, 2026 UTC.
- Every build, guest, packaging and upload step passed. API evidence is retained in `workflow-result.json`.

Graphical authentication, non-root terminal return, GUI crash/session revocation,
first-owner enrollment and independent packaged-VM enrollment/login all passed.
The downloaded graphical guest log contains
`SYNVEIL_USER_TERMINAL_RETURNED` and `SYNVEIL_GRAPHICAL_LOGIN_CLEANED`.
This validates released-key keyboard acquisition on the configured QEMU target
after the two earlier graphical terminal-return failures; it does not establish
their exact root cause or physical/multiple-keyboard behavior.

The production audit storage adapter passed the entire native pipeline, including
audit capacity/tail refusal, tmpfs read-only/exhaustion, disposable ext4 exhaustion,
acknowledged append/export after abrupt emulator loss, completed rollover and
constructed archive-only recovery across abrupt stop/reboot. Core/seat recovery,
read-only observer, absent/corrupt state disk and settings persistence also passed.

The native evidence ZIP (artifact `12044478562`, 15318277 bytes) was downloaded
and SHA256 verified:
`f910a78fcff20c68f7f26fe5ae0ebcd76faf95f255636dbc801bfec2da61e5ab`.
The authenticated preview artifact is `12045036709`, 23900774 ZIP bytes,
digest `sha256:2d99dba43fbbec4b29eeefca94e719ddf32eb5356c167d5ed2d826c09ce31c43`,
expiring October 23, 2026 at 12:05:50 UTC. The checksum is not a release
signature. See workflow Artifacts and [FIRST_INSTALL.md](../../FIRST_INSTALL.md)
for extraction and preview scope.

Later unsafe active-path replacement and process-kill tests are outside this
revision. Interrupted filesystem operations, volatile-cache/physical power loss,
real block-device faults, independent authenticated integrity and retention
release remain open. The durable-audit roadmap item remains unchecked.
