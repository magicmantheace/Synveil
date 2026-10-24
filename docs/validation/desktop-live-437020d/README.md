# XFCE panel, Settings Manager and two independent UEFI boots

- **Tested commit:** `437020d26070d0e2a231ad79087b39026fcfb47c`
- **CI:** [successful full desktop run 39593790664](https://github.com/magicmantheace/Synveil/actions/runs/39593790664)
- **Image class:** *experimental* Debian Trixie XFCE live ISO, **not** native Synveil
- **Result:** `preview-config: success`, `iso-build: success`, both QEMU boots: `PASS`

## What was actually tested

One ISO build was inspected for a bootable UEFI El Torito entry, then used
unchanged in **two separate QEMU processes**, each with private new OVMF
UEFI variables. Both guests used a read-only virtual ISO optical drive,
with `-net none` and **no writable block-device guest data disk**.
The host recognized the exact Debian live menu and sent one fixed Return.

Each independent guest launched the XFCE graphical session as an ephemeral,
non-root user. The real Xfwm4 window manager handled separate Thunar and
XFCE Terminal application windows and passed move/resize/maximize/minimize/
restore operations. The second-stage test additionally required a running
XFCE panel and opened a third GUI application, the **XFCE Settings Manager**.
The terminal, file manager and Settings window all remained discoverable
and interactive after the panel and settings checks.

Both guests separately reported the real window-operation pass marker.
QMP captured a nonblank 1280x800 RGB screenshot after each guest's
successful session proof. Build logs and both screenshot/serial pairs are
retained in the CI workflow artifact for seven days, **without the ISO**.

Relevant CI log messages appeared **twice**, once for each independent
boot:

```text
[desktop-qemu] recognized Debian live menu; sent one Return to boot default entry
[desktop-qemu] guest interactions passed; real scanout passed
[desktop-qemu] diagnostic marker observed: SYNVEIL_QEMU_RELAY_STARTED
[desktop-qemu] diagnostic marker observed: SYNVEIL_QEMU_DESKTOP_PROBE_STARTED
[desktop-qemu] PASS
```

## Explicit limitations

This proves repeatable *live desktop startup*, not persistence: the
virtual machine did not keep home files or preferences across processes.
The current live-config may automatically start a disposable user; this
is **not secure first-user graphical enrollment**.

The native source-built Synveil kernel/core are separate from the Debian
ISO. No A/B root slots contain bootable systems. The six-partition
file-only GPT layout from `tools/ab_image_prototype.py` is **unformatted
and unbootable**, even though its partition metadata passes independent
unit tests. No updater, rollback service or physical installer exists.
Nothing here demonstrates RTX 3060 or HP Pavilion hardware boot.

Do not distribute or flash the developer ISO as an owner-ready release.
