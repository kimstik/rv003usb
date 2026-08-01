/* bench_common.c — UART0 console, print helpers, TCM code arena, histogram
 * and IRQ plumbing shared by the WG015 P1 calibration benches.
 *
 * KISS: no printf, no framework — a polled PL011-type driver and integer
 * printers are all a calibration console needs.
 */

#include "bench_common.h"

/*===========================================================================
 * UART0 @ A0(RX)/A1(TX), 115200 8N1, polled.
 *
 * Register map mined from the vendor SDK clone (niiet_riscv_sdk), since
 * K1921VG015_min.h intentionally carries no UART block:
 *   platform/Device/K1921VG015/include/K1921VG015.h:12065
 *       #define UART0_BASE (0x30006000UL)
 *   K1921VG015.h:7744-7803 (UART_TypeDef): DR+0x00, RSR+0x04, FR+0x18,
 *       ILPR+0x20, IBRD+0x24, FBRD+0x28, LCRH+0x2C, CR+0x30, IFLS+0x34,
 *       IMSC+0x38, RIS+0x3C, MIS+0x40, ICR+0x44, DMACR+0x48
 *   K1921VG015.h:7392-7394: FR.BUSY=bit3, FR.RXFE=bit4, FR.TXFF=bit5
 *   K1921VG015.h:7459-7460: LCRH.FEN=bit4, LCRH.WLEN=bits6:5
 *   K1921VG015.h:7497-7502: CR.UARTEN=bit0, CR.TXE=bit8, CR.RXE=bit9
 *   K1921VG015.h:328/345:   RCU CGCFGAPB.UART0EN = bit6 (CGCFGAPB @RCU+0x08)
 *   K1921VG015.h:429/446:   RCU RSTDISAPB.UART0EN = bit6 (RSTDISAPB @RCU+0x18)
 *   K1921VG015.h:1060-1061: RCU_TypeDef ... UARTCLKCFG[5] right after
 *       PLLSYSSTAT(+0x60) + Reserved6[3]  ==> UARTCLKCFG[n] @ RCU + 0x70 + 4n
 *   K1921VG015.h:762-781:   UARTCLKCFG.CLKEN=bit0, .RSTDIS=bit8,
 *       .CLKSEL=bits17:16 (0=HSI,1=HSE,2=PLL0,3=PLL1), .DIVEN=bit20
 * Init sequence mirrors platform/Device/K1921VG015/source/retarget.c:27-45;
 * pin mux per include/retarget.h:29-32 (RETARGET_UART_PORT=GPIOA, RX=PIN0,
 * TX=PIN1) and retarget.c:36-38 (ALTFUNCNUM PIN0/PIN1 = 1, ALTFUNCSET).
 * ALTFUNCNUM is 2 bits per pin (K1921VG015.h:5208-5209).
 *===========================================================================*/

#define BENCH_UART0_BASE 0x30006000u

typedef struct
{
	volatile uint32_t DR;      /* +0x00 */
	volatile uint32_t RSR;     /* +0x04 */
	volatile uint32_t _r0[4];  /* +0x08..0x14 */
	volatile uint32_t FR;      /* +0x18 */
	volatile uint32_t _r1;     /* +0x1C */
	volatile uint32_t ILPR;    /* +0x20 */
	volatile uint32_t IBRD;    /* +0x24 */
	volatile uint32_t FBRD;    /* +0x28 */
	volatile uint32_t LCRH;    /* +0x2C */
	volatile uint32_t CR;      /* +0x30 */
	volatile uint32_t IFLS;    /* +0x34 */
	volatile uint32_t IMSC;    /* +0x38 */
	volatile uint32_t RIS;     /* +0x3C */
	volatile uint32_t MIS;     /* +0x40 */
	volatile uint32_t ICR;     /* +0x44 */
	volatile uint32_t DMACR;   /* +0x48 */
} bench_uart_t;

#define UART0X ((bench_uart_t *)BENCH_UART0_BASE)

_Static_assert( sizeof( bench_uart_t ) == 0x4C, "UART layout" );

#define UART_FR_BUSY  ( 1u << 3 )
#define UART_FR_RXFE  ( 1u << 4 )
#define UART_FR_TXFF  ( 1u << 5 )
#define UART_LCRH_FEN ( 1u << 4 )
#define UART_LCRH_WLEN8 ( 3u << 5 )
#define UART_CR_UARTEN ( 1u << 0 )
#define UART_CR_TXE   ( 1u << 8 )
#define UART_CR_RXE   ( 1u << 9 )

#define RCU_CGCFGAPB_UART0EN  ( 1u << 6 )
#define RCU_RSTDISAPB_UART0EN ( 1u << 6 )
/* RCU->UARTCLKCFG[0] @ +0x70 (derivation above) */
#define RCU_UARTCLKCFG0 ( *(volatile uint32_t *)( RCU_BASE + 0x70 ) )
#define RCU_UARTCLKCFG_CLKEN      ( 1u << 0 )
#define RCU_UARTCLKCFG_RSTDIS     ( 1u << 8 )
#define RCU_UARTCLKCFG_CLKSEL_PLL0 ( 2u << 16 )

