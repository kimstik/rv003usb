// bootloader_wg015 — USB HID bootloader for NIIET K1921VG015 (WG015).
//
// Ported from bootloader/bootloader.c (CH32V003) per PLAN.md Р8.  Unlike the
// V003 original, this links the shared rv003usb.c protocol layer with its
// RV003USB_BOOTLOADER hooks (the bootloader_v006 pattern) instead of carrying
// private usb_pid_handle_* copies — the WG015 C seams live in rv003usb.c.
//
// Wire protocol: byte-compatible with the V003 loader.  Host sends HID
// feature reports into the scratchpad; blob code at scratchpad+4 runs after
// the magic trailer 0x1234abcd is seen AND one more IN arrives; readback via
// get-feature-report returns the scratchpad (blob leaves status in word 0).
//
// Fixed TCM contract (also in wg015-usb-bootloader.ld):
//   scratchpad @ 0x40000000, 1152 B, blob entry = +4
//   runwordpad @ 0x40000480
//
// Boot-flag contract (seam #4, shim ch32fun.h): RTC_REG[0] is ONE-SHOT
// (read+cleared here) and honored only after a SYSRST (boot-6):
//   WG015_BOOT_FLAG_APP  -> fast-path jump to app before any USB/PLIC/DPU init
//   WG015_BOOT_FLAG_STAY -> normal loader entry (app wrote it via the reboot
//                           feature report + RCU->RSTSYS)

#include "ch32fun.h"
#include <stdint.h>
#include "rv003usb.h"

// App slot: flash page 1 onward (PLAN Р8; R8 escalation: 8K loader, +0x2000).
#define APP_BASE 0x80001000u

#define SCRATCHPAD_SIZE (1024+128)

// ld-provided absolute symbols (NOT in .bss - not zeroed by startup).
extern uint8_t scratchpad[SCRATCHPAD_SIZE];
extern volatile int32_t runwordpad;
// referenced by rv003usb.c under RV003USB_BOOTLOADER
uint32_t runwordpadready = 0;
volatile uint8_t reset_timeout = 0;

// Entry timeout as an rdcycle DELTA, not an iteration count: the V003
// "75 ms per unit" constant was calibrated to the V003 loop speed (boot-7).
#define BOOTLOADER_TIMEOUT_MS 5000
#define CYCLES_PER_MS 48000u // startup_wg015.S guarantees 48.000 MHz
#define BOOTLOADER_TIMEOUT_CYCLES (BOOTLOADER_TIMEOUT_MS * CYCLES_PER_MS)

// Optional enter-bootloader button: define port/pin/level to use, e.g.
//   #define BOOTLOADER_BTN_PORT B
//   #define BOOTLOADER_BTN_PIN  3
//   #define BOOTLOADER_BTN_TRIG_LEVEL 0  // level that MEANS "stay in loader"

#define LOCAL_CONCAT(A, B) A##B
#define LOCAL_EXP(A, B) LOCAL_CONCAT(A,B)
#define LOCAL_CONCAT3(A, B, C) A##B##C
#define LOCAL_EXP3(A, B, C) LOCAL_CONCAT3(A,B,C)

// SECRET word (flash offset 0xFC0): XOR-folded OFFSET of boot_usercode from
// 0x8000_0000 (boot-5 - the V003 formula packed a full 16-bit address).
extern uint32_t _boot_firmware_xor;
uint32_t secret_xor __attribute__((section(".secret_address"))) __attribute__((used)) = (uint32_t)(&_boot_firmware_xor);

void boot_usercode( void ) __attribute__((section(".boot_firmware"), noinline));

static int app_is_present( void )
{
	// RISC-V app image starts with code at APP_BASE (no vector table): the
	// only cheap sanity check is "first word is not erased/zero flash".
	uint32_t first = *(const uint32_t *)APP_BASE;
	return first != 0xFFFFFFFFu && first != 0;
}

