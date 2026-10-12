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

This is a **single-app prototype** with keyboard and optional delegated
absolute QEMU tablet input: general physical mouse support,
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

## Guest evidence for actual user-terminal lifecycle

The QMP guest test no longer equates a UID1000 tty1 process with a
functional desktop. On a separate test-only authenticated login, it
navigates the Terminal tile using fixed key events, requires a **child
UID1000 sh process** of the graphical dashboard, sends only the literal
shell `exit` built-in and verifies that the **same dashboard PID**
remains alive. It then logs out via Escape and checks full UID1000
relock. Separate checks test Ctrl+Alt+F2 lease revocation, wrong passwords,
and destruction of a user session after killing *only* the root graphical
login process. Host-only fake-QMP fixtures are required to reject any
missing intermediate evidence, but QEMU/image evidence is still pending.

## Reestablishing tty1 line discipline after display crashes

The deterministic root VT launcher now normalizes the authenticated tty1
line discipline before every session start. The UID1000 framebuffer dashboard
uses raw-mode keyboard input, and a SIGKILL cannot execute its normal
`tcsetattr` restore path. Persisting that raw state would break the next
session's fixed interactive terminal. The launcher explicitly re-enables
canonical input, echo, signals and output processing, while resetting common
Linux control bytes. The C self-test exercises reset from disabled flags.
This is source-level implementation pending compiled host and native QEMU
validation; it does not change the unauthenticated developer terminal.

## Reject keyboard-capable evdev descriptors as pointer leases

The developer seat's optional evdev pointer classification explicitly
rejects devices reporting text-keyboard capabilities (letters, digits,
Enter, Backspace, Space, Shift), even if the device also advertises absolute
X/Y and a left button. UID1000 receives a raw event descriptor, not an
OS-filtered pointer stream, so a combined keyboard/tablet would otherwise
allow user code to observe keystrokes outside the intended pointer boundary.
A pure bitmap self-test covers accepted pointer and rejected hybrid cases.
This is a conservative first-QEMU-target filter rather than a production
input security policy; native guest testing remains required.

The evdev pointer classifier has been tightened further: rather than
sampling selected letter and modifier codes, it rejects **every** key code
below Linux `BTN_MISC` while still requiring `BTN_LEFT` and valid ABS axes.
This rejects partial or unusual composite keyboards too. The VT reset now
uses `TCSAFLUSH` so stale raw tty keystrokes cannot flow into the next
canonical session after a crash. Both hardenings remain unverified in QEMU.

## Prevent framebuffer memory inheritance into the user terminal

Closing the inherited framebuffer descriptor alone did **not** protect
its existing `mmap` from `fork`: the fixed UID1000 shell could inherit
a direct mapped view of screen memory without an open device node. The
authenticated session now marks its framebuffer mapping `MADV_DONTFORK`
before accepting input or launching terminals, and **fails closed** if
the framebuffer driver/kernel refuses that protection. No privilege is
added and developer diagnostics retain their previous behavior. A host
fork/mincore fixture confirms the exact isolation helper makes an
anonymous mapping absent in the child; fbdev-specific behavior remains
a native-QEMU runtime gate before installation.

## Prevent same-UID introspection of the running dashboard

Even with framebuffer `MADV_DONTFORK` and close-on-exec descriptors,
a UID1000 terminal program could otherwise inspect its same-UID GUI
parent with `ptrace` or `/proc/PID/mem`. The fixed authenticated
dashboard now calls `PR_SET_DUMPABLE,0` before accepting input, denying
same-UID ptrace-style access to its framebuffer mapping and pointer state.
The change is local to the GUI process; it does not mutate the unprivileged
shell's privileges or confer new authority. A host fork fixture checks
that the exact helper sets `PR_GET_DUMPABLE` to zero. Guest privilege
and session lifecycle checks must still pass before release.

## Reacquire the login keyboard before declaring denial

A rejected graphical authentication attempt must never emit the
`SYNVEIL_GRAPHICAL_AUTH_DENIED` test marker while the evdev keyboard is
closed. The QMP test immediately sends another password after that marker;
an early marker let the next attempt start before EVIOCGRAB, losing keys.
The root login now restores VT2, reacquires the device, repaints, **then**
emits the denied marker. If regrab fails, it reports input unavailable
instead of claiming it is ready. Positive relock evidence follows the same
post-grab rule. The original failing native guest needs to be rebuilt and
retested to determine whether other problems remain.

## First-owner enrollment: separate from developer serial

The root graphical login may provision a credential only with the immutable
auth-desktop marker, an independently mounted persistent /var, a root-private
/var/lib/synveil directory, no existing credential and **no** developer
serial-recovery marker. The keyboard remains exclusively grabbed during
first-entry and confirmation. On match, the GUI launches only the pinned
Rust one-shot enroller through a private CLOEXEC Unix socketpair, with no
password in argv, environment, shell, logs or plaintext files. Both input
buffers are wiped after the attempt; the subprocess has a bounded deadline
and its inherited framebuffer mapping is excluded by MADV_DONTFORK. The
backend does not change existing file-ownership, authentication or seat
policy. Re-enrollment is refused by both UI gating and the Rust verifier.
The new two-boot locked-serial QEMU scenario is pending validation and
owner recovery is still unimplemented.

### Graphical enrollment storage identity guard

In addition to the root-owned private state directory and a separate
mounted device, the onboarding GUI now requires an exact `/dev/vdb` ext4
mount at `/var`, matching the explicitly configured developer QEMU state
disk. A tmpfs-mounted `/var` or another disk cannot appear to enroll a
password that the Rust verifier would refuse after reboot. This is a
**QEMU-only fixed device policy**, not proof of a trusted or encrypted
physical-disk identity: a physical installer must have its own authenticated
volume binding and recovery model. The mount predicate is host-tested against
other disk names, filesystems and mountpoints.
