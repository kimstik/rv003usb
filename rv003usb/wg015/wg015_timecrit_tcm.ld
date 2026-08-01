/* wg015_timecrit_tcm.ld — rv003usb WG015 port, TCM variant (PLAN Р6 отход /
 * risk R1): .timecrit runs from TCM-A (load address in flash, copied by
 * startup_wg015.S), data/stack stay in TCM-B — independent TCM ports avoid
 * I+D contention (research_flash.md §6).
 *
 * Select with:  make LDSCRIPT=.../wg015_timecrit_tcm.ld  (see Makefile.wg015,
 * TIMECRIT_TCM=1).  NOTE: activating this variant requires the z8 far-branch
 * trampolines (flash and TCM are ~1 GB apart — c.beqz into C handlers cannot
 * reach; PLAN Р2).
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
	FLASH (rx)  : ORIGIN = 0x80000000, LENGTH = 1024K
	TCMA  (rwx) : ORIGIN = 0x40000000, LENGTH = 128K
	TCMB  (rwx) : ORIGIN = 0x40020000, LENGTH = 128K
}

/* One-line variant switch: .timecrit executes from TCM-A. */
REGION_ALIAS("REGION_TIMECRIT", TCMA);

INCLUDE wg015_common.ld
