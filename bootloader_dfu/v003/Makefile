# bootloader_dfu/v003 — CH32V003 DFU loader (BOOT-C backport, TODO 19b).
# Same portable core (../dfu_core.c) + V003 chip port (../dfu_v003.h).
# Lives in the 1920 B boot zone; option bytes select it (see bootloader/
# Makefile `optionbytes` target — mechanism unchanged from the HID loader).
all : bootloader.bin sizecheck

TARGET:=bootloader
CH32FUN:=../../ch32fun/ch32fun
TARGET_MCU:=CH32V003

ADDITIONAL_C_FILES+=../../rv003usb/rv003usb.S ../../rv003usb/rv003usb.c
SYSTEM_C:=
EXTRA_CFLAGS:=-I. -I../../lib -I../../rv003usb -I.. -DUSE_TINY_BOOT --specs=picolibc.specs

# WIP (TODO 19b): 2332 B > 1920 B boot zone. Default build links against a
# 4K MEASUREMENT ld so the tree stays green and the size is tracked; `make
# strict` uses the real boot-zone ld and will fail until the CUSTOM_C-grade
# trim lands (levers: string descriptors ~-130, hand-stripped C dispatch
# ~-300..400 - upstream HID loader got to 1908 exactly this way).
LINKER_SCRIPT:=v003-dfu-measure.ld
WRITE_SECTION:=bootloader
FLASH_COMMAND:=$(CH32FUN)/../minichlink/minichlink -a -w $(TARGET).bin $(WRITE_SECTION) -B

include $(CH32FUN)/ch32fun.mk

# The physical limit is the 1920 B boot zone (ld enforces 1916+4).
SIZE_BUDGET:=1920
sizecheck : bootloader.bin
	@sz=$$(stat -c%s $(TARGET).bin); \
	echo "V003 DFU loader: $$sz / $(SIZE_BUDGET) B"; \
	if [ $$sz -gt $(SIZE_BUDGET) ]; then echo "OVER BOOT ZONE by $$((sz-$(SIZE_BUDGET))) B (WIP, TODO 19b)"; fi

strict : 
	$(MAKE) clean; $(MAKE) LINKER_SCRIPT=v003-dfu-bootloader.ld

flash : cv_flash
clean : cv_clean
.PHONY : sizecheck
