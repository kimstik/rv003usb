/* bench5_slot.c — P1.5: THE decisive G1 bench — isomorphic slot emulation
 * with an eviction adversary (PLAN §4 P1.5 + G1; redteam FL1/FL2/FL3).
 *
 * Slot cluster (bench_kernels.S:slot_packet, FL2-isomorphic):
 *   3 slot bodies at entry distances A->B 128 B, B->C 160 B, A->C 288 B
 *   (real RX paths span ~300 B, rv003usb.S:298-424), per-slot path chosen
 *   by PRBS9 DATA, a taken backward branch inside every slot + the backward
 *   dispatch branch, an lbu byte fetch in the TX-like body (S:890 site),
 *   and a B2 DATAOUTTGL marker opening every slot (per-slot times are the
 *   LA's job — FL9 forbids per-slot rdcycle; firmware sees only the
 *   packet-aggregate rdcycle pair).
 *
 * Packet = 102 slots = the worst-case drift window of the timing model
 * (88 bits + max stuffing, PLAN §3), so ONE packet measurement IS the
 * FL3 "sliding 102-slot window" cumulative sum; the histogram of packet
 * excursions over 10^4 PRBS packets is the G1-(b) input, and its worst
 * bin is the worst cumulative excursion.
 *
 * Adversary (FL1): between packets execute >4 KB of unrelated flash code
 * (evictor_4k, 4.4 KB) — variant 'ev'; variant 'ev+fi' adds fence.i.
 * G1-(a) determinism: identical PRBS seed replayed warm must give ZERO
 * cycle spread — any true nondeterminism means flash FAILS (static padding
 * cannot fix randomness).
 *
 * Excursion = packet_cycles - baseline, baseline = min over 128 warm
 * packets of the same variant.  Interpretation for G1-(b): worst excursion
 * must fit inside the re-derived phase tolerance (P3 TUNE sweep) minus
 * 2 cycles margin minus the sync constant (PLAN §4 G1).
 */

#include "bench_common.h"

#define B5_SLOTS      102u
#define B5_PACKETS    10000u
#define B5_WARMUP     128u
#define B5_COLD_PAIRS 100u

static hist_t b5_hist;
static uint8_t b5_buf[64];          /* TCM-B .bss: TX-like lbu source */
static uint32_t b5_prbs;

typedef enum { EV_NONE, EV_CODE, EV_CODE_FI } b5_ev_t;

static uint32_t b5_packet( slot_fn fn, b5_ev_t ev )
{
	if( ev != EV_NONE )
	{
		evictor_4k();
		if( ev == EV_CODE_FI ) fence_i();
	}
	return fn( B5_SLOTS, &b5_prbs, b5_buf, GPIOB_BASE );
}

static void b5_variant( const char *name, slot_fn fn, b5_ev_t ev )
{
	uart0_puts( "-- " );
	uart0_puts( name );
	uart0_puts( " --\n" );

	/* baseline: min of warm packets (no adversary), continuous PRBS */
	b5_prbs = 0x1FF;
	uint32_t base = 0xFFFFFFFFu;
	for( uint32_t i = 0; i < B5_WARMUP; i++ )
	{
		uint32_t c = b5_packet( fn, EV_NONE );
		if( c < base ) base = c;
	}

	hist_reset( &b5_hist );
	uint32_t raw_min = 0xFFFFFFFFu, raw_max = 0;
	for( uint32_t i = 0; i < B5_PACKETS; i++ )
	{
		uint32_t c = b5_packet( fn, ev );
		if( c < raw_min ) raw_min = c;
		if( c > raw_max ) raw_max = c;
		hist_add( &b5_hist, ( c > base ) ? ( c - base ) : 0u );
		if( ( i % 1000u ) == 999u ) uart0_putc( '.' );
	}
	uart0_puts( "\n  baseline(min of " );
	print_dec( B5_WARMUP );
	uart0_puts( " warm)=" );
	print_dec( base );
	uart0_puts( " cycles/packet; raw min/max=" );
	print_dec( raw_min ); uart0_putc( '/' ); print_dec( raw_max );
	uart0_puts( "\n  excursion histogram (cycles over baseline, per 102-slot window):\n" );
	hist_print_buckets( &b5_hist );
	uart0_puts( "  WORST cumulative excursion = " );
	print_dec( b5_hist.max );
	uart0_puts( " cycles (FL3/G1-(b) input)\n" );
}

