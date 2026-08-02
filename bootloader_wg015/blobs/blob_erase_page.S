/* blob_erase_page.S — erase ONE 4K flash page, measuring the erase time.
 *
 * Layout/ABI: see blob_rescale_timings.S.
 *   PARAM0 (+8)   = page address (4K-aligned, >= 0x8000_1000)
 *   RESULT0 (+24) = rdcycle delta CMD-write -> BUSY clear (boot-3: the host
 *                   paces subsequent transfers on this measured time)
 *   status (+0)   = 0xB007C0DE ok / 0xBADADD00 address rejected
 *
 * ADDRESS GUARD (boot-2/R14): the loader is an ordinary page 0 of the same
 * 1 MB flash — reject anything below APP_BASE and outside flash; the CMD
 * value is a constant with only KEY|ERSEC, so ALLSEC/NVRON can never be set.
 * Runs with mstatus.MIE cleared (R7): fetches during erase return garbage,
 * so no flash-resident ISR may run; this code executes from TCM.
 */

	.section .text
	.word	0x000000aa          /* +0: report-id / status word */
	j	code                /* +4: entry */
	.org	8
	.word	0, 0, 0, 0          /* +8: PARAM0 = page address */
	.word	0, 0                /* +24: RESULT0 = erase cycles */
	.org	32
code:
	lw	t1, 8(a0)           /* PARAM0: page address */
	li	t2, 0x80001000      /* address guard: never the loader page */
	bltu	t1, t2, fail
	li	t2, 0x80100000      /* ...and never past the 1 MB flash */
	bgeu	t1, t2, fail
	slli	t2, t1, 20          /* low 12 bits must be zero (4K page) */
	bnez	t2, fail

	csrrci	a5, mstatus, 8      /* IRQ off for the whole dead window (R7) */
	li	t0, 0x3000D000
1:	lw	t2, 0x48(t0)        /* wait for a previous op, if any */
	andi	t2, t2, 1
	bnez	t2, 1b
	sw	t1, 0x00(t0)        /* ADDR */
	rdcycle	t3
	li	t2, 0xC0DE0004      /* CMD = KEY | ERSEC only (never ALLSEC/NVRON) */
	sw	t2, 0x44(t0)
	nop                         /* >= 5 NOP before the first BUSY read (F6) */
	nop
	nop
	nop
	nop
2:	lw	t2, 0x48(t0)
	andi	t2, t2, 1
	bnez	t2, 2b
	rdcycle	t4
	sub	t4, t4, t3
	sw	t4, 24(a0)          /* RESULT0: measured erase cycles */
	li	t2, 0xB007C0DE
	sw	t2, 0(a0)           /* status: OK */
	andi	a5, a5, 8
	beqz	a5, 3f
	csrsi	mstatus, 8
3:	ret

fail:	li	t2, 0xBADADD00
	sw	t2, 0(a0)
	ret
