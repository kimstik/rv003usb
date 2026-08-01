/* wg015_common.ld — common section layout for the rv003usb WG015 port.
 *
 * Included by wg015_flash.ld (default: .timecrit executes from FLASH) and
 * wg015_timecrit_tcm.ld (R1 fallback: .timecrit runs from TCM-A).  The
 * includer must define MEMORY regions FLASH/TCMA/TCMB and
 * REGION_ALIAS("REGION_TIMECRIT", ...).  Structure mirrors the SDK's
 * k1921vg015_*.ld -> k1921vg015_common.lds include scheme.
 *
 * Placement rules (PLAN Р3/Р6): code in FLASH by default; everything read in
 * the cycle-counted path lives in RAM — .rodata.usbdesc (descriptors, read
 * byte-wise inside the TX slot, rv003usb.S:887-890) is folded into .data and
 * copied to TCM-B by startup_wg015.S.  Data/stack in TCM-B, time-critical
 * code (if relocated) in TCM-A — the banks have independent ports
 * (research_flash.md §6).
 *
 * TODO(port): bootloader layout (loader 4K @0x8000_0000, app @+0x1000, PLAN
 * Р8) is parameterized in P5 — this scaffold links against full flash.
 */

STACK_SIZE = DEFINED(STACK_SIZE) ? STACK_SIZE : 2048;

SECTIONS
{
	/* Startup at the flash base = assumed reset PC (F7, SDK-inferred). */
	.init : ALIGN(4)
	{
		KEEP(*(.init))
		KEEP(*(.init.*))
		. = ALIGN(4);
	} > FLASH

	/* .data placed in the script BEFORE .text so KEEP(*(.rodata.usbdesc*))
	 * claims the descriptors ahead of the .rodata.* glob below (ld
	 * first-match rule).  LMA sits in flash right after .init. */
	.data : ALIGN(4)
	{
		__data_start = .;
		KEEP(*(.rodata.usbdesc))
		KEEP(*(.rodata.usbdesc.*))
		*(.data .data.*)
		*(.sdata .sdata.*)
		. = ALIGN(4);
		__data_end = .;
	} > TCMB AT> FLASH
	__data_lma = LOADADDR(.data);

	/* Time-critical code.  Default variant: REGION_TIMECRIT = FLASH, so
	 * VMA == LMA and the startup copy loop degenerates to a no-op.  TCM
	 * variant: REGION_TIMECRIT = TCMA, load-address stays in flash and
	 * startup copies it (one-line switch = which top-level .ld you link
	 * with; z8 far-branch trampolines become live in that variant). */
	.timecrit : ALIGN(4)
	{
		__timecrit_start = .;
		KEEP(*(.timecrit))
		KEEP(*(.timecrit.*))
		. = ALIGN(4);
		__timecrit_end = .;
	} > REGION_TIMECRIT AT> FLASH
	__timecrit_lma = LOADADDR(.timecrit);

	.text : ALIGN(4)
	{
		*(.text .text.*)
		*(.rodata .rodata.*)
		*(.srodata .srodata.*)
		. = ALIGN(4);
	} > FLASH

	.bss (NOLOAD) : ALIGN(4)
	{
		__bss_start = .;
		*(.sbss .sbss.*)
		*(.bss .bss.*)
		*(COMMON)
		. = ALIGN(4);
		__bss_end = .;
	} > TCMB

	/* gp covers .sdata/.sbss with the standard +/-2K window */
	PROVIDE(__global_pointer$ = __data_start + 0x800);

	/* Stack: top of TCM-B, growing down. */
	PROVIDE(__stack_top = ORIGIN(TCMB) + LENGTH(TCMB));
	ASSERT(__bss_end + STACK_SIZE <= ORIGIN(TCMB) + LENGTH(TCMB),
	       "TCM-B overflow: .data+.bss+stack exceed 128K")

	/DISCARD/ :
	{
		*(.eh_frame .eh_frame.*)
		*(.note .note.*)
	}
}