/* G1-(a): identical seed, warm — repeats must be cycle-identical. */
static void b5_determinism( const char *name, slot_fn fn )
{
	uint32_t worst = 0;
	for( uint32_t seed = 1; seed <= 20; seed++ )
	{
		uint32_t ref = 0;
		b5_prbs = seed;
		fn( B5_SLOTS, &b5_prbs, b5_buf, GPIOB_BASE );  /* warm the paths */
		for( int r = 0; r < 3; r++ )
		{
			b5_prbs = seed;
			uint32_t c = fn( B5_SLOTS, &b5_prbs, b5_buf, GPIOB_BASE );
			if( r == 0 ) { ref = c; continue; }
			uint32_t d = ( c > ref ) ? c - ref : ref - c;
			if( d > worst ) worst = d;
		}
	}
	uart0_puts( "  " );
	uart0_puts( name );
	uart0_puts( " same-seed warm spread (20 seeds x 3 reps): max " );
	print_dec( worst );
	uart0_puts( worst ? " cycles  << NONDETERMINISM: G1-(a) FAIL for this placement\n"
	                  : " cycles (deterministic)\n" );
}

/* cold-vs-warm first-packet delta: evict+fence.i, packet1 (cold), packet2
 * (warm), same seed both. */
static void b5_cold_warm( const char *name, slot_fn fn )
{
	int32_t dmin = 0x7FFFFFFF, dmax = -0x7FFFFFFF;
	int64_t dsum = 0;
	for( uint32_t i = 0; i < B5_COLD_PAIRS; i++ )
	{
		evictor_4k();
		fence_i();
		b5_prbs = 0x0AB;
		uint32_t c1 = fn( B5_SLOTS, &b5_prbs, b5_buf, GPIOB_BASE );
		b5_prbs = 0x0AB;
		uint32_t c2 = fn( B5_SLOTS, &b5_prbs, b5_buf, GPIOB_BASE );
		int32_t d = (int32_t)c1 - (int32_t)c2;
		if( d < dmin ) dmin = d;
		if( d > dmax ) dmax = d;
		dsum += d;
	}
	uart0_puts( "  " );
	uart0_puts( name );
	uart0_puts( " cold-warm first-packet delta: mean " );
	print_sdec( (int32_t)( dsum / (int32_t)B5_COLD_PAIRS ) );
	uart0_puts( " min " );  print_sdec( dmin );
	uart0_puts( " max " );  print_sdec( dmax );
	uart0_puts( " cycles\n" );
}

void bench5_run( void )
{
	uart0_puts(
		"\n=== BENCH 5: isomorphic slot emulation + evictor (P1.5/G1) ===\n"
		"packet = 102 PRBS-selected slots (=FL3 sliding window); 10^4\n"
		"packets/variant; ONE rdcycle pair per packet (FL9); per-slot edges\n"
		"on B2 for the LA.  ev = 4.4KB flash evictor between packets (FL1);\n"
		"fi = +fence.i.  G1: (a) zero same-seed spread, or (b) worst\n"
		"excursion within the P3-derived tolerance - 2 - sync const.\n" );

	bench_gpio_init();
	for( uint32_t i = 0; i < sizeof( b5_buf ); i++ )
		b5_buf[i] = (uint8_t)( 0xA5u ^ ( i * 7u ) );   /* descriptor-ish */

	slot_fn f_flash = (slot_fn)(uintptr_t)slot_packet;
	tcm_code_reset();
	slot_fn f_tcm = (slot_fn)tcm_code_copy( slot_packet, slot_packet_end );

	b5_variant( "FLASH, no evictor      ", f_flash, EV_NONE );
	b5_variant( "FLASH, evictor         ", f_flash, EV_CODE );
	b5_variant( "FLASH, evictor+fence.i ", f_flash, EV_CODE_FI );
	b5_variant( "TCM,   no evictor      ", f_tcm,   EV_NONE );
	b5_variant( "TCM,   evictor+fence.i ", f_tcm,   EV_CODE_FI );

	uart0_puts( "-- G1-(a) determinism (same seed, warm repeats) --\n" );
	b5_determinism( "FLASH", f_flash );
	b5_determinism( "TCM  ", f_tcm );

	uart0_puts( "-- cold vs warm first packet --\n" );
	b5_cold_warm( "FLASH", f_flash );
	b5_cold_warm( "TCM  ", f_tcm );

	GPIOB->DATAOUTCLR = BENCH_MARKER_MASK;   /* re-park marker */
	uart0_puts( "bench5 done.\n" );
}
