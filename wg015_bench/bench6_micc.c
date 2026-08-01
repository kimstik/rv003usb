/* bench6_micc.c — P1.6: PLIC MICC / GPIO INTSTATUS access cost + the
 * back-to-back IRQ exit-tail/re-entry window (PLAN §4 P1.6; redteam T7:
 * after our IN-data EOP the host ACK arrives as a NEW packet ~2 bit-times
 * + SYNC later, so full-tail + re-entry must be < ~200 cycles).
 *
 * Part 1 — access cost, aggregate over 1024 unrolled ops (FL9), kernels in
 * TCM-A so only the bus/MMR access is measured:
 *   lw MICC  = PLIC claim   (returns 0 here: GPIO source disabled, nothing
 *              pending — a harmless read of the claim register)
 *   sw MICC 0 = PLIC complete of id 0 — spec-defined no-op, pure bus cost
 *   lw/sw INTSTATUS (sw value 0 = W1C of nothing)
 *
 * Part 2 — back-to-back IRQ: irq6_handler (bench_kernels.S) re-arms a new
 * falling edge on B3 after the W1C on every even entry, so a request is
 * pending while the FULL exit tail (W1C -> claim -> complete -> restore ->
 * mret) executes and the core re-enters immediately.
 *   gap = stamps[odd] - stamps[even]
 *       = handler-after-rdcycle + full tail + trap re-entry.
 * The B2 marker toggles at each entry: the LA sees the same gap (Р10).
 */

#include "bench_common.h"

#define B6_REPS  16u
#define B6_N     ( B6_REPS * 64u )
#define B6_PAIRS ( B6_MAX_EVENTS / 2u )

static hist_t b6_hist;

static void b6_access_case( const char *name, const char *ks, const char *ke,
                            volatile void *addr, uint32_t val )
{
	tcm_code_reset();
	kern3_fn fn = (kern3_fn)tcm_code_copy( ks, ke );
	fn( addr, 2, val );
	uint32_t d = fn( addr, B6_REPS, val );
	uart0_puts( "  " );
	uart0_puts( name );
	uart0_puts( ": " );
	print_cyc100( d, B6_N );
	uart0_puts( " cycles/op (x1024)\n" );
}

static void b6_access_costs( void )
{
	/* GPIO source must be quiet: nothing enabled, nothing pending */
	bench_irq_all_off();
	b6_access_case( "baseline addi        ", k_base, k_base_end,
	                (volatile void *)&PLIC_MICC, 0 );
	b6_access_case( "lw  PLIC MICC (claim)", k_lw, k_lw_end,
	                (volatile void *)&PLIC_MICC, 0 );
	b6_access_case( "sw  PLIC MICC 0      ", k_sw, k_sw_end,
	                (volatile void *)&PLIC_MICC, 0 );
	b6_access_case( "lw  GPIOB INTSTATUS  ", k_lw, k_lw_end,
	                (volatile void *)&GPIOB->INTSTATUS, 0 );
	b6_access_case( "sw  GPIOB INTSTATUS 0", k_sw, k_sw_end,
	                (volatile void *)&GPIOB->INTSTATUS, 0 );
}

static void b6_back_to_back( void )
{
	bench_gpio_init();
	b6_count = 0;
	set_mtvec( irq6_handler );

	GPIOB->INTSTATUS  = 0xFFFFu;
	GPIOB->INTTYPESET = BENCH_TRIG_MASK;
	GPIOB->INTPOLCLR  = BENCH_TRIG_MASK;
	GPIOB->INTENSET   = BENCH_TRIG_MASK;
	bench_plic_gpio_enable();
	WG015_EnableMachineExternalIRQ();

	uint32_t pairs_done = 0, timeouts = 0;
	while( pairs_done < B6_PAIRS )
	{
		uint32_t want = ( pairs_done + 1u ) * 2u;
		/* arm B3 high, settle through the 2-clk sync, fire one falling
		 * edge; the handler itself chains the second event of the pair. */
		GPIOB->DATAOUTSET = BENCH_TRIG_MASK;
		for( int i = 0; i < 8; i++ )
			(void)GPIOB->DATA;
		GPIOB->DATAOUTCLR = BENCH_TRIG_MASK;
		uint32_t guard = 0;
		while( b6_count < want )
		{
			if( ++guard > 1000000u ) { timeouts++; break; }
		}
		if( guard > 1000000u ) break;
		pairs_done++;
	}

	bench_irq_all_off();

	if( timeouts )
	{
		uart0_puts( "  IRQ-TIMEOUT after " );
		print_dec( pairs_done );
		uart0_puts( " pairs (wired-back trigger dead?)\n" );
		if( !pairs_done ) return;
	}

	hist_reset( &b6_hist );
	for( uint32_t i = 0; i < pairs_done; i++ )
		hist_add( &b6_hist, b6_stamps[2 * i + 1] - b6_stamps[2 * i] );

	hist_print_stats( "  entry->re-entry gap", &b6_hist );
	hist_print_buckets( &b6_hist );
	uart0_puts(
		"  gap = handler-after-rdcycle + FULL tail (W1C,claim,complete,\n"
		"  restore,mret) + trap re-entry.  Budget check (T7): tail+re-entry\n"
		"  < ~200 cycles => " );
	uart0_puts( ( hist_median( &b6_hist ) < 200u ) ? "PASS (median)" :
	            "FAIL (median) — ACK re-entry window at risk (R9/tier-b)" );
	uart0_puts( "\n  (exact tail-only figure: LA gap between B2 marker edges\n"
	            "  minus handler head; handler length is constant.)\n" );
}

void bench6_run( void )
{
	uart0_puts(
		"\n=== BENCH 6: MICC/INTSTATUS cost + back-to-back IRQ (P1.6/T7) ===\n" );
	uart0_puts( "-- MMR access cost (kernels in TCM, aggregate x1024) --\n" );
	b6_access_costs();
	uart0_puts( "-- back-to-back IRQ: full ISR tail + re-entry gap --\n" );
	b6_back_to_back();
	uart0_puts( "bench6 done.\n" );
}
