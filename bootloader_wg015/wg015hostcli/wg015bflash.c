// wg015bflash — host-side flasher for the rv003usb WG015 (K1921VG015)
// USB HID bootloader (bootloader_wg015/).
//
// A real tool, not the busbflash smoke test: device identity is verified
// BEFORE any blob is sent (complete-8: a wrong-chip blob is a brick vector),
// transfers are paced around the multi-ms flash dead windows with retries
// (boot-3), and addresses below APP_BASE are refused host-side (boot-2/R14 —
// the device blobs guard independently).
//
// Usage:
//   wg015bflash info
//   wg015bflash erase  ADDR LEN
//   wg015bflash write  FILE [ADDR]     (erase + program + verify)
//   wg015bflash verify FILE [ADDR]
//   wg015bflash run                    (reset into the app)
// ADDR defaults to APP_BASE (0x80001000).  Options: -u 16|64 program unit (R6).

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <wchar.h>

#if defined(HIDAPI_STUB) || !defined(__has_include) || !__has_include(<hidapi/hidapi.h>)
// Syntax-check stub: hidapi headers absent in this environment.  Build the
// real tool with libhidapi-hidraw-dev (see README.md).
#define WG015_HIDAPI_STUBBED 1
typedef struct hid_device_ hid_device;
struct hid_device_info {
	char *path;
	unsigned short vendor_id, product_id;
	wchar_t *serial_number;
	unsigned short release_number;
	wchar_t *manufacturer_string, *product_string;
	unsigned short usage_page, usage;
	int interface_number;
	struct hid_device_info *next;
};
int hid_init( void );
int hid_exit( void );
struct hid_device_info * hid_enumerate( unsigned short vid, unsigned short pid );
void hid_free_enumeration( struct hid_device_info * );
hid_device * hid_open_path( const char *path );
void hid_close( hid_device * );
int hid_send_feature_report( hid_device *, const unsigned char *, size_t );
int hid_get_feature_report( hid_device *, unsigned char *, size_t );
#else
#include <hidapi/hidapi.h>
#endif

#include "../blobs/blobs.h"

#define VID 0x1209
#define PID 0xb003
// bcdDevice gate: WG015 loaders report 0x0200 (usb_config.h); V003-family
// loaders report 0x0000 and speak a different (WCH) blob ISA.
#define BCD_WG015 0x0200

#define APP_BASE    0x80001000u
#define FLASH_END   0x80100000u
#define PAGE_SIZE   4096u
#define SECRET_ADDR 0x80000FC0u
#define SECRET_KEY  0xFC0u

// Device-side contract (bootloader_wg015/wg015-usb-bootloader.ld + blobs/):
#define SCRATCH_SIZE   1152
#define BLOB_PARAM_OFF 8       // PARAM0..3 at +8..+20
#define BLOB_RESULT_OFF 24     // RESULT0 (rdcycle delta) at +24
#define BLOB_DATA_OFF  0x120   // chunk payload area
#define CHUNK_MAX      768     // divisible by both 16 and 64, <= 832 blob guard
#define STATUS_OK      0xB007C0DEu
#define STATUS_BADADDR 0xBADADD00u
#define STATUS_BADPARAM 0xBADBAD00u
#define MAGIC          0x1234abcdu
#define CYCLES_PER_US  48       // device runs at 48.000 MHz

// Report sizes incl. the report-id byte (HID descriptor: 0xaa=127, 0xab=1151).
#define REPORT_SMALL_LEN 128
#define REPORT_BIG_LEN   1152

static hid_device *hd;
static int opt_unit = 16;       // R6: 16 (РП/SVD) vs 64 (SDK driver) program unit

