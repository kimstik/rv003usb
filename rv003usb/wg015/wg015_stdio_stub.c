/* Minimal picolibc stdout so demo printf() links; discards output.
 * TODO(port): replace with UART0 (A0/A1) retarget in the P0 bench bring-up. */
#include <stdio.h>

static int wg015_putc( char c, FILE *f )
{
	(void)c; (void)f;
	return 0;
}

static FILE __stdio = FDEV_SETUP_STREAM( wg015_putc, NULL, NULL, _FDEV_SETUP_WRITE );
FILE *const stdout = &__stdio;
