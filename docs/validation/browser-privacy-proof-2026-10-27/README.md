# Browser privacy proof — no vendor calls (2026-10-27)

Addendum to `../browser-qemu-proof-2026-10-27/`: the same joint
Priority 3+4 acceptance, re-run on an image whose shipped
browser configuration makes **no outbound connections to the
distribution or the vendor at runtime** (owner requirement).
Browser updates arrive only through Synveil's own signed A/B
releases.

**Verdict: PASSED.** `tests/smoke-native-browser.py` ended
`SYNVEIL_BROWSER_QEMU_PROOF_PASSED`; the receipt in this
directory records `demonstrated: true` and
`gate: {validated: true, failures: []}`, re-derived from the
recorded artifacts by `validate_browser_results` (including the
new egress checks at every readback). All B0–B7 steps of the
joint acceptance passed on this image as well (verified HTTPS,
downloads with digest checks, collision uniquification,
oversized-download failure, bad-CA refusal with 0 GETs on the
bad port out of 17 total GETs, cold-reboot persistence, A/B
update to v2, rollback to v1, sandbox records).

## Identities

- Source commit: `7420ec79b75abac2ce67b7a62caf661013a156b6`
  (build receipt and browser receipt agree).
- Proof disk SHA-256:
  `761d24b9d3061dce737bb464981eda8969dc4eae99c673f10afe95e65ec98ce5`
- Generation UKI SHA-256: v1
  `c9342ba8966236a1185335d66eeb809a6d7500a0042bedc04b3d480716c0579b`,
  v2 `0cc39784b01fd5cb63f5374a561886f406dbcfd9bba33d397e04810879191a79`,
  v3 `66c7ce7966275a053b56cb2820bfc9256712edade465b861ac1c538bd54ad27c`
- Chromium: Debian `156.0.8078.47-1~deb13u1` (payload 1,290
  files / 488,609,498 bytes, as in `build-receipt.json`).
- Test CA SHA-256:
  `278f183d9e64b369fdb28a100ff9daa73f070a0dae2146f1ef8ba09f55487d82`
  (unchanged; fixture `test.synveil.invalid` at 10.0.2.2,
  good port 8443, bad-CA port 8444).
- Managed policy file SHA-256 (as installed in every
  generation at `/etc/chromium/policies/managed/synveil-privacy.json`):
  `12ee2d5ba8a892ceaaf8a82e91a3c80281d8e404d08a4d7e44bc15a08ca944dc`
  — the build receipt records the same value.

## What was disabled, and how it was verified

Three shipped layers (details in `docs/BROWSER.md`,
"No vendor calls"):

1. **Managed policies** (`build/rootfs/chromium-policies.json`):
   ComponentUpdatesEnabled=false, MetricsReportingEnabled=false,
   SafeBrowsingProtectionLevel=0, SearchSuggestEnabled=false,
   AlternateErrorPagesEnabled=false, NetworkPredictionOptions=2,
   DnsOverHttpsMode=off, BrowserNetworkTimeQueriesEnabled=false,
   BrowserSignin=0, SyncDisabled=true, DefaultSearchProviderEnabled=false,
   EnableMediaRouter=false, TranslateEnabled=false,
   BackgroundModeEnabled=false, QuicAllowed=false,
   WebRtcIPHandlingPolicy=disable_non_proxied_udp.
2. **Launcher flags and profile seed**: --disable-background-networking,
   --disable-component-update, --disable-domain-reliability,
   --disable-sync, --no-pings, --disable-client-side-phishing-detection,
   --disable-features=MediaRouter,OptimizationHints,TranslateUI;
   seeded Preferences repeat the core (no suggestions, no
   prediction, Safe Browsing off, translate off, DoH off).
3. **Name-layer null-routing** in the image `/etc/hosts`
   (127.0.0.1): update.googleapis.com, clients2.google.com,
   clientservices.googleapis.com, accounts.google.com,
   safebrowsing.googleapis.com, sb-ssl.google.com,
   ssl.gstatic.com, optimizationguide-pa.googleapis.com,
   translate.googleapis.com, edgedl.me.gvt1.com.

