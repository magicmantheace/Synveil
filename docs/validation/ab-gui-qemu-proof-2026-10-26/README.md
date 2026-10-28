# Graphical update controls — QEMU proof record (2026-10-26)

**Result: graphical update controls DEMONSTRATED in the
authenticated desktop inside Synveil (QEMU).** All four
scenarios pass, ending with `SYNVEIL_AB_GUI_QEMU_PROOF_PASS`
from `tests/smoke-native-ab-gui.py`: the core path — stage,
reboot into trial, confirm — and an owner rollback were driven
**through the GUI** by emulated pointer input; every pass is
gated on the extracted A/B control record, not on the GUI's own
claims. This is QEMU evidence only. **Physical hardware: NOT
validated.** Same proof disk and build as the agent-driven
record (`docs/validation/ab-qemu-proof-2026-10-26/`); the
shipped test image (SHA-256 `6b10b482…`) is untouched.

## Identities

- Proof artifacts built from source commit
  `55d9db88aecc96fdc07d0d695e2855148a69ca4c` (builder's receipt:
  `build-receipt.json` in this directory) — the corrected head
  itself. (First regenerated at `8167ced` after the gate
  correction, then rebuilt and re-run at `55d9db8` once the
  host's `gawk` build dependency was restored; the commits
  between them are documentation only, no guest code changed.
  The receipt and logs here are from the `55d9db8` run.)
- Proof disk: 1,115,684,864 bytes, SHA-256
  `19ca7a06bef9dc3284025c60305f755efb6a4898617e6a7499685b5c46b1c647`.
- Generations: v1 `synveil-gen-v1` UKI SHA-256
  `86c2b7ec7dfa54eeb7cf742ebe0577acf41e4268047c3de6f9e7b9fdeac759b6`;
  v2 `synveil-gen-v2` UKI SHA-256
  `3eb862f94d37cb219d34380d3ca6c62c4b0238428d90bf4c257fc93b7cb6d165`;
  v3 `synveil-gen-v3` UKI SHA-256
  `b9b5d34738e9719bd86f5629466bc19246ac6d48cf0733699fc33dcdb8d5660d`.
- Publisher trust: key id `synveil-dev-2026-01`, **development
  only**; the private key is only in the git-ignored
  `out/ab-proof/trust/` — never in Git, images, or this record.

## What was built

- **Owner confirmation is a real second key.** Promotion of a
  trial no longer follows automatically from passing health
  checks: the guest agent parks a healthy trial at
  `SYNVEIL_AB_HEALTH_PASSED … awaiting=owner-confirm`, and only
  the owner's explicit typed `confirm` action promotes it
  (`86e70b5`). `confirm` re-verifies the running generation
  stamp, the health phase, the slot digest, and an unchanged
  trial record before promoting. A healthy trial the owner
  never confirms consumes one attempt per boot and falls back
  when attempts run out.
- **The GUI** (`src/desktop-wayland/update-window.c`): a native
  Wayland client in the tree's existing pattern (modelled on
  `first-window.c`, wl_shm + FreeType from the image's own
  fonts), running as the authenticated user in the Weston
  session — never as root (it refuses). It shows the running
  and confirmed generations, the trial state with attempts
  remaining, the staged/available update, and the outcome of
  the last action; five buttons — Stage update, Restart into
  trial, Confirm update, Roll back, Refresh — each enabled
  only when the agent's status says the action is meaningful.
  Agent refusals are shown verbatim under a red banner, never
  as success.
- **Typed boundary only.** The GUI speaks the existing agent
  protocol (`synveil.ab-agent/v1` over
  `/run/synveil-ab/agent.sock`): `status`, `stage`, `confirm`,
  `rollback`, `reboot`. No shell, no polkit, no arbitrary
  commands; the agent (root) still verifies signatures, writes
  only the inactive slot, and enforces migration safety —
  the GUI can ask for nothing the protocol does not name.
  Status gains read-only fields for the desktop: per-slot
  summaries, health phase, and an `available` probe of
  attached release media (the same verification staging
  performs).
