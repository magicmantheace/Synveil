# Native audit path checks: aa17982

- Revision: `aa179829587f28ed555e09127ebb3a6faf72de9f`.
- [Native workflow 38906503764](https://github.com/magicmantheace/Synveil/actions/runs/38906503764): **success**, October 16, 2026 UTC.
- All 43 build, guest, packaging and upload steps passed; API results and artifact identities are retained in `workflow-result.json`.

This revalidates the released-key login and audit-adapter baseline with the
test-only unsafe post-rotation path regressions present in the source tree.
Graphical terminal return/session revocation, first-owner enrollment,
packaged-VM enrollment/login, every audit storage/reboot gate, core recovery,
observer and settings persistence passed. The prior downloaded-log evidence
is retained separately at [4390a8c](../native-4390a8c/README.md).

The native artifact is `12050762296`, digest
`sha256:169eb1a0853bb11067e06cf1f4d66a49b73c7ff2596370094cb7995c33a7ce23`.
The authenticated preview artifact is `12050722462`, 23901261 ZIP bytes,
digest `sha256:354ac10966e01b50b4eb099531c971faae96756dbb5000c9df5f3a09654b6bc6`,
expiring October 23, 2026 at 13:59:40 UTC. These are GitHub API artifact
identities, not release signatures. See workflow Artifacts and
[FIRST_INSTALL.md](../../FIRST_INSTALL.md) for the preview contract.

The later writer-process SIGKILL fixtures are outside this revision.
This does not establish interrupted kernel operations, real block-device
faults, volatile-cache/physical power loss, independent authenticated integrity,
retention release or physical-install readiness.
