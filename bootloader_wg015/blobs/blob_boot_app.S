/* blob_boot_app.S — boot the application.
 *
 * Deviation from V003 (which counted a negative runwordpad down into
 * boot_usercode on a live-USB loader): writes WG015_BOOT_FLAG_APP into
 * RTC_REG[0] and pulls RCU->RSTSYS.  After the soft reset the loader's
 * fast path (bootloader.c, boot-1) sees SYSRST + the flag and jumps to the
 * app BEFORE any USB/PLIC/DPU init — the app starts in near-reset state.
 *
 * The device drops off the bus immediately; the host gets no status
 * readback and must treat a successful send as completion.
 *
 * Layout/ABI: see blob_rescale_timings.S (no params, no results).
 */

	.section .text
	.word	0x000000aa          /* +0: report-id / status word */
	j	code                /* +4: entry */
	.org	8
	.word	0, 0, 0, 0          /* +8: params (none) */
	.word	0, 0                /* +24: results (none) */
	.org	32
code:
	csrci	mstatus, 8          /* no ISR between flag write and reset (R7) */
	li	t0, 0x38011020      /* PMURTC RTC_REG[0] (survives soft reset) */
	li	t1, 0x0AFF10AD      /* WG015_BOOT_FLAG_APP (shim ch32fun.h) */
	sw	t1, 0(t0)
	li	t0, 0x3000E000      /* RCU */
	li	t1, 0xA55A0001      /* RSTSYS = KEY A55A | RSTEN: one write = reset */
	sw	t1, 0xC0(t0)
1:	j	1b                  /* park until the reset takes effect */
