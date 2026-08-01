// bootloader_wg015_dfu — USB DFU bootloader for NIIET K1921VG015 (WG015).
//
// DFU (USB Device Firmware Upgrade 1.1) port of the samdx1-usb-dfu-bootloader
// concept onto the rv003usb bitbang stack: works with stock dfu-util, no
// custom host tool needed for flashing (tools/wg015mkdfu.py only prepares
// the .dfu image: length word @+0x10, appended CRC32, DFU suffix).
//
// Integration with rv003usb.c (no RV003USB_BOOTLOADER hooks — plain app):
//   * enumeration: standard descriptor_list path (usb_config.h)
//   * DFU class requests: usb_handle_other_control_message
//     (RV003USB_OTHER_CONTROL=1, rv003usb.c:498-503)
//   * DNLOAD payload: generic control-out capture (ist->setup_request=2,
//     RV003USB_SUPPORT_CONTROL_OUT=1, rv003usb.c:369-388) into
//     dnload_buf = [rxlen][64 data bytes]; rxlen==wLength marks completion.
//
// Flash op deferral (the samd11 "status trick", adapted): DFU_GETSTATUS in
// dfuDNLOAD-SYNC answers a dfuDNBUSY status with bwPollTimeout (50 ms when
// the block starts a 4K page => erase+program, 8 ms otherwise) and ARMS the
// op; the main loop waits a quiet-bus window, masks IRQs and runs the
// RAM-resident (TCM-B) flash routine — no flash fetch happens while the
// controller is BUSY (research_flash.md §4: reads during op return garbage).
//
// Entry policy (same RTC_REG[0] one-shot contract as bootloader_wg015):
//   WG015_BOOT_FLAG_APP  -> jump to app immediately (near-reset state)
//   WG015_BOOT_FLAG_STAY -> stay in DFU
//   no flag              -> app CRC32 check (length word @APP_BASE+0x10,
//                           trailing CRC32) — valid: boot app; else stay.

#include "ch32fun.h"
#include <stdint.h>
#include "rv003usb.h"

// App slot: flash page 1 onward — same as the HID loader (interchangeable).
#define APP_BASE       0x80001000u
#define FLASH_END      0x80100000u
#define CYCLES_PER_MS  48000u // startup_wg015.S guarantees 48.000 MHz

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

// GETSTATUS answer, lives in TCM-B (.bss/.data — clocked TX path reads RAM):
// [bStatus][bwPollTimeout x3][bState][iString] (+2 pad for alignment)
static uint8_t dfu_status[8] __attribute__((aligned(4))) =
	{ DFU_STATUS_OK, 0, 0, 0, DFU_STATE_dfuIDLE, 0, 0, 0 };

// Control-OUT capture buffer (rv003usb.c:369-388 contract):
// word 0 = received length (stack writes wLength there when complete),
// then payload; the capture path writes full 8-byte packets, so keep
// 8 bytes of slack past the 64-byte block for a partial final packet.
static uint32_t dnload_buf[1 + 16 + 2] __attribute__((aligned(4)));

// Deferred-op state, ISR (usb_handle_other_control_message) -> main loop.
static volatile uint32_t dfu_addr;        // pending block flash address
static volatile uint32_t pending_len;     // pending block byte count (1..64)
static volatile uint8_t  do_flash_op;     // armed by GETSTATUS(DNBUSY)
static volatile uint8_t  do_manifest;     // armed by DNLOAD with wLength==0
static volatile uint32_t arm_cycles;      // rdcycle at arming

static int app_is_present( void )
{
	uint32_t first = *(const uint32_t *)APP_BASE;
	return first != 0xFFFFFFFFu && first != 0;
}

// Standard reflected CRC32 (poly 0xEDB88320), bitwise — no table (flash
// budget over speed; ~160 cycles/byte at 48 MHz).
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

// samd11 convention: word at APP_BASE+0x10 = total image length INCLUDING
// the appended 4-byte CRC32; CRC32 covers [APP_BASE, APP_BASE+len-4).
static int app_crc_ok( void )
{
	uint32_t total = *(const uint32_t *)( APP_BASE + 0x10 );
	if( total < 0x18 || total > ( FLASH_END - APP_BASE ) || ( total & 3 ) )
		return 0;
	return crc32_range( (const uint8_t *)APP_BASE, total - 4 ) ==
	       *(const uint32_t *)( APP_BASE + total - 4 );
}

static void __attribute__((noreturn)) jump_to_app( void )
{
	extern char __stack_top[];
	// Handoff contract (as bootloader_wg015): 48.000 MHz PLL + flash LAT=1
	// stay configured; fresh sp at TCM-B top; mtvec parked on the app entry.
	asm volatile(
		"csrw mtvec, %0\n"
		"mv sp, %1\n"
		"jr %0\n"
		: : "r"(APP_BASE), "r"(__stack_top) );
	__builtin_unreachable();
}