/* UART kernel clock = PLL0 out = SYSCLK = 48.000 MHz (startup_wg015.S);
 * PL011 16x oversampling: DIV = 48e6/(16*115200) = 26 + 3/64 (26.0469,
 * actual baud 115177, error +0.02%). */
#define BENCH_UARTCLK 48000000u
#define BENCH_BAUD    115200u
#define BENCH_IBRD    ( BENCH_UARTCLK / ( 16u * BENCH_BAUD ) )
#define BENCH_FBRD    ( ( ( BENCH_UARTCLK % ( 16u * BENCH_BAUD ) ) * 64u \
                          + ( 8u * BENCH_BAUD ) ) / ( 16u * BENCH_BAUD ) )

void uart0_init( void )
{
	/* GPIOA + UART0 clocks out of gate and reset (retarget.c:31-34) */
	RCU->CGCFGAHB  |= RCU_CGCFGAHB_GPIOAEN;
	RCU->RSTDISAHB |= RCU_RSTDISAHB_GPIOAEN;
	RCU->CGCFGAPB  |= RCU_CGCFGAPB_UART0EN;
	RCU->RSTDISAPB |= RCU_RSTDISAPB_UART0EN;

	/* A0=RX, A1=TX to AF1 (retarget.c:36-38; 2-bit ALTFUNCNUM fields) */
	GPIOA->ALTFUNCNUM = ( GPIOA->ALTFUNCNUM & ~0xFu ) | 0x5u; /* pins 0,1 = AF1 */
	GPIOA->ALTFUNCSET = 0x3u;

	/* UART0 kernel clock: PLL0 (48 MHz), no divider (retarget.c:39-41) */
	RCU_UARTCLKCFG0 = RCU_UARTCLKCFG_CLKSEL_PLL0 | RCU_UARTCLKCFG_RSTDIS |
	                  RCU_UARTCLKCFG_CLKEN;

	UART0X->IBRD = BENCH_IBRD;
	UART0X->FBRD = BENCH_FBRD;
	UART0X->LCRH = UART_LCRH_FEN | UART_LCRH_WLEN8;      /* 8N1, FIFOs on */
	UART0X->CR   = UART_CR_TXE | UART_CR_RXE | UART_CR_UARTEN;
}

void uart0_putc( char c )
{
	while( UART0X->FR & UART_FR_TXFF )
		;
	UART0X->DR = (uint8_t)c;
}

void uart0_flush( void )
{
	while( UART0X->FR & UART_FR_BUSY )
		;
}

int uart0_getc_block( void )
{
	while( UART0X->FR & UART_FR_RXFE )
		;
	return (int)( UART0X->DR & 0xFF );
}

void uart0_puts( const char *s )
{
	while( *s )
	{
		if( *s == '\n' ) uart0_putc( '\r' );
		uart0_putc( *s++ );
	}
}

/*===========================================================================
 * Integer printers
 *===========================================================================*/
void print_dec( uint32_t v )
{
	char b[11];
	int i = 10;
	b[i] = 0;
	do { b[--i] = (char)( '0' + v % 10u ); v /= 10u; } while( v );
	uart0_puts( &b[i] );
}

void print_sdec( int32_t v )
{
	if( v < 0 ) { uart0_putc( '-' ); print_dec( (uint32_t)-v ); }
	else print_dec( (uint32_t)v );
}

void print_hex8( uint32_t v )
{
	for( int i = 28; i >= 0; i -= 4 )
		uart0_putc( "0123456789ABCDEF"[( v >> i ) & 0xF] );
}

void print_cyc100( uint32_t delta, uint32_t nops )
{
	/* cycles/op x100, 64-bit intermediate so 40M-cycle deltas don't wrap */
	uint32_t x100 = (uint32_t)( ( (uint64_t)delta * 100u ) / nops );
	print_dec( x100 / 100u );
	uart0_putc( '.' );
	uart0_putc( (char)( '0' + ( x100 / 10u ) % 10u ) );
	uart0_putc( (char)( '0' + x100 % 10u ) );
}

/*===========================================================================
 * TCM code arena @ TCM-A base (0x4000_0000): the flash-variant linker
 * script leaves TCM-A entirely unused (wg015_flash.ld), so benches own it.
 *===========================================================================*/
static uint32_t tcm_brk = WG015_TCMA_BASE;

void tcm_code_reset( void )
{
	tcm_brk = WG015_TCMA_BASE;
}

void *tcm_code_copy( const void *start, const void *end )
{
	uint32_t len = (uint32_t)( (uintptr_t)end - (uintptr_t)start );
	tcm_brk = ( tcm_brk + 63u ) & ~63u;   /* 64-align: keeps mod-4 layout */
	uint8_t *dst = (uint8_t *)tcm_brk;
	const uint8_t *src = (const uint8_t *)start;
	for( uint32_t i = 0; i < len; i++ )
		dst[i] = src[i];
	tcm_brk += len;
	fence_i();                            /* new code visible to fetch */
	return dst;
}

