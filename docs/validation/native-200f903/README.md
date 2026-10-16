# Partial native storage-adapter validation: 200f903

Revision `200f903f439c4ecaa3334eb59a3231f552bb952a` in
[native workflow 38837397399](https://github.com/magicmantheace/Synveil/actions/runs/38837397399)
finished with **failure**. API step/artifact evidence is in `workflow-result.json`.

All native audit gates passed with the new production storage adapter:
capacity/incomplete tails, tmpfs/read-only and ext4 exhaustion/recovery,
acknowledged journal/snapshot reboot, completed rollover and archive-only recovery.
Core recovery, state/settings persistence, first-user onboarding and independent
packaged-VM checks also passed. These results do not make the pipeline successful.

The authenticated graphical gate failed at `checking-user-return`. The guest
confirmed incorrect-password rejection, valid login, VT-switch revocation,
independent login and a UID1000 terminal. It emitted
`SYNVEIL_USER_TERMINAL_READY` but never `SYNVEIL_USER_TERMINAL_RETURNED`.
No QMP error appeared. The existing log does not distinguish a still-running
shell from a lost or unusable dashboard. Later fixed failure-reason diagnostics
require native validation; the failure is not claimed resolved.

The downloaded native evidence artifact `12021169366` (15319812 ZIP bytes)
matched SHA256 `db181571a5b528f5f6dab623724c8ade1c3edc7e461584617505f42592c661d4`
before its graphical log was inspected. An authenticated preview artifact was
uploaded, but that upload does not override the failed graphical gate or qualify
this revision as a fully validated preview.

Separately, host CI at this revision failed a new rotation test's archive
inspection assertion after it passed pre-merge. PR #17 adds a test-only bounded
WouldBlock retry with diagnostic errors, and a real-lock regression. That
correction passed host CI at `16ef68a` but is absent from this native revision.
The earlier fully successful native baseline remains
[c97f554](../native-c97f554/README.md).
