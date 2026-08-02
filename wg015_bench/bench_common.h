/* bench_common.h — shared pieces of the WG015 P1 calibration bench set
 * (PLAN.md §4 P1, doc/wg015/calibration.md is filled from these printouts).
 *
 * Assembler-clean: bench_kernels.S includes this header too; everything
 * C-only sits behind !__ASSEMBLER__ (same convention as K1921VG015_min.h).
 */

#ifndef _BENCH_COMMON_H
#define _BENCH_COMMON_H

#include "ch32fun.h"   /* wg015 shim -> K1921VG015_min.h (asm-clean) */

/*---------------------------------------------------------------------------
 * Bench pin map (PLAN Р5/Р10: B2 = DBG0 marker, B3/B4 reserved -> B3 is the
 * IRQ wired-back trigger).  Both on GPIOB so one clock enable suffices.
 *---------------------------------------------------------------------------*/
#define BENCH_MARKER_MASK  0x04   /* B2: LA marker, DATAOUTTGL per event/slot */
#define BENCH_TRIG_MASK    0x08   /* B3: output driven AND edge-IRQ input     */

/* bench6 back-to-back IRQ: pairs of entries; stamps array bound (asm+C). */
#define B6_MAX_EVENTS      2048

#ifndef __ASSEMBLER__

#include <stdint.h>

/*---------------------------------------------------------------------------
 * UART0 console, polled (see bench_common.c for the SDK file:line mining).
 *---------------------------------------------------------------------------*/
void uart0_init( void );
void uart0_putc( char c );
void uart0_flush( void );          /* drain TX FIFO (before timing runs) */
int  uart0_getc_block( void );     /* blocking read */
void uart0_puts( const char *s );

void print_dec( uint32_t v );
void print_sdec( int32_t v );
void print_hex8( uint32_t v );     /* 8 hex digits */
/* delta cycles over nops operations, printed as "NN.NN cycles/op" (x100
 * fixed point; FL9: aggregate rdcycle only, never per-op). */
void print_cyc100( uint32_t delta, uint32_t nops );

/*---------------------------------------------------------------------------
 * rdcycle (HCLK-synchronous, F3/Р9).  Aggregate use only — FL9 forbids
 * per-slot/per-op CSR reads (CSR access drains the pipeline).
 *---------------------------------------------------------------------------*/
static inline uint32_t rdcycle32( void )
{
	uint32_t r;
	asm volatile( "rdcycle %0" : "=r"(r) );
	return r;
}

/* fence.i — the only architectural I-cache control on this core (F2).
 * Makefile.wg015's -march=rv32imc_zicsr predates the Zifencei split, so the
 * opcode is enabled locally for just this instruction. */
static inline void fence_i( void )
{
	asm volatile( ".option push\n\t"
	              ".option arch, +zifencei\n\t"
	              "fence.i\n\t"
	              ".option pop" ::: "memory" );
}

static inline void set_mtvec( const void *p )
{
	asm volatile( "csrw mtvec, %0" :: "r"(p) );
}

/*---------------------------------------------------------------------------
 * PRBS9 (x^9 + x^5 + 1, period 511) — path selector for bench5, seeds.
 *---------------------------------------------------------------------------*/
static inline uint32_t prbs9_next( uint32_t s )
{
	uint32_t bit = ( ( s >> 8 ) ^ ( s >> 4 ) ) & 1u;
	return ( ( s << 1 ) | bit ) & 0x1FFu;
}

/*---------------------------------------------------------------------------
 * TCM code arena: benches copy position-independent asm kernels to TCM-A
 * (0x4000_0000, unused by the flash-variant linker script) and execute them
 * there.  Kernels are PIC by construction: internal PC-relative branches
 * only + absolute (%hi/%lo or register-passed) data references.
 *---------------------------------------------------------------------------*/
void *tcm_code_copy( const void *start, const void *end );
void  tcm_code_reset( void );

/*---------------------------------------------------------------------------
 * Distribution helper: histogram of small cycle values, exact min/median/max
 * derived from counts (no per-sample buffers; FL9-clean: values fed in are
 * already aggregate rdcycle deltas).
 *---------------------------------------------------------------------------*/
