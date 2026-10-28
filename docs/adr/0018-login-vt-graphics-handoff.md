# ADR 0018: Graphical login hands off the VT by dropping KD_GRAPHICS

- Status: Accepted
- Date: 2026-10-25
- Supersedes: none
- Superseded by: none

## Context

The graphical greeter (`synveil-display --login`) owns VT2: it quiets the
keyboard (K_OFF) and sets the VT to KD_GRAPHICS so the framebuffer
greeter owns scanout while it waits for input
(`synveil_login_vt_quiet`). On successful authentication, veil-seat
spawns `synveil-vt`, which issues VT_ACTIVATE for tty1 and waits
(bounded) for the switch.

The kernel's VT layer refuses that switch. In `set_console()`:

```c
if (!vc_cons_allocated(nr) || vt_dont_switch ||
    (vc->vt_mode.mode == VT_AUTO && vc->vc_mode == KD_GRAPHICS))
    return -EINVAL;
```

A VT_AUTO console in KD_GRAPHICS is treated as owned by a graphics
client, and switches away from it are silently dropped: no console
callback is scheduled, the target VT never becomes active, and the
session launcher's bounded wait expires (`activate tty1: Connection
timed out`). The authenticated session therefore cannot start whenever
the greeter holds VT2 in KD_GRAPHICS — observed deterministically in
QEMU with the graphical-login fixture and the joined image, and
reproduced with a bare `chvt` in the guest (blocked in
`__vt_event_wait` while the active VT stays tty2).

## Decision

The login flow owns VT2's display mode, so the handoff is its job:

1. Immediately before attempting authentication, the greeter drops
   VT2 to KD_TEXT (`auth_handoff_login_vt`), after releasing the
   evdev grab as before. The kernel then permits the session's
   VT_ACTIVATE to tty1.
2. The handoff fails closed: if the mode cannot be dropped and
   verified, no session is attempted and the attempt renders as
   denied with the typed secret cleared.
3. When the session ends, the existing restore path
   (`auth_restore_login_vt`) re-activates VT2 and re-applies
   K_OFF + KD_GRAPHICS before the greeter accepts input again, so
   the login VT's scanout protection is unchanged while it waits.

The session side (`synveil-vt`) is unchanged: it still activates
tty1 with a bounded wait and still restores tty1's saved modes on
exit. No VT_PROCESS handshake is introduced; the mode drop is the
minimal change that satisfies the kernel's switch guard without
weakening the greeter's locked-state guarantees.

## Consequences

- Graphical login and enrollment can hand off to the authenticated
  session (framebuffer terminal and Weston alike) in the same boot.
- Between the mode drop and the switch to tty1, VT2 is a text
  console; the framebuffer content is not redrawn over because the
  session VT becomes foreground immediately after, and any failure
  path re-runs the restore + render sequence before input resumes.
