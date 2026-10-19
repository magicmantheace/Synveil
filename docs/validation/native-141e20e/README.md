# Persistent audit sink status: 141e20e

Revision `141e20e8b558a7ef36016f684acfad958694b072` passed all 45 steps of
[native run 39163522194](https://github.com/magicmantheace/Synveil/actions/runs/39163522194),
job `117781621003`. Steps 25 through 27 exercised the real native core's new
persistent-audit health status during rollover, tmpfs exhaustion/read-only
recovery and disposable ext4 exhaustion.

The native evidence ZIP (artifact `12151515222`, 16821871 bytes) was downloaded
and verified against SHA256
`774ba5ed6faaaef0daaa744ede0d64c4ee0516b939d2c961f6a7b5ebad5bb91d`.
Its native and image manifests both identify the exact clean revision. The
retained logs contain each required status assertion and exactly one final pass
marker for each of the three gates.

The rollover gate required configured `healthy` status with zero failures
before faults, then configured `degraded` status with a nonzero failure count
for both exhausted archive capacity and an incomplete journal tail. The tmpfs
gate required `degraded` when full, `healthy` after space recovery, `degraded`
while read-only and `healthy` after a writable remount. The ext4 gate required
`degraded` while full and `healthy` after synchronized space recovery. These
markers are emitted only after the real helper's captured `synctl --json status`
passes its configured/state/count checks.

Exact-revision [host run 39163522216](https://github.com/magicmantheace/Synveil/actions/runs/39163522216),
job `117781621016`, also passed all 136 Rust tests, formatting, clippy and the
actual built release CLI tracing suite. The local pinned-toolchain workspace,
Python control-flow suite and full lint passed before publication.

The failed-write count is process-local and resets on process restart. This
evidence does not establish survival of successful synchronization across
volatile-device-cache loss or physical power loss, real device-error behavior,
automatic archive retention or independent authenticated integrity. The
durable-audit roadmap item remains unchecked.
