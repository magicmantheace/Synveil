# Partial native validation: ffd0b0a

Revision `ffd0b0a87a3314ea25c20ff536679787d06a1123` in
[native run 38634696072](https://github.com/magicmantheace/Synveil/actions/runs/38634696072)
finished with **failure**. Job step evidence is retained in `workflow-result.json`.

Native build, audit rollover/capacity/incomplete-tail checks, and the isolated
tmpfs exhaustion/read-only/read-write recovery gate passed. Later authenticated
and independent packaged-VM tests also passed, but the graphical settings
write/reboot gate failed. This is not a complete successful pipeline.

The retained `qemu-ui-preferences-write.log` shows desktop readiness and home
seeding, followed by checks of a settings file that never appeared. It contains
no settings-save marker; phase two was not reached. The QMP helper's immediate
press/release sequence can miss a guest USB poll. A bounded press hold and
regression coverage are implemented later, pending fresh native confirmation.

This evidence supports the isolated audit gate only. It does not validate
later export/verify tools, resolve the settings failure, or establish physical
installation, ext4 failure recovery or power-loss durability.
