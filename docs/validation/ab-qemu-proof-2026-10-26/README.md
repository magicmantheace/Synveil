# A/B bootable update/rollback — QEMU proof record (2026-10-26)

**Result: bootable update/rollback DEMONSTRATED in QEMU.** All nine
scenario records pass on one disk, in sequence, ending with
`SYNVEIL_AB_QEMU_PROOF_PASS` from `tests/smoke-native-ab.py`.
This is QEMU evidence only. **Physical hardware: NOT validated**
(the proof disk is a separate artifact from the shipped test
image; the shipped image, SHA-256
`6b10b482239ca3e771bd20d85a5f4e0dc414a00fc34334408eb134b150bacfc3`,
is untouched by this work).

> **CORRECTION (2026-10-26, later the same day).** The receipt
> originally committed with this record was **defective**: it
> recorded S2 "v2 confirmed" with confirmed version **1**/slot A
> and S3 "rollback to v1" with version **2**/slot B (exactly
> inverted), S0's known file as **null**, and still claimed
> `bootable_update_rollback_demonstrated: true` — the driver's
> success gate trusted the scenarios' own pass flags without
> checking that the extracted control records matched what each
> scenario claimed. An independent review caught it. The gate
> has been rewritten (see "Gate correction" below), the
> artifacts rebuilt, and **both proofs re-run end-to-end**; the
> receipt, build receipt, and scenario logs in this directory
> are the regenerated ones, and every recorded field below was
> re-verified against them. The original claim was withdrawn
> until this regeneration completed.

## Identities

- Proof artifacts (disk + generations) built from source commit
  `55d9db88aecc96fdc07d0d695e2855148a69ca4c`
  (`build-receipt.json` in this directory is the builder's own
  receipt). The artifacts were first regenerated at `8167ced`
  after the gate correction, then **rebuilt and both proofs
  re-run at `55d9db8`** — the corrected head itself — after the
  host's `gawk` build dependency was restored (the rebuild had
  been blocked by its absence; `build/scripts/doctor.sh` now
  passes in full). Commits between `8167ced` and `55d9db8` are
  documentation and this record only; no guest code changed.
  The receipts and logs in this directory are from the
  `55d9db8` runs.
- Proof disk: 1,115,684,864 bytes, SHA-256
  `19ca7a06bef9dc3284025c60305f755efb6a4898617e6a7499685b5c46b1c647`.
- Generations (UKI = kernel EFI-stub bzImage with embedded
  initramfs; identity stamped at build time in
  `/etc/synveil/generation.json` and the slot's `gen.json`):
  - v1 `synveil-gen-v1`: 61,170,688 bytes, SHA-256
    `86c2b7ec7dfa54eeb7cf742ebe0577acf41e4268047c3de6f9e7b9fdeac759b6`
    (Weston background `0xFF101820`)
  - v2 `synveil-gen-v2`: 61,162,496 bytes, SHA-256
    `3eb862f94d37cb219d34380d3ca6c62c4b0238428d90bf4c257fc93b7cb6d165`
    (background `0xFF0B3D2E` — the observable change)
  - v3 `synveil-gen-v3`: 60,503,040 bytes, SHA-256
    `b9b5d34738e9719bd86f5629466bc19246ac6d48cf0733699fc33dcdb8d5660d`
    (background `0xFF3D0B0B`; `veil-seat` replaced by an exit-1
    stub so the *real* health path fails it — no check weakened)
- Publisher trust: key id `synveil-dev-2026-01`, **development
  only**. The private key exists only in the git-ignored
  `out/ab-proof/trust/` directory and is not in Git, any image,
  or this record. Guest verification is in-tree Ed25519
  (veil-core `ed25519.rs`, verify-only; the offline cargo
  registry contains no signature crates to pin, so the
  implementation is in-tree with FIPS 180-4 + RFC 8032 /
  OpenSSL-generated known-answer tests).

## Design as proven (differs from the original Pass A sketch)

- **Boot selection writes NO firmware variables.** The firmware
  loads the ESP fallback (a permanent copy of generation A);
  whichever generation runs, its early-init A/B hook reads the
  control record on STATE and converges to the desired slot
  (pending trial first, else confirmed) by chain-loading that
  slot's UKI with `kexec_file_load` (cmdline NUL-terminated,
  slot bytes re-hashed against the record before transfer).
  BootOrder / Boot#### / BootNext are never read or written;
  normal Windows startup is untouched by construction.
  (`tools/ab_update.py`'s efivarfs BootNext design from the
  first Pass B iteration was deleted, commit `892469b`.)