#define BENCH_HIST_N 512

typedef struct
{
	uint16_t h[BENCH_HIST_N];
	uint32_t n;
	uint32_t min, max;
	uint32_t over;      /* samples >= BENCH_HIST_N (counted, not binned) */
} hist_t;

void     hist_reset( hist_t *hs );
void     hist_add( hist_t *hs, uint32_t v );
uint32_t hist_median( const hist_t *hs );  /* ~BENCH_HIST_N if median in overflow */
void     hist_print_stats( const char *name, const hist_t *hs );
void     hist_print_buckets( const hist_t *hs );

/*---------------------------------------------------------------------------
 * GPIO bench pins + PLIC helpers
 *---------------------------------------------------------------------------*/
void bench_gpio_init( void );             /* GPIOB clock, B2/B3 outputs, B3 high */
void bench_plic_gpio_enable( void );      /* PRI[5]=7, MTHR=0, MIEM0 |= line5   */
void bench_plic_gpio_disable( void );
void bench_irq_all_off( void );           /* INTENCLR + PLIC off + MIE off +
                                           * mtvec back to the startup stub     */

void wg015_trap_entry( void );            /* startup_wg015.S weak park-loop     */

/*---------------------------------------------------------------------------
 * Shared state written by the asm IRQ handlers (bench_kernels.S, absolute
 * %hi/%lo addressing -> must be global, non-static; lives in TCM-B .bss).
 *---------------------------------------------------------------------------*/
extern volatile uint32_t bench_irq_cycle;   /* rdcycle captured in handler   */
extern volatile uint32_t bench_irq_flag;    /* handler-completed semaphore   */
extern volatile uint32_t b6_count;          /* bench6 entry counter          */
extern volatile uint32_t b6_stamps[B6_MAX_EVENTS]; /* bench6 entry rdcycles  */

/*---------------------------------------------------------------------------
 * Asm kernels (bench_kernels.S).  All PIC; *_end brackets for TCM copy.
 * Common measured-kernel ABI: a0 = target address, a1 = outer reps (64
 * accesses each), a2 = store value; returns aggregate rdcycle delta.
 *---------------------------------------------------------------------------*/
typedef uint32_t (*kern3_fn)( volatile void *addr, uint32_t reps, uint32_t val );
typedef uint32_t (*kern0_fn)( void );
typedef uint32_t (*slot_fn)( uint32_t nslots, uint32_t *prbs_io,
                             const uint8_t *buf, uint32_t gpiob_base );

extern const char k_base[],          k_base_end[];         /* 64x addi          */
extern const char k_lw[],            k_lw_end[];           /* 64x lw  0(a0)     */
extern const char k_sw[],            k_sw_end[];           /* 64x sw  a2,0(a0)  */
extern const char k_br_taken_al[],   k_br_taken_al_end[];  /* 64x taken, tgt 4n   */
extern const char k_br_taken_mis[],  k_br_taken_mis_end[]; /* 64x taken, tgt 4n+2 */
extern const char k_br_untaken[],    k_br_untaken_end[];   /* 64x never-taken     */
extern const char k_straight1k[],    k_straight1k_end[];   /* 1024 straight instr */
extern const char k_jump32[],        k_jump32_end[];       /* 64x j over 32 B     */
extern const char slot_packet[],     slot_packet_end[];    /* bench5 cluster      */

extern const char irq3_handler[],    irq3_handler_end[];   /* bench3 full ISR   */
extern const char irq3_stub[],       irq3_stub_end[];      /* bench3 head stub  */
extern const char irq6_handler[];                          /* bench6 ISR (flash)*/

void evictor_4k( void );   /* >4 KB unrelated straight-line flash code (FL1) */

/*---------------------------------------------------------------------------
 * Benches
 *---------------------------------------------------------------------------*/
void bench1_run( void );
void bench2_run( void );
void bench3_run( void );
void bench4_run( void );
void bench5_run( void );
void bench6_run( void );

#endif /* !__ASSEMBLER__ */

#endif /* _BENCH_COMMON_H */