- **Session integration:** `weston-session` launches the GUI
  only when `/etc/synveil/ab-system` exists, so single-image
  sessions are behaviorally unchanged; the GUI is auxiliary
  (the session neither waits on it nor ends with it). Its log
  (`$HOME/update-gui.log`) is cumulative across sessions — an
  update flow spans a reboot — with fsynced markers; the
  session's readiness check reads only lines past the
  pre-launch byte offset (`8167ced`).

## Scenarios (GUI-driven vs agent-driven, stated plainly)

| Scenario | Driven by | Result | Gate (from extracted control record) |
|---|---|---|---|
| G0 provision/enroll, GUI present | agent (setup) + GUI observed | PASS | GUI window mapped with normal banner; GUI session log has READY + STATUS; credential recorded; known file exact content |
| G1 stage → trial reboot → confirm | **GUI clicks** (Stage, Restart, Confirm) | PASS | All three clicks recorded in the GUI log; all three agent actions ok; confirmed = **2/B/synveil-gen-v2**; credential + known file survive |
| G2 rollback → trial → confirm | **GUI clicks** (Roll back, Confirm) | PASS | Confirmed = **1/A/synveil-gen-v1**; credential + known file survive the rollback |
| G3 bad-signature release | **GUI click** (Stage) | PASS | Agent refusal (signature) recorded; confirmed still v1; no trial created |

The remaining A/B scenarios (failing candidate fallback,
interrupted staging, both-slots-corrupt fail-closed) stay
**agent-driven** in `tests/smoke-native-ab.py` — see the
companion record — because they need fault injection (power
cuts, corrupted slots) that pointer input cannot express; the
split is deliberate, not an omission.

Receipt discipline: the GUI driver applies the same corrected
gate as the agent driver (see the companion record's "Gate
correction"): every scenario's pass is re-derived in
`finish()` from the recorded control records, survival
artifacts (journal-replayed reads), and the GUI's own log;
`gate_violations` in `gui-smoke-receipt.json` is empty and
`graphical_update_controls_demonstrated` is derived, not
asserted. `tests/smoke-gates.py` covers both validators,
including rejecting the first GUI run's receipt pattern
(journal-lagged reads showing the inverted/stale values).

## Failures encountered on the way (root causes, all fixed)

1. `gui_window_rect`'s height scan omitted the title color, so
   clicks computed from a height of 1 landed above the window
   (`b49ed0d`).
2. GUI marker lines were not durable across a hard QEMU quit;
   the GUI now fsyncs them (`b49ed0d`). Host-side STATE/DATA
   reads had the mirror-image bug (ext4 journal lag) and now
   read journal-replayed copies (`1c492f1`).
3. `weston-session` deleted the GUI log at each session start,
   erasing the stage session's evidence at the trial reboot —
   caught by the corrected gate when G1's flow had visibly
   confirmed v2 but the log read showed the stage lines
   missing (`8167ced`).
4. One G0 run missed the window in the find budget while the
   host was still replaying package installs after a VM
   replacement; budget raised and the window/banner fields
   split for diagnosability (`ae4403a`).

## Not demonstrated / open

- GUI-driven failing-candidate and interrupted-update flows:
  agent-driven only (fault injection), stated above.
- A user-*changed* setting surviving update/rollback: no
  settings surface exists yet to change one through; the
  enrollment-time preference file's survival is gated in the
  agent proof (S2/S3 `pref_same`).
- Rollback-unsafe migration refusal shown in the GUI: the
  agent enforces it and the GUI renders agent refusals
  verbatim (proven with the bad-signature refusal in G3), but
  no rollback-unsafe *candidate* fixture exists yet to stage
  through the GUI.
- HTTPS browsing + persistent downloads: **NO** (Priorities
  3–4, not attempted).
- Firmware variables written by any part of this work: **NO**
  — boot selection remains the kexec chain-load design; no
  efivarfs code exists in the tree.
