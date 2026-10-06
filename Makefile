# SPDX-License-Identifier: MPL-2.0

.PHONY: doctor fetch toolchain rootfs kernel image manifest qemu smoke all clean distclean

doctor fetch toolchain rootfs kernel image manifest qemu smoke all clean distclean:
	bash build.sh $@
