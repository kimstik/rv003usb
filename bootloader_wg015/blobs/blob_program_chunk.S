/* blob_program_chunk.S — program up to 832 bytes from the scratchpad data
 * area into flash.  Parameterized 16- vs 64-byte program unit (R6: РП/SVD
 * say 128 bits = DATA0..3, the SDK driver loops 16 words = 64 B; which one
 * is real needs the hardware test — the host picks with PARAM2).
 *
 * Layout/ABI: see blob_rescale_timings.S.
 *   PARAM0 (+8)   = destination flash address (16-byte aligned, >= APP_BASE)
 *   PARAM1 (+12)  = unit count
 *   PARAM2 (+16)  = unit size in bytes: 16 or 64
 *   data          = at scratchpad+0x120 (this code must stay below 0x120)
 *   RESULT0 (+24) = rdcycle delta over the whole program loop (boot-3 pacing)
 *   status  (+0)  = 0xB007C0DE ok / 0xBADADD00 bad address / 0xBADBAD00 bad params
 *
 * ADDRESS GUARD (boot-2/R14) and IRQ-off (R7) as in blob_erase_page.S.
 */

	.section .text
	.word	0x000000aa          /* +0: report-id / status word */
	j	code                /* +4: entry */
	.org	8
	.word	0, 0, 0, 0          /* +8: PARAM0 dest, PARAM1 count, PARAM2 unit */
	.word	0, 0                /* +24: RESULT0 = program cycles */
	.org	32
code:
	lw	t1, 8(a0)           /* dest */
	lw	t2, 12(a0)          /* unit count */
	lw	t3, 16(a0)          /* unit size */
	li	t4, 16              /* unit must be 16 or 64 */
	beq	t3, t4, unit_ok
	li	t4, 64
	bne	t3, t4, badparam
unit_ok:
	beqz	t2, badparam
	mul	t4, t2, t3          /* total bytes */
	li	t5, 832             /* data area 0x120..0x460 of the scratchpad */
	bgtu	t4, t5, badparam
	li	t5, 0x80001000      /* address guard: never the loader page */
	bltu	t1, t5, badaddr
	li	t5, 0x80100000
	add	t4, t4, t1          /* end address */
	bgtu	t4, t5, badaddr
	andi	t4, t1, 15          /* ADDR is 16-byte aligned (РП А.4) */
	bnez	t4, badaddr

	csrrci	a5, mstatus, 8      /* IRQ off for all dead windows (R7) */
	li	t0, 0x3000D000
	addi	a2, a0, 0x120       /* src cursor */
	rdcycle	a4
unit_loop:
1:	lw	t5, 0x48(t0)        /* wait not BUSY (also drains previous unit) */
	andi	t5, t5, 1
	bnez	t5, 1b
	sw	t1, 0x00(t0)        /* ADDR */
	li	a3, 4               /* DATA register cursor: +0x04.. */
	mv	t6, t3
copy:	lw	t5, 0(a2)
	add	t4, t0, a3
	sw	t5, 0(t4)           /* DATA[i] (unit 64 spills into +0x14..0x40,
	                             exactly what the SDK driver does - R6) */
	addi	a2, a2, 4
	addi	a3, a3, 4
	addi	t6, t6, -4
	bnez	t6, copy
	li	t5, 0xC0DE0002      /* CMD = KEY | WR only (never ALLSEC/NVRON) */
	sw	t5, 0x44(t0)
	nop                         /* >= 5 NOP before the first BUSY read (F6) */
	nop
	nop
	nop
	nop
2:	lw	t5, 0x48(t0)
	andi	t5, t5, 1
	bnez	t5, 2b
	add	t1, t1, t3
	addi	t2, t2, -1
	bnez	t2, unit_loop

	rdcycle	t5
	sub	t5, t5, a4
	sw	t5, 24(a0)          /* RESULT0: total program cycles */
	li	t5, 0xB007C0DE
	sw	t5, 0(a0)           /* status: OK */
	andi	a5, a5, 8
	beqz	a5, 3f
	csrsi	mstatus, 8
3:	ret

badaddr:
	li	t5, 0xBADADD00
	sw	t5, 0(a0)
	ret
badparam:
	li	t5, 0xBADBAD00
	sw	t5, 0(a0)
	ret