// ---- RAM-resident flash routine ---------------------------------------
// section(".data.ramfunc") rides to TCM-B with the ordinary startup .data
// copy (wg015_common.ld folds .data.* into .data > TCMB AT> FLASH).
// Self-contained: no calls, no rodata — verified in the disassembly.  The
// caller masks mstatus.MIE around the call; NOTHING may fetch from flash
// while STAT.BUSY=1 (research_flash.md §4).  Sequence per РП А.4 + SDK:
// ADDR -> DATA0..3 -> CMD(KEY|op) -> >=5 NOP -> poll BUSY.  A 64-byte block
// is programmed as 4 x 16-byte units (РП program unit = 128 bits).
static void __attribute__((section(".data.ramfunc"), noinline))
flash_write_block( uint32_t addr, const uint32_t * src )
{
	WG015_FLASH_TypeDef * const fl = WG015_FLASH;
	if( ( addr & ( WG015_FLASH_PAGE_SIZE - 1 ) ) == 0 )
	{
		// Block starts a 4K page: erase it first.
		while( fl->STAT & FLASH_STAT_BUSY );
		fl->ADDR = addr;
		fl->CMD = FLASH_CMD_KEY | FLASH_CMD_ERSEC; // never ALLSEC/NVRON
		asm volatile( "nop\nnop\nnop\nnop\nnop" );
		while( fl->STAT & FLASH_STAT_BUSY );
	}
	for( int unit = 0; unit < 4; unit++ )
	{
		fl->ADDR = addr;
		fl->DATA[0] = src[0];
		fl->DATA[1] = src[1];
		fl->DATA[2] = src[2];
		fl->DATA[3] = src[3];
		fl->CMD = FLASH_CMD_KEY | FLASH_CMD_WR;
		asm volatile( "nop\nnop\nnop\nnop\nnop" );
		while( fl->STAT & FLASH_STAT_BUSY );
		addr += 16;
		src  += 4;
	}
}

int main( void )
{
	// ---- Entry decision (RTC_REG[0] one-shot, honored only after SYSRST) --
	uint32_t flag = WG015_RTC_REG(0);
	if( flag ) WG015_RTC_REG(0) = 0;
	if( !( RCU->RSTSTAT & RCU_RSTSTAT_SYSRST ) )
		flag = 0; // stale flag after POR: ignore

	if( flag == WG015_BOOT_FLAG_APP && app_is_present() )
		jump_to_app(); // fast-path: near-reset state (post-manifest reboot)

	if( flag != WG015_BOOT_FLAG_STAY && app_crc_ok() )
		jump_to_app(); // normal boot: app checks out

	// ---- Flash controller timebase -> 48 MHz, once per loader run --------
	// Reset defaults assume ~100 MHz clk (research_flash.md §1); registers
	// are write-locked while BUSY (never busy this early, check is free).
	if( !( WG015_FLASH->STAT & FLASH_STAT_BUSY ) )
	{
		WG015_FLASH->TACCR  = 1;       // ceil(48 MHz * 20 ns)
		WG015_FLASH->TNVSR  = 240000;  // 5 ms
		WG015_FLASH->TERSR  = 4800000; // 100 ms (erase timebase)
		WG015_FLASH->TNVHR  = 240;     // 5 us
		WG015_FLASH->TNVH1R = 4800;    // 100 us
		WG015_FLASH->TRCVR  = 480;     // 10 us
		WG015_FLASH->TPGSR  = 480;     // 10 us
	}

	usb_setup(); // WG015 seam #2 in rv003usb.c: GPIO, DPU, mtvec, PLIC

	// ---- Main loop --------------------------------------------------------
	while(1)
	{
		// dnload_buf[0] is written from the ISR (control-out capture
		// completion, rv003usb.c:384) — must be re-read every pass.
		if( do_flash_op && *(volatile uint32_t *)&dnload_buf[0] == pending_len )
		{
			// Quiet-bus window: the GETSTATUS(dfuDNBUSY) IN answer goes out
			// within the next LS frame; the host then waits bwPollTimeout
			// (8/50 ms).  3 ms after arming the bus is ours.
			while( WG015_rdcycle() - arm_cycles < 3 * CYCLES_PER_MS );

			// Pad a partial final block to the full 64-byte program unit.
			uint8_t * b = (uint8_t *)&dnload_buf[1];
			for( uint32_t i = pending_len; i < 64; i++ ) b[i] = 0xFF;

			// IRQ-off: our USB ISR is flash-resident; no fetch while BUSY.
			asm volatile( "csrc mstatus, %0" : : "r"(WG015_MSTATUS_MIE) );
			flash_write_block( dfu_addr, &dnload_buf[1] );
			asm volatile( "csrs mstatus, %0" : : "r"(WG015_MSTATUS_MIE) );

			do_flash_op = 0;
			dfu_addr = 0;
			dfu_status[0] = DFU_STATUS_OK;
			dfu_status[1] = 0; // bwPollTimeout back to 0
			dfu_status[4] = DFU_STATE_dfuDNLOAD_IDLE;
		}

		if( do_manifest )
		{
			// Let the DNLOAD status stage + one GETSTATUS round complete.
			while( WG015_rdcycle() - arm_cycles < 25 * CYCLES_PER_MS );
			do_manifest = 0;
			if( app_crc_ok() )
			{
				// Reboot into the app through a full system reset: the
				// DPU pull-up drops, host re-enumerates whatever the app is.
				WG015_RTC_REG(0) = WG015_BOOT_FLAG_APP;
				RCU->RSTSYS = RCU_RSTSYS_MAGIC;
				while(1);
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
			// erase(4K page start)+program: 50 ms; program-only: 8 ms
			dfu_status[1] = ( ( dfu_addr & ( WG015_FLASH_PAGE_SIZE - 1 ) ) == 0 ) ? 50 : 8;
			dfu_status[4] = DFU_STATE_dfuDNBUSY;
			arm_cycles = WG015_rdcycle();
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
			uint32_t addr = APP_BASE + wValue * 64;
			if( wLength > 64 || addr < APP_BASE || addr + 64 > FLASH_END )
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
			arm_cycles = WG015_rdcycle();
			do_manifest = 1;
		}
		break;

	case 0x02A1: // DFU_UPLOAD — direct flash read (legal outside flash ops)
	{
		uint32_t addr = APP_BASE + wValue * 64;
		uint32_t len = ( wLength < 64 ) ? wLength : 64;
		if( addr >= FLASH_END ) len = 0;             // past end: short/ZLP
		else if( addr + len > FLASH_END ) len = FLASH_END - addr;
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
