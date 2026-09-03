// dfu_v003.h — CH32V003 chip port of the DFU bootloader core (TODO 19b,
// BOOT-C backport).  The loader lives in the 1920 B boot zone (0x1FFFF000,
// mapped at 0x0 when the option byte selects it); the app is main flash.
//
// Key differences from the WG015 port:
//   * flash ops run XIP: the V003 core STALLS on flash fetch while the
//     controller is busy (upstream ch32fun flash helpers work this way) —
//     no RAM-resident routine needed; the stall lands inside bwPollTimeout.
//   * page = 64 B fast page = one DFU transfer -> every block erases+programs.
//   * "jump to app" = the hardware boot-mode switch + system reset
//     (bootloader/bootloader.c:110-115 idiom) — not a literal jump.
//   * entry qualification: soft-reset signature in RCC->RSTSCKR means the
//     app requested the loader (rv003usb.c V003 reboot path sets the
//     boot-mode bit then soft-resets) -> STAY.  A cold boot with a
//     CRC-valid app boots the app.
#ifndef _DFU_PORT_V003_H
#define _DFU_PORT_V003_H

#include <stdint.h>
#include "ch32fun.h" // the real WCH header (ch32fun submodule)

// ---- Geometry / protocol constants -----------------------------------------
#define DFU_APP_BASE      0x08000000u // main flash (visible while in boot zone)
#define DFU_FLASH_END     0x08004000u // 16K
#define DFU_PAGE_SIZE     64u         // fast-erase page
#define DFU_XFER_SIZE     64u         // wTransferSize == page: erase per block
#define DFU_CYCLES_PER_MS 48000u      // TINY_BOOT sets 48 MHz (HSIx2 PLL)

// Fast page erase+program of 64 B is sub-ms on this part; 3 ms is generous.
#define DFU_POLL_ERASE_MS 3
#define DFU_POLL_PROG_MS  3

// Sentinels only (no RTC_REG on V003): STAY is derived from the reset cause,
// APP fast-path never fires (the hardware boot switch IS the fast path).
#define DFU_FLAG_APP  0xFFFFFFFFu
#define DFU_FLAG_STAY 2u

// ---- Small inlines ----------------------------------------------------------
static inline uint32_t dfu_port_cycles( void ) { return SysTick->CNT; }

static inline void dfu_port_irq_disable( void ) { __disable_irq(); }
static inline void dfu_port_irq_enable( void )  { __enable_irq(); }

// Reset-cause qualification (bootloader/bootloader.c:240-248 idiom): the
// exact soft-reset signature means the app deliberately rebooted into the
// loader -> STAY.  Flags are cleared via RMVF either way.
static inline uint32_t dfu_port_flag_read_and_clear( void )
{
	uint32_t r = RCC->RSTSCKR;
	RCC->RSTSCKR = r | 0x1000000; // RMVF: clear reset-cause flags
	return ( r == 0x10000000 ) ? DFU_FLAG_STAY : 0;
}

// Boot the app through the hardware switch + full system reset
// (bootloader/bootloader.c:110-115): STATR bit14=0 selects user code.
static inline void __attribute__((noreturn)) dfu_port_reboot_to_app( void )
{
	FLASH->BOOT_MODEKEYR = FLASH_KEY1;
	FLASH->BOOT_MODEKEYR = FLASH_KEY2;
	FLASH->STATR = 0;            // bit14=0: boot user code
	FLASH->CTLR = CR_LOCK_Set;
	PFIC->SCTLR = 1 << 31;       // system reset
	while(1);
}

// Same mechanism: on V003 there is no direct-jump handoff (different
// mapping at 0x0), the boot switch is both the fast and the normal path.
static inline void __attribute__((noreturn)) dfu_port_jump_app( void )
{
	dfu_port_reboot_to_app();
}

// No flash timebase on V003 - but SysTick is OUR job here: TINY_BOOT only sets
// the PLL, and the HID loader does the same thing explicitly (bootloader.c:121).
// Without this dfu_port_cycles() returns a frozen 0 and every wait loop in the
// core spins forever.
static inline void dfu_port_flash_timebase_init( void )
{
	SysTick->CTLR = 5; // enable, HCLK source (FUNCONF_SYSTICK_USE_HCLK)
}

// 64-byte fast page: erase + program, XIP (core stalls while BSY — fine,
// we are inside the host's bwPollTimeout window and IRQs are masked).
static void dfu_port_flash_write_block( uint32_t addr, const uint32_t * src )
{
	// Unlock controller + fast-mode
	FLASH->KEYR = FLASH_KEY1;     FLASH->KEYR = FLASH_KEY2;
	FLASH->MODEKEYR = FLASH_KEY1; FLASH->MODEKEYR = FLASH_KEY2;

	// Fast erase this 64 B page (DFU_PAGE_SIZE == block size)
	FLASH->CTLR = CR_PAGE_ER;
	FLASH->ADDR = addr;
	FLASH->CTLR = CR_PAGE_ER | CR_STRT_Set;
	while( FLASH->STATR & FLASH_STATR_BSY );

	// Fast program: reset buffer, load 16 words, start
	FLASH->CTLR = CR_PAGE_PG;
	FLASH->CTLR = CR_PAGE_PG | CR_BUF_RST;
	while( FLASH->STATR & FLASH_STATR_BSY );
	volatile uint32_t * dst = (volatile uint32_t *)addr;
	for( int i = 0; i < 16; i++ )
	{
		dst[i] = src[i];
		FLASH->CTLR = CR_PAGE_PG | CR_BUF_LOAD;
		while( FLASH->STATR & FLASH_STATR_BSY );
	}
	FLASH->ADDR = addr;
	FLASH->CTLR = CR_PAGE_PG | CR_STRT_Set;
	while( FLASH->STATR & FLASH_STATR_BSY );

	FLASH->CTLR = CR_LOCK_Set;
}

#endif // _DFU_PORT_V003_H
