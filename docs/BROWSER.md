# Networking and the graphical browser

Status: **QEMU-proven** (Priority 3/4 acceptance,
`docs/validation/browser-qemu-proof-2026-10-27/`). The
browser is stock-configured with no connection blocking
(owner decision, 2026-10-27); the earlier privacy record
(`docs/validation/browser-privacy-proof-2026-10-27/`)
describes a superseded image — see the addendum there and
"Stock configuration" below. No physical
hardware claim: the TG01's Ethernet/Wi-Fi devices, their kernel
drivers, and firmware are unverified — the proof uses QEMU's
`virtio-net-pci` with user networking (slirp), which is also the
only network device the current kernel configuration targets.

## The choice: distribution Chromium (owner decision, 2026-10-26)

The owner directed a prebuilt **distribution Chromium** over a
lightweight engine: real-world web compatibility and Chromium's
sandbox were worth the size. A from-source Chromium build is out
of scope for this build system (depot_tools/GN cannot be
expressed in it), so the browser is the Debian 13 package set,
pinned and hash-verified like every other sealed component:

- **Chromium 156.0.8078.47-1~deb13u1** (amd64), resolved by apt
  from snapshot.debian.org at the pinned timestamp
  **20261024T000000Z** (trixie + trixie-security), starting from
  an empty dpkg status so the closure is the full runtime
  closure (217 packages, 218 MB of .debs).
- Every downloaded .deb is verified twice: its sha256 and size
  must match the snapshot's own Packages index entry, and the
  resolved set must equal `build/manifests/distro-chromium.json`
  exactly. A moved snapshot or a substituted package fails the
  build (`tools/distro_chromium.py`).
- The closure is extracted and **pruned to the real payload**:
  the DT_NEEDED graph rooted at the Chromium binaries, the
  dlopen-only sets the graph cannot see (NSS modules, gdk-pixbuf
  loaders, gio modules, mesa DRI), and the data trees. What
  maintainer scripts would have generated (the CA bundle, the
  GLib schema cache) is generated deterministically instead.
  Sealed payload: **1,290 files, 465 MB**
  (`tools/distro_chromium_assets.py`).
- Two non-obvious seal contracts, both found by the guest
  refusing to start: the Debian wrapper's runtime files under
  `etc/chromium.d/` and `etc/chromium/` (default flags, API key
  stubs, `master_preferences`) are sealed as data, and the
  prune keeps the **non-ELF resources** inside
  `usr/lib/chromium/` (ICU data, .pak files, snapshot blobs) —
  a DT_NEEDED-only prune deletes them and the browser dies at
  startup. `master_preferences` was reviewed line by line: it
  carries Debian's own defaults, including
  `safebrowsing.enabled=false` (a distribution default, not a
  change made here); nothing in it weakens the sandbox or TLS.
  The project's baseline owns fontconfig and xkb data, so the
  payload's copies of those trees are dropped — the assembled
  rootfs must have zero path collisions.
- Workspace umask here is 0007, which silently produced a
  payload the session user (uid 1000) could not traverse or
  read. Every sealed file is mode-checked at seal time: the
  stage build **refuses to seal** any payload file that lacks
  other-read, and generated artifacts are pinned to 0644.
