/* bench2_branch.c — P1.2: taken/untaken branch cost, aligned vs misaligned
 * targets, from TCM and from flash (PLAN §4 P1.2; F3: branch penalty 0 or 1
 * on the 2-stage BM-310 pipe — this bench decides which).
 *
 * Method: 64 branch units per rep, 16 reps => 1024 branches, aggregate
 * rdcycle pair per run (FL9).  Units are 4-byte `beq zero,zero` (assembler
 * cannot compress x0-relative branches, so unit layout is exact):
 *   taken/aligned    target at 4n
 *   taken/misaligned target at 4n+2 (a skipped c.nop before the label;
 *                    the re-alignment c.nop after the target IS executed —
 *                    that row carries +~1 cycle of its own, see printout)
 *   untaken          fall-through only
 * Each 64-unit rep also closes with one taken backward branch (loop).
 * cycles/branch = printed value; subtract the bench1 baseline row (~1.03)
 * to get the penalty over a 1-cycle ALU op.
 */

#include "bench_common.h"

#define B2_REPS 16u
#define B2_N    ( B2_REPS * 64u )

typedef struct
{
	const char *name;
	const char *kstart, *kend;
} b2_case_t;

static const b2_case_t b2_cases[] = {
	{ "baseline addi     ", k_base,         k_base_end },
	{ "taken, target 4n  ", k_br_taken_al,  k_br_taken_al_end },
	{ "taken, target 4n+2", k_br_taken_mis, k_br_taken_mis_end },
	{ "untaken           ", k_br_untaken,   k_br_untaken_end },
};

static void b2_pass( int from_tcm )
{
	for( uint32_t i = 0; i < sizeof( b2_cases ) / sizeof( b2_cases[0] ); i++ )
	{
		kern3_fn fn;
		if( from_tcm )
		{
			tcm_code_reset();
			fn = (kern3_fn)tcm_code_copy( b2_cases[i].kstart, b2_cases[i].kend );
		}
		else
			fn = (kern3_fn)(uintptr_t)b2_cases[i].kstart;

		fn( 0, 2, 0 );                       /* warm-up */
		uint32_t d = fn( 0, B2_REPS, 0 );    /* measured */
		uart0_puts( "  " );
		uart0_puts( b2_cases[i].name );
		uart0_puts( ": " );
		print_cyc100( d, B2_N );
		uart0_puts( " cycles/branch (x1024, total " );
		print_dec( d );
		uart0_puts( ")\n" );
	}
}

void bench2_run( void )
{
	uart0_puts(
		"\n=== BENCH 2: branch cost (P1.2) ===\n"
		"1024 branch units, ONE rdcycle pair per run; cycles/branch x100.\n"
		"Penalty = row - baseline row.  Taken-vs-untaken delta = branch\n"
		"penalty; 4n vs 4n+2 delta = misaligned-target fetch cost.\n"
		"NOTE: the 4n+2 kernel executes one extra c.nop per unit (alignment\n"
		"filler) — subtract ~1.00 from that row before comparing.\n" );

	uart0_puts( "-- kernels in TCM-A (pipeline-only penalty) --\n" );
	b2_pass( 1 );
	uart0_puts( "-- kernels in flash (adds prefetch/cache effects) --\n" );
	b2_pass( 0 );
	uart0_puts( "-- kernels in flash, cold (fence.i before each run) --\n" );
	for( uint32_t i = 0; i < sizeof( b2_cases ) / sizeof( b2_cases[0] ); i++ )
	{
		kern3_fn fn = (kern3_fn)(uintptr_t)b2_cases[i].kstart;
		fence_i();
		uint32_t d = fn( 0, B2_REPS, 0 );
		uart0_puts( "  " );
		uart0_puts( b2_cases[i].name );
		uart0_puts( ": " );
		print_cyc100( d, B2_N );
		uart0_puts( " cycles/branch (cold)\n" );
	}
	uart0_puts( "bench2 done.\n" );
}
