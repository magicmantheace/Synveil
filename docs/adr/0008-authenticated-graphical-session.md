# ADR 0008: Authenticated Non-Root Graphical Session Boundary

- Status: Accepted (security boundary; implementation pending)
- Date: 2026-10-11
- Supersedes: none
- Superseded by: none

## Context

The direct-framebuffer VM prototype starts a root-owned diagnostic launcher
and can explicitly open a UID 1000 demo terminal. That is suitable for QEMU
bring-up, not a login manager or a user-facing desktop. Adding a password
dialog inside the existing launcher would silently merge rendering, device
input, authentication, and privileged session authority.

The first user test must have a graphical session without exposing an
unauthenticated root shell as a normal desktop capability. Serial recovery
must remain independent of graphics and of any optional intelligence layer.

## Decision

1. Treat the current direct-framebuffer launcher, locked demo account, and
   developer root console as a **gated developer VM** interface only.
2. Introduce a small deterministic, root-owned *seat/session supervisor*
   that owns authentication transitions and grants only the devices needed
   for the active local session. It must not execute arbitrary command text,
   accept an AI-issued login assertion, or provide a generic root shell.
3. Authenticate users through a separately auditable local credential verifier
   with root-owned credential storage. Select and pin its source-built
   password-hashing/verification dependency in a follow-up ADR before coding
   the credential protocol. Never implement an ad hoc password hash.
4. Only after successful verification may the supervisor create a user session,
   set supplementary groups, drop to the target non-root UID/GID, enter a
   private user home, and launch the allowed graphical programs. Session
   processes never inherit root authority from the supervisor.
5. Model session state explicitly: locked -> authenticating -> active ->
   logging out -> locked. Restarting the GUI must not automatically restore
   an authenticated session. Terminal launch belongs to the active user's
   non-root session; development recovery remains separately gated.
6. Keep authentication material and transient session state out of desktop
   preferences, AI memory, and general telemetry. No saved preference grants
   authority. Local operation must not require a network connection.
7. Revoke device access, clean session-owned processes, and recover the
   lock/login surface on logout, GUI crash, or supervisor recovery.
8. Preserve serial recovery independently. Before an owner-facing image, replace
   or explicitly protect the bootstrap's unauthenticated privileged serial
   shell. A production login must never assume an unlocked developer console.

## Implementation sequencing

- Stabilize QEMU graphics, storage, UI persistence and graphical crash
  recovery, with evidence attached to exact commits.
- Add a testable session-state protocol and fake-verifier/denied-login host
  fixtures; these do not provide real authentication.
- Choose a source-built credential verifier and write its security-specific
  ADR, then integrate authenticated login with a non-root graphical process.
- Verify denied/valid login, logout, repeated crashes, locked-state reboot,
  permission ownership, missing/corrupt state and independent serial recovery
  in QEMU before user-facing installation.

## Consequences and non-goals

This ADR fixes the **trust boundary** but does not select PAM, Wayland,
a particular hash library, or a windowing framework. The existing framebuffer
diagnostic remains a temporary developer interface and is not promoted to a
secure display manager by this decision. No current QEMU success is evidence
of authenticated graphical sessions.