- The distribution's libc and libstdc++ are pruned by the graph:
  the image runs the project's own glibc 2.44 / GCC 16 runtimes
  (newer than Debian 13's), and every payload ELF's DT_NEEDED
  closure is re-checked against the finished generation rootfs
  at image build time. The distribution's system directories
  (etc, systemd units, its own userland) never enter the image.
- The payload keeps the multiarch layout, so compiled-in dlopen
  paths keep working; the session launcher puts
  `/usr/lib/x86_64-linux-gnu` on `LD_LIBRARY_PATH` for the
  browser process tree only.

Alternatives that were evaluated (and why they lost): Firefox —
no recipe/packaging route in this build system that fits the
sealed-image discipline, and the compatibility/sandbox story is
not better than Chromium's; WebKitGTK — a very large stack for
this milestone; **NetSurf (GTK3)** — fully worked up as the
fallback: `tools/native_browser.py` builds it from the pinned
Buildroot recipes and the session/download design was proven
against its source, but its engine is far behind on web
compatibility and it has no process sandbox at all. The owner
chose Chromium; the NetSurf builder remains in-tree as the
fallback if Chromium integration ever hits a genuine wall (its
stage build was stopped mid-run at the direction change and has
not been sealed).

## Sandbox posture

Chromium's sandbox is a hard acceptance gate, not a flag:

- The launcher never passes `--no-sandbox` (nor any flag that
  weakens the sandbox or TLS). If the sandbox cannot engage, the
  browser does not run and the proof fails — that is the design.
- The kernel carries `CONFIG_USER_NS=y` and
  `CONFIG_SECCOMP_FILTER=y`; Debian's Chromium ships no setuid
  helper, so the **namespace sandbox + seccomp-bpf** is the
  mechanism.
- On every start, the launcher inspects one of the browser's
  renderers through `/proc` and appends a record to
  `~/sandbox-probe-status.txt`: the renderer's user-namespace id
  versus the session's, and its seccomp mode. The zygote applies
  the seccomp filter just after the renderer appears, so the
  probe settle-polls the same pid (bounded ~20 s) before
  recording — a first-sight read raced that window on loaded
  first boots and recorded seccomp 0 for a renderer that was
  filtered moments later. The acceptance
  gate requires records whose renderer namespace differs from
  the session's with seccomp mode 2 (filter) — kernel-level
  evidence, re-derived from the DATA partition, with negative
  gate tests for the unsandboxed shapes.

## Integration

`tools/native_ab.py --browser` builds the proof disk under
`out/browser-proof/`. The Chromium payload grows a generation
rootfs from ~124 MB to ~560 MB, so the browser disk's layout
grows with it (ESP/slots/recovery 768 MB, DATA 384 MB; the plain
A/B proof keeps its established layout). Each generation gets:

- the sealed Chromium payload (byte-verified against the seal),
- `configure-network` + `udhcpc-script` in
  `/usr/libexec/synveil/` (link wait, DHCP,
  `SYNVEIL_NETWORK_READY`),
- the launcher `/usr/bin/synveil-browser` and the
  `/etc/synveil/browser` + `/etc/synveil/files-app` markers the
  Weston session keys on,
- a `[launcher]` entry (icon) in `weston.ini` — the graphical
  launch entry in the desktop panel.

The session (`build/rootfs/weston-session`) starts the browser
after login when the marker exists. First run, the launcher
seeds (only when absent): `~/Downloads` (0700), the profile at
`~/.config/chromium` with a minimal Preferences pinning the
download directory to `/home/synveil/Downloads` with no
per-download prompt, and — in proof builds only — an NSS
database. Profile, downloads, and session state live under
`/home/synveil` on the DATA partition: outside both replaceable
generations, surviving update and rollback by construction (and
the acceptance proves they do).

Chromium runs with `--ozone-platform=wayland` (native Wayland;
there is no X server or XWayland in the image),
`--disable-dev-shm-usage` (the initramfs `/dev/shm` is not a
place for browser IPC), `--disable-gpu` (software rendering;
the QEMU display path is simpledrm-class), and a 16 MB disk
cache cap so the profile cannot crowd the DATA partition.

## TLS and trust

TLS verification is Chromium's default and is **never disabled**
— not in the browser, not in any fixture, not with any flag. The
image's trust bundle is the distribution's ca-certificates
bundle (generated from the sealed Mozilla roots). The proof's
controlled endpoint is a local HTTPS server whose certificate
(SAN `test.synveil.invalid`, `10.0.2.2`) is signed by a per-run
test CA; in proof builds that CA is **appended** to the bundle
and imported into the profile's NSS database with certutil —
both recorded in the build receipt (the driver refuses to run
against a disk whose receipt names a different CA). The NSS
seeding is done by the launcher on first start (proof builds
only, gated on the in-image `proof-ca.pem`): it creates
`~/.pki/nssdb` **before** invoking certutil — certutil creates
the database files but not their parent directory, and the
seed silently produced an empty database until that was fixed
(the failure was instructive: with the CA absent from the NSS
store, Chromium rejected the fixture certificate outright —
TLS verification working exactly as designed). A second
endpoint serves the same name from an unrelated CA; the gate
requires zero GET requests there plus at least one refused
handshake — a browser that verified nothing could not produce
that record.

## Stock configuration — no connection blocking (owner decision, 2026-10-27)

The shipped browser is **stock-configured**: nothing in the
image blocks any connection. There is no managed policy, no
`/etc/hosts` null-routing, and no launch flag or seeded
preference that disables a networked feature — component
updates, variations, metrics reporting, Safe Browsing, sync,
sign-in, search suggestions, translate, media router,
DNS-over-HTTPS, network time, QUIC and WebRTC all behave
exactly as the distribution's Chromium 156 ships them. What
remains from the integration is not connection control: the
Wayland/Ozone launch, the kernel sandbox (untouched), TLS
verification (untouched), the persistent profile on DATA,
and a profile seed that pins only the download directory to
`~/Downloads`.

This is the owner's explicit decision of 2026-10-27 ("I
don't want you blocking connections at all"), which removed
in full the three-layer blocking regime an earlier design
had shipped (managed privacy policy, launcher flags/profile
seed, hosts null-routing — briefly with an accounts.google.com
exception the same day, now moot). The earlier privacy
validation record,
`docs/validation/browser-privacy-proof-2026-10-27/`,
describes that superseded image and carries an addendum
saying so; its measured values stand for the image it
measured and for no other.

Consequently the acceptance gate no longer judges egress at
all: vendor endpoints in a proof run are expected behavior,
not failures. Proof builds still capture Chromium's net log
into `~/net-logs` on DATA, and the receipt still enumerates
the observed DNS names and remote endpoints (IPv4 and IPv6)
as an informational record — nothing in those sets can fail
a run (`tests/smoke-gates.py` carries the positive fixture:
`test_vendor_egress_not_gated`). The gate continues to judge
what it always judged besides egress: HTTPS with bad-CA
refusal, downloads with digests, collision handling and
persistence, sandbox evidence, and the update/rollback legs.

## Downloads

Chromium's own semantics, exercised by the acceptance:

- Downloads land in `/home/synveil/Downloads` automatically —
  no prompt, no execution, no installation. Chromium never
  auto-opens downloads, and nothing in the session does either.
- **No silent overwrites:** a colliding name is uniquified
  (`payload.bin` → `payload (1).bin`); the original file is
  never touched. The gate checks both digests after a collision.
- Cancel/interrupt: Chromium's download UI offers cancel and
  resume; an interrupted download leaves at most a
  `.crdownload` partial. Insufficient storage fails the download
  with an error state; the acceptance proves no complete
  oversized file remains afterwards.
- The graphical file-access path is `synveil-files`
  (`src/desktop-wayland/files-window.c`), a small read-only
  native Wayland window the session opens alongside the
  browser: it lists Downloads, then the home directory — names
  and sizes, dotfiles skipped, nothing opened, executed, moved
  or deleted — with a Refresh button and a session log
  (`~/files-window.log`) recording every scan.

## The acceptance

`tests/smoke-native-browser.py` (see its docstring for B0–B7):
graphical login → browser autostarts → the proof page loads
over verified HTTPS → a known payload downloads → its digest
is computed **inside the guest** and the bytes are read back
from the disk image host-side → collision/uniquify → oversized
download → bad-CA refusal → cold reboot → A/B update to v2 →
second payload → rollback to v1 — with digests, profile,
credential, and sandbox records verified at every readback,
and every verdict re-derived by `validate_browser_results`
from the recorded artifacts (the server's request log travels
inside the receipt; missing artifacts fail). Negative tests
for the gate live in `tests/smoke-gates.py` (`BrowserGate`).

Reproduce:

```
python3 tests/smoke-native-browser.py --prepare-tls   # prints the build command
python3 tools/native_ab.py --build --browser --browser-test-ca <ca.pem> \
    --browser-test-host 10.0.2.2 test.synveil.invalid \
    --browser-start-url https://test.synveil.invalid:8443/
python3 tests/smoke-native-browser.py                  # non-root
```

## Open items

- Hardware networking (TG01 devices/drivers) — unverified.
- The browser proof disk's layout (768 MB slots) is a proof
  configuration; a production layout would revisit the
  uncompressed-UKI size (the Chromium binary alone is 316 MB,
  kept bit-identical to the distribution package on purpose).
- Chromium's own window under Weston opens at its requested
  size; no maximize-state work was done for it.
- The NetSurf fallback stage (above) is unsealed.
