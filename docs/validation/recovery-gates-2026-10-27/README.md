# ADR 0010 recovery gates A–C — QEMU evidence record (2026-10-27)

**Result: ADR 0010 gates A, B and C are DEMONSTRATED in QEMU** at the
exact commits stated per gate below. **Gate D (tier 4, the
forgotten-password recovery factor) is NOT implemented** — a draft
specification awaits owner approval, so per ADR 0010 gate E physical
installation remains blocked until the complete recovery path is
exercised. **Physical hardware: NOT tested.** QEMU evidence only.

This record was assembled by reconciling the pass's primary artifacts
(driver logs, verdict JSONs, guest serial transcripts) after an
earlier completion narrative could not be substantiated from the
repository; the per-attempt history below, including the failed
attempts, is the accurate one.

## Identities

- Branch: `synveil/first-boot-desktop-muse`.
- Implementation commits: `42ce0db` (tier-2 `veil-recovery`),
  `2a8a0e8` (QEMU acceptance drivers), `63dff06` (maintenance echo
  control and lock probes), `e36d8f7` (acceptance write-back
  instrumentation), `915751d` (acceptance fixture journal-replay fix).
  Tier-4 draft specification: `af50cba`
  (`docs/RECOVERY_FACTOR_SPEC.md`, awaiting owner approval).
- A/B proof disk used by the gate B/C acceptance: built from source
  `63dff06`, SHA-256
  `a744509d69907fc70d3e790a08e21917d3e1b2d98da98fb63835bbe47755515e`
  (built by `tools/native_ab.py`; the builder's receipt is the
  working-tree file `out/ab-proof/build-receipt.json`, and the disk
  hash is recorded in each attempt's verdict file; all runs worked
  on a disposable copy).
- Gate A fixture: built at `915751d` (its `gate-a-fixture.json` stamps
  `git_commit` `915751db24383ce9355cb2964949c0aa99332304`); image
  SHA-256 `5194f24eb07de080a740d82e5e7d2ef2b493e7b330a6f54ae5b305cd5217b980`,
  kernel `6.18.55-synveil` built 2026-10-27 21:54:29 UTC.

## Gate A — read-only recovery status (tier 1)

Driver: `tests/build-guest-fixture.py --case recovery-status`, which
builds the dedicated fixture (core, seat and graphical executables
absent; kernel command line carries `synveil.recovery=status`) and
boots it under `tests/smoke-serial-locked.py --recovery-status`.

`gate-a-serial.log` is the guest serial transcript of the 2026-10-27
21:55 UTC run at `915751d`. It shows, in order:

```
SYNVEIL_RECOVERY_STATUS_V1
SYNVEIL_RECOVERY_STATE_NOT_MOUNTED
SYNVEIL_RECOVERY_CORE_UNAVAILABLE
SYNVEIL_RECOVERY_SEAT_UNAVAILABLE
SYNVEIL_RECOVERY_FRAMEBUFFER_PRESENT
SYNVEIL_RECOVERY_READ_ONLY
SYNVEIL_SERIAL_RECOVERY_LOCKED
```

The smoke then injects a shell probe; the probe text appears only as
console echo in its deliberately split form — the contiguous executed
marker `SYNVEIL_SERIAL_UNLOCK_BYPASS` appears **0 times** in the
transcript, no bootstrap shell appears, and there is no kernel panic.
The smoke verdict line was `serial-root lock smoke passed`. An
earlier run the same day (20:04 UTC) produced the same marker set;
the transcript in this record is the 21:55 UTC re-run.

Boundary: the mode is selected by a build-time kernel argument and
is **not reachable under the shipped images' fixed kernel command
line**. Gate A is demonstrated on the dedicated fixture only.

## Gate B — authenticated maintenance (tier 2)

Implementation: `veil-recovery` (commit `42ce0db`) — a text-mode local
prompt entered via `synveil.recovery=maint`, authenticating through
the separately audited Argon2id verifier (ADR 0009), never the
graphical seat broker. Commands are exact-match enumerated (`status`,
`restore`, `exit`); input is never interpreted as shell text. Host
contract checks: `tests/recovery-maint.py` (5 tests, re-run OK this
pass).

### B1 — the maintenance path itself

Driver: `tests/smoke-recovery-maint.py` against synthetic test-only
credentials. `gate-b1-verdict.json` (source `63dff06`):

| case | result |
| --- | --- |
| enroll-ok | pass |
| maint-ok | pass |
| maint-wrong | pass (denied) |
| maint-absent | pass (denied) |
| enroll-corrupt | pass (denied) |
| maint-corrupt | pass (denied) |

`secret_in_image: false`; driver verdict
`SYNVEIL_RECOVERY_MAINT_SMOKE_PASS` (`gate-b1-attempt2.log`).

### B2 — restore on the A/B substrate

Driver: `tests/smoke-recovery-maint-ab.py` on a disposable copy of
the A/B proof disk: provision → enroll → fixture surgery (slot B
populated with generation v2, control record extended, known-content
owner file placed on DATA) → denied maintenance boot (three wrong
passwords) → authenticated maintenance boot performing `status` and
`restore` → normal boot handing over to the restored trial → host
checks (DATA byte-intact, control record trial armed, audit log).

Attempt history (all preserved in this record):

