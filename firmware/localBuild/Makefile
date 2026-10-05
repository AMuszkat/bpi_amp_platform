# Board address/user come from ../local.mk (gitignored) or the environment.
# See ../local.mk.example.
-include ../local.mk
BOARD_HOST ?= bpi-amp.local
BOARD_USER ?= $(USER)

TARGET_MACHINE_AM ?= $(BOARD_USER)@$(BOARD_HOST)
TARGET_MACHINE_ROOT ?= root@$(BOARD_HOST)
TARGET_KERNEL := 6.12.22-current-meson64

all:
	ssh $(TARGET_MACHINE_AM) 'mkdir -p ~/localModuleBuild'
	scp dummy-codec-am.c $(TARGET_MACHINE_AM):~/localModuleBuild/
	scp targetMakefile_dummy_codec $(TARGET_MACHINE_AM):~/localModuleBuild/Makefile
	ssh $(TARGET_MACHINE_AM) 'cd ~/localModuleBuild && make all -j8'
	scp $(TARGET_MACHINE_AM):~/localModuleBuild/dummy-codec-am.ko $(CURDIR)/
	scp dummy-codec-am.ko $(TARGET_MACHINE_ROOT):/lib/modules/$(TARGET_KERNEL)/kernel/sound/soc/codecs/

axg_card:
	ssh $(TARGET_MACHINE_AM) 'mkdir -p ~/localModuleBuild'
	scp snd-soc-meson-axg-sound-card.c $(TARGET_MACHINE_AM):~/localModuleBuild/
	scp axg-tdm.h $(TARGET_MACHINE_AM):~/localModuleBuild/
	scp meson-card.h $(TARGET_MACHINE_AM):~/localModuleBuild/
	scp targetMakefile_axg_card $(TARGET_MACHINE_AM):~/localModuleBuild/Makefile
	ssh $(TARGET_MACHINE_AM) 'cd ~/localModuleBuild && make all -j8'
	scp $(TARGET_MACHINE_AM):~/localModuleBuild/snd-soc-meson-axg-sound-card.ko $(CURDIR)/
	scp snd-soc-meson-axg-sound-card.ko $(TARGET_MACHINE_ROOT):/lib/modules/$(TARGET_KERNEL)/kernel/sound/soc/

max98396:
	ssh $(TARGET_MACHINE_AM) 'mkdir -p ~/localModuleBuild'
	scp max98396.c $(TARGET_MACHINE_AM):~/localModuleBuild/snd-soc-max98396.c
	scp max98396.h $(TARGET_MACHINE_AM):~/localModuleBuild/
	scp targetMakefile_max98396 $(TARGET_MACHINE_AM):~/localModuleBuild/Makefile
	ssh $(TARGET_MACHINE_AM) 'cd ~/localModuleBuild && make all -j8'
	scp $(TARGET_MACHINE_AM):~/localModuleBuild/*max98396.ko $(CURDIR)/
	scp *max98396.ko $(TARGET_MACHINE_ROOT):/lib/modules/$(TARGET_KERNEL)/kernel/sound/soc/codecs/
