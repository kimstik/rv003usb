# bootloader_wg015_dfu — thin wrapper over the shared WG015 build
# (mirrors bootloader_wg015/Makefile).  NOTE: RV003USB_BOOTLOADER is NOT
# defined here — the DFU loader does not use the scratchpad/runwordpad blob
# hooks in rv003usb.c; all protocol handling goes through
# RV003USB_OTHER_CONTROL/RV003USB_SUPPORT_CONTROL_OUT (usb_config.h).
#
# Build:  make PREFIX=riscv64-unknown-elf-      (or riscv-none-elf-)

TARGET := bootloader
LDSCRIPT := wg015-dfu-bootloader.ld

include ../../rv003usb/wg015/Makefile.wg015

# Size discipline (as bootloader_wg015): current 2748 B; hard ld cap 4096.
SIZE_BUDGET := 3000
all : sizecheck
sizecheck : $(TARGET).elf
	@sz=$$($(SIZE) $(TARGET).elf | awk 'NR==2{print $$1+$$2}'); \
	if [ $$sz -gt $(SIZE_BUDGET) ]; then \
		echo "SIZE BUDGET EXCEEDED: $$sz > $(SIZE_BUDGET) B"; exit 1; \
	else echo "size ok: $$sz / $(SIZE_BUDGET) B"; fi
.PHONY : sizecheck
