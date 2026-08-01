/* wg015-dfu-bootloader.ld — rv003usb USB DFU bootloader on K1921VG015.
 *
 * Layout: loader = flash page 0, 4K @0x8000_0000 (assumed reset PC, F7);
 * app @0x8000_1000 (APP_BASE in bootloader.c — same slot as the HID loader,
 * the two loaders are interchangeable).  Unlike wg015-usb-bootloader.ld
 * there is NO SECRET tail (no host-blob protocol) and no scratchpad/
 * runwordpad contract — DFU keeps all state in ordinary .data/.bss.
 * Uses the common WG015 section layout via INCLUDE (resolved through the
 * -L rv003usb/wg015 flag set by Makefile.wg015).
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
	/* Loader budget = one full 4K page. */
	FLASH  (rx) : ORIGIN = 0x80000000, LENGTH = 4096
	TCMA  (rwx) : ORIGIN = 0x40000000, LENGTH = 128K
	TCMB  (rwx) : ORIGIN = 0x40020000, LENGTH = 128K
}

/* Bootloader is always the flash-first variant (Р6 default).  The flash
 * write/erase routine is section(".data.ramfunc") and rides to TCM-B with
 * the ordinary .data copy in startup_wg015.S. */
REGION_ALIAS("REGION_TIMECRIT", FLASH);

INCLUDE wg015_common.ld
