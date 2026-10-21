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

## Offline staged-image byte checks

Optional `--staged-slot-a FILE --staged-slot-b FILE` arguments now hash both
explicitly named, bounded regular *host files*. The tool refuses symlinks,
multiple hard links, named pipes, device nodes, zero-length files and
filesystem metadata changes during hashing. Both hashes are compared to the
manifest. A corrupt candidate leads to an advisory confirmed-slot fallback
only when the confirmed staged image also matches. A corrupt confirmed image
always fails closed. No disk writes, boot selection or PARTUUID lookup occur.

This is an offline **integrity comparison only**. An attacker able to edit
both the manifest and images can forge matching hashes. Neither the images nor
the metadata are authenticated, and there is no secure boot or real installed
system generation yet.

## Experimental file-only GPT layout generator (development)

A separate *opt-in*, non-privileged `tools/ab_image_prototype.py` now
writes **only a newly named, sparse regular file** under
`out/install-prototype/`. It builds a protective MBR, CRC-checked primary
and backup GPT headers and matching partition-entry arrays. Its six
unformatted partitions are **ESP, SYSTEM_A, SYSTEM_B, STATE, DATA and
RECOVERY**, with distinct randomly generated partition UUIDs and 1 MiB
alignment. A second exclusive-create JSON record declares that no
filesystems, EFI boot programs, user credentials or signed releases exist.

Run `python3 tools/ab_image_prototype.py --name isolated-test` only in
a disposable checkout with sufficient virtual free space (nominally 6 GiB,
sparse). No other destination, device, formatting, mounting, erase or
bootloader argument is accepted. Both preexisting files and symlinked
output parents are rejected; the tool never uses `dd`, `mkfs`,
`sfdisk`, loop mounts, or any `/dev` path. The file is unbootable
and **must not** be flashed, installed or represented as a usable system.
Tests inspect its actual GPT CRCs and mirror without privilege.

This is evidence of a plausible *disk structure only*, not an installed
A/B boot manager. Stage-3 acceptance still requires authenticated owner
login, independent recovery, safe partition/device selection, actual
rootfilesystems, immutable slot mounting, rollback durability, persistent
data through two boots and image signatures. The existing read-only
`system_generation.py` advisory API is unchanged.
