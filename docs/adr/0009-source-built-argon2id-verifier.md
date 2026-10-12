# ADR 0009: Source-Built Local Password Verifier

- Status: Accepted architecture; implementation and target audit pending
- Date: 2026-10-11
- Follows: [ADR 0008](0008-authenticated-graphical-session.md)

## Decision

Use **Argon2id v=19** with the RustCrypto `argon2` password-hashing
implementation and its typed PHC password-hash parser, compiled into a small,
independently privileged verifier. Pin the exact source and transitive crate
versions through the committed Cargo.lock before enabling the feature.
Build the verifier against Synveil's native target toolchain and audit the
resulting static link map. No hand-written password hashing, SHA-256 password
database, model credential judgment, cloud login, or script-driven password
checking is acceptable.

The initial credential record is one root-owned local account with a
non-root UID/GID, using a PHC string containing the Argon2id algorithm,
version, cost parameters, per-credential random salt, and resulting hash.
A fixed expected account identity is resolved from trusted root-owned
configuration; the graphical UI cannot submit its own UID/GID. There is
**no default password, empty credential, auto-login, or password-bearing
state file shipped in any public image.**

## Enrollment and verification boundary

1. Allow explicit local credential enrollment only from an already trusted
   operator/recovery context. It must use operating-system entropy to create
   a fresh salt and atomically publish a mode-0600, single-link credential
   record in a root-owned mode-0700 directory. Refuse symlinks and
   pre-existing untrusted files; never overwrite credentials on boot.
2. The verifier accepts a bounded username and password over a private
   local connection, never as CLI arguments or environment variables.
   Require correct peer credentials, bounded message framing, finite
   timeouts and per-seat attempt throttling. Do not accept model output as
   a login assertion.
3. Use a documented default Argon2id work factor of **64 MiB memory,
   three passes, one lane**, subject to benchmark and target resource limits
   before release. Require at least 16 unpredictable bytes of salt.
   Verify PHC algorithm/version and strictly cap *encoded* cost and field
   lengths before hashing to prevent resource-exhaustion attacks from a
   tampered record. Exact hash/tag size and bounded policy must be exercised
   in tests, not inferred from a PHC parser's permissiveness.
4. Return only denied/verified outcomes and the fixed trusted non-root
   identity. Do not log passwords, hashes, individual keystrokes, salts,
   or password-dependent error details. Treat corrupt/unavailable records
   exactly as denial; no console/shell fallback can authorize a GUI login.
5. Keep credentials, display settings, model memory, and user home separate.
   Clean the authentication transaction on timeout or display loss. Successful
   login starts a non-root user session only through the root-owned seat
   supervisor defined in ADR 0008; logout/crash must revoke it before another
   login.

## Validation gates

- Host tests: PHC parsing and cost caps, denial/acceptance, fake verifier,
  stale-attempt replay, rate limits, random-salt uniqueness, file
  ownership/symlink/hardlink refusal, no secret logging.
- QEMU: source-built static verifier; no account enrolled -> locked;
  rejected/wrong password -> locked; explicit trusted enrollment then
  correct login -> UID 1000 session; logout/crash -> clean lock; serial
  recovery remains separate. Repeat with missing/corrupt state and
  restart-exhausted desktop.
- Verify the target has enough free memory for the selected work factor.
  Failure must deny login safely, not reduce hashing cost automatically.

## Developer-only enrollment foundation

A one-time `veil-enroll --password-stdin` tool is now compiled alongside
the native core, and its static ELF/link map must be audited by the native
target pipeline. This intentionally takes a password only from a **non-TTY
protected input stream** and refuses existing credentials without replacement.
It generates 16 random salt bytes from the kernel CSPRNG, hashes with the
pinned RustCrypto implementation, and synchronizes a root-owned mode-0600
PHC record under the private mode-0700
`/var/lib/synveil/auth/` directory. Enrollment and verification require a
real mounted ext4 /var state volume; an ephemeral boot does not establish
an account. The root/serial developer console is still unauthenticated;
this is not a public onboarding procedure.

The development QEMU workflow now exercises one-time enrollment using a
random **test-only** password generated inside the guest, asserts permissions
and refuses a second write. The test password is never embedded in a build
artifact. Guest validation is pending at this revision.

## Current status

The developer QEMU desktop remains **unauthenticated**. The existing
`SessionTracker` is host-testable and fail-closed but does not create
credentials or launch sessions. This ADR selects a library and authority
boundary; it does not claim any of the above gates have passed.

## Sources

- [RustCrypto Argon2](https://docs.rs/argon2/latest/argon2/):
  Argon2id variant and PHC password-hashing API.
- [RustCrypto password-hash](https://docs.rs/password-hash/latest/password_hash/):
  PHC string format and parser.
