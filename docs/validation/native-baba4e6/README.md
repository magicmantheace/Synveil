# Native graphical two-boot credential and scanout evidence — baba4e6

- Exact Git commit: `baba4e6ba2843c4c8cd36ed8eaec8cde3f7fb453`
- Workflow: [Native two-boot owner enrollment regression, run 39722192206](https://github.com/magicmantheace/Synveil/actions/runs/39722192206)
- GitHub result: **completed success**, including host validation, pinned native
  toolchain/native image build, two independent QEMU UEFI guest boots, and
  the owner's persisted-credential challenge after reboot.
- The acceptance test checks a fresh, parsed, nonuniform QMP RGB screenshot
  before a wrong password on the second boot; the deliberately wrong
  password is rejected and a later correct password opens the UID1000
  graphical session. Both boots separately require post-authentication
  validated RGB scanout.
- The successful guest result is specific to the **native framebuffer
  dashboard**, not an established compositor or XFCE native integration.
- No physical machine, production EFI signing, external install, or
  owner-controlled credential recovery was tested.

Prior exact-source regression runs [39717363014](https://github.com/magicmantheace/Synveil/actions/runs/39717363014)
and [39686260376](https://github.com/magicmantheace/Synveil/actions/runs/39686260376)
failed at the rebooted pre-password scanout gate. A passing later SHA
must not retroactively turn those failures into successes.
