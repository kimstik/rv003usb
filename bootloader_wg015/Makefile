# bootloader_wg015 — thin wrapper over the shared WG015 build
# (mirrors bootloader/Makefile; USE_TINY_BOOT is NOT used here because
# startup_wg015.S is the startup — the WCH tiny-boot block is V003-only).
#
# Build:  make PREFIX=riscv64-unknown-elf-      (or riscv-none-elf-)

TARGET := bootloader
LDSCRIPT := wg015-usb-bootloader.ld
CFLAGS += -DRV003USB_BOOTLOADER=1

include ../rv003usb/wg015/Makefile.wg015

# Host-side blob binaries (see blobs/) are built separately:
blobs :
	$(MAKE) -C blobs PREFIX=$(PREFIX)

.PHONY : blobs