1. **Attempt 1 — FAIL** (driver as of `63dff06`, disk at `63dff06`).
   `gate-b2-attempt1.log` / `gate-b2-attempt1-verdict.json`:
   provision, enroll and maint-denied passed; maint-restore,
   restored-boot, data-intact, control-trial-armed and audit-log
   failed. In the guest, the status read reported slot B as `null`
   and the DATA probe file was absent, although the host surgery
   images contained both edits.
2. **Attempt 2 — no usable result** (driver at `e36d8f7`, which only
   added write-back instrumentation). The driver log
   (`gate-b2-attempt2.log`) is empty — the run produced no case
   output. Its surviving guest serial log confirms the attempt-1
   symptom: the status JSON shows `"slots": {"A": {...}, "B": null}`.
3. **Attempt 3 — PASS** (driver at `915751d`).
   `gate-b2-attempt3.log` / `gate-b2-attempt3-verdict.json`: all nine
   cases pass, including the instrumentation case
   `fixture-survives-denied-boot`. The guest status JSON in
   `gate-b2-attempt3-maint-restore.log` now shows slot B present
   (`synveil-gen-v2`, SHA-256
   `5b56f41a76e81841bf7487160c4d63221b36298ee2a1b88bd3e090b165a92307`),
   followed by `SYNVEIL_AB_ROLLBACK result=trial slot=B version=2`
   and `SYNVEIL_RECOVERY_MAINT_RESTORE_OK slot=B version=2`. The
   extracted audit log (`gate-b2-attempt3-audit.log`) records three
   `auth-denied` events and a denied session end, then `auth-ok`,
   `action name=status result=ok` and
   `action name=restore result=armed slot=B version=2`. The DATA
   owner probe file's SHA-256
   (`e9f7e3e43a9160017bf40524801f56af2918c09e16e78dcec862a89aaeb92984`)
   is identical before and after the restore.

### Root cause of the attempt-1/2 failure — test fixture, not product

The defect was in the acceptance fixture's host surgery, **not** in
the guest or in `veil-recovery`. The provision and enroll boots are
killed, leaving unreplayed ext4 journals on the STATE and DATA
partitions. `debugfs` edits applied to the extracted images landed
on top of that journaled state; on the next guest boot, mount-time
journal replay restored the pre-edit blocks and silently discarded
the edits. Three observations localize it: (a) the journal-free FAT
ESP edit (maintenance entry point) persisted and worked; (b) the
guest-enrolled credential — itself the journaled state — survived;
(c) host write-back checks on the disk file itself (added in
`e36d8f7`) saw the edits present before the boot and absent after.
Fix (`915751d`): `replay_journal()` runs `e2fsck -fy` on each
extracted ext4 image before host edits and reads, so host and guest
observe the same filesystem state.

## Gate C — generation layout and rollback (tier 3)

The substrate was demonstrated on disposable QEMU disks in the
[A/B proof record](../ab-qemu-proof-2026-10-26/README.md) (nine
scenarios at `55d9db8`, including an interrupted update, a failing
generation falling back, bad-signature refusal, and user-data
survival across update and rollback), and the host suite
`tests/ab-update.py` passes 18/18 (re-run this pass). This pass adds
the recovery-specific leg the ADR asks for: the rollback in gate B2
attempt 3 is initiated from the authenticated maintenance path
itself, on a disposable QEMU disk, with the DATA partition probe
byte-identical across the restore (SHA-256 above).

## Gate D — recovery factor (tier 4)

**Not implemented.** `docs/RECOVERY_FACTOR_SPEC.md` (commit
`af50cba`) is a draft: three design options with a recommendation,
custody/rotation/revocation flows, and ten enumerated owner
decisions (D1–D10). No recovery-factor code exists, and none may be
written until the owner approves the design, per ADR 0010 sequence
D. Password reset remains impossible by design at this stage; a lost
password on an installed system is still unrecoverable.

## Host suites re-run for this record (2026-10-27, at `915751d`)

- `cargo fmt --all -- --check`: clean
- `cargo test`: **166 passed, 0 failed**
- `python3 tests/ab-update.py`: 18 tests OK
- `python3 tests/recovery-status.py`: 4 tests OK
- `python3 tests/recovery-maint.py`: 5 tests OK
- `python3 tests/native-guest-fixture.py`: 8 tests OK

## Files in this record

- `gate-a-serial.log` — gate A guest serial transcript (`915751d`)
- `gate-a-fixture.json` — gate A fixture manifest (artifact hashes)
- `gate-b1-verdict.json`, `gate-b1-attempt2.log` — B1 verdict + driver log
- `gate-b2-attempt1.log`, `gate-b2-attempt1-verdict.json` — B2 attempt 1 (FAIL)
- `gate-b2-attempt2.log` — B2 attempt 2 driver log (empty; no result)
- `gate-b2-attempt3.log`, `gate-b2-attempt3-verdict.json` — B2 attempt 3 (PASS)
- `gate-b2-attempt3-maint-restore.log` — attempt 3 guest serial log of the restore boot
- `gate-b2-attempt3-audit.log` — maintenance audit log extracted after attempt 3

## What this record does not claim

- No physical-hardware evidence of any kind; the TG01 has not booted
  any of these artifacts.
- The A/B proof disk is a separate artifact from the shipped
  test-media image; nothing here modifies or re-validates that image.
- Gate A is demonstrated on a dedicated fixture build, not on the
  shipped image (whose fixed kernel command line cannot select the
  mode).
- Tier 4 / password recovery is unimplemented; a first install
  remains blocked on it, on the A/B installation milestone, and on
  physical validation.