Why layer 3 exists: measured against this exact build, a stock
Chromium 156 still issues component-update checks
(`update.googleapis.com/service/update2/json`) and an identity
`ListAccounts` fetch at startup **with the policies loaded** —
the policy loader logs the file as found, and the network-time
service (clients2.google.com/time) demonstrably obeys its
policy (present in a no-policy control run, absent with
policies), while the component updater's data-component checks
are documented by the CIS benchmark as not covered by
ComponentUpdatesEnabled. Privacy distributions remove these
in source; this project ships the distribution binary, so the
OS name layer guarantees the property instead.

Verification is egress-based, from Chromium's own net log
(proof builds run with `--log-net-log` into `~/net-logs` on
the persistent DATA partition; shipped builds never log). The
gate extracts, per readback, the names carried by real DNS
wire transactions and the literal socket endpoints, and fails
on anything but the fixture (loopback allowed — that is where
null-routed attempts die). Recorded at every readback
(B1/B5/B6/B7) of this run:

- DNS wire names: **[]** — no DNS query left the guest in any
  boot (the fixture name is served from `/etc/hosts`).
- Sockets: only `10.0.2.2:8443`, `10.0.2.2:8444` (fixture) and
  `127.0.0.1:443`, `127.0.0.1:80` (null-routed attempts refused
  on loopback).
- Attempted hosts (disclosed, not hidden): the browser still
  *tries* `update.googleapis.com` and `accounts.google.com`
  internally; with the shipped configuration those attempts
  can never become a DNS query or an external connection.
- Net-log files accumulate per browser start on DATA
  (2/2/4/6 files across the readbacks) — every session of the
  acceptance is covered.

Gate regression tests (`tests/smoke-gates.py`, BrowserGate):
a foreign DNS name fails, a foreign socket fails, a missing
net log fails; the consistent fixture (with internal attempts
recorded but egress clean) validates.

Serial logs of the three boots are alongside the receipts.
QEMU only (user networking/slirp); no firmware variables were
written; TLS verification was never disabled anywhere,
including fixtures.

## Corrections (2026-10-27, after this record was written)

- **Parser scope (independent review, verified firsthand):**
  the net-log socket parser behind this record matched only
  IPv4 endpoints, so IPv6 endpoints were silently dropped from
  the evidence. The measured IPv4 values above stand as
  recorded; the absolute reading of "no outbound vendor
  connections" exceeded what the gate could see. The parser
  now extracts IPv6 endpoints (bracketed and bare), the gate
  judges them, and `tests/smoke-gates.py` carries the
  negative regression fixtures (an IPv6 vendor endpoint
  fails; IPv6 loopback is contained like 127.0.0.1). The
  shipped image was not changed by the parser fix.
- **Owner decision (2026-10-27):** accounts.google.com is
  unblocked so Google sign-in works. It is no longer in the
  image's null-route list (nine hosts remain); the gate
  treats it as the disclosed owner-authorized exception —
  its DNS egress is allowed, and a socket is allowed only
  when the same log binds it to that name through its
  resolver source. Test media built after this decision
  carries the change; see
  `docs/validation/test-media-2026-10-27/`.
- **Superseded in full (2026-10-27, later the same day):**
  the owner directed that no connections be blocked at all
  ("I don't want you blocking connections at all"). The
  entire regime this record measured — the hosts
  null-routing, the managed privacy policy, and the
  blocking launcher flags/profile seed — was removed from
  the shipped browser; the vendor-egress gate condition
  was removed with it (net-log egress is now disclosed in
  receipts, never gated), and the accounts exception above
  became moot. The shipped browser is stock-configured;
  see docs/BROWSER.md, "Stock configuration — no
  connection blocking". This record describes a superseded
  image only; the test-media package was rebuilt from the
  post-removal head.
