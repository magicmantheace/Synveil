# Partial native rollover recovery evidence: c8a759b

Revision `c8a759ba2a3690a6c616c67d41e3c7ad9cccffa2` in
[native workflow 38780755118](https://github.com/magicmantheace/Synveil/actions/runs/38780755118)
finished with **failure**. API step evidence is retained in `workflow-result.json`.

Both acknowledged-write crash gates passed, including the completed 4 MiB
rollover. The host retained separate active/archive digests, killed and reaped
the emulator, then verified both originals and both snapshots on a second
boot. The retained rollover read log contains the actual standalone
`SYNVEIL_AUDIT_CRASH_READ_OK` marker. Existing tmpfs/ext4 exhaustion and audit
capacity/tail gates passed as well.

Two graphical gates failed: demo-terminal lifetime after launcher crash and
authenticated framebuffer login/crash recovery. The demo log reached desktop
readiness but never found a terminal or emitted the desktop-restart marker.
The authenticated fixture enrolled its test credential and stopped in the
`denying` phase, without an authentication-denied marker. No QMP rejection was
reported. Later independent onboarding/package tests passed; that does not
make this a complete successful pipeline.

The Q35 launcher also supplied an implicit PS/2 keyboard alongside the explicit
USB keyboard, while the guest selects the first suitable evdev device. That
ambiguity is a plausible cause of all-input loss, not a confirmed diagnosis
from these logs. A later launcher change disables i8042/vmport only when the
explicit graphical USB input set is present. Host argument regressions and a
fresh native run are required; these failures must not be reported resolved
until that run passes.

This partial record supports completed-rollover recovery under emulator-process
loss only. It does not validate the later receipt parser, graphical launcher
change, interrupted rotation, device-cache loss or physical power failure.
