/* main.c — WG015 P1 calibration bench set: UART0 menu (PLAN §4 P1).
 *
 * Terminal: 115200 8N1 on UART0, RX=A0 / TX=A1 (vendor SDK retarget pins).
 * Every bench prints a self-explaining header: a person with this firmware
 * plus an LA on B2 (DBG0 marker) / B3 (IRQ trigger) fills in
 * doc/wg015/calibration.md without reading the code.
 *
 * All numbers are LAT- and silicon-specific: record the CHIP MARKING (R13)
 * and the printed LAT next to every result.
 */

#include "bench_common.h"

static void banner( void )
{
	uint32_t ctrl = WG015_FLASH->CTRL;
	uart0_puts( "\n==============================================\n"
	            " rv003usb WG015 P1 calibration bench (K1921VG015)\n"
	            " SYSCLK 48.000 MHz (HSE+PLL, startup_wg015.S)\n"
	            " marker DBG0=B2, IRQ trigger=B3, UART0 A0/A1@115200\n" );
	uart0_puts( " FLASH_CTRL=0x" );
	print_hex8( ctrl );
	uart0_puts( " LAT=" );
	print_dec( ( ctrl & FLASH_CTRL_LAT_Msk ) >> FLASH_CTRL_LAT_Pos );
	uart0_puts( " RSTSTAT=0x" );
	print_hex8( RCU->RSTSTAT );
	uart0_puts( "\n record chip marking with every log (R13)!\n"
	            "==============================================\n" );
}

static void menu( void )
{
	uart0_puts( "\nP1 menu:\n"
	            " 1  lw/sw GPIO latency (aggregate, TCM+flash)\n"
	            " 2  branch cost taken/untaken, aligned/misaligned\n"
	            " 3  IRQ entry latency, flash/TCM handler, T5 gate\n"
	            " 4  flash fetch profile, CEN, LAT integrity (R11)\n"
	            " 5  slot emulation + evictor (G1 decisive, FL1-3)\n"
	            " 6  MICC/INTSTATUS cost, back-to-back IRQ (T7)\n"
	            " a  run all (1..6)\n"
	            " ?  this menu\n> " );
}

static void dispatch( int c )
{
	switch( c )
	{
		case '1': bench1_run(); break;
		case '2': bench2_run(); break;
		case '3': bench3_run(); break;
		case '4': bench4_run(); break;
		case '5': bench5_run(); break;
		case '6': bench6_run(); break;
		case 'a':
			bench1_run(); bench2_run(); bench3_run();
			bench4_run(); bench5_run(); bench6_run();
			break;
		default:  menu(); return;
	}
	menu();
}

int main( void )
{
	uart0_init();
	bench_gpio_init();
	banner();
	menu();
	for( ;; )
	{
		int c = uart0_getc_block();
		uart0_putc( (char)c );      /* echo */
		uart0_puts( "\n" );
		dispatch( c );
	}
}
