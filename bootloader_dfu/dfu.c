// dfu.c — portable USB DFU 1.1 bootloader core.
//
// THREE-LAYER SPLIT (each layer replaceable independently):
//   * this file        — DFU protocol: state machine, request semantics,
//                         CRC32 app gate, deferred-flash main loop.
//                         No chip registers, no USB-stack types.
//   * dfu_transport.h  — per-target one-liner including the TRANSPORT port
//                         (dfu_rv003usb.h = bitbang rv003usb stack; a
//                         hardware-USB stack would be a sibling header).
//                         Supplies: dfu_transport_init(), the rx capture
//                         buffer (dfu_rx_data/dfu_rx_ready) and the mapping
//                         from stack callbacks to dfu_class_request().
//   * dfu_chip.h       — per-target one-liner including the CHIP port
//                         (dfu_015.h, dfu_v003.h): geometry, boot flag,
//                         reset/jump, cycle counter, IRQ mask, flash write.
//
// Build: single TU. The target dir's bootloader.c includes this file; this
// file includes dfu_chip.h at the top and dfu_transport.h at the bottom
// (the transport implements the stack-facing entry points and may call
// dfu_class_request(), which is defined above the include).
//
// Image convention (samd11): length word at DFU_APP_BASE+0x10 = total image
// length INCLUDING the appended 4-byte CRC32; CRC32 covers [base, len-4).
// tools/wg015mkdfu.py prepares such images for dfu-util.

#include <stdint.h>
#include "dfu_chip.h"

// Size-vs-feature toggles (a chip port may override in its dfu_chip.h):
#ifndef DFU_ENABLE_UPLOAD
#define DFU_ENABLE_UPLOAD 1   // DFU_UPLOAD readback (verify support)
#endif
#ifndef DFU_ENABLE_APPCRC
#define DFU_ENABLE_APPCRC 1   // CRC32 app gate; 0 = presence check only
#endif

// DFU 1.1 states / status codes (only the ones used).
#define DFU_STATE_dfuIDLE         2
#define DFU_STATE_dfuDNLOAD_SYNC  3
#define DFU_STATE_dfuDNBUSY       4
#define DFU_STATE_dfuDNLOAD_IDLE  5
#define DFU_STATE_dfuMANIFEST     7
#define DFU_STATE_dfuERROR        10
#define DFU_STATUS_OK             0
#define DFU_STATUS_errVERIFY      7
#define DFU_STATUS_errADDRESS     8

// Transport-provided (defined in dfu_transport.h, included at the bottom).
static void      dfu_transport_init( void );
static uint8_t * dfu_rx_data( void );          // captured DNLOAD payload
static int       dfu_rx_ready( uint32_t len ); // capture complete?

// dfu_class_request() -> transport action:
#define DFU_ACT_ACK      0  // no data phase; transport just ACKs/ZLPs
#define DFU_ACT_REPLY    1  // send *reply (replen bytes) on the IN data phase
#define DFU_ACT_CAPTURE  2  // arm OUT capture of wLength bytes into rx buffer

// GETSTATUS answer, lives in RAM (clocked TX paths read RAM):
// [bStatus][bwPollTimeout x3][bState][iString] (+2 pad for alignment).
// Zero-init + runtime state set in main(): the V003 TINY_BOOT startup has
// no .data copy (rv003usb.S:1058), and everything but bState is 0 anyway.
static uint8_t dfu_status[8] __attribute__((aligned(4)));

// Deferred-op state, ISR (class request) -> main loop.
static volatile uint32_t dfu_addr;        // pending block flash address
static volatile uint32_t pending_len;     // pending block byte count
static volatile uint8_t  do_flash_op;     // armed by GETSTATUS(DNBUSY)
static volatile uint8_t  do_manifest;     // armed by DNLOAD with wLength==0
static volatile uint32_t arm_cycles;      // chip cycle counter at arming

static int app_is_present( void )
{
	uint32_t first = *(const uint32_t *)DFU_APP_BASE;
	return first != 0xFFFFFFFFu && first != 0;
}

#if DFU_ENABLE_APPCRC
// Standard reflected CRC32 (poly 0xEDB88320), bitwise — no table (flash
// budget over speed).
static uint32_t crc32_range( const uint8_t * p, uint32_t len )
{
	uint32_t crc = 0xFFFFFFFFu;
	while( len-- )
	{
		crc ^= *p++;
		for( int i = 0; i < 8; i++ )
			crc = ( crc >> 1 ) ^ ( 0xEDB88320u & ~( ( crc & 1 ) - 1 ) );
	}
	return ~crc;
}
#endif

#if DFU_ENABLE_APPCRC
static int app_crc_ok( void )
{
	uint32_t total = *(const uint32_t *)( DFU_APP_BASE + 0x10 );
	if( total < 0x18 || total > ( DFU_FLASH_END - DFU_APP_BASE ) || ( total & 3 ) )
		return 0;
	return crc32_range( (const uint8_t *)DFU_APP_BASE, total - 4 ) ==
	       *(const uint32_t *)( DFU_APP_BASE + total - 4 );
}
#else
#define app_crc_ok() app_is_present() // presence check only (size-trimmed)
#endif

