# Live release guest interruption: d846b81

Revision `d846b81f59fb3f8e5f83c7dcfa4446d6a0e9962d` passed all 45 steps of
[native run 39071778326](https://github.com/magicmantheace/Synveil/actions/runs/39071778326),
job `117510707415`. This includes all four live-release checkpoints, confirmed
emulator SIGKILL/reap and recovery across eight independent QEMU boots.

The native evidence ZIP (artifact `12116683063`, 16801824 bytes) was downloaded
and verified against SHA256
`b498ca40d108f2117eb7460ea5475b08d8915eac7fecee76f941c9250da43438`.
Each phase log contains exactly one matching checkpoint and one confirmed host
kill/reap entry. Each reboot log contains exactly one standalone recovery marker.
The guest proof's fixture-manifest hash was also verified. `guest-proof.json`
and `workflow-result.json` retain the proof, API steps and artifact metadata.

| Cut | Archive observed after reboot |
| --- | --- |
| Before unlink | Present |
| After unlink, before directory sync | Present |
| After journal-directory sync | Absent |
| After one completion byte | Absent |

Restoration of the archive after unlink is an allowed unsynchronized namespace
outcome. The gate verified both host-retained digests and inspected the residual
receipt before recovery. Surviving archives required fresh explicit receipts;
absent archives refused retry. The original receipt and active/snapshot history
survived the recovery checks unchanged. All earlier recovery, graphical,
persistence and packaging gates also passed. PR #27 merged as `7a24de3`.

This proves these guest kernel-loss/filesystem-replay cases while host page cache
survives. It does not establish volatile-device-cache or physical power-loss
survival, real device-error behavior or independent authenticated integrity.
Later full-completion-write and completion-file-sync cuts are outside this revision.
The durable-audit roadmap item remains unchecked.
