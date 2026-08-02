/* bench1_gpio.c — P1.1: lw/sw GPIO latency (PLAN §4 P1.1, risk R3).
 *
 * Method: aggregate rdcycle around N=1024 unrolled accesses (16 reps x 64
 * ops; FL9: never a per-access CSR read).  Kernels are copied to TCM-A and
 * run there first — that is THE number for the port (data-path cost with
 * 0-ws instruction fetch); the same kernels are then run in place from
 * flash so fetch interference is visible as the difference.
 *
 * Printed value = cycles/op x100 including 3.1% loop overhead (2 loop
 * instructions per 64 ops) — the `baseline addi` row measures exactly that
 * overhead + 1-cycle IPC, so: true_op_cost ~= row - (baseline - 1.03).
 * R3 watch: lw GPIO > 8 cycles forces a sample-phase rebuild (PLAN §5).
 */

#include "bench_common.h"

#define B1_REPS 16u              /* x64 = 1024 ops */
#define B1_N    ( B1_REPS * 64u )

static uint32_t b1_flash_data[4] = { 0x12345678, 0, 0, 0 }; /* .data (TCM-B) */
static const uint32_t b1_flash_ro[4] = { 0xA5A5A5A5, 1, 2, 3 }; /* .rodata (flash) */
static volatile uint32_t b1_tcm_var;

typedef struct
{
	const char *name;
	const char *kstart, *kend;   /* kernel bracket */
	volatile void *addr;
	uint32_t val;
} b1_case_t;

static void b1_run_case( const b1_case_t *c, int from_tcm )
{
	kern3_fn fn;
	if( from_tcm )
	{
		tcm_code_reset();
		fn = (kern3_fn)tcm_code_copy( c->kstart, c->kend );
	}
	else
		fn = (kern3_fn)(uintptr_t)c->kstart;

	fn( c->addr, 2, c->val );                     /* warm-up, discarded */
	uint32_t d = fn( c->addr, B1_REPS, c->val );  /* measured           */
	uart0_puts( "  " );
	uart0_puts( c->name );
	uart0_puts( ": " );
	print_cyc100( d, B1_N );
	uart0_puts( " cycles/op (x1024, total " );
	print_dec( d );
	uart0_puts( ")\n" );
}

void bench1_run( void )
{
	/* MASKLB window for the marker bit: masked ABSOLUTE write of B2 only
	 * (PLAN Р2: the BSHR-equivalent used by USB_TX_*). */
	volatile void *masklb_b2 =
		(volatile void *)( GPIOB_BASE + GPIO_MASKLB_OFFSET +
		                   ( BENCH_MARKER_MASK << 2 ) );

	const b1_case_t cases[] = {
		{ "baseline addi          ", k_base, k_base_end, &b1_tcm_var, 0 },
		{ "lw  GPIOB->DATA        ", k_lw, k_lw_end, &GPIOB->DATA, 0 },
		{ "sw  GPIOB->DATAOUTTGL 0", k_sw, k_sw_end, &GPIOB->DATAOUTTGL, 0 },
		{ "sw  DATAOUTTGL B2 mark ", k_sw, k_sw_end, &GPIOB->DATAOUTTGL,
		  BENCH_MARKER_MASK },
		{ "sw  MASKLB[B2] window  ", k_sw, k_sw_end, 0 /* set below */, 0 },
		{ "sw  GPIOB->DATAOUT     ", k_sw, k_sw_end, &GPIOB->DATAOUT, 0 },
		{ "lw  TCM-B variable     ", k_lw, k_lw_end, &b1_tcm_var, 0 },
		{ "sw  TCM-B variable     ", k_sw, k_sw_end, &b1_tcm_var, 5 },
		{ "lw  flash .rodata      ", k_lw, k_lw_end,
		  (volatile void *)b1_flash_ro, 0 },
		{ "lw  .data (TCM-B)      ", k_lw, k_lw_end, b1_flash_data, 0 },
	};
	const uint32_t ncases = sizeof( cases ) / sizeof( cases[0] );

	uart0_puts(
		"\n=== BENCH 1: lw/sw GPIO latency (P1.1) ===\n"
		"N=1024 unrolled ops, ONE rdcycle pair per run (FL9), cycles/op x100\n"
		"incl. +3.1% loop overhead (see `baseline addi` row = overhead+1.0).\n"
		"DATAOUTTGL with value 0 is an architectural no-op store (Р10);\n"
		"the B2-mask row toggles the DBG0 marker for LA cross-check.\n"
		"GATE R3: lw GPIO > 8 cycles => sample-phase rebuild required.\n" );

	uart0_puts( "-- kernels in TCM-A (clean fetch; the port numbers) --\n" );
	for( uint32_t i = 0; i < ncases; i++ )
	{
		b1_case_t c = cases[i];
		if( !c.addr ) c.addr = masklb_b2;
		b1_run_case( &c, 1 );
	}

	uart0_puts( "-- same kernels executing from flash (fetch adds) --\n" );
	for( uint32_t i = 0; i < ncases; i++ )
	{
		b1_case_t c = cases[i];
		if( !c.addr ) c.addr = masklb_b2;
		b1_run_case( &c, 0 );
	}

	/* Leave the marker latch low again (TGL runs flipped it 1024x = even,
	 * MASKLB/DATAOUT writes forced 0) — re-park deterministically. */
	GPIOB->DATAOUTCLR = BENCH_MARKER_MASK;
	GPIOB->DATAOUTSET = BENCH_TRIG_MASK;
	uart0_puts( "bench1 done.\n" );
}
