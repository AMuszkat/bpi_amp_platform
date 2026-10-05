# --- Per-developer settings ------------------------------------------------
# Board address, ssh user and kernel tree location are private to each setup,
# so they are NOT hardcoded here. Put them in local.mk (gitignored; start from
# local.mk.example) or export them in the environment, e.g.:
#   export BOARD_HOST=myboard.local
-include local.mk
BOARD_HOST ?= bpi-amp.local
BOARD_USER ?= $(USER)

DEFAULT_CONFIG_FILE := config-6.12.13-current-meson64
LINUX ?= linuxMainLine
DTB1 := meson-g12b-bananapi-cm4-cm4io.dtb
TARGET_MACHINE ?= root@$(BOARD_HOST)
TARGET_KERNEL1 := 6.12.20-current-meson64
TARGET_KERNEL2 := 6.12.22-current-meson64

# --- Target/host build arch ------------------------------------------------
# The target is always arm64. This Makefile was originally used on a native
# arm64 build host, so CROSS_COMPILE was empty. Auto-detect here: cross-compile
# only when this build host is NOT itself arm64. Both vars are overridable from
# the environment / command line, e.g.:
#   make CROSS_COMPILE= dtbs                        # force native (arm64 host)
#   make CROSS_COMPILE=aarch64-linux-gnu- image     # force cross
# Cross-building image/modules needs: apt install gcc-aarch64-linux-gnu
# (dtbs alone can use the host gcc as preprocessor). export -> the kernel
# sub-make ('make -C $(LINUX)') inherits these, so bare 'make dtbs' just works.
ARCH ?= arm64
ifeq ($(filter aarch64 arm64,$(shell uname -m)),)
  CROSS_COMPILE ?= aarch64-linux-gnu-
else
  CROSS_COMPILE ?=
endif
export ARCH CROSS_COMPILE

config:
	cp -f defconfig $(LINUX)/arch/arm64/configs/defconfig
	make -C $(LINUX) -j8 defconfig
	scp $(LINUX)/.config $(TARGET_MACHINE):/boot/config-$(TARGET_KERNEL)
	ssh $(TARGET_MACHINE) 'rm -f /boot/config-6.12.13-current-meson64'

clean_config:
	rm -f $(LINUX)/.config

image:
	make -C $(LINUX) -j8 Image
	gzip -c $(LINUX)/arch/arm64/boot/Image > $(LINUX)/arch/arm64/boot/vmlinuz-$(TARGET_KERNEL)

install_image:
	scp $(LINUX)/arch/arm64/boot/vmlinuz-$(TARGET_KERNEL) $(TARGET_MACHINE):/boot/vmlinuz-$(TARGET_KERNEL)
	scp $(LINUX)/System.map $(TARGET_MACHINE):/boot/System.map-$(TARGET_KERNEL)
	ssh $(TARGET_MACHINE) 'rm -f /boot/initrd.img-6.12.13-current-meson64 && rm -f /boot/System.map-6.12.13-current-meson64 && rm -f /boot/uInitrd-6.12.13-current-meson64 && rm -f /boot/vmlinuz-6.12.13-current-meson64'

dtbs:
	cp meson-*.dts $(LINUX)/arch/arm64/boot/dts/amlogic/
	cp meson-*.dtsi $(LINUX)/arch/arm64/boot/dts/amlogic/
	@echo 'dtb-$$(CONFIG_ARCH_MESON) += meson-g12b-bananapi-cm4-cm4io.dtb' > $(LINUX)/arch/arm64/boot/dts/amlogic/Makefile
	make -C $(LINUX) -j8 dtbs

install_dtbs:
#ssh $(TARGET_MACHINE) 'mv -f /boot/dtb-6.* /boot/dtb-$(TARGET_KERNEL) 2>/dev/null || true'
	scp $(LINUX)/arch/arm64/boot/dts/amlogic/$(DTB1) $(TARGET_MACHINE):/boot/dtb-$(TARGET_KERNEL1)/amlogic/$(DTB1)

modules:
	make -C $(LINUX) -j8 modules LOCALVERSION=""

install_modules:
	sudo make -C $(LINUX) -j8 modules_install INSTALL_MOD_PATH=$(shell pwd)
	tar --use-compress-program=pigz -cf $(TARGET_KERNEL).tar.gz -C lib/modules $(TARGET_KERNEL)
	scp -r $(TARGET_KERNEL).tar.gz $(TARGET_MACHINE):/lib/modules/
	ssh $(TARGET_MACHINE) 'tar -xvf /lib/modules/$(TARGET_KERNEL).tar.gz -C /lib/modules/ && rm /lib/modules/$(TARGET_KERNEL).tar.gz'

clean_modules:
	sudo rm -f -R lib
	rm -f $(TARGET_KERNEL).tar.gz
	ssh $(TARGET_MACHINE) 'rm -f -R /lib/modules/$(TARGET_KERNEL)*'

initrd:
	ssh $(TARGET_MACHINE) 'update-initramfs -c -k $(TARGET_KERNEL)'
	ssh $(TARGET_MACHINE) 'mkimage -A arm64 -O linux -T ramdisk -C gzip -n "uInitrd" -d /boot/initrd.img-$(TARGET_KERNEL) /boot/uInitrd-$(TARGET_KERNEL)'

#install_initrd:
#	scp /boot/initrd.img-$(TARGET_KERNEL) $(TARGET_MACHINE):/boot/initrd.img-$(TARGET_KERNEL)
#	scp uInitrd $(TARGET_MACHINE):/boot/uInitrd

clean_initrd:
	rm -f initrd.img-$(TARGET_KERNEL)
	rm -f uInitrd
	ssh $(TARGET_MACHINE) 'rm -f /boot/initrd.img-$(TARGET_KERNEL) && rm -f /boot/uInitrd'

update_symlink:
	ssh $(TARGET_MACHINE) 'ln -sf vmlinuz-$(TARGET_KERNEL) /boot/Image'
	ssh $(TARGET_MACHINE) 'ln -sf uInitrd-$(TARGET_KERNEL) /boot/uInitrd'
	ssh $(TARGET_MACHINE) 'ln -sf dtb-$(TARGET_KERNEL) /boot/dtb'

clean: clean_config clean_modules clean_initrd
	make -C $(LINUX) mrproper
	make -C $(LINUX) clean

patch:
	for patch in patch/*.patch; do \
		echo "Applying $$patch with git apply..."; \
		git -C $(LINUX) apply $$patch || exit 1; \
	done

all: image install_image dtbs install_dtbs modules install_modules initrd update_symlink

install: install_image install_dtbs install_modules