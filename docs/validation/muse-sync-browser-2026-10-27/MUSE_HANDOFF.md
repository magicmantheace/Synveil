> RESOLVED upstream at 68df81f: prose now agrees with the JSONs.
> This historical handoff is superseded by ../muse-sync-recovery-2026-10-28/MUSE_HANDOFF.md.

# Muse handoff: contradictory broken-state acceptance evidence

Reviewed Muse tip: `677421cfc81bf3a193b2bd00d3d26619f7f3979a`.
The README truncation is repaired. One substantive evidence conflict remains.

In `docs/validation/test-media-2026-10-27/README.md`, the restored claims
section states the broken-state boot is **skipped**, because the A/B layout
uses recovery diagnostics instead of the harness's locked-login path.
But `guest-validation-vga.json` and `guest-validation-usb.json` both contain
`broken_state_fails_closed_to_locked_login: true` and neither has the
`broken_state_boot` skip explanation. The earlier numbered item 3 in the
same README also still says the destroyed-STATE boot passed to locked login.
All three statements cannot describe the same acceptance run.

Please reconcile the records against the exact commands and actual raw
logs for image `4be22adaaddb4c5e5abab87d8d636abc18cd4f4e67265b0a1fac188db1925553`.
If that leg was skipped, remove the pass claim and record the skip consistently
in both JSONs and prose. If it ran, provide the matching evidence and correct
the skipped explanation. Do not manufacture a pass or infer the historical
command from the harness defaults. Re-run the affected acceptance if the
original command/log cannot be recovered. Preserve failed attempts.

The original worker has not changed this functionality or evidence. The
current owner-package record and associated physical-readiness doc updates
are deferred from integration pending reconciliation. Chromium implementation,
historical browser proof and release-media fixes are transferred separately;
this issue does not establish a browser/runtime regression.

Original roadmap remains paused. No physical writes or firmware changes
were performed, and no independent hardware compatibility claim is made.
