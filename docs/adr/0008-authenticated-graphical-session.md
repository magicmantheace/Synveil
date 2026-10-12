# ADR 0008: Authenticated Non-Root Graphical Session Boundary

- Status: Accepted (security boundary; developer preview implemented; production pending)
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
8. Preserve explicit developer serial recovery, but do not expose it on
   the authenticated-preview image. A rootfs build marker now locks all
   unprotected serial root-shell paths, including early mount-failure
   recovery, while a second deliberately unsafe developer-only marker can
   re-enable the serial shell in isolated QEMU credential-enrollment tests.
   An owner-facing install still needs first-user enrollment and a separate,
   authenticated recovery environment.

## Seat broker implementation progress

The experimental `veil-seat` runs separately from `veil-core` with a
root-owned mode-0600 Unix socket and a fixed `SVL1` bounded password
frame. Linux SO_PEERCRED checks require the already-privileged developer
graphical peer. It never accepts a client-provided UID, GID, executable,
command or password hash. After Argon2id verification it can only launch
the existing fixed `synveil-vt --authenticated-terminal` path, which drops to
UID/GID 1000 and enables NoNewPrivs.

The authenticating client must retain an open socket lease; disconnect,
crash or explicit client close terminates the fixed terminal parent and
relocks the seat. The fixed launcher now also requests Linux
`PR_SET_PDEATHSIG(SIGKILL)` in a guarded pre-exec child, then checks its
expected parent to close the fork/exit race. Thus a fatal *broker* failure
must kill its VT launcher, which in turn kills the unprivileged child.
A separate fresh QEMU disk and guest recovery case exercise this harder
path rather than treating ordinary logout as proof. Denials and malformed frames cannot start a shell.
The opt-in framebuffer `--login` client now uses this seat-bound password
protocol, with a dedicated graphical tty2, exclusive evdev keyboard capture
for secret entry, explicit tty2 restoration after a tty1 terminal session,
and no unauthenticated Escape-to-menu shortcut. The normal developer menu
remains separate and unauthenticated; this is still a **developer-only**
preview, not a production-grade compositor or installer. Its authenticated
preview profile locks normal serial root recovery; the QEMU enrollment
fixture alone uses the explicitly unsafe developer override.

A dedicated QEMU guest scenario now checks no default login works without
state, and another enrolls a fresh *guest-generated* secret, launches the
real non-root VT through the seat, kills its lease client and checks the
session is gone while core status remains accessible. Full guest evidence
is required before upgrading this progress claim.

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

## Graphical VT/logout hardening (developer preview)

The direct-framebuffer login now explicitly owns VT2, releases its exclusive
Linux evdev keyboard grab before authenticated tty1 use, and restores VT2
before showing the locked screen again. Escape clears password text rather
than dismissing the login. The isolated guest graphical smoke has separate
observations for invalid-password denial, normal `exit`/relock, a second
login, and a GUI crash that revokes the seat lease. These source-level
checks are **not** claims of QEMU success until a local native target run
provides evidence from the corresponding exact Git commit.

The `start-desktop` supervisor now rejects a symlink, directory, or
permission-relaxed authentication marker instead of falling back to the
unauthenticated developer menu. A dedicated offline policy fixture checks
both the normal developer profile and this locked-profile fail-closed
behavior. An opt-in QEMU graphical profile still requires native guest
validation and is not an installation image.


## Authenticated VT lease revocation (implemented; validation pending)

The root-owned seat broker now reads the kernel's `VT_GETSTATE` for
`/dev/tty0` while its fixed non-root terminal is active. A five-second
startup deadline allows tty1 to activate; after tty1 has been observed,
a switch to any other virtual terminal terminates and reaps that session
before another login is accepted. An unreadable or invalid VT state fails
closed. This closes the obvious path of switching back to a hidden unlocked
tty1 terminal from the login screen. Polling is bounded to 100 ms; it does
not claim to detect a leave-and-return between polls or replace a full
production seat manager. The existing socket lease and parent-death
cleanup still apply.

Host unit cases cover initial activation deadlines, switching away from
tty1 after authentication, and loss of VT visibility. The dedicated QEMU
graphical-login smoke now injects *only* a fixed Ctrl+Alt+F2 chord using
the private QMP test channel, then requires the login surface to reappear
and no living UID1000 shell to remain, before exercising normal logout
and GUI-crash cleanup in two further sessions. The fixture is a test
specification, **not a passing QEMU result**. GitHub Actions are unavailable
for this development period; local offline checks and commit-matched guest
runs remain the source of validation evidence.


