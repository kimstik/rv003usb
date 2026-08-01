/* blob_rescale_timings.S — set FLASH TACCR..TPGSR for 48.000 MHz.
 *
 * Common WG015 blob layout (see also wg015hostcli/wg015bflash.c):
 *   +0  report-id word (host patches byte 0 = HID report id; blob writes
 *       STATUS here on completion: 0xB007C0DE ok / 0xBADxxxxx error)
 *   +4  entry point (loader calls scratchpad+4 with a0=scratchpad,
 *       a1=&runwordpad; standard call, return with ret)
 *   +8..+20   PARAM0..PARAM3 (patched by host)
 *   +24..+28  RESULT0 (rdcycle delta), RESULT1
 *   +32 code
 * Position-independent RV32IMC: absolute constants + local branches only.
 *
 * The loader already rescales these at startup (Р8); this blob exists for
 * host-driven re-scaling and as the R6/boot-3 measurement vehicle.
 * Registers are write-locked while STAT.BUSY=1, hence the leading poll.
 */

	.section .text
	.word	0x000000aa          /* +0: report-id / status word */
	j	code                /* +4: entry */
	.org	8
	.word	0, 0, 0, 0          /* +8: params (none) */
	.word	0, 0                /* +24: results */
	.org	32
code:
	csrrci	a5, mstatus, 8      /* IRQ off during flash-reg access (R7) */
	li	t0, 0x3000D000      /* FLASH controller */
1:	lw	t1, 0x48(t0)        /* STAT.BUSY: timing regs locked while set */
	andi	t1, t1, 1
	bnez	t1, 1b
	li	t1, 1               /* TACCR:  ceil(48 MHz * 20 ns)  */
	sw	t1, 0x1C(t0)
	li	t1, 240000          /* TNVSR:  5 ms                  */
	sw	t1, 0x20(t0)
	li	t1, 4800000         /* TERSR:  100 ms erase timebase */
	sw	t1, 0x24(t0)
	li	t1, 240             /* TNVHR:  5 us                  */
	sw	t1, 0x28(t0)
	li	t1, 4800            /* TNVH1R: 100 us                */
	sw	t1, 0x2C(t0)
	li	t1, 480             /* TRCVR:  10 us                 */
	sw	t1, 0x30(t0)
	sw	t1, 0x34(t0)        /* TPGSR:  10 us                 */
	li	t1, 0xB007C0DE
	sw	t1, 0(a0)           /* status: OK */
	andi	a5, a5, 8           /* restore MIE only if it was set */
	beqz	a5, 2f
	csrsi	mstatus, 8
2:	ret
