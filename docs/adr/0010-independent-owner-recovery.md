# ADR 0010: Independent Owner Recovery (Proposed)

- Status: **Proposed — owner authorization and implementation not yet granted**
- Date: 2026-10-12
- Related: [authenticated graphical session](0008-authenticated-graphical-session.md), [password verifier](0009-source-built-argon2id-verifier.md)
- Scope: future installed Synveil systems; current authenticated QEMU preview remains unrecoverable after password loss

## Problem and security boundary

A graphical desktop or seat-broker failure must not strand the owner, but
failure cannot activate an unauthenticated root shell. Password loss is a
different condition from graphics failure and must not silently reset account
credentials. Synveil must recover with the local AI stopped, missing or
malfunctioning.

The current QEMU preview has a locked root serial console and stores its
authentication record on a separate ext4 state volume. The older
`developer-serial-recovery` marker is a **test-only unsafe override**, not an
owner recovery protocol. Do not repurpose it for physical installation.

## Proposed recovery tiers

1. **Unprivileged diagnosis:** A small deterministic maintenance program
   should boot without the compositor, seat broker, network, or inference
   engine. It may display bounded boot/storage/service status and export
   redacted diagnostics after an explicit local request. It cannot expose
   private files, credentials, authentication hashes, or a root shell.
2. **Authenticated maintenance:** A text-mode local owner prompt on a dedicated
   VT or isolated recovery image should authenticate through a separately
   audited verifier, not a running graphical seat broker. It grants only
   enumerated operations (e.g. disable failed service, restore a known-good
   system generation, inspect validated state-health results). Each action
   requires preconditions and a postcondition check. Never interpret user
   input as arbitrary shell text.
3. **State or upgrade recovery:** An installed layout must have independently
   identified system slots and a known-good boot target, with a bounded
   boot-attempt counter. On failed boot, choose a verified previous system
   generation while leaving the user-data partition untouched. If state
   cannot be authenticated or mounted safely, offer read-only diagnostics
   rather than initializing, reformatting or guessing a block device.
4. **Forgotten password:** Require a separately provisioned, high-entropy,
   owner-retained recovery factor established during trusted enrollment,
   verified offline by the deterministic recovery environment. No default
   token, recovery key in the public image, network dependency, password
   backdoor, silently enrolled factor, or AI-authored reset. Recovery-factor
   rotation/revocation needs normal authentication. A recovery request and
   any account change must be explicitly confirmed and auditable.

The exact secret format, custody method, credential-reset authority, and
cryptographic binding to the installation are **unresolved**. They require
an explicit owner-approved design before password-reset code is enabled.
A corrupt state volume must not make an ordinary password or copied state
file sufficient to authorize recovery; the independent trust anchor needs
a separately reviewed storage and integrity design. Recovery cannot promise
decryption of encrypted user data when required keys have been lost.

## Boot-chain and storage constraints

- Recovery entry must be explicit and local; boot failure alone may offer
  diagnostics but never infer authorization to change credentials.
- Boot-image checksums prove accidental integrity only. Verified/signed boot,
  recovery-image authenticity, and anti-rollback are separate release gates.
- Disk selection must use validated identifiers and cross-check expected
  filesystem metadata; never assume QEMU's `/dev/vdb` names on real hardware.
- No path may format or rewrite an existing filesystem without an independent
  explicit owner confirmation identifying the actual target and expected
  data loss.
- Recovery and normal session paths must share the same trusted credential
  verification rules, while keeping their service lifecycles independent.
- Recovery must not rely on GPU acceleration, a local model, cloud service,
  a healthy desktop, or unrestricted privileged command execution.

## Implementation sequence and evidence required

A. Implement a bounded, read-only recovery status interface and test it in
QEMU with display and seat broker absent. No privileged mutations.

B. Add a local text-mode authenticated maintenance path against synthetic
test-only credentials. Demonstrate successful legitimate authentication,
denial for absent/corrupt/wrong credentials, and no serial-root bypass.

C. Define persistent system-generation layout and rollback metadata; test
interrupted updates and failed boots with *disposable* QEMU disks. Require
that user data remains intact across rollback.

D. Submit the explicit offline recovery-factor specification for owner
approval. Only then implement controlled account recovery, revocation,
logging, tamper tests and disaster-recovery exercises.

E. Record exact commit and complete guest evidence for each gate. Host
fixtures alone are not accepted as recovery validation. Physical
installation remains blocked until the complete recovery path is exercised.

## Read-only diagnostic prototype (host-only until guest validation)

The experimental initramfs now supports an explicit
`synveil.recovery=status` kernel argument. Before attempting any persistent
state mount or starting core, seat, graphics, network, or inference, it prints
only fixed state-mount, core-binary, seat-binary and framebuffer-presence
markers. It accepts no interactive commands or file paths, and then locks
the serial console regardless of the authenticated/developer image profile.
Absence of a mounted state volume in this mode is expected, not proof that
the volume is corrupt. This is not a filesystem diagnosis, owner login,
password reset, usable physical boot selector, or recovery authorization.

Host checks exercise the diagnostic output and serial-root fail-closed order.
An isolated QEMU fixture for the explicit diagnostic boot is now implemented.
It removes the core, seat and graphical executable, requires a fixed read-only
status transcript, and probes that the locked serial console cannot execute
root commands. **Passing host fixtures is not proof of successful native
QEMU recovery; guest evidence at an exact revision is still required.**
do not mark ADR gate A complete until that evidence exists.

## Explicit non-decision

This ADR does not approve or implement password resets, a physical installer,
serial-root access, recovery-key enrollment, an AI emergency bypass or a
privileged generic command runner. It records a proposed dependency order so
non-sensitive recovery diagnostics can proceed independently.
