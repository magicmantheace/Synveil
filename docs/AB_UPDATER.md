# Bootable A/B OS updater with rollback — design (2026-10-25)

Status: **implemented in Pass B (guest agent + two-slot assembly +
QEMU proof); demonstration status lives in the validation record,
not here.** This document is the design contract for turning the
signed regular-file transaction fixture (`docs/VM_AB_UPDATE.md`,
`tools/ab_vm_update.py`) into a working bootable A/B updater for
the native joined image. It does not replace Synveil with another
distribution, and it does not weaken any existing boundary: the
credential authority, Argon2id protections, non-root sessions and
fail-closed authentication are untouched.

Two corrections from the independent review of the Pass A design
(2026-10-25) are incorporated below and marked where they land:
migration safety is **bidirectional**, and boot selection writes
**no firmware variables at all**.

## Layout (extends ADR 0011)

One GPT disk, partitions identified by PARTUUID, never by enumeration
name:

| Partition | Contents | Written by updates? |
|---|---|---|
| ESP | `EFI/BOOT/BOOTX64.EFI` = a plain copy of generation A (the chain entry point, see below); `EFI/synveil/recovery.efi` = recovery-status UKI (ADR 0010 tier A) staged at assembly | No |
| SYSTEM_A | FAT, `EFI/synveil/gen.efi` (one generation UKI) + `gen.json` identity | Only while inactive |
| SYSTEM_B | same | Only while inactive |
| STATE | ext4 — credentials, settings, **update control record + history** | Never by slot writes |
| DATA | owner files (home, downloads etc.) | Never |
| RECOVERY | reserved for the ADR 0010 recovery program | Never |

A *generation* is exactly the joined image's boot payload: one UKI
(kernel + initramfs rootfs, the same artifact `tools/native_joined.py`
produces per build) plus a build-time stamp (`generation.json`:
version, source commit, state format, and a unique `generation_id`).
Generations are whole and immutable; a slot holds one generation,
identified on disk by `gen.json` carrying the same `generation_id`.

## Boot selection — inside Synveil's own boot path (no firmware writes)

**Standing boundary (owner, 2026-10-25): firmware-variable changes
of any kind — BootOrder, BootNext, Boot#### — require the owner's
explicit authorization. The updater does not hold it and writes
none.** An earlier draft of this design proposed replacing
BootOrder and arming trials with BootNext; that is rejected: it
mutates permanent firmware configuration and could displace other
systems' startup (e.g. Windows). This design preserves existing
firmware entries and normal startup of anything else on the
machine, with the Synveil disk connected or not, by construction:
nothing firmware-visible changes when an update is staged,
trialled, confirmed, or rolled back.

Selection instead works like this:

- The firmware boots *some* Synveil generation exactly as it does
  today: the ESP fallback file on removable media, or an entry the
  owner picked once in the firmware menu. That pick is the only
  firmware interaction, and it is the owner's, not the updater's.
- Every generation's **init hook** (before any service starts)
  reads the control record on STATE and computes the desired
  generation: the pending trial if one is valid, else the
  confirmed generation. Identity is by `generation_id` from the
  build-time stamp — firmware state (BootCurrent and friends) is
  never consulted, so it can never disagree with the record.
- If the running generation is not the desired one, the hook
  **chain-loads** the desired slot's image with the
  `kexec_file_load` syscall (kernel `CONFIG_KEXEC_FILE=y`) and
  reboots into it. Before the transfer it verifies the target
  slot's bytes against the record (digest + size). The target
  generation's own hook then finds itself desired and proceeds to
  services. Convergence takes at most one extra hop from any entry
  point, including a stale fallback copy whose slot has since been
  re-staged.
- If no chain is needed or possible — running generation is the
  confirmed one and a chain failed — boot proceeds on the
  confirmed generation. If the running generation is *not* the
  confirmed one and the chain to the desired generation cannot be
  made, the hook fails closed: it runs the read-only recovery
  diagnostics in place and holds; the wrong system never runs and
  no service starts.

## Attempt accounting — before control transfer, and before services

1. **Before a trial can transfer control**, the generation handing
   over decrements `attempts`, sets `awaiting_health`, sets
   `chain_in_flight`, and persists the control record (atomic
   tmp+rename+fsync on STATE) — all *before* `kexec_file_load`.
   If the trial generation is booted directly (an owner menu pick
   of its file), its own hook performs the same decrement before
   services instead; `chain_in_flight` distinguishes the two so no
   boot is ever accounted twice, and no trial boot is ever
   unaccounted.