static void __attribute__((noreturn)) jump_to_app( void )
{
	extern char __stack_top[];
	// Handoff contract (Р8): 48.000 MHz PLL + flash LAT=1 stay configured
	// (documented clock state); fresh sp at TCM-B top; mtvec parked on the
	// app entry so a stray trap lands in app code, not in loader state.
	asm volatile(
		"csrw mtvec, %0\n"
		"mv sp, %1\n"
		"jr %0\n"
		: : "r"(APP_BASE), "r"(__stack_top) );
	__builtin_unreachable();
}

// Exit-to-app with full USB teardown (boot-1 contract).  Returns (without
// booting) when no valid app is present.  Kept under the V003 name; host
// blobs can locate it across loader versions through the SECRET word.
void boot_usercode( void )
{
	if( !app_is_present() ) return;

	// 1) No interrupts of ours may ever fire in the app's world.
	asm volatile( "csrc mstatus, %0" : : "r"(WG015_MSTATUS_MIE) );
	asm volatile( "csrw mie, zero" );

	GPIO_TypeDef * usbport = LOCAL_EXP( GPIO, USB_PORT );

	// 2) GPIO source quiesced: mask + W1C all pending pin flags.
	usbport->INTENCLR  = 0xFFFF;
	usbport->INTSTATUS = 0xFFFF;

	// 3) PLIC: source off; claim+complete once to release a latched gateway
	// request (claim of nothing returns 0 - completing 0 is harmless).
	WG015_PLIC_DisableIRQ( WG015_IRQ_GPIO );
	WG015_PLIC_Complete( WG015_PLIC_Claim() );

	// 4) DPU released with a disconnect pulse: drive the 1.5k low so the
	// host drops the loader device (V003 boot.c:105-108 logic), then float
	// the pin - the app decides whether/when to go on-bus itself.
	usbport->DATAOUTCLR = 1u << USB_PIN_DPU;
	{
		uint32_t t0 = WG015_rdcycle();
		while( WG015_rdcycle() - t0 < 20u * CYCLES_PER_MS );
	}
	usbport->OUTENCLR = 1u << USB_PIN_DPU;

	jump_to_app();
}