static uint32_t le32( const uint8_t *p )
{
	return p[0] | (p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void putle32( uint8_t *p, uint32_t v )
{
	p[0]=v; p[1]=v>>8; p[2]=v>>16; p[3]=v>>24;
}

// ---- identity-gated open (complete-8) --------------------------------------

static int open_device( void )
{
	if( hid_init() ) { fprintf( stderr, "hid_init failed\n" ); return -1; }
	struct hid_device_info *list = hid_enumerate( VID, PID ), *d;
	unsigned short seen = 0xffff;
	char *path = 0;
	for( d = list; d; d = d->next )
	{
		seen = d->release_number;
		if( d->release_number == BCD_WG015 ) { path = d->path; break; }
	}
	if( !path )
	{
		if( seen != 0xffff )
			fprintf( stderr, "Found %04x:%04x with bcdDevice %04x, need %04x "
				"(WG015): refusing - wrong-chip blobs can brick the device.\n",
				VID, PID, seen, BCD_WG015 );
		else
			fprintf( stderr, "No %04x:%04x bootloader found.\n", VID, PID );
		hid_free_enumeration( list );
		return -1;
	}
	hd = hid_open_path( path );
	hid_free_enumeration( list );
	if( !hd ) { fprintf( stderr, "hid_open_path failed (udev rule? see README)\n" ); return -1; }
	return 0;
}

// ---- blob transport with pacing (boot-3) -----------------------------------

// Send blob (+optional params/payload) as one feature report.  The device
// arms execution when it sees the MAGIC trailer and starts it right after the
// transfer's status stage - nothing else may be in flight until we poll.
static int send_blob( const unsigned char *blob, size_t bloblen,
	const uint32_t *params, int nparams,
	const uint8_t *payload, size_t payloadlen )
{
	uint8_t buf[REPORT_BIG_LEN];
	size_t len = ( payloadlen == 0 && bloblen <= REPORT_SMALL_LEN - 8 )
		? REPORT_SMALL_LEN : REPORT_BIG_LEN;
	memset( buf, 0, sizeof buf );
	memcpy( buf, blob, bloblen );
	buf[0] = ( len == REPORT_SMALL_LEN ) ? 0xaa : 0xab; // report id doubles as scratchpad[0] low byte
	for( int i = 0; i < nparams; i++ )
		putle32( buf + BLOB_PARAM_OFF + 4*i, params[i] );
	if( payloadlen )
		memcpy( buf + BLOB_DATA_OFF, payload, payloadlen );
	putle32( buf + len - 4, MAGIC );
	int r = hid_send_feature_report( hd, buf, len );
	if( r < 0 ) { fprintf( stderr, "send_feature_report failed\n" ); return -1; }
	return 0;
}

// Poll the scratchpad status word after waiting out the expected dead window.
// Failed control transfers while flash is BUSY (IRQs off device-side) are
// expected and retried.  On success optionally returns RESULT0.
static int poll_status( unsigned wait_us, uint32_t *result0 )
{
	usleep( wait_us );
	for( int try = 0; try < 100; try++ )
	{
		uint8_t buf[REPORT_SMALL_LEN+1];
		buf[0] = 0xaa;
		int r = hid_get_feature_report( hd, buf, sizeof buf );
		if( r >= 1 + BLOB_RESULT_OFF + 4 )
		{
			uint32_t st = le32( buf+1 ); // payload starts after report id
			if( st == STATUS_OK )
			{
				if( result0 ) *result0 = le32( buf+1 + BLOB_RESULT_OFF );
				return 0;
			}
			if( st == STATUS_BADADDR ) { fprintf( stderr, "device: address rejected\n" ); return -2; }
			if( st == STATUS_BADPARAM ) { fprintf( stderr, "device: bad params\n" ); return -3; }
			// else: blob not executed yet (status still 0x000000aa/ab)
		}
		usleep( 5000 );
	}
	fprintf( stderr, "timeout waiting for blob completion\n" );
	return -4;
}

// Adaptive pacing state: start conservative, then trust measured times.
static unsigned erase_wait_us = 250000;
static unsigned prog_wait_us  = 30000;

static int do_rescale( void )
{
	if( send_blob( blob_rescale_timings, sizeof blob_rescale_timings, 0, 0, 0, 0 ) ) return -1;
	return poll_status( 2000, 0 );
}

static int do_erase_page( uint32_t addr )
{
	uint32_t p[1] = { addr }, cycles = 0;
	if( send_blob( blob_erase_page, sizeof blob_erase_page, p, 1, 0, 0 ) ) return -1;
	int r = poll_status( erase_wait_us, &cycles );
	if( !r && cycles )
		erase_wait_us = cycles / CYCLES_PER_US + cycles / CYCLES_PER_US / 4 + 2000; // +25% margin
	return r;
}

static int do_program_chunk( uint32_t addr, const uint8_t *data, size_t len )
{
	uint32_t cycles = 0;
	uint32_t p[3] = { addr, (uint32_t)(len / opt_unit), (uint32_t)opt_unit };
	if( send_blob( blob_program_chunk, sizeof blob_program_chunk, p, 3, data, len ) ) return -1;
	int r = poll_status( prog_wait_us, &cycles );
	if( !r && cycles )
		prog_wait_us = cycles / CYCLES_PER_US + cycles / CYCLES_PER_US / 4 + 2000;
	return r;
}

static int do_read_chunk( uint32_t addr, uint8_t *out, size_t len )
{
	uint32_t p[2] = { addr, (uint32_t)len };
	if( send_blob( blob_read_chunk, sizeof blob_read_chunk, p, 2, 0, 0 ) ) return -1;
	if( poll_status( 2000, 0 ) ) return -1;
	uint8_t buf[REPORT_BIG_LEN+1];
	buf[0] = 0xab;
	int r = hid_get_feature_report( hd, buf, sizeof buf );
	if( r < 1 + BLOB_DATA_OFF + (int)len ) { fprintf( stderr, "readback failed (%d)\n", r ); return -1; }
	memcpy( out, buf + 1 + BLOB_DATA_OFF, len );
	return 0;
}

// ---- commands --------------------------------------------------------------

static int check_range( uint32_t addr, size_t len )
{
	if( addr < APP_BASE )
	{
		fprintf( stderr, "refusing address 0x%08x below APP_BASE 0x%08x "
			"(loader page is not protected by hardware)\n", addr, APP_BASE );
		return -1;
	}
	if( addr + len > FLASH_END || addr + len < addr )
	{
		fprintf( stderr, "range beyond flash end 0x%08x\n", FLASH_END );
		return -1;
	}
	return 0;
}

static int cmd_info( void )
{
	uint8_t w[4];
	if( do_read_chunk( SECRET_ADDR, w, 4 ) ) return -1;
	uint32_t secret = le32( w );
	uint32_t off = secret & 0xffff;
	if( ( ( secret >> 16 ) ^ off ) != SECRET_KEY )
	{
		fprintf( stderr, "SECRET word 0x%08x fails integrity (key 0x%03x)\n", secret, SECRET_KEY );
		return -1;
	}
	printf( "WG015 bootloader OK; boot_usercode @ 0x%08x (SECRET 0x%08x)\n",
		0x80000000u + off, secret );
	return 0;
}

static int cmd_erase( uint32_t addr, size_t len )
{
	if( ( addr & (PAGE_SIZE-1) ) ) { fprintf( stderr, "erase address must be 4K-aligned\n" ); return -1; }
	if( check_range( addr, len ) ) return -1;
	// Page-by-page between report exchanges: keeps each USB dead window
	// bounded to one erase (boot-3).
	for( uint32_t a = addr; a < addr + len; a += PAGE_SIZE )
	{
		if( do_erase_page( a ) ) return -1;
		printf( "erased 0x%08x (next wait %u us)\n", a, erase_wait_us );
	}
	return 0;
}

static uint8_t * load_file( const char *fn, size_t *lenout )
{
	FILE *f = fopen( fn, "rb" );
	if( !f ) { fprintf( stderr, "can't open %s\n", fn ); return 0; }
	fseek( f, 0, SEEK_END );
	long sz = ftell( f );
	fseek( f, 0, SEEK_SET );
	if( sz <= 0 ) { fclose( f ); fprintf( stderr, "empty file\n" ); return 0; }
	size_t padded = ( (size_t)sz + 63 ) & ~(size_t)63; // pad to a 64 B unit boundary
	uint8_t *buf = malloc( padded );
	memset( buf, 0xff, padded );
	if( fread( buf, 1, sz, f ) != (size_t)sz ) { fclose( f ); free( buf ); return 0; }
	fclose( f );
	*lenout = padded;
	return buf;
}

static int cmd_verify_buf( const uint8_t *buf, size_t len, uint32_t addr )
{
	uint8_t rb[CHUNK_MAX];
	for( size_t o = 0; o < len; o += CHUNK_MAX )
	{
		size_t l = len - o > CHUNK_MAX ? CHUNK_MAX : len - o;
		if( do_read_chunk( addr + o, rb, l ) ) return -1;
		if( memcmp( rb, buf + o, l ) )
		{
			for( size_t i = 0; i < l; i++ )
				if( rb[i] != buf[o+i] )
				{
					fprintf( stderr, "verify FAILED @0x%08zx: want %02x got %02x\n",
						addr + o + i, buf[o+i], rb[i] );
					return -1;
				}
		}
	}
	return 0;
}

static int cmd_write( const char *fn, uint32_t addr )
{
	size_t len;
	uint8_t *buf = load_file( fn, &len );
	if( !buf ) return -1;
	if( ( addr & (PAGE_SIZE-1) ) ) { fprintf( stderr, "write address must be 4K-aligned (page erase)\n" ); free( buf ); return -1; }
	if( check_range( addr, len ) ) { free( buf ); return -1; }
	if( do_rescale() ) { free( buf ); return -1; }
	size_t elen = ( len + PAGE_SIZE - 1 ) & ~(size_t)(PAGE_SIZE-1);
	if( cmd_erase( addr, elen ) ) { free( buf ); return -1; }
	for( size_t o = 0; o < len; o += CHUNK_MAX )
	{
		size_t l = len - o > CHUNK_MAX ? CHUNK_MAX : len - o;
		if( do_program_chunk( addr + o, buf + o, l ) ) { free( buf ); return -1; }
		printf( "\rprogrammed %zu/%zu B", o + l, len ); fflush( stdout );
	}
	printf( "\n" );
	int r = cmd_verify_buf( buf, len, addr );
	free( buf );
	if( !r ) printf( "write+verify OK (%zu B @0x%08x)\n", len, addr );
	return r;
}

static int cmd_verify( const char *fn, uint32_t addr )
{
	size_t len;
	uint8_t *buf = load_file( fn, &len );
	if( !buf ) return -1;
	int r = cmd_verify_buf( buf, len, addr );
	free( buf );
	if( !r ) printf( "verify OK (%zu B @0x%08x)\n", len, addr );
	return r;
}

static int cmd_run( void )
{
	// Device writes WG015_BOOT_FLAG_APP + RSTSYS and drops off the bus; a
	// successful send is all the confirmation we get (see blob_boot_app.S).
	if( send_blob( blob_boot_app, sizeof blob_boot_app, 0, 0, 0, 0 ) ) return -1;
	printf( "boot-app blob sent; device resets into the app\n" );
	return 0;
}

static void usage( void )
{
	fprintf( stderr,
		"wg015bflash [-u 16|64] COMMAND\n"
		"  info                device identity + SECRET check\n"
		"  erase  ADDR LEN     page-erase a region (hex ok)\n"
		"  write  FILE [ADDR]  erase+program+verify (default 0x%08x)\n"
		"  verify FILE [ADDR]\n"
		"  run                 reset into the app\n", APP_BASE );
}

int main( int argc, char **argv )
{
#ifdef WG015_HIDAPI_STUBBED
	fprintf( stderr, "built without hidapi - install libhidapi-hidraw-dev and rebuild\n" );
	return 1;
#else
	int ai = 1;
	if( ai < argc && !strcmp( argv[ai], "-u" ) )
	{
		if( ai+1 >= argc ) { usage(); return 1; }
		opt_unit = atoi( argv[ai+1] );
		if( opt_unit != 16 && opt_unit != 64 ) { fprintf( stderr, "-u must be 16 or 64\n" ); return 1; }
		ai += 2;
	}
	if( ai >= argc ) { usage(); return 1; }
	const char *cmd = argv[ai++];

	if( open_device() ) return 2;

	int r = -1;
	if( !strcmp( cmd, "info" ) )
		r = cmd_info();
	else if( !strcmp( cmd, "erase" ) && ai+1 < argc )
		r = cmd_erase( (uint32_t)strtoul( argv[ai], 0, 0 ), (size_t)strtoul( argv[ai+1], 0, 0 ) );
	else if( !strcmp( cmd, "write" ) && ai < argc )
		r = cmd_write( argv[ai], ai+1 < argc ? (uint32_t)strtoul( argv[ai+1], 0, 0 ) : APP_BASE );
	else if( !strcmp( cmd, "verify" ) && ai < argc )
		r = cmd_verify( argv[ai], ai+1 < argc ? (uint32_t)strtoul( argv[ai+1], 0, 0 ) : APP_BASE );
	else if( !strcmp( cmd, "run" ) )
		r = cmd_run();
	else
		usage();

	hid_close( hd );
	hid_exit();
	return r ? 1 : 0;
#endif
}
