# Networking + browser + persistent downloads — QEMU proof record (2026-10-27)

**Result: Priorities 3 and 4 DEMONSTRATED in QEMU** — the joint
guest-level acceptance passed end to end on one disk, in
sequence, ending with `SYNVEIL_BROWSER_QEMU_PROOF_PASSED` from
`tests/smoke-native-browser.py` and a receipt whose gate
re-derives every verdict from the recorded artifacts
(`browser-receipt.json`: `demonstrated: true`,
`gate: {validated: true, failures: []}`).

This is QEMU evidence only. **Physical hardware: NOT validated**
— networking used QEMU `virtio-net-pci` with user networking
(slirp); the TG01's network devices, drivers, and firmware are
unverified. The proof disk is a separate artifact from the
shipped test image, which this work does not touch.

## Identities

- Proof disk built from source commit
  `728e626a3c8bac5aa61e76017c737ae0af1632bf` (`build-receipt.json`
  in this directory is the builder's own receipt).
  Disk SHA-256:
  `f60706721e7d59ed4319a9ff84a570b3f67faeaf13b1a40caa456e30ce04c762`.
- Generation payload SHA-256: v1
  `79dc7df1ca26c2f959c33600c00a68ab7de7c952347879e53fe469ec0ccc8299`,
  v2 `6c8fbbe5832acfbd319fbbaf897f9b45e9b388c0b7235f5688cbeb0f58d85d88`,
  v3 `514a1d081fbcf0da9ae438ef5cec60f9386aff3034e72adcdd90c1f02a199b8a`.
- Browser: distribution **Chromium 156.0.8078.47-1~deb13u1**,
  pinned snapshot 20261024T000000Z (see `docs/BROWSER.md`).
- Proof driver (host-side only) at commit `329ccb1` when the
  passing run executed; commits between the image build and the
  run touch only the driver, its readbacks, and docs — no guest
  code changed.
- TLS fixture: per-run test CA, SHA-256
  `278f183d9e64b369fdb28a100ff9daa73f070a0dae2146f1ef8ba09f55487d82`,
  endpoint `https://test.synveil.invalid:8443/` (guest → host at
  10.0.2.2), unrelated-CA endpoint on port 8444. CA private keys
  are run-local under `out/` and never enter Git or the image
  (only the CA certificate is provisioned, through the image
  build — see `docs/BROWSER.md`).
- Publisher key: the flagged development key
  `synveil-dev-2026-01`, as in all prior records.

## What the acceptance exercised (B0–B7, all inside the guest)

Graphical enrollment and login → session networking up
(`SYNVEIL_NETWORK_READY`, DHCP over virtio-net/slirp) → browser
autostarts as the non-root session user → the controlled page
loads over **verified HTTPS** (padlock page proof by pixel
share) → **B1** a known 256 KiB payload downloads to
`/home/synveil/Downloads`; its SHA-256 computed *inside the
guest* matches the fixture digest, and the bytes read back
from the disk image host-side match too → **B2** the same
download again: Chromium uniquifies to `payload (1).bin`, the
original is byte-identical afterwards (no silent overwrite) →
**B3** a 448 MiB download (larger than DATA free space) fails
with no complete file and no oversized partial left behind,
browser still alive → **B4** the unrelated-CA endpoint: zero
GET requests reached it, three refused handshakes are in the
server log, the interstitial replaced the page, and Back
restored it → **B5** cold reboot: page loads again, downloads
and profile intact, the files window's own log shows the
payload listed → **B6** A/B update to v2 staged from release
media, trial boot health-passes, owner-confirmed; a second
payload downloads and verifies; downloads, profile, credential
and sandbox records survive → **B7** rollback to v1: trial
boot, health, confirm; both payloads still verify; the
sandbox record count reaches six (one per browser start, each
with a renderer user namespace different from the session's
and seccomp mode 2).

Server-side counts (from the receipt's embedded request log):
7 `GET /` over the run's sessions; **0 GETs on the bad-CA
port**; 3 refused handshakes there.

## Gate discipline

`validate_browser_results` re-derives every verdict from the
recorded artifacts (control records, readback bytes and
digests, files-window log, sandbox probe file, the fixture
server's request log) — scenario self-reports are not trusted,
missing artifacts fail, and `tests/smoke-gates.py`
(`BrowserGate`, 26 tests) carries negative regressions proving
an inconsistent receipt cannot pass. Regression suites at the
same head: `tests/smoke-gates.py` OK, `tests/ab-update.py` OK,
`tests/network-config.py` OK, `tests/ab-release-crosscheck.py`
OK.

## Defects found and fixed on the way (all in Git history)

- Release media was hard-sized at 160 MB; the browser
  generation's 248 MB UKI silently overflowed it because
  `debugfs` exits 0 when its write fails. The guest agent
  rightly refused the media ("release payload hash
  mismatch"). `tools/ab_release.py` now sizes media from the
  payload and verifies the payload back out of the finished
  filesystem by SHA-256 before the disk is assembled.
- The driver waited for trial health before logging in; the
  guest's session health check needs the authenticated
  session, so trial attempts burned. Order now mirrors the
  A/B proof driver (login first).
- The terminal marker wait matched the boot's own
  `SYNVEIL_AB_CONFIRMED_BOOT` line by prefix, so a garbled
  confirm attempt looked successful. Waits are anchored to
  lines recorded after typing.
- The readback of the collision file `payload (1).bin` split
  its spaced path in `debugfs` and reported a healthy guest
  file as empty; spaced names are now read by inode
  (verified against a real run disk: 262,144 bytes, fixture
  digest).
- The sandbox probe read a renderer's seccomp state at first
  sight, racing the zygote on loaded first boots; it now
  settle-polls the same pid.
- The files window's Refresh click was computed from a title
  rectangle polluted by the desktop's top panel; the driver
  now locates the button by its own fill color, validated on
  a live session frame and a diagnostic boot (guest log shows
  two full scans: open + refresh).
- Chromium integration defects from earlier in the pass (the
  `/etc/chromium.d` runtime contract, non-ELF resources in
  the prune, NSS seed directory, mode discipline under a
  0007 umask) are documented in `docs/BROWSER.md`.

## Explicit answers for this pass

- HTTPS browsing ran inside Synveil: **YES** (QEMU guest,
  verified TLS against the provisioned test CA; bad-CA
  endpoint refused).
- Persistent downloads demonstrated inside Synveil: **YES**
  (across cold reboot, A/B update, and rollback).
- Any firmware variables written: **NO** (the boot design
  writes none; nothing in this pass changes that).
- TLS verification disabled anywhere: **NO** (no flag, no
  fixture exception; the controlled endpoint is trusted by
  provisioning its CA through the image build).

## Honestly open / unverified

- Hardware networking and the TG01 generally: unverified;
  QEMU virtio-net only.
- The 768 MB-slot browser proof layout is a proof
  configuration, not a production layout.
- Cancel/resume of an in-progress download is Chromium's own
  UI behavior; the acceptance proves the insufficient-storage
  and no-complete-file outcomes, not a mid-download cancel.
- Chromium window management under Weston (maximize state)
  is untouched.
- The NetSurf fallback stage remains unsealed.

## Addendum (2026-10-27, later the same day)

The owner's no-vendor-calls requirement landed after this
record was written. The joint acceptance was re-run on an
image carrying the shipped privacy configuration (managed
policies, launcher flags, profile seed, and /etc/hosts
null-routing of the vendor phone-home endpoints); it passed
in full, with the net-log egress gate clean at every readback.
See `../browser-privacy-proof-2026-10-27/` and
`docs/BROWSER.md`, "No vendor calls". The identities in this
record describe the earlier image and remain accurate for it.
