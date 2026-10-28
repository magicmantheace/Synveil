# Muse handoff: reset must not succeed with its recovery factor still active

Reviewed source: ef72f3fbcf925ad420c93e4b195b28f708cbb73d,
projects/synveil/src/veil-core/src/bin/veil-recovery.rs, run_reset().

The sequence is replace_root_credential(password), then revoke_factor().
On revocation failure the code emits RESET_OK_REVOKE_FAILED and returns
ExitCode::SUCCESS. The password has already changed, but the used factor
record remains active. An interruption between those writes also leaves a
window before revocation. Normal-path reuse tests do not cover this state.

This conflicts with ADR 0020 D7's automatic revocation of a used factor.
Do not describe the reset as a completed single-use transaction in this state.

Independent fault test in original integration:
python3 tests/muse-reset-revocation-fault.py --source-file <Muse checkout>/projects/synveil/src/veil-core/src/bin/veil-recovery.rs

It compiles the actual run_reset function unchanged, with storage/input test
doubles that force revoke_factor to fail after replacement succeeds. Expected
current defect: password_changed=true, revoke_attempted=true,
factor_active=true, success=true; the safety assertion must fail. This does
not claim an actual guest power-cut reproduction. CI records the execution;
local rustc is absent. If compilation fails instead, that is not reproduction.

Please implement and review a crash-consistent reset transaction with defined
recovery behavior for failures between credential replacement and revocation.
Cover failed writes/fsyncs and interrupted boots at both boundaries, verify
old-factor reuse cannot silently succeed, preserve credential/data safety,
and update the acceptance record with exact source/image identities. Do not
merely rename the success marker or suppress the error. The original worker
has not implemented a competing fix.

Only tier-2 maintenance and separately verified upstream work have been
transferred. Tier-4 reset/enrollment/rotation, the new recovery-inclusive
package evidence and associated readiness claims remain deferred. Muse keeps
primary development priority; original roadmap is paused.