- **Attempt accounting before services:** the hook decrements
  the persisted trial attempt record before any service starts;
  an exhausted trial is expired by the hook itself and the
  confirmed generation boots in the same power cycle. A trial
  that boots directly (not via chain) decrements itself. A
  `chain_in_flight` flag prevents double accounting across the
  hand-over.
- **Fail closed:** if chain-load of the desired slot fails on a
  non-confirmed generation, or the running generation's own slot
  digest does not match the record, the hook runs the
  recovery-status diagnostics and holds — the wrong system
  never runs.
- **Bidirectional migration safety:** a candidate is staged
  only if it can read the current STATE format **and** the
  confirmed generation can read the candidate's format
  (readable range `[min_reader_state_format, state_format]`);
  owner rollback applies the same check to the retained
  generation, and refuses to discard a booted trial whose
  writes the confirmed generation cannot read. Host core:
  `tools/ab_update.py` (`tests/ab-update.py` 18/18); the guest
  agent mirrors the same rules.
- **In-guest agent** `veil-ab` (in veil-core): verifies release
  signatures against the build-time pinned publisher
  (`/etc/synveil/publisher.json`), writes only the inactive
  slot, re-hashes on read-back, and persists the trial record
  before any transfer. Health checks on a trial boot: STATE
  read/write, credential PHC parses (Argon2id), veil-core
  status socket answers, Wayland session live
  (`/run/user/1000/synveil-0` + weston + veil-seat), slot
  digest equals the trial record, running stamp identity.
  Health deadline 300 s; on failure the agent reboots and the
  attempt accounting handles the rest. Action socket
  `/run/synveil-ab/agent.sock`, schema `synveil.ab-agent/v1`
  (status / stage / rollback / reboot), driven in the proof by
  five fixed QMP-typed commands — never arbitrary text.

## Scenarios (all on the one proof disk, in order)

Machine-readable detail: `smoke-receipt.json` (per-scenario
pass/fail plus the extracted control records and survival
hashes). Raw serial logs (one per boot; S1+S2 share a boot by
design) are committed alongside this README and retained with
the build outputs under `out/ab-proof/smoke-evidence/`.

| Scenario | Result | What it proves |
|---|---|---|
| S0 provision/enroll | PASS | Fresh disk enrolls; account credential, and a known file on DATA (`/home/synveil/ab-known-file`) created as survival baseline |
| S1 stage v2 | PASS | Signed v2 release staged from release media into the inactive slot; trial record persisted |
| S2 trial v2 confirmed | PASS | Reboot chain-loads v2; observable change verified by framebuffer color proof; graphical login works; health checks pass; agent confirms v2; credential + known file unchanged |
| S3 owner rollback to v1 | PASS | Owner rollback arms a trial of retained v1; v1 boots, logs in, color-proves v1, health-confirms; credential + known file survive the rollback |
| S4 failing candidate | PASS | v3 (seat stubbed) staged from v1; three trial boots each end in `SYNVEIL_AB_HEALTH_FAILED check=session` and reboot; attempts 3→2→1→0 accounted across boots; the entry hook then prints `SYNVEIL_AB_TRIAL_EXHAUSTED` and boots confirmed v1 in the same cycle; login succeeds; trial record cleared; survival holds |
| S5 invalid signature | PASS | Corrupted-signature release refused in-guest (`release signature verification failed`); no trial created; confirmed slot untouched (v1, digest identical) |
| S6 interrupted staging | PASS | Power (QMP quit) cut the moment `SYNVEIL_AB_STAGE_COPYING` prints; no `SYNVEIL_AB_STAGED`; control record afterwards: no trial, confirmed untouched |
| S6b stage after interrupt | PASS | The system still boots its confirmed generation, a fresh stage of v2 succeeds, and an owner rollback decision discards the pending (never-booted) trial |
| S7 corrupt slots fail closed | PASS | On a branch copy of the disk, both slot images' `gen.efi` are zeroed while the ESP fallback copy is intact; the hook's digest check refuses (`SYNVEIL_AB_CHAIN_FAILED` on the entry generation, then the fail-closed path prints `SYNVEIL_AB_FAIL_CLOSED` + recovery diagnostics `SYNVEIL_RECOVERY_STATUS_V1`); `SYNVEIL_LOGIN_READY` never appears — no wrong system runs |

## Saved-setting leg — survival gated, change still not demonstrated

