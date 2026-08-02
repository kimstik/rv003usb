// dfu_core.c — portable USB DFU 1.1 bootloader core for the rv003usb stack.
//
// DFU port of the samdx1-usb-dfu-bootloader concept: works with stock
// dfu-util, no custom host flashing tool (tools/wg015mkdfu.py only prepares
// the image: length word @+0x10, appended CRC32, DFU suffix).
//
// CHIP-INDEPENDENT: everything hardware lives behind dfu_port.h, supplied by
// the target directory (see bootloader_wg015_dfu/dfu_port.h for the contract:
// DFU_APP_BASE/DFU_FLASH_END/DFU_PAGE_SIZE/DFU_XFER_SIZE, DFU_FLAG_APP/STAY,
// DFU_POLL_ERASE_MS/PROG_MS, DFU_CYCLES_PER_MS, dfu_port_cycles,
// dfu_port_irq_disable/enable, dfu_port_flash_timebase_init,
// dfu_port_flash_write_block (RAM-resident, erases on page start),
// dfu_port_flag_read_and_clear (one-shot + reset-cause qualification),
// dfu_port_reboot_to_app, dfu_port_jump_app).
//
// Integration with rv003usb.c (no RV003USB_BOOTLOADER hooks — plain app):
//   * enumeration: standard descriptor_list path (usb_config.h)
//   * DFU class requests: usb_handle_other_control_message
//     (RV003USB_OTHER_CONTROL=1, rv003usb.c:498-503)
//   * DNLOAD payload: generic control-out capture (ist->setup_request=2,
//     RV003USB_SUPPORT_CONTROL_OUT=1, rv003usb.c:369-388) into
//     dnload_buf = [rxlen][data]; rxlen==wLength marks completion.
//
// Flash op deferral (the samd11 "status trick", adapted for a flash-resident
// USB stack): DFU_GETSTATUS in dfuDNLOAD-SYNC answers dfuDNBUSY with
// bwPollTimeout and ARMS the op; the main loop waits a quiet-bus window,
// masks IRQs and runs the RAM-resident flash routine — nothing may fetch
// from flash while it is busy.
//
// Entry policy (one-shot boot flag, same contract as the blob loader):
//   DFU_FLAG_APP  -> jump to app immediately (near-reset state)
//   DFU_FLAG_STAY -> stay in DFU
//   no flag       -> app CRC32 check (length word @DFU_APP_BASE+0x10,
//                    trailing CRC32) — valid: boot app; else stay.

#include "ch32fun.h"
#include <stdint.h>
#include "rv003usb.h"
#include "dfu_port.h"

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

// GETSTATUS answer, lives in RAM (clocked TX path reads RAM):
// [bStatus][bwPollTimeout x3][bState][iString] (+2 pad for alignment)
static uint8_t dfu_status[8] __attribute__((aligned(4))) =
	{ DFU_STATUS_OK, 0, 0, 0, DFU_STATE_dfuIDLE, 0, 0, 0 };

// Control-OUT capture buffer (rv003usb.c:369-388 contract):
// word 0 = received length (stack writes wLength there when complete),
// then payload; the capture path writes full 8-byte packets, so keep
// 8 bytes of slack past the block for a partial final packet.
static uint32_t dnload_buf[1 + DFU_XFER_SIZE/4 + 2] __attribute__((aligned(4)));

// Deferred-op state, ISR (usb_handle_other_control_message) -> main loop.
static volatile uint32_t dfu_addr;        // pending block flash address
static volatile uint32_t pending_len;     // pending block byte count
static volatile uint8_t  do_flash_op;     // armed by GETSTATUS(DNBUSY)
static volatile uint8_t  do_manifest;     // armed by DNLOAD with wLength==0
static volatile uint32_t arm_cycles;      // port cycle counter at arming

static int app_is_present( void )
{
	uint32_t first = *(const uint32_t *)DFU_APP_BASE;
	return first != 0xFFFFFFFFu && first != 0;
}

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

// samd11 convention: word at DFU_APP_BASE+0x10 = total image length INCLUDING
// the appended 4-byte CRC32; CRC32 covers [APP_BASE, APP_BASE+len-4).
static int app_crc_ok( void )
{
	uint32_t total = *(const uint32_t *)( DFU_APP_BASE + 0x10 );
	if( total < 0x18 || total > ( DFU_FLASH_END - DFU_APP_BASE ) || ( total & 3 ) )
		return 0;
	return crc32_range( (const uint8_t *)DFU_APP_BASE, total - 4 ) ==
	       *(const uint32_t *)( DFU_APP_BASE + total - 4 );
}

