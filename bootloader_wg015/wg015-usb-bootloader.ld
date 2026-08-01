/* wg015-usb-bootloader.ld — rv003usb USB bootloader on K1921VG015 (PLAN Р8).
 *
 * Layout: loader = flash page 0, 4K @0x8000_0000 (assumed reset PC, F7);
 * app @0x8000_1000 (APP_BASE in bootloader.c; R8 escalation: 8K, app @+0x2000).
 * Uses the common WG015 section layout via INCLUDE (resolved through the
 * -L rv003usb/wg015 flag set by Makefile.wg015).
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
	/* Loader budget = one 4K page minus the 64-byte SECRET tail; a link
	 * overflow here IS the R8 gate (escalate to 8K + app @+0x2000). */
	FLASH  (rx) : ORIGIN = 0x80000000, LENGTH = 4096 - 64
	SECRET (r)  : ORIGIN = 0x80000FC0, LENGTH = 64
	/* First 4K of TCM-A belong to the host-blob contract (see PROVIDEs at
	 * the bottom); loader code itself never occupies TCM-A (flash-first). */
	TCMA  (rwx) : ORIGIN = 0x40001000, LENGTH = 128K - 4K
	TCMB  (rwx) : ORIGIN = 0x40020000, LENGTH = 128K
}

/* Bootloader is always the flash-first variant (Р6 default). */
REGION_ALIAS("REGION_TIMECRIT", FLASH);

INCLUDE wg015_common.ld

/* Bootloader extras: appended after the common layout — GNU ld processes
 * SECTIONS commands in order, so FLASH allocation continues after .text. */
SECTIONS
{
	/* boot_usercode: the exit-to-app routine, at a loader-version-dependent
	 * address published through the SECRET word.  boot-5: the V003 formula
	 * packed a full 16-bit ADDRESS, ((a^0x77C)<<16)|a — it silently breaks
	 * at 0x8000_xxxx; here the OFFSET from 0x8000_0000 is folded instead.
	 * ((off|K)-(off&K)) == off^K (ld script has no ^ operator); the XOR key
	 * K = 0xFC0 = the SECRET offset, mirroring the V003 convention. */
	.boot_firmware : ALIGN(4)
	{
		_boot_firmware_xor = ABSOLUTE(
			( ( ((ABSOLUTE(.) - 0x80000000) | 0xFC0)
			  - ((ABSOLUTE(.) - 0x80000000) & 0xFC0) ) << 16 )
			| (ABSOLUTE(.) - 0x80000000) );
		KEEP(*(.boot_firmware))
		. = ALIGN(4);
	} > FLASH

	/* SECRET word at fixed flash offset 0xFC0.  Host decode: off = low 16
	 * bits; integrity check high 16 == off ^ 0xFC0; boot_usercode address
	 * = 0x8000_0000 + off. */
	.secret_address : { KEEP(*(.secret_address)) } > SECRET
}

/* Host-blob contract, FIXED for all loader versions (byte-compatible with the
 * V003 scratchpad protocol, bootloader/bootloader.c:300-311):
 *   scratchpad @ 0x40000000 (TCM-A base), 1152 B = 1024+128; blob code is
 *              entered at scratchpad+4 (word 0 = HID report-id/status word);
 *   runwordpad @ 0x40000480 (int32, right after the scratchpad);
 *   0x40000484..0x40000FFF — free runpad space for blobs.
 * Blobs execute from TCM-A while flash is BUSY (no-read-while-write, F6). */
PROVIDE( scratchpad = 0x40000000 );
PROVIDE( runwordpad = 0x40000480 );
