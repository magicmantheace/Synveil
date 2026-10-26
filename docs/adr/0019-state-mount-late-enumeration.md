# ADR 0019: State mount waits, bounded, for late-enumerating media

- Status: Accepted
- Date: 2026-10-25
- Supersedes: the "early-probed device" constraint recorded in ADR 0017
- Superseded by: none

## Context

ADR 0017 identified the state volume by its GPT partition identity and
noted that the volume had to sit on an early-probed device, because the
initramfs mount helper resolved the partition exactly once, before any
service started.

The owner's install target is an **external USB SSD**. USB storage
enumeration is asynchronous: the device, its partition table, and the
per-partition uevents can appear seconds after init begins. A
single-shot resolution races that enumeration and would report "no
state partition" on exactly the hardware the image is built for,
silently disabling enrollment and persistence on physical media while
working in every directly attached QEMU arrangement.

## Decision

`build/rootfs/mount-state` polls for the designated partition before
giving up:

1. While **zero** partitions carry the state identity, rescan every
   ~250 ms for a bounded **~10 seconds**.
2. Exactly one match proceeds exactly as before; **more than one
   match fails closed immediately** (waiting cannot disambiguate, and
   the credential/enrollment layers keep their own duplicate checks).
3. After the deadline with no match, boot continues without
   persistence, exactly as before — a missing state volume is a
   degraded boot, never a hang and never a format prompt.

Nothing else moves: the wait lives entirely inside the existing
synchronous mount step, which still completes before veil-core,
veil-seat, and the graphical login start, so no consumer needs to
learn about enumeration timing.

## Consequences

- First boot from USB installation media finds its state partition
  even when USB enumeration finishes several seconds into init.
- Boots with no state volume pay up to ~10 s before continuing
  without persistence; directly attached media (the QEMU proofs)
  resolve on the first scan and pay nothing.
- The QEMU proof obligation is a state volume attached over USB
  (`usb-storage`), exercised end to end through enrollment and a
  cold-reboot login, recorded in the pass validation record.
