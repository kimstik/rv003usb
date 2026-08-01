/* bench3_irq.c — P1.3: IRQ entry latency, median AND spread, handler in
 * flash vs TCM, plus the TCM-stub->flash-tail transition (PLAN §4 P1.3,
 * Р7, risk R2; redteam T5 gate).
 *
 * Trigger: B3 is an OUTPUT whose pad is also read back by the GPIO input
 * stage (2-clk non-bypassable synchronizer) and armed as a falling-edge
 * interrupt on PLIC line 5 — a store to DATAOUTCLR IS the "wire" event.
 * There is no software way to set INTSTATUS (it is W1C-only, РП §11), so
 * if silicon ever gated the input path while OUTEN is set, the fallback is
 * an external jumper B3->B4 — the bench prints IRQ-TIMEOUT if the wired
 * -back event never arrives.
 *
 * Measured delta = rdcycle(handler instr 7) - rdcycle(just before the
 * triggering store).  It therefore CONTAINS: the triggering sw GPIO cost,
 * pad + 2-clk input sync, edge detect + PLIC + trap entry, and 6 handler
 * instructions (stash a0/a1, marker toggle at instr 6).  The B2 marker
 * gives the LA the same event chain with zero extra intrusion (Р10).
 *
 * T5 GATE: median(edge->1st instr) + 4 instr + lw GPIO + 2-clk sync must be
 * <= ~55 cycles (keepalive SE0 = 2 bit = 64 cycles).  The raw median is a
 * CONSERVATIVE stand-in for that chain: it already includes the sync, the
 * entry, and 7 instructions (vs the formula's 4+lw), plus the triggering
 * store; so raw median <= 55 => gate passes with margin.  Refine on paper
 * with bench1's sw/lw GPIO numbers when filling calibration.md.
 */

#include "bench_common.h"

#define B3_EVENTS 10000u

static hist_t b3_hist;

static inline uint32_t b3_trigger( void )
{
	uint32_t t0;
	/* rdcycle immediately glued to the falling-edge store */
	asm volatile(
		"rdcycle %0\n\t"
		"sw %2, %3(%1)"
		: "=&r"(t0)
		: "r"(GPIOB_BASE), "r"(BENCH_TRIG_MASK),
		  "i"(GPIO_DATAOUTCLR_OFFSET) );
	return t0;
}

static void b3_settle_high( void )
{
	GPIOB->DATAOUTSET = BENCH_TRIG_MASK;
	for( int i = 0; i < 8; i++ )
		(void)GPIOB->DATA;    /* >> 2-clk sync: high level well sampled */
}

/* returns 0 on success, 1 on timeout */
static int b3_variant( const char *name, const void *vec, int cold )
{
	hist_reset( &b3_hist );
	set_mtvec( vec );

	GPIOB->INTSTATUS  = 0xFFFFu;          /* W1C leftovers               */
	GPIOB->INTTYPESET = BENCH_TRIG_MASK;  /* edge                        */
	GPIOB->INTPOLCLR  = BENCH_TRIG_MASK;  /* falling                     */
	GPIOB->INTENSET   = BENCH_TRIG_MASK;
	bench_plic_gpio_enable();
	WG015_EnableMachineExternalIRQ();

	uint32_t timeouts = 0;
	for( uint32_t ev = 0; ev < B3_EVENTS; ev++ )
	{
		b3_settle_high();
		if( cold )
		{
			evictor_4k();   /* FL1: also evict, not only invalidate */
			fence_i();
		}
		bench_irq_flag = 0;
		uint32_t t0 = b3_trigger();
		uint32_t guard = 0;
		while( !bench_irq_flag )
		{
			if( ++guard > 1000000u ) { timeouts++; break; }
		}
		if( guard > 1000000u ) continue;
		hist_add( &b3_hist, bench_irq_cycle - t0 );
	}

	bench_irq_all_off();

	if( timeouts )
	{
		uart0_puts( "  " );
		uart0_puts( name );
		uart0_puts( ": IRQ-TIMEOUT x" );
		print_dec( timeouts );
		uart0_puts( " (wired-back trigger dead? see header comment)\n" );
		return 1;
	}
	hist_print_stats( name, &b3_hist );
	hist_print_buckets( &b3_hist );
	return 0;
}

void bench3_run( void )
{
	uart0_puts(
		"\n=== BENCH 3: IRQ entry latency (P1.3, T5) ===\n"
		"delta = rdcycle@handler-instr-7 - rdcycle@trigger-store; includes\n"
		"trigger sw + 2-clk sync + PLIC/trap entry + 6 handler instrs.\n"
		"B2 marker toggles at handler instr 6 (LA cross-check, P10).\n"
		"10000 events per variant; buckets are raw-delta cycles.\n" );

	bench_gpio_init();

	/* 1) handler fully in flash, warm (spin loop keeps cache hot) */
	int to = b3_variant( "  flash warm      ",
	                     irq3_handler, 0 );
	uint32_t med_warm = hist_median( &b3_hist );

	/* 2) handler fully in flash, cold: evictor + fence.i before EVERY event */
	if( !to ) b3_variant( "  flash cold      ", irq3_handler, 1 );
	uint32_t med_cold_flash = hist_median( &b3_hist );

	/* 3) handler fully in TCM-A (head stub candidate, R2 escape) */
	tcm_code_reset();
	void *h_tcm = tcm_code_copy( irq3_handler, irq3_handler_end );
	if( !to ) b3_variant( "  TCM warm        ", h_tcm, 0 );
	uint32_t med_tcm = hist_median( &b3_hist );
	if( !to ) b3_variant( "  TCM cold(evict) ", h_tcm, 1 );

	/* 4) TCM head stub -> absolute far jump -> flash tail (z8 shape):
	 * same head, so (stub-variant delta) - (TCM delta) = 0 by construction
	 * up to rdcycle; the LA sees the stub->tail transition after the
	 * marker; the printed numbers still bound the stub cost, and the tail
	 * runs cold when the evictor is on. */
	tcm_code_reset();
	void *stub_tcm = tcm_code_copy( irq3_stub, irq3_stub_end );
	if( !to ) b3_variant( "  TCMstub->flash  ", stub_tcm, 0 );
	if( !to ) b3_variant( "  TCMstub cold    ", stub_tcm, 1 );

	if( !to )
	{
		uart0_puts( "T5 GATE (median raw <= 55, conservative):\n" );
		uart0_puts( "  flash warm med=" );  print_dec( med_warm );
		uart0_puts( " cold med=" );          print_dec( med_cold_flash );
		uart0_puts( " tcm med=" );           print_dec( med_tcm );
		uart0_puts( "\n  verdict(worst flash): " );
		uart0_puts( ( med_cold_flash <= 55 ) ? "PASS" :
		            "FAIL -> TCM-A head stub MANDATORY (R2)" );
		uart0_puts( "\n" );
	}
	uart0_puts( "bench3 done.\n" );
}
