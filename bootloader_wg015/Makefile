# bootloader_wg015 — thin wrapper over the shared WG015 build
# (mirrors bootloader/Makefile; USE_TINY_BOOT is NOT used here because
# startup_wg015.S is the startup — the WCH tiny-boot block is V003-only).
#
# Build:  make PREFIX=riscv64-unknown-elf-      (or riscv-none-elf-)

TARGET := bootloader
LDSCRIPT := wg015-usb-bootloader.ld
CFLAGS += -DRV003USB_BOOTLOADER=1

include ../rv003usb/wg015/Makefile.wg015

# Size discipline: the ld script hard-fails above 4032, but creep is caught
# earlier — soft budget with headroom for P6 features. Current: 2352 B
# (vs V003 1920: +250 startup/PLL, +80 exit contract, +100 RV32I@0x8000_0000
# address tax; vs the comparable shared-C-layer v006 loader at 3324 we are
# 30% leaner). Trim pass scheduled at P6, after silicon proof.
SIZE_BUDGET := 2600
all : sizecheck
sizecheck : $(TARGET).elf
	@sz=$$($(SIZE) $(TARGET).elf | awk 'NR==2{print $$1+$$2}'); \
	if [ $$sz -gt $(SIZE_BUDGET) ]; then \
		echo "SIZE BUDGET EXCEEDED: $$sz > $(SIZE_BUDGET) B"; exit 1; \
	else echo "size ok: $$sz / $(SIZE_BUDGET) B"; fi

# Host-side blob binaries (see blobs/) are built separately:
blobs :
	$(MAKE) -C blobs PREFIX=$(PREFIX)

.PHONY : blobs sizecheck
