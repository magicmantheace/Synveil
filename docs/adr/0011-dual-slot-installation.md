# ADR 0011: Dual-slot installation planning (Proposed)

- Status: Proposed, not approved for a physical install
- Date: 2026-10-13
- Related: ADR 0005 (QEMU state volume); ADR 0010 (independent recovery)

## Candidate layout and decision

A future installed Synveil may use separate EFI, A/B immutable system,
persistent state and user-data partitions. Each has a unique, canonical
PARTUUID, independent of enumeration names such as /dev/vdb. System slots
carry expected image SHA256 digests. Metadata names a confirmed slot and
optionally a different candidate and its remaining trial attempts (0-3).

The read-only Python prototype in tools/system_generation.py requires
exact metadata fields and rejects missing, duplicate, symlinked and
oversized input. With attempts remaining, its advisory result selects
the candidate. After exhaustion it selects the confirmed slot.

## Explicitly NOT implemented

This module does not access disk partitions, authenticate metadata,
verify image bytes, decrement a persistent counter, run an installer,
modify an EFI boot target, reboot, format, delete data, or open a root
shell. A digest *listed* in JSON is not evidence that disk content matches,
and SHA256 is not a signature. It is neither a trusted bootloader nor a
safe physical boot selection mechanism.

Production requires separate system image verification, authenticated
metadata, atomic boot-attempt accounting, hardware-bound disk identity,
crash-safe failover, user-data isolation and disposable QEMU tests for
interrupted upgrades before physical installation may be enabled.