2. **Exhaustion**: when a boot finds the trial with `attempts`
   already 0 (and not the in-flight transfer delivering that very
   boot), it clears the trial with a history entry and proceeds —
   or hands over — as the confirmed generation. No firmware
   fallback is involved at any point; the confirmed generation was
   never displaced.
3. A candidate that cannot mount STATE or parse the record never
   starts services: its init falls into the existing fail-closed
   paths, and the next boot from any other entry point expires the
   trial per rule 2 once attempts are spent.

## Health confirmation — two-key (health + owner)

Confirmation takes two keys, and either one alone does nothing:

1. **Health.** The agent's health checks must pass in the running
   candidate: STATE mounted read-write and the control record
   re-read intact; the credential record parses under the same PHC
   machinery the verifier uses (a full password verification
   happens at the graphical login); veil-core answers a status
   probe on its socket; the Weston session is live (Wayland
   socket, Weston and the seat broker running); the running
   generation's stamp identity matches the trial record and the
   slot it occupies still hashes to the staged digest. The agent
   runs these automatically on a trial boot and records the
   outcome (`SYNVEIL_AB_HEALTH_PASSED` / the failing check); it
   never promotes on health alone.
2. **Owner.** The owner then issues the typed `confirm` action —
   the desktop update GUI's Confirm button, or `veil-ab request
   confirm`. The action re-verifies that the running system is the
   trial generation, that this boot's health outcome is *passed*,
   and that the candidate slot still hashes to the trial record,
   and only then promotes: `confirmed = trial`, the trial is
   cleared, and a history entry is appended — nothing else; there
   is no firmware state to promote or clear.

Any health failure: reboot unpromoted; attempts bound the retries;
exhaustion leaves the confirmed generation in charge, and the
failed candidate stays in its slot, marked in history, until an
owner decision or a newer stage overwrites it. A healthy trial the
owner neither confirms nor rolls back is not silently promoted
either: each further boot consumes another attempt, and
exhaustion falls back the same way. (This two-key split is the
owner Priority 2 amendment of 2026-10-26; Pass B's original design
promoted on health alone. The host core `tools/ab_update.py`
always modelled `confirm` as an explicit operator action — the
guest now matches it.)

## Update transaction (running system)

Performed by the update service through the existing typed privilege
boundary (the desktop never gets a root shell; the Priority 2 GUI
below is wired to exactly these actions):

1. Fetch release metadata + payload from the configured source
   (in the QEMU proof: a local release medium identified by
   PARTUUID; the transport is not the trust mechanism).
2. Authenticate: Ed25519 signature over the exact metadata bytes,
   verified against the **pinned publisher key** (below). Then
   verify the payload's SHA-256 and size against the metadata.
3. Refuse, before any write, on: bad signature; expired or
   not-yet-valid metadata; version not strictly greater than the
   confirmed version (**replay/downgrade refusal** — a re-offered
   old release is rejected even if validly signed); migration
   window violated (below); a trial already pending; confirmed
   slot failing its digest check (never build on a corrupt base).
4. Write the payload to the **inactive slot only**, fsync, read back
   and re-verify the digest from the slot itself. The running and
   confirmed slot is never opened for writing.
5. Persist the trial record (accounting rule 1) and record the
   staged generation in the slot map; report "restart to try the
   update". Nothing is armed in firmware — the next boot's hook
   does the hand-over. Interruption at any point before step 5
   leaves the system exactly as it was; interruption during step 4
   leaves an unverifiable inactive slot that no record points at —
   it is inert.

## Publisher trust

- The verification key is **provisioned independently of any
  update**: it is pinned into the system generation at build time
  from the release-signing infrastructure, and an update can never
  introduce, replace, or extend trust. A release that supplies its
  own key is refused by construction (the verifier has no input for
  a key).
- The current key is a **development key**, identified as
  `synveil-dev-2026-01` in metadata and in every record; its private
  half exists only in the (git-ignored) fixture/proof area, never in
  Git, images, logs, or CI artifacts. Production requires a
  separately generated key provisioned the same way; the mechanism
  does not change.
- Rotation: a metadata field `next_key` may *announce* a successor
  fingerprint, but the successor becomes trusted only when a
  generation carrying it (pinned at build time) is itself confirmed
  — trust moves with confirmed generations, never with metadata
  alone. Revocation: a generation whose pinned key set excludes a
  compromised key refuses its signatures; because downgrades are
  refused, revocation takes effect with the next confirmed update
  and cannot be rolled back past.
- Signature verification is distinct from Secure Boot (the images
  remain unsigned for firmware purposes) and from reproducible-build
  claims (none are made).
- In-guest verification route (Pass B, settled): veil-core carries
  small in-tree SHA-256/SHA-512 and Ed25519 verify-only
  implementations — no new crates, preserving the `--locked
  --offline` build discipline and the pinned registry. Provenance:
  the field arithmetic follows RFC 8032's reference formulas, the
  SHA-512 constants were recomputed from first principles during
  development (which caught a transcription error), and both are
  covered by published test vectors plus OpenSSL-generated
  known-answer vectors in the crate tests.

## Migration compatibility

Metadata carries `state_format` (the STATE layout version the
generation writes) and `min_reader_state_format` (the oldest STATE
format the generation can read); a generation's **readable range**
is `[min_reader_state_format, state_format]`. The Pass A core
originally checked only the forward direction; the independent
review reproduced an acceptance that stranded rollback, and the
rules below — implemented identically in `tools/ab_update.py` and
the guest agent, with tests — are the correction:

- **Stage, forward**: the candidate must be able to read STATE as
  the confirmed generation leaves it:
  `candidate.min_reader_state_format <= confirmed.state_format <=
  candidate.state_format`.
- **Stage, rollback direction**: after the candidate runs, STATE
  will be at the candidate's format, and the confirmed generation
  — the rollback target — must still be able to read it:
  `confirmed.min_reader_state_format <= candidate.state_format <=
  confirmed.state_format`.
- **Owner rollback**: arming a rollback to a retained generation
  is refused unless the current STATE format lies within the
  retained generation's readable range. Discarding a trial that
  already booted is likewise refused if the trial may have
  advanced STATE beyond the confirmed generation's readable range.
- A candidate whose metadata declares `rollback_breaking: true`
  is always refused in this milestone (the field exists so the
  refusal is explicit, not silent).
- Credentials are never migrated by an update: the credential store
  format is generation-independent, and a generation that cannot
  parse it fails health confirmation and falls back.

## Both slots unusable

Digest checks happen where selection happens — in the boot hook.
If the confirmed slot's bytes no longer match the record, no
Synveil generation will run as the system: whichever generation
the firmware loaded fails closed into the read-only recovery
diagnostics (ADR 0010 tier A) and holds, with STATE and DATA
untouched. The recovery-status UKI also remains on the ESP at
`EFI/synveil/recovery.efi` for an owner firmware-menu pick. If no
generation file can be loaded at all (every slot and the fallback
copy corrupt), recovery for removable media is rewriting the
media — that is media recovery, not installed-system recovery,
and this design does not claim otherwise. Interactive
installed-system recovery beyond diagnostics remains ADR 0010's
open program. Credentials, home, settings, and history survive
every failure above because they were never inside a slot.

## Graphical update controls (Priority 2)

`synveil-update-gui` (`src/desktop-wayland/update-window.c`) is an
ordinary unprivileged Wayland client in the authenticated session,
launched by `weston-session` only on A/B generations (the
`ab-system` marker gates it; a single-image session is unchanged).
It is a face on the agent, nothing more:

- Its only channel is the agent's typed socket
  (`synveil.ab-agent/v1`; actions `status`, `stage`, `confirm`,
  `rollback`, `reboot`). It sends fixed action words and nothing
  else — there is no text input in the window, no command line, no
  shell anywhere in the path, and the client runs as the session
  user (it refuses to run as root).
- Everything it shows comes from the agent's `status` reply at
  request time: running generation, confirmed generation, trial
  (with attempts remaining), this boot's health outcome, the slot
  map, the publisher key (with the development-key fact on its
  face), the update history, and an `available` probe that mounts
  the release media read-only and runs the same signature
  verification staging uses, so "an update is available" on screen
  means a signed one is. It refreshes every few seconds and after
  every action, and a refresh never overwrites an action's message.
- Buttons are gated by the same state (Stage needs media attached
  and no pending trial; Confirm needs the trial running with
  health *passed*; Restart needs a trial waiting to boot), but the
  gating is presentation only — the agent re-checks everything.
- Refusals are shown as refusals: a bad signature, a
  rollback-unsafe migration, a failed health check, or no media
  appears on screen in the agent's own words under an error banner,
  never as success, and the state rows continue to show the record
  as it actually is.

## Fixtures vs installed operations

`tools/ab_vm_update.py` remains a host fixture. The transaction core
specified here is implemented once in
`tools/ab_update.py` (schema v2: pinned trust, validity window,
bidirectional migration window, history, owner rollback with the
readability guard, both-slots outcome) with host tests, and
mirrored in the guest by the update agent (`veil-ab`) and the
early-init accounting hook, which are separate code held to the
same record schema (`synveil.ab-control/v2`). Host-fixture results
are never presented as guest boot evidence; the QEMU proof (real
two-slot disk, real boots, chain-load hand-overs) is its own
record under `docs/validation/`.