/*===========================================================================
 * Histogram / distribution
 *===========================================================================*/
void hist_reset( hist_t *hs )
{
	for( int i = 0; i < BENCH_HIST_N; i++ ) hs->h[i] = 0;
	hs->n = 0;
	hs->min = 0xFFFFFFFFu;
	hs->max = 0;
	hs->over = 0;
}

void hist_add( hist_t *hs, uint32_t v )
{
	hs->n++;
	if( v < hs->min ) hs->min = v;
	if( v > hs->max ) hs->max = v;
	if( v < BENCH_HIST_N ) hs->h[v]++;
	else hs->over++;
}

uint32_t hist_median( const hist_t *hs )
{
	uint32_t half = hs->n / 2u, acc = 0;
	for( uint32_t i = 0; i < BENCH_HIST_N; i++ )
	{
		acc += hs->h[i];
		if( acc > half ) return i;
	}
	return BENCH_HIST_N; /* median lies in the overflow region */
}

void hist_print_stats( const char *name, const hist_t *hs )
{
	uart0_puts( name );
	uart0_puts( ": n=" );    print_dec( hs->n );
	uart0_puts( " min=" );   print_dec( hs->min );
	uart0_puts( " med=" );   print_dec( hist_median( hs ) );
	uart0_puts( " max=" );   print_dec( hs->max );
	if( hs->over )
	{
		uart0_puts( " over>=" );
		print_dec( BENCH_HIST_N );
		uart0_puts( ":" );
		print_dec( hs->over );
	}
	uart0_puts( "\n" );
}

/* Buckets: 0..15 singles, then powers of two: 16-31, 32-63, ... 256-511, >=512 */
void hist_print_buckets( const hist_t *hs )
{
	for( uint32_t i = 0; i < 16; i++ )
	{
		if( !hs->h[i] ) continue;
		uart0_puts( "    " ); print_dec( i );
		uart0_puts( ": " );   print_dec( hs->h[i] );
		uart0_puts( "\n" );
	}
	for( uint32_t lo = 16; lo < BENCH_HIST_N; lo <<= 1 )
	{
		uint32_t hi = lo << 1, cnt = 0;
		if( hi > BENCH_HIST_N ) hi = BENCH_HIST_N;
		for( uint32_t i = lo; i < hi; i++ ) cnt += hs->h[i];
		if( !cnt ) continue;
		uart0_puts( "    " ); print_dec( lo );
		uart0_puts( "-" );    print_dec( hi - 1 );
		uart0_puts( ": " );   print_dec( cnt );
		uart0_puts( "\n" );
	}
	if( hs->over )
	{
		uart0_puts( "    >=" ); print_dec( BENCH_HIST_N );
		uart0_puts( ": " );     print_dec( hs->over );
		uart0_puts( "\n" );
	}
}

/*===========================================================================
 * GPIO bench pins + PLIC
 *===========================================================================*/
void bench_gpio_init( void )
{
	RCU->CGCFGAHB  |= RCU_CGCFGAHB_GPIOBEN;
	RCU->RSTDISAHB |= RCU_RSTDISAHB_GPIOBEN;
	GPIOB->DATAOUTCLR = BENCH_MARKER_MASK;    /* B2 marker low  */
	GPIOB->DATAOUTSET = BENCH_TRIG_MASK;      /* B3 trigger high (idle) */
	GPIOB->OUTENSET   = BENCH_MARKER_MASK | BENCH_TRIG_MASK;
}

void bench_plic_gpio_enable( void )
{
	PLIC_PRI( WG015_IRQ_GPIO ) = 7;   /* Р7: our source prio 7 */
	PLIC_MTHR = 0;
	PLIC_MIEM0 |= 1u << WG015_IRQ_GPIO;
}

void bench_plic_gpio_disable( void )
{
	PLIC_MIEM0 &= ~( 1u << WG015_IRQ_GPIO );
}

void bench_irq_all_off( void )
{
	WG015_DisableGlobalIRQ();
	GPIOB->INTENCLR = 0xFFFFu;
	GPIOB->INTSTATUS = 0xFFFFu;        /* W1C leftovers */
	bench_plic_gpio_disable();
	/* never leave mtvec aimed at a TCM copy the arena may recycle */
	set_mtvec( (const void *)wg015_trap_entry );
}

/*===========================================================================
 * Shared IRQ-handler state (referenced with absolute %hi/%lo from
 * bench_kernels.S — must stay global definitions, TCM-B .bss).
 *===========================================================================*/
volatile uint32_t bench_irq_cycle;
volatile uint32_t bench_irq_flag;
volatile uint32_t b6_count;
volatile uint32_t b6_stamps[B6_MAX_EVENTS];
