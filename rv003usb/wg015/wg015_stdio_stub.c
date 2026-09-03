/* Minimal stdout plumbing so demo printf() links; output is discarded.
 * TODO(port): replace with UART0 (A0/A1) retarget in the P0 bench bring-up.
 * Two libc flavors are supported (see Makefile.wg015 LIBC_SPECS). */
#include <stdio.h>

#ifdef __PICOLIBC__

static int wg015_putc( char c, FILE *f )
{
	(void)c; (void)f;
	return 0;
}

static FILE __stdio = FDEV_SETUP_STREAM( wg015_putc, NULL, NULL, _FDEV_SETUP_WRITE );
FILE *const stdout = &__stdio;

#else /* newlib: printf() bottoms out in _write(); the rest are link stubs */

#include <sys/stat.h>

int _write( int fd, const char * buf, int len ) { (void)fd; (void)buf; return len; }
int _read( int fd, char * buf, int len )        { (void)fd; (void)buf; (void)len; return 0; }
int _close( int fd )                            { (void)fd; return -1; }
int _lseek( int fd, int off, int whence )       { (void)fd; (void)off; (void)whence; return 0; }
int _fstat( int fd, struct stat * st )          { (void)fd; st->st_mode = S_IFCHR; return 0; }
int _isatty( int fd )                           { (void)fd; return 1; }
void _exit( int code )                          { (void)code; for(;;); }
void * _sbrk( int incr )
{
	extern char end[];          /* set by the linker script */
	static char * brk;
	char * prev;
	if( !brk ) brk = end;
	prev = brk;
	brk += incr;
	return prev;
}

#endif
