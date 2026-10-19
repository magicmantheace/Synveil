# Live release guest interruption: cbf272c

Revision `cbf272ce007a0ad7a15a346289fbd6f3c484cc8f` passed all 45 steps of
[native run 39161628602](https://github.com/magicmantheace/Synveil/actions/runs/39161628602),
job `117775955379`. This includes all six live-release checkpoints, confirmed
emulator SIGKILL/reap and recovery across twelve independent QEMU boots.

The native evidence ZIP (artifact `12149504713`, 16815429 bytes) was downloaded
and verified against SHA256
`4b6a894e27d1cb8d138ac49db2cf7402bab956edf8901abd3c307d0f474b85f0`.
Each phase log contains exactly one matching checkpoint, one confirmed host
kill/reap entry and both retained digests. Each reboot log contains exactly one
standalone recovery marker. The guest proof's fixture-manifest hash was also
verified. `guest-proof.json` and `workflow-result.json` retain the proof, API
steps and artifact metadata.

| Cut | Archive observed after reboot |
| --- | --- |
| Before unlink | Present |
| After unlink, before directory sync | Present |
| After directory sync | Absent |
| After one completion byte | Absent |
| After the full completion line was written | Absent |
| After completion-file sync returned | Absent |

Restoration of the archive after unlink is an allowed unsynchronized namespace
outcome. The gate verified both host-retained digests and inspected the residual
receipt before recovery. The full-write cut accepted only a valid retained
authorization/completion prefix; the synchronized cut required the complete
receipt. Surviving archives required fresh explicit receipts, while absent
archives refused retry. The original receipt and active/snapshot histories
survived the recovery checks unchanged. All earlier recovery, graphical,
persistence and packaging gates also passed.

This proves these guest kernel-loss/filesystem-replay cases while host page cache
survives. It does not establish volatile-device-cache or physical power-loss
survival, real device-error behavior or independent authenticated integrity.
The durable-audit roadmap item remains unchecked.
