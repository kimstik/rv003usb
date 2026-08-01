# bootloader_wg015_dfu — thin wrapper over the shared WG015 build
# (mirrors bootloader_wg015/Makefile).  NOTE: RV003USB_BOOTLOADER is NOT
# defined here — the DFU loader does not use the scratchpad/runwordpad blob
# hooks in rv003usb.c; all protocol handling goes through
# RV003USB_OTHER_CONTROL/RV003USB_SUPPORT_CONTROL_OUT (usb_config.h).
#
# Build:  make PREFIX=riscv64-unknown-elf-      (or riscv-none-elf-)

TARGET := bootloader
LDSCRIPT := wg015-dfu-bootloader.ld

include ../rv003usb/wg015/Makefile.wg015