// ---- DFU class request semantics (transport-agnostic) ----------------------
// req = (bRequest<<8)|bmRequestType; returns a DFU_ACT_* action.
static int dfu_class_request( uint32_t req, uint32_t wValue, uint32_t wLength,
                              const uint8_t ** reply, uint32_t * replen )
{
	switch( req )
	{
	case 0x03A1: // DFU_GETSTATUS
		if( dfu_status[4] == DFU_STATE_dfuDNLOAD_SYNC && dfu_addr )
		{
			// samd11 status trick, deferred-op flavor: answer "busy, poll
			// me in N ms" and arm the flash op for the main loop.
			dfu_status[0] = DFU_STATUS_OK;
			dfu_status[1] = ( ( dfu_addr & ( DFU_PAGE_SIZE - 1 ) ) == 0 )
			                ? DFU_POLL_ERASE_MS : DFU_POLL_PROG_MS;
			dfu_status[4] = DFU_STATE_dfuDNBUSY;
			arm_cycles = dfu_port_cycles();
			do_flash_op = 1;
		}
		*reply = dfu_status;
		*replen = ( wLength < 6 ) ? wLength : 6;
		return DFU_ACT_REPLY;

	case 0x05A1: // DFU_GETSTATE
		*reply = &dfu_status[4];
		*replen = ( wLength < 1 ) ? wLength : 1;
		return DFU_ACT_REPLY;

	case 0x0121: // DFU_DNLOAD
		if( wLength > 0 )
		{
			uint32_t addr = DFU_APP_BASE + wValue * DFU_XFER_SIZE;
			if( wLength > DFU_XFER_SIZE || addr < DFU_APP_BASE
			    || addr + DFU_XFER_SIZE > DFU_FLASH_END )
			{
				// ADDRESS GUARD: never the loader, never past flash end.
				dfu_status[0] = DFU_STATUS_errADDRESS;
				dfu_status[4] = DFU_STATE_dfuERROR;
				return DFU_ACT_ACK; // payload gets ACKed and dropped
			}
			do_flash_op = 0; // clear any stale arm from a torn transfer
			dfu_addr = addr;
			pending_len = wLength;
			dfu_status[4] = DFU_STATE_dfuDNLOAD_SYNC;
			return DFU_ACT_CAPTURE; // transport captures wLength bytes
		}
		// wLength==0: manifest.  Main loop verifies CRC, then reboots.
		dfu_status[0] = DFU_STATUS_OK;
		dfu_status[1] = 100; // bwPollTimeout while manifesting
		dfu_status[4] = DFU_STATE_dfuMANIFEST;
		arm_cycles = dfu_port_cycles();
		do_manifest = 1;
		return DFU_ACT_ACK;

#if DFU_ENABLE_UPLOAD
	case 0x02A1: // DFU_UPLOAD — direct flash read (legal outside flash ops)
	{
		uint32_t addr = DFU_APP_BASE + wValue * DFU_XFER_SIZE;
		uint32_t len = ( wLength < DFU_XFER_SIZE ) ? wLength : DFU_XFER_SIZE;
		if( addr >= DFU_FLASH_END ) len = 0;         // past end: short/ZLP
		else if( addr + len > DFU_FLASH_END ) len = DFU_FLASH_END - addr;
		*reply = (const uint8_t *)addr;
		*replen = len;
		return DFU_ACT_REPLY;
	}
#endif

	case 0x0421: // DFU_CLRSTATUS
	case 0x0621: // DFU_ABORT
		dfu_status[0] = DFU_STATUS_OK;
		dfu_status[1] = 0;
		dfu_status[4] = DFU_STATE_dfuIDLE;
		dfu_addr = 0;
		do_flash_op = 0;
		do_manifest = 0;
		return DFU_ACT_ACK;

	default: // 0x0021 DFU_DETACH & anything else: plain ACK
		return DFU_ACT_ACK;
	}
}

int main( void )
{
	// ---- Entry decision (one-shot flag, qualified by the chip port) ------
	uint32_t flag = dfu_port_flag_read_and_clear();

	if( flag == DFU_FLAG_APP && app_is_present() )
		dfu_port_jump_app(); // fast-path: near-reset state (post-manifest)

	if( flag != DFU_FLAG_STAY && app_crc_ok() )
		dfu_port_jump_app(); // normal boot: app checks out

	dfu_status[4] = DFU_STATE_dfuIDLE; // rest of the status buf is 0 = OK

	dfu_port_flash_timebase_init();

	dfu_transport_init();

	// ---- Main loop --------------------------------------------------------
	while(1)
	{
		if( do_flash_op && dfu_rx_ready( pending_len ) )
		{
			// Quiet-bus window: the GETSTATUS(dfuDNBUSY) answer goes out
			// promptly; the host then waits bwPollTimeout.
			while( dfu_port_cycles() - arm_cycles < 3 * DFU_CYCLES_PER_MS );

			// Pad a partial final block to the full program block.
			uint8_t * b = dfu_rx_data();
			for( uint32_t i = pending_len; i < DFU_XFER_SIZE; i++ ) b[i] = 0xFF;

			// IRQ-off: the USB ISR may be flash-resident; no fetch while
			// the flash controller is busy (chip port's problem to respect).
			dfu_port_irq_disable();
			dfu_port_flash_write_block( dfu_addr, (const uint32_t *)b );
			dfu_port_irq_enable();

			do_flash_op = 0;
			dfu_addr = 0;
			dfu_status[0] = DFU_STATUS_OK;
			dfu_status[1] = 0; // bwPollTimeout back to 0
			dfu_status[4] = DFU_STATE_dfuDNLOAD_IDLE;
		}

		if( do_manifest )
		{
			// Let the DNLOAD status stage + one GETSTATUS round complete.
			while( dfu_port_cycles() - arm_cycles < 25 * DFU_CYCLES_PER_MS );
			do_manifest = 0;
			if( app_crc_ok() )
				dfu_port_reboot_to_app(); // full reset; host re-enumerates
			dfu_status[0] = DFU_STATUS_errVERIFY;
			dfu_status[4] = DFU_STATE_dfuERROR;
		}
	}
}

// Transport layer last: implements the stack-facing entry points using
// dfu_class_request() and the static protos declared above.
#include "dfu_transport.h"
