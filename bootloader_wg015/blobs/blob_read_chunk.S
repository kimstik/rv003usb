/* blob_read_chunk.S — copy up to 832 bytes of flash into the scratchpad data
 * area (scratchpad+0x120) for host readback.  Used by the CLI `verify`
 * command (host-side compare) and to read the SECRET word at 0x8000_0FC0.
 *
 * Layout/ABI: see blob_rescale_timings.S.
 *   PARAM0 (+8)  = source address (4-byte aligned; anywhere in flash,
 *                  loader page included - reads are harmless)
 *   PARAM1 (+12) = length in bytes (4-byte multiple, <= 832)
 *   status (+0)  = 0xB007C0DE ok / 0xBADADD00 / 0xBADBAD00
 *
 * Plain reads, no flash command - still runs IRQ-off for uniformity (R7).
 */

	.section .text
	.word	0x000000aa          /* +0: report-id / status word */
	j	code                /* +4: entry */
	.org	8
	.word	0, 0, 0, 0          /* +8: PARAM0 src, PARAM1 len */
	.word	0, 0                /* +24: results (unused) */
	.org	32
code:
	lw	t1, 8(a0)           /* src */
	lw	t2, 12(a0)          /* len */
	li	t3, 0x80000000
	bltu	t1, t3, badaddr
	li	t3, 0x80100000
	add	t4, t1, t2
	bgtu	t4, t3, badaddr
	beqz	t2, badparam
	li	t3, 832
	bgtu	t2, t3, badparam
	or	t3, t1, t2
	andi	t3, t3, 3
	bnez	t3, badparam

	csrrci	a5, mstatus, 8
	addi	a2, a0, 0x120       /* dest = scratchpad data area */
1:	lw	t3, 0(t1)
	sw	t3, 0(a2)
	addi	t1, t1, 4
	addi	a2, a2, 4
	addi	t2, t2, -4
	bnez	t2, 1b
	li	t3, 0xB007C0DE
	sw	t3, 0(a0)           /* status: OK */
	andi	a5, a5, 8
	beqz	a5, 2f
	csrsi	mstatus, 8
2:	ret

badaddr:
	li	t3, 0xBADADD00
	sw	t3, 0(a0)
	ret
badparam:
	li	t3, 0xBADBAD00
	sw	t3, 0(a0)
	ret