Adam's sequence asks that a saved setting survive update and
rollback. The regenerated proof now **gates** on it: S2 and S3
require `pref_same` (the desktop preference file's hash from
STATE identical before and after) alongside credential and
known-file survival, and both pass. What remains **not
demonstrated** is a setting *changed by the user* surviving:
the desktop still exposes no user-facing settings surface
through which a setting can be changed (the greeter's arrows
are inert, proven by pixel-identical captured frames), so the
preference file that survives is the enrollment-time one.

## Both-slots-dead recovery — what S7 does and does not claim

The chain design has no ESP recovery *entry point* in the old
design's sense; the ESP fallback is the generation-A entry
point, and recovery from a both-slots-corrupt disk on removable
media is **media rewrite** (the same story as the shipped test
image: rewriting the media is the recovery; it is not
installed-system recovery). S7 claims only the fail-closed
behavior above: corrupt slots are detected by digest and the
machine holds at recovery diagnostics instead of running a
wrong or partial system.

## Failures encountered on the way (root causes, all fixed)

Recorded here because the fixes are part of the deliverable
(all in `tests/smoke-native-ab.py` / guest code, commits noted
in git log `66c50b6..a784616`):

1. `kexec_file_load` EINVAL — the kernel requires the cmdline
   buffer to include its NUL terminator; the hook passed
   `/proc/cmdline` verbatim (`5f55ab3`).
2. Health session probe searched for a `wayland-*` socket; the
   session's display is named `synveil-0`, so no trial could
   ever confirm (`99cca31`).
3. Marker-prefix collisions in the driver: a bare
   `SYNVEIL_AB_CONFIRMED` wait matched `SYNVEIL_AB_CONFIRMED_BOOT`
   from the entry generation of the same boot (one S2 result was
   a false pass before this was caught and the wait made exact
   + anchored).
4. Cross-boot line-buffer hazard: post-reboot logins matched the
   previous boot's `LOGIN_READY`/`AUTH_ACTIVE` lines; login()
   now takes the caller's boot-cycle mark.
5. `Boot.seen()` is prefix-based; S5's mid-line substring check
   could never pass (the guest refusal itself was correct).

## Gate correction (2026-10-26, later) — what was wrong and what changed

The independent review of the original Pass B head found four
defects; all four are fixed and the fixes are part of this
record:

1. **Proof success gate defective** (above). Both drivers now
   re-derive every verdict from the recorded artifacts:
   confirmed version+slot+generation must equal the scenario's
   expectation (`2/B/synveil-gen-v2` after the update,
   `1/A/synveil-gen-v1` after rollback/fallback/refusals);
   survival requires real artifacts on **both** sides (a file
   missing on both sides fails — `None == None` no longer
   passes); STATE/DATA reads run on journal-replayed copies
   (`e2fsck -y` on a copy; the evidence disk is never modified);
   `finish()` writes `gate_violations` into the receipt and
   returns non-zero if any exist. `tests/smoke-gates.py`
   (12 tests, no QEMU) proves the validators reject the exact
   original defect pattern, fabricated-consistent receipts
   pass, and missing/false fields fail (`ff17803`).
2. **`tools/ab_release.py::build_media()` truncated existing
   outputs.** It now refuses an existing output of any kind,
   an existing staging path, a symlink parent, and a missing
   parent — all before creating anything; outputs use
   exclusive creation. `tests/ab-release-crosscheck.py` proves
   refusal leaves bytes unchanged (7 new tests, 10/10 total)
   (`d46a8c5`).
3. **`cargo fmt --all --check` failed** at the reviewed head;
   fixed (`6866c35`), check clean, `cargo test` green.
4. **The saved boot proof predated the owner-confirmation +
   GUI changes.** Both proofs were re-run at the corrected
   head on the rebuilt artifacts; this record and the GUI
   record (`docs/validation/ab-gui-qemu-proof-2026-10-26/`)
   hold the regenerated receipts.

Two further evidence bugs were found by the corrected gate
during regeneration and fixed: the known-file fixture wrote
without `sync`, so the file could sit at 0 bytes in the guest
page cache when QEMU quit (fixture now syncs, `05c12d6`), and
`weston-session` deleted the update GUI's log at every session
start, erasing the stage session's evidence at the trial
reboot (log is now cumulative with an offset-aware readiness
check, `8167ced`).

## Not in this pass

- HTTPS browsing + persistent downloads inside Synveil: **NO**
  (Priorities 3–4, not attempted here).
- Graphical update controls: **demonstrated** — see
  `docs/validation/ab-gui-qemu-proof-2026-10-26/` (same disk,
  same build; the core path stage → trial reboot → confirm and
  a rollback were driven through the GUI in the authenticated
  desktop).
- Physical boot of the A/B disk: not attempted; QEMU TCG only.
