/* wg015_flash.ld — rv003usb WG015 port, DEFAULT variant (PLAN Р6):
 * all code, including .timecrit, executes from flash (flash-first).
 * Switch to the TCM-A variant by linking with wg015_timecrit_tcm.ld instead
 * (same common layout, one REGION_ALIAS differs).
 *
 * Memory map per РП табл. 6.1 (research_core_irq.md §6): flash 1M
 * @0x8000_0000; RAM0 split TCM-A/TCM-B 128K+128K (research_flash.md §6).
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
	FLASH (rx)  : ORIGIN = 0x80000000, LENGTH = 1024K
	TCMA  (rwx) : ORIGIN = 0x40000000, LENGTH = 128K
	TCMB  (rwx) : ORIGIN = 0x40020000, LENGTH = 128K
}

/* One-line variant switch: .timecrit stays in FLASH (VMA == LMA). */
REGION_ALIAS("REGION_TIMECRIT", FLASH);

INCLUDE wg015_common.ld
