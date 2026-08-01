/* bench4_flash.c — P1.4: flash fetch profile + LAT=1 integrity stress
 * (PLAN §4 P1.4, F2, risks R1/R11; redteam FL7->R11).
 *
 * (a) straight-line 1024-instruction run, cold vs warm.  "Cold" = fence.i
 *     (the ONLY architectural I-cache control on the BM-310S6 core,
 *     research_bm310.md — the cache itself cannot be disabled) plus the
 *     FL1 evictor so prefetch buffers AND cache lines are gone.
 * (b) branch-over-32B: 64 jumps whose targets always leave the documented
 *     2x128-bit prefetch window -> per-branch miss cost.
 * (c) CEN (FLASH_CTRL bit1) 0 vs 1: UNDOCUMENTED in the RM (only the SDK
 *     sets it, presumed NIIET prefetch gate — research_flash.md §1); this
 *     bench only OBSERVES both settings and restores the original value.
 *     CFLUSH (bit8, write-only, undocumented) is pulsed once and timed.
 * (d) the same kernels from TCM-A: the 0-ws reference floor.
 * (e) R11 integrity stress: repeated checksum of 256 KB of flash at the
 *     CURRENT LAT (reset default 1).  Any mismatch = LAT=1 unsafe =>
 *     escalate LAT=2 and RERUN ALL OF P1 (every number is LAT-specific).
 */

#include "bench_common.h"

#define B4_INTEGRITY_WORDS  ( 256u * 1024u / 4u )
#define B4_INTEGRITY_PASSES 64u

static uint32_t b4_run1( kern0_fn fn ) { return fn(); }

static void b4_fetch_case( const char *name, const char *ks, const char *ke,
                           uint32_t nops )
{
	kern0_fn ff = (kern0_fn)(uintptr_t)ks;

	/* flash cold: full eviction first */
	evictor_4k();
	fence_i();
	uint32_t cold = b4_run1( ff );
	/* flash warm: back-to-back repeat */
	uint32_t warm = b4_run1( ff );

	/* TCM reference */
	tcm_code_reset();
	kern0_fn ft = (kern0_fn)tcm_code_copy( ks, ke );
	b4_run1( ft );
	uint32_t tcm = b4_run1( ft );

	uart0_puts( "  " );        uart0_puts( name );
	uart0_puts( ": cold " );   print_cyc100( cold, nops );
	uart0_puts( "  warm " );   print_cyc100( warm, nops );
	uart0_puts( "  tcm " );    print_cyc100( tcm, nops );
	uart0_puts( "  cycles/instr (totals " );
	print_dec( cold ); uart0_puts( "/" );
	print_dec( warm ); uart0_puts( "/" );
	print_dec( tcm );  uart0_puts( ")\n" );
}

static void b4_fetch_profile( void )
{
	b4_fetch_case( "straight-line 1024i (2KB)", k_straight1k, k_straight1k_end,
	               1024u );
	b4_fetch_case( "64x jump-over-32B        ", k_jump32, k_jump32_end, 64u );
}

static void b4_cen_experiment( void )
{
	uint32_t ctrl0 = WG015_FLASH->CTRL;
	uart0_puts( "  FLASH_CTRL initial = 0x" );
	print_hex8( ctrl0 );
	uart0_puts( " (LAT=" );
	print_dec( ( ctrl0 & FLASH_CTRL_LAT_Msk ) >> FLASH_CTRL_LAT_Pos );
	uart0_puts( ", CEN=" );
	print_dec( ( ctrl0 & FLASH_CTRL_CEN ) ? 1 : 0 );
	uart0_puts( ")\n" );

	for( int cen = 0; cen <= 1; cen++ )
	{
		uint32_t ctrl = cen ? ( ctrl0 | FLASH_CTRL_CEN )
		                    : ( ctrl0 & ~(uint32_t)FLASH_CTRL_CEN );
		WG015_FLASH->CTRL = ctrl;
		uint32_t rb = WG015_FLASH->CTRL;
		fence_i();
		uart0_puts( "  -- CEN=" );
		print_dec( (uint32_t)cen );
		uart0_puts( " (readback 0x" );
		print_hex8( rb );
		uart0_puts( ") --\n" );
		b4_fetch_profile();
	}

	/* CFLUSH pulse (undocumented, write-only): time the following cold run */
	WG015_FLASH->CTRL = ctrl0 | FLASH_CTRL_CFLUSH;
	fence_i();
	uart0_puts( "  -- after CFLUSH pulse (CTRL restored) --\n" );
	WG015_FLASH->CTRL = ctrl0;
	b4_fetch_profile();
	WG015_FLASH->CTRL = ctrl0;   /* restore, belt and braces */
}

static void b4_integrity( void )
{
	const volatile uint32_t *p = (const volatile uint32_t *)WG015_FLASH_MEM_BASE;
	uint32_t ref_s = 0, ref_x = 0, mism = 0;

	uart0_puts( "  scanning 256KB x " );
	print_dec( B4_INTEGRITY_PASSES );
	uart0_puts( " passes (add+rot-xor checksums, pass0 = reference) " );

	for( uint32_t pass = 0; pass < B4_INTEGRITY_PASSES; pass++ )
	{
		uint32_t s = 0, x = 0;
		for( uint32_t i = 0; i < B4_INTEGRITY_WORDS; i++ )
		{
			uint32_t w = p[i];
			s += w;
			x = ( ( x << 1 ) | ( x >> 31 ) ) ^ w;
		}
		if( pass == 0 ) { ref_s = s; ref_x = x; }
		else if( s != ref_s || x != ref_x ) mism++;
		if( ( pass & 7u ) == 7u ) uart0_putc( '.' );
	}
	uart0_puts( "\n  reference sum=0x" ); print_hex8( ref_s );
	uart0_puts( " xor=0x" );              print_hex8( ref_x );
	uart0_puts( "\n  MISMATCHED PASSES: " );
	print_dec( mism );
	uart0_puts( mism ? "  << LAT=1 UNSAFE: set LAT=2 and RERUN ALL P1 (R11)\n"
	                 : "  (0 = this run clean; soak longer for the R11 verdict)\n" );
}

void bench4_run( void )
{
	uart0_puts(
		"\n=== BENCH 4: flash fetch profile + LAT integrity (P1.4) ===\n"
		"cold = evictor(4.4KB)+fence.i; warm = immediate repeat; tcm = same\n"
		"kernel copied to TCM-A (0-ws floor).  cycles/instr x100; jump case\n"
		"is cycles/branch-over-32B (prefetch-window miss).  CEN/CFLUSH are\n"
		"UNDOCUMENTED — observed only, CTRL restored afterwards.\n" );

	uart0_puts( "-- fetch profile at current FLASH_CTRL --\n" );
	b4_fetch_profile();
	uart0_puts( "-- CEN / CFLUSH observation (P1.4v) --\n" );
	b4_cen_experiment();
	uart0_puts( "-- R11 LAT integrity stress (G1 precondition) --\n" );
	uart0_puts( "  LAT now = " );
	print_dec( ( WG015_FLASH->CTRL & FLASH_CTRL_LAT_Msk ) >> FLASH_CTRL_LAT_Pos );
	uart0_puts( "; all P1 numbers are tagged with this LAT.\n" );
	b4_integrity();
	uart0_puts( "bench4 done.\n" );
}