## Bounded login-frame processing (host implementation; guest validation pending)

The seat broker now applies its existing monotonic denied-login cooldown
*before* receiving the next password frame. Header and secret payload use a
single four-second monotonic deadline rather than a resettable per-read socket
timeout, preventing trickled input from indefinitely occupying the serial
single-client authorization loop. A malformed or incomplete frame releases
the authenticating state without granting authority. Unit cases cover a
slowly delivered but otherwise valid frame and a throttled peer that sends
no password bytes. These changes do **not** make the in-memory cooldown
persistent across broker process restarts; nor do they substitute for a full
QEMU login, timeout and recovery run.

## Bounded login virtual-terminal recovery (host implementation; guest pending)

The root graphical login no longer performs an unbounded `VT_WAITACTIVE`
when taking back tty2. It issues `VT_ACTIVATE` and polls the kernel's
`VT_GETSTATE` against a two-second monotonic deadline. A missing or stuck
VT fails the display process so the existing three-attempt desktop
supervisor can apply its recovery budget instead of hanging forever.
The display self-test includes deadline-boundary assertions. This does not
prove that tty2 restoration or terminal cleanup works on a real QEMU
console; those still need exact-image guest validation.

## Bounded graphical seat-response handling (host implementation; guest pending)

The framebuffer login client now uses a single monotonic four-second deadline
for the complete fixed authorization reply, rather than allowing a peer to
extend a per-`recv` timeout by slowly sending bytes. A small self-test
exercises both a complete `ACTIVE` response and a truncated response from
a peer that remains connected. After a successful response the existing
socket lease still remains open until the session exits. QEMU is required
to validate the display and broker together.

## UID1000 graphical session prototype (implementation; guest validation pending)

Authenticated seat login now launches a **fixed source-built graphical user
session** instead of immediately executing an interactive shell. The root
VT helper owns device activation, opens the framebuffer, forks, then drops
UID/GID and supplementary groups before exec. Only its inherited fd 3
allows UID1000 to draw on the kernel framebuffer; the user session does
not receive root or device-node permissions. It reads keyboard navigation
from its own tty1 in raw mode, shows Status/Storage/Network/Terminal/Settings
via the existing small source-built renderer, and uses the private
UID1000 home for non-authorization display preferences. Terminal selection
starts only a fixed UID1000 shell; exiting returns to the user dashboard.
Escape from the top-level launcher logs out. The seat broker still owns the
socket lease and revokes the process chain on lost login display or VT switch.

This is a **keyboard-first, single-app prototype**: graphical mouse input,
window management, process-tree/cgroup teardown guarantees, interactive
first-user enrollment, screen locking and complete guest tests remain
open. It does not promote the root diagnostic menu to a user desktop, and
is not ready for an owner install.

The native graphical-login QMP test now waits for a real UID1000
`synveil-display --session` process attached to tty1 before testing VT
switch and normal logout. Its logout sequence uses one fixed Escape key,
not the old shell's `exit` command; the fake-QMP host fixture asserts
that behavior. This remains an **unexecuted guest test** until the
matching native image is built and booted.

## Mouse input and device lease hardening (implemented, unvalidated)

The fixed root VT launcher now optionally opens one Linux evdev pointer with
a left button and valid absolute X/Y axes. It delegates only this read-only
descriptor to the authenticated UID1000 framebuffer dashboard; the user
process never receives root privilege or general /dev/input access. It uses
the existing tile hit-testing and pointer cursor renderer. A missing or
unplugged pointer leaves keyboard navigation functional. Before opening
the fixed non-root shell, the dashboard closes the original inheritable
framebuffer/pointer descriptors and retains only CLOEXEC copies, so the
interactive shell does not receive these handles.

The initial target is QEMU's absolute USB tablet, not all physical mice.
Pointer isolation, guest rendering, logout, and device revocation require
native QEMU validation. No full non-root desktop installation milestone is
marked complete by this source change.

## Login VT restoration on ambiguous denial

The graphical login always attempts a bounded return to tty2 after a
credential submission, not only after a received ACTIVE response. A
disconnected socket or interrupted response can follow successful broker
authentication and a tty1 activation. The UI must never repaint a locked
password prompt or reopen the login keyboard while the authenticated VT
remains in front. If tty2 cannot be restored, it exits to the bounded
supervisor and does not report relock.

The graphical guest crash check now kills **only the root-owned login
display**, identified by both UID 0 and its fixed '--login' command
line, and then checks whether the separate UID1000 session died. Killing
both processes would not establish socket-lease revocation.