int main( void )
{
	// ---- Entry decision (one-shot flag, qualified by the port) -----------
	uint32_t flag = dfu_port_flag_read_and_clear();

	if( flag == DFU_FLAG_APP && app_is_present() )
		dfu_port_jump_app(); // fast-path: near-reset state (post-manifest)

	if( flag != DFU_FLAG_STAY && app_crc_ok() )
		dfu_port_jump_app(); // normal boot: app checks out

	dfu_port_flash_timebase_init();

	usb_setup(); // per-target seam in rv003usb.c: GPIO, DPU, vector, IRQ

	// ---- Main loop --------------------------------------------------------
	while(1)
	{
		// dnload_buf[0] is written from the ISR (control-out capture
		// completion, rv003usb.c:384) — must be re-read every pass.
		if( do_flash_op && *(volatile uint32_t *)&dnload_buf[0] == pending_len )
		{
			// Quiet-bus window: the GETSTATUS(dfuDNBUSY) IN answer goes out
			// within the next LS frame; the host then waits bwPollTimeout.
			while( dfu_port_cycles() - arm_cycles < 3 * DFU_CYCLES_PER_MS );

			// Pad a partial final block to the full program block.
			uint8_t * b = (uint8_t *)&dnload_buf[1];
			for( uint32_t i = pending_len; i < DFU_XFER_SIZE; i++ ) b[i] = 0xFF;

			// IRQ-off: the USB ISR is flash-resident; no fetch while busy.
			dfu_port_irq_disable();
			dfu_port_flash_write_block( dfu_addr, &dnload_buf[1] );
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
			{
				// Reboot into the app through a full system reset: the
				// pull-up drops, host re-enumerates whatever the app is.
				dfu_port_reboot_to_app();
			}
			dfu_status[0] = DFU_STATUS_errVERIFY;
			dfu_status[4] = DFU_STATE_dfuERROR;
		}
	}
}

// ---- DFU class request dispatch (rv003usb.c:498-503) -----------------------
// LSB = bmRequestType, MSB = bRequest.  e-> was zeroed by the dispatcher;
// leaving it zeroed makes the stack answer with a ZLP/empty (= ACK).
void usb_handle_other_control_message( struct usb_endpoint * e, struct usb_urb * s, struct rv003usb_internal * ist )
{
	uint32_t req     = s->wRequestTypeLSBRequestMSB;
	uint32_t wValue  = s->lValueLSBIndexMSB & 0xffff;
	uint32_t wLength = s->wLength;

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
		e->opaque = dfu_status;
		e->max_len = ( wLength < 6 ) ? wLength : 6;
		break;

	case 0x05A1: // DFU_GETSTATE
		e->opaque = &dfu_status[4];
		e->max_len = ( wLength < 1 ) ? wLength : 1;
		break;

	case 0x0121: // DFU_DNLOAD
		if( wLength > 0 )
		{
			uint32_t addr = DFU_APP_BASE + wValue * DFU_XFER_SIZE;
			if( wLength > DFU_XFER_SIZE || addr < DFU_APP_BASE
			    || addr + DFU_XFER_SIZE > DFU_FLASH_END )
			{
				// ADDRESS GUARD: never the loader page, never past flash.
				dfu_status[0] = DFU_STATUS_errADDRESS;
				dfu_status[4] = DFU_STATE_dfuERROR;
				break; // no capture armed: payload is ACKed and dropped
			}
			// Arm generic control-out capture (rv003usb.c:369-388).
			do_flash_op = 0; // clear any stale arm from a torn transfer
			dnload_buf[0] = 0;
			dfu_addr = addr;
			pending_len = wLength;
			e->opaque = (uint8_t *)dnload_buf;
			e->max_len = wLength;
			e->count = 0;
			ist->setup_request = 2;
			dfu_status[4] = DFU_STATE_dfuDNLOAD_SYNC;
		}
		else
		{
			// wLength==0: manifest.  Main loop verifies CRC, then reboots.
			dfu_status[0] = DFU_STATUS_OK;
			dfu_status[1] = 100; // bwPollTimeout while manifesting
			dfu_status[4] = DFU_STATE_dfuMANIFEST;
			arm_cycles = dfu_port_cycles();
			do_manifest = 1;
		}
		break;

	case 0x02A1: // DFU_UPLOAD — direct flash read (legal outside flash ops)
	{
		uint32_t addr = DFU_APP_BASE + wValue * DFU_XFER_SIZE;
		uint32_t len = ( wLength < DFU_XFER_SIZE ) ? wLength : DFU_XFER_SIZE;
		if( addr >= DFU_FLASH_END ) len = 0;         // past end: short/ZLP
		else if( addr + len > DFU_FLASH_END ) len = DFU_FLASH_END - addr;
		e->opaque = (uint8_t *)addr;
		e->max_len = len;
		break;
	}

	case 0x0421: // DFU_CLRSTATUS
	case 0x0621: // DFU_ABORT
		dfu_status[0] = DFU_STATUS_OK;
		dfu_status[1] = 0;
		dfu_status[4] = DFU_STATE_dfuIDLE;
		dfu_addr = 0;
		do_flash_op = 0;
		do_manifest = 0;
		break;

	default: // 0x0021 DFU_DETACH & anything else: plain ACK
		break;
	}
}
