// dfu_port.h — K1921VG015 (WG015) port of the DFU bootloader core.
// This is the WHOLE chip-specific surface of ../rv003usb/dfu_core.c; a new
// target (e.g. a V003 backport, TODO 19b) supplies its own dfu_port.h +
// usb_config.h + Makefile/ld and reuses the core untouched.
#ifndef _DFU_PORT_WG015_H
#define _DFU_PORT_WG015_H

#include <stdint.h>
#include "ch32fun.h" // WG015 shim: registers, WG015_RTC_REG, flags, rdcycle

// ---- Geometry / protocol constants -----------------------------------------
#define DFU_APP_BASE      0x80001000u // page 1 onward, same as the blob loader
#define DFU_FLASH_END     0x80100000u
#define DFU_PAGE_SIZE     WG015_FLASH_PAGE_SIZE // 4096
#define DFU_XFER_SIZE     64u         // wTransferSize (must match usb_config.h)
#define DFU_CYCLES_PER_MS 48000u      // startup_wg015.S guarantees 48.000 MHz

// bwPollTimeout, ms: erase(page start)+program vs program-only.  Real times
// are measured on silicon (TODO.md 14); these are safe-side estimates.
#define DFU_POLL_ERASE_MS 50
#define DFU_POLL_PROG_MS  8

// One-shot boot flag values (contract shared with the blob loader and the
// app-side REBOOT seam in rv003usb.c).
#define DFU_FLAG_APP  WG015_BOOT_FLAG_APP
#define DFU_FLAG_STAY WG015_BOOT_FLAG_STAY

// ---- Small inlines ----------------------------------------------------------
static inline uint32_t dfu_port_cycles( void ) { return WG015_rdcycle(); }

static inline void dfu_port_irq_disable( void )
{
	asm volatile( "csrc mstatus, %0" : : "r"(WG015_MSTATUS_MIE) );
}
static inline void dfu_port_irq_enable( void )
{
	asm volatile( "csrs mstatus, %0" : : "r"(WG015_MSTATUS_MIE) );
}

// One-shot flag: read RTC_REG[0], clear it, honor only after a soft reset
// (a stale flag must not redirect a cold power-on — PLAN boot-6).
static inline uint32_t dfu_port_flag_read_and_clear( void )
{
	uint32_t flag = WG015_RTC_REG(0);
	if( flag ) WG015_RTC_REG(0) = 0;
	if( !( RCU->RSTSTAT & RCU_RSTSTAT_SYSRST ) )
		flag = 0;
	return flag;
}

// Reboot into the app through a full system reset: DPU drops, the host
// re-enumerates whatever the app is.  Does not return.
static inline void __attribute__((noreturn)) dfu_port_reboot_to_app( void )
{
	WG015_RTC_REG(0) = WG015_BOOT_FLAG_APP;
	RCU->RSTSYS = RCU_RSTSYS_MAGIC;
	while(1);
}

// Direct jump handoff (near-reset state guaranteed by the caller: this runs
// before usb_setup).  Contract as the blob loader: 48 MHz PLL + LAT=1 stay;
// fresh sp at TCM-B top; mtvec parked on the app entry.
static inline void __attribute__((noreturn)) dfu_port_jump_app( void )
{
	extern char __stack_top[];
	asm volatile(
		"csrw mtvec, %0\n"
		"mv sp, %1\n"
		"jr %0\n"
		: : "r"(DFU_APP_BASE), "r"(__stack_top) );
	__builtin_unreachable();
}

// Flash controller timebase -> 48 MHz, once per loader run.  Reset defaults
// assume ~100 MHz clk (research_flash.md §1); registers are write-locked
// while BUSY (never busy this early, the check is free).
static inline void dfu_port_flash_timebase_init( void )
{
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
}

// ---- RAM-resident flash write ----------------------------------------------
// section(".data.ramfunc") rides to TCM-B with the ordinary startup .data
// copy (wg015_common.ld folds .data.* into .data > TCMB AT> FLASH).
// Self-contained: no calls, no rodata — verify in the disassembly.  The core
// masks IRQs around the call; NOTHING may fetch from flash while STAT.BUSY=1
// (research_flash.md §4).  Sequence per РП А.4 + SDK: ADDR -> DATA0..3 ->
// CMD(KEY|op) -> >=5 NOP -> poll BUSY.  A 64-byte block = 4 x 16-byte units
// (РП program unit = 128 bits).  Erases the 4K page when the block starts it.
static void __attribute__((section(".data.ramfunc"), noinline, used))
dfu_port_flash_write_block( uint32_t addr, const uint32_t * src )
{
	WG015_FLASH_TypeDef * const fl = WG015_FLASH;
	if( ( addr & ( DFU_PAGE_SIZE - 1 ) ) == 0 )
	{
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

#endif // _DFU_PORT_WG015_H
