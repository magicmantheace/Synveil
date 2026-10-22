# VM-only signed A/B slot transaction prototype

This is a **pre-install host fixture**, not a Synveil installer, authenticated
bootloader, or user-ready update service. It operates solely on a named,
pre-existing regular-file GPT disk in `out/install-prototype/`. It cannot
open a physical device, mount a partition, select firmware boot entries,
modify Windows, enroll a user or install an EFI binary.

The file-backed GPT layout comes from `tools/ab_image_prototype.py`, with the
canonical ESP, SYSTEM_A, SYSTEM_B, STATE, DATA and RECOVERY partitions. The
updater parses **both** GPT headers and partition tables, verifies CRCs and
PARTUUIDs against the layout manifest, and refuses any mismatch. It validates
the confirmed image before staging the inactive system partition. It refuses
path traversal, hardlinked or symlinked file inputs and already-pending trials.

The fixture `initialize-fixture` action accepts an explicitly named
`<slug>.base.bin` regular file and writes it to SYSTEM_A as an **unsigned test
baseline** (version 1), then creates a synchronized control record. It does
not make the disk bootable. The release fixture uses four additional regular
files in the same private directory:

- `<slug>.release.bin`: opaque system payload, up to 64 MiB;
- `<slug>.release.json`: JSON with schema `synveil.vm-ab-release/v1`, strictly
  increasing integer `version`, exact `bytes`, lowercase `sha256`;
- `<slug>.release.sig`: detached Ed25519 signature over the exact JSON bytes;
- `<slug>.fixture-trust.pem`: corresponding public verification key.

**The fixture public key is supplied alongside the release and is not a
production trust anchor.** This verifies signature plumbing, not publisher
authority. Future deployment requires an immutable/pinned vendor key, protected
signed metadata with expiry/rollback policy, key rotation, measured device
identity, and a verified boot chain.

The `stage` action verifies metadata, signature and payload hash, copies only
to the inactive GPT slot, fsyncs the image, reads back the written region,
then atomically records a three-attempt trial in `<slug>.control.json`. A
simulated interruption during copying or between the slot sync and control
publication cannot select the incomplete candidate. A trial attempt is
synchronously decremented *before* returning a proposed slot choice.
`boot-trial` falls back to the confirmed slot on candidate corruption or
exhaustion. `rollback` discards a pending trial. `confirm` requires that a
trial choice was recorded, but **does not run guest health checks**: it is a
manual host-fixture operation, not automatic boot confirmation. Interrupted
control temporary files are ignored and safely discarded.

For local host acceptance:

```sh
python3 tests/ab-image-prototype.py
python3 tests/ab-vm-update.py
```

The new tests generate ephemeral Ed25519 keypairs with OpenSSL, create a 24
MiB disposable GPT file, initialize A, stage signed B, corrupt trial bytes,
exercise interrupted-write cuts and attempt exhaustion, verify region
isolation (including DATA/STATE/RECOVERY/ESP), and test refusals.

**Not established by these tests:** real EFI slot selection, installed-root
filesystem semantics, partition write durability after a physical power cut,
actual QEMU boots of these A/B slots, rollback-safe data migrations, confirmed
kernel/userspace service health, owner authentication, signed production
trust, physical storage safety or Windows/BitLocker compatibility. None of
those gates may be marked complete based on this prototype.
