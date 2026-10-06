# SPDX-License-Identifier: MPL-2.0

.PHONY: lint doctor fetch toolchain rootfs kernel image manifest qemu smoke all clean distclean

lint doctor fetch toolchain rootfs kernel image manifest qemu smoke all clean distclean:
	bash build.sh $@