int main( void )
{
	// ---- Entry decision (Р8) --------------------------------------------
	uint32_t flag = WG015_RTC_REG(0);
	if( flag ) WG015_RTC_REG(0) = 0;  // one-shot: always cleared (boot-6)
	if( !( RCU->RSTSTAT & RCU_RSTSTAT_SYSRST ) )
		flag = 0;                     // stale flag after POR: ignore (boot-6)

	if( flag == WG015_BOOT_FLAG_APP && app_is_present() )
		jump_to_app();                // fast-path: near-reset state, before
		                              // any USB/PLIC/DPU init (boot-1)

#if defined(BOOTLOADER_BTN_PORT) && defined(BOOTLOADER_BTN_PIN)
	{
		WG015_GPIO_CLOCK_ENABLE( LOCAL_EXP3( RCU_CGCFGAHB_GPIO, BOOTLOADER_BTN_PORT, EN ) );
		GPIO_TypeDef * btnport = LOCAL_EXP( GPIO, BOOTLOADER_BTN_PORT );
#if BOOTLOADER_BTN_TRIG_LEVEL
		btnport->PULLMODE &= ~(1u << BOOTLOADER_BTN_PIN); // WG015 has pull-UP only
#else
		btnport->PULLMODE |= 1u << BOOTLOADER_BTN_PIN;
#endif
		// 2-clk input synchronizer + pull settle
		uint32_t t0 = WG015_rdcycle();
		while( WG015_rdcycle() - t0 < CYCLES_PER_MS );
		uint32_t lvl = ( btnport->DATA >> BOOTLOADER_BTN_PIN ) & 1;
		if( lvl != BOOTLOADER_BTN_TRIG_LEVEL )
			boot_usercode(); // returns if no app - fall into the loader
	}
#endif

	// ---- Flash controller timebase -> 48 MHz, once per loader run (Р8) --
	// Reset defaults assume a ~100 MHz clk (research_flash.md §1); registers
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

	runwordpad = 0; // absolute-address symbol: startup does not zero it

	usb_setup();    // WG015 seam #2 in rv003usb.c: GPIO, DPU, mtvec, PLIC

	// ---- Main loop -------------------------------------------------------
	// localpad keeps the V003 blob-exec semantics (wire compat):
	//   runwordpad > 0 -> execute scratchpad blob after N-1 iterations
	//   runwordpad < 0 -> boot app after N-1 iterations
	// Only the ENTRY timeout is an rdcycle delta (boot-7).
	uint32_t t0 = WG015_rdcycle();
	int32_t localpad = 0;
	while(1)
	{
#if BOOTLOADER_TIMEOUT_MS
		if( !reset_timeout && localpad == 0 &&
		    ( WG015_rdcycle() - t0 ) > BOOTLOADER_TIMEOUT_CYCLES )
		{
			boot_usercode();
			reset_timeout = 1; // no app to boot: stop retrying, stay alive
		}
#endif
		if( localpad > 0 )
		{
			if( --localpad == 0 )
			{
				/* Scratchpad structure (V003-compatible):
					4-bytes:  report-id word (0xaa/0xab; blob status on return)
						... code (entered here at +4)
					4-bytes:  LONG( 0x1234abcd ) trailer, zeroed on detect

					Blobs run with mstatus.MIE cleared internally (R7) and
					begin with the flash address guard (boot-2). */
				typedef void (*setype)( uint32_t *, volatile int32_t * );
				setype scratchexec = (setype)(scratchpad+4);
				scratchexec( (uint32_t*)&scratchpad[0], &runwordpad );
			}
		}
		else if( localpad < 0 )
		{
			if( ++localpad == 0 )
				boot_usercode(); // returns if no app
		}

		int32_t commandpad = runwordpad;
		if( commandpad )
		{
			localpad = commandpad - 1;
			runwordpad = 0;
		}
	}
}

// ---- rv003usb.c user hooks (bootloader_v006 pattern) ------------------------

void usb_handle_user_in_request( struct usb_endpoint * e, uint8_t * unused, int endp, uint32_t sendtok, struct rv003usb_internal * ist )
{
	// Only the mandatory interrupt IN endpoint lands here; nothing to say.
	usb_send_empty( sendtok );
}

void usb_handle_user_data( struct usb_endpoint * e, int current_endpoint, uint8_t * data, int len, struct rv003usb_internal * ist )
{
	if( e->opaque )
	{
		uint8_t * start = &e->opaque[e->count<<3];
		for( int i = 0; i < len; i++ )
			start[i] = data[i];
		e->count++;

		// If the last 4 bytes of this 8-byte chunk are the magic, arm
		// execution (started by the following IN, see rv003usb.c).
		uint32_t * last4 = (uint32_t*)(start + 4);
		if( *last4 == 0x1234abcd )
		{
			*last4 = 0;
			runwordpadready = 1;
			e->opaque = 0;
		}
		if( e->count >= SCRATCHPAD_SIZE/8 ) e->opaque = 0;
	}
}

void usb_handle_hid_get_report_start( struct usb_endpoint * e, int reqLen, uint32_t lValueLSBIndexMSB )
{
	if( reqLen > SCRATCHPAD_SIZE ) reqLen = SCRATCHPAD_SIZE;
	e->max_len = reqLen;
	e->opaque = scratchpad;
}

void usb_handle_hid_set_report_start( struct usb_endpoint * e, int reqLen, uint32_t lValueLSBIndexMSB )
{
	e->max_len = SCRATCHPAD_SIZE;
	runwordpad = 1; // request stoppage of any pending countdown
	e->opaque = scratchpad;
}
