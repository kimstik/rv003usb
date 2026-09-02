/* ch32fun.h — WG015 (K1921VG015) SHIM header for the rv003usb stack.
 *
 * PLAN.md Р9: rv003usb.S:1 and rv003usb.c:10 do `#include "ch32fun.h"` and are
 * NOT modified for this port.  Instead, Makefile.wg015 puts this directory
 * first on the include path, so this shim is found instead of the (empty in
 * this repo) ch32fun submodule header.  It provides the WG015 equivalents of
 * everything the shared stack pulls from the real ch32fun.h.
 *
 * Inventory (PLAN Р9): register structs (K1921VG015_min.h), XW_C_* ->
 * standard RV32 byte/half ops, SysTick replacement (rdcycle), NVIC_EnableIRQ
 * analog (PLIC), DEBUG sink address, FUNCONF_SYSTICK_USE_HCLK=1.
 */

#ifndef _WG015_CH32FUN_SHIM_H
#define _WG015_CH32FUN_SHIM_H

#include "K1921VG015_min.h"

/* Target marker for per-demo configs (PLAN Р9: LED/WCH-specific demo blocks
 * go under #if !WG015, pins per-target). */
#ifndef WG015
#define WG015 1
#endif

/* rdcycle is HCLK-synchronous, so the SysTick-source guard at
 * rv003usb/rv003usb.h:17-19 is satisfied by construction (PLAN Р9). */
#ifndef FUNCONF_SYSTICK_USE_HCLK
#define FUNCONF_SYSTICK_USE_HCLK 1
#endif

/*===========================================================================
 * XW_C_* — WCH custom compressed byte/half ops -> standard RV32 instructions.
 * Call sites: rv003usb.S:439,477,485,665,890 (S:890 is inside the counted TX
 * loop).  NOTE: these expand to 32-bit instructions where the XW originals
 * were 16-bit — the z7 cycle ledger and P1.5 slot emulation must account for
 * the changed fetch footprint (PLAN Р2 contract table).
 *===========================================================================*/
#ifdef __ASSEMBLER__
#define XW_C_LBU( rd, rs1, imm )  lbu rd, imm(rs1)
#define XW_C_LHU( rd, rs1, imm )  lhu rd, imm(rs1)
#define XW_C_SB( rs2, rs1, imm )  sb rs2, imm(rs1)
#endif

/*===========================================================================
 * DEBUG sink (PLAN Р10 zero-intrusiveness markers).
 *
 * rv003usb.S aims its always-present debug stores at TIM1_BASE+0x58 (inactive
 * default, "Go nowhere", rv003usb.S:36-37) or TIM1_BASE+0x24 (active
 * RV003USB_DEBUG_TIMING).  On WG015 the architectural no-op sink is a GPIO
 * DATAOUTTGL register: `sw x0, DATAOUTTGL` toggles nothing (research_gpio.md
 * §1), identical bus traffic whether debug is on or off.  TIM1_BASE is
 * defined so the DEFAULT (+0x58) store lands on GPIOB->DATAOUTTGL (DBG0 home
 * port B per PLAN §7).
 *
 * TODO(port): RV003USB_DEBUG_TIMING=1 (rv003usb.S:31-33, rv003usb.c:62-125)
 * is V003-only (TIM1/RCC/MCO); the +0x24 store would land on an undecoded
 * GPIO offset.  The C/asm port task must #error it out for WG015.
 *===========================================================================*/
#define WG015_DEBUG_SINK_ADDR ( GPIOB_BASE + GPIO_DATAOUTTGL_OFFSET )
#define TIM1_BASE ( WG015_DEBUG_SINK_ADDR - 0x58 )

/*===========================================================================
 * C-side helpers
 *===========================================================================*/
#ifndef __ASSEMBLER__

#include <stdint.h>

/* --- cycle counter: SysTick->CNT replacement (USB_TICK_READ contract) ---- */
static inline uint32_t WG015_rdcycle( void )
{
	uint32_t r;
	asm volatile( "rdcycle %0" : "=r"(r) );
	return r;
}

/* --- PLIC: NVIC_EnableIRQ-equivalents (research_core_irq.md §4/§5) ------- */
/* Priority must be written BEFORE enabling (reset prio 0 = source disabled);
 * EIP asserts only when pending prio > MTHR. */
static inline void WG015_PLIC_SetPriority( int irqn, int prio )
{
	PLIC_PRI( irqn ) = prio;
}

static inline void WG015_PLIC_EnableIRQ( int irqn )
{
	PLIC_MIEM0 |= 1u << irqn;
}

static inline void WG015_PLIC_DisableIRQ( int irqn )
{
	PLIC_MIEM0 &= ~( 1u << irqn );
}

/* claim = read MICC (returns source, clears pending); complete = write the
 * source number back.  The gateway holds off same-source re-requests until
 * complete — order for the USB ISR: GPIO INTSTATUS W1C first, THEN complete
 * (PLAN Р2 USB_ISR_ACK). */
static inline uint32_t WG015_PLIC_Claim( void )
{
	return PLIC_MICC;
}

static inline void WG015_PLIC_Complete( uint32_t src )
{
	PLIC_MICC = src;
}

/* NVIC_EnableIRQ analog: set PRI[n], set MIEM0 bit.  PLAN Р7 priorities: our
 * (GPIO/USB) source = 7, other sources below ours but above MTHR.
 * TODO(port): the V003 call site NVIC_EnableIRQ(EXTI7_0_IRQn) at
 * rv003usb.c:153 must become WG015_EnableIRQ(WG015_IRQ_GPIO, 7) in the C
 * seam #2 (usb_setup) port — EXTI7_0_IRQn is intentionally NOT defined here
 * so a missed site fails at compile time. */
static inline void WG015_EnableIRQ( int irqn, int prio )
{
	WG015_PLIC_SetPriority( irqn, prio );
	WG015_PLIC_EnableIRQ( irqn );
}

/* --- machine-level global interrupt control ------------------------------ */
#define WG015_MIE_MEIE ( 1u << 11 )  /* mie.MEIE   */
#define WG015_MSTATUS_MIE ( 1u << 3 )/* mstatus.MIE */

static inline void WG015_EnableMachineExternalIRQ( void )
{
	asm volatile( "csrs mie, %0" :: "r"(WG015_MIE_MEIE) );
	asm volatile( "csrs mstatus, %0" :: "r"(WG015_MSTATUS_MIE) );
}

static inline void WG015_DisableGlobalIRQ( void )
{
	asm volatile( "csrc mstatus, %0" :: "r"(WG015_MSTATUS_MIE) );
}

/* ch32fun API compatibility for demo code: clock is fully brought up by
 * startup_wg015.S, so SystemInit is a no-op; Delay_Ms counts rdcycle. */
static inline void SystemInit( void ) { }
static inline void Delay_Ms( uint32_t ms )
{
	uint32_t start = (uint32_t)({ uint32_t r; asm volatile("rdcycle %0":"=r"(r)); r; });
	uint32_t cycles = ms * 48000u; /* FUNCONF_SYSTEM_CORE_CLOCK/1000 */
	while( (uint32_t)({ uint32_t r; asm volatile("rdcycle %0":"=r"(r)); r; }) - start < cycles );
}

/* --- GPIO bring-up pieces for the usb_setup() C seam (PLAN Р3 seam #2) --- */
/* Ports are unclocked AND held in reset after power-up: enable both gates
 * (research_gpio.md §1).  Replaces RCC->APB2PCENR at rv003usb.c:60. */
#define WG015_GPIO_CLOCK_ENABLE( gpioen_msk ) do { \
	RCU->CGCFGAHB  |= (gpioen_msk); \
	RCU->RSTDISAHB |= (gpioen_msk); \
} while(0)

/* After reset all pins are GPIO inputs, tri-state, pull-off — D+/D- input
 * config (rv003usb.c:128-140 CFGLR block) needs only OUTENCLR insurance. */
#define WG015_GPIO_INPUT( port, pinmask )      do { (port)->OUTENCLR = (pinmask); } while(0)
#define WG015_GPIO_OUTPUT( port, pinmask )     do { (port)->OUTENSET = (pinmask); } while(0)
#define WG015_GPIO_SET( port, pinmask )        do { (port)->DATAOUTSET = (pinmask); } while(0)
#define WG015_GPIO_CLR( port, pinmask )        do { (port)->DATAOUTCLR = (pinmask); } while(0)

/* D- falling-edge IRQ config (replaces AFIO/EXTI at rv003usb.c:142-145):
 * edge type, falling polarity, unmask pin; then WG015_EnableIRQ(GPIO,7). */
#define WG015_GPIO_IRQ_FALLING( port, pinmask ) do { \
	(port)->INTTYPESET = (pinmask); /* edge */ \
	(port)->INTPOLCLR  = (pinmask); /* falling */ \
	(port)->INTENSET   = (pinmask); \
} while(0)

/* Flash controller timebase -> 48 MHz.  Reset defaults assume ~100 MHz clk
 * (research_flash.md S1); the registers are write-locked while BUSY, so the
 * guard is free.  Single source of truth for both loaders (the HID loader's
 * host-side blobs necessarily carry their own PIC copy). */
static inline void WG015_FlashTimebase48MHz( void )
{
	if( !( WG015_FLASH->STAT & FLASH_STAT_BUSY ) )
	{
		WG015_FLASH->TACCR  = 1;       /* ceil(48 MHz * 20 ns) */
		WG015_FLASH->TNVSR  = 240000;  /* 5 ms   */
		WG015_FLASH->TERSR  = 4800000; /* 100 ms */
		WG015_FLASH->TNVHR  = 240;     /* 5 us   */
		WG015_FLASH->TNVH1R = 4800;    /* 100 us */
		WG015_FLASH->TRCVR  = 480;     /* 10 us  */
		WG015_FLASH->TPGSR  = 480;     /* 10 us  */
	}
}

/* Seam #4 (REBOOT_TO_BOOTLOADER, PLAN Р3/Р8): boot-flag contract with the
 * bootloader. One-shot: the loader reads RTC_REG[0], clears it immediately,
 * and honors it only when RCU->RSTSTAT reports SYSRST (not POR). Values are
 * fixed here and used by both sides (app path lives in rv003usb.c). */
#define WG015_RTC_REG(n)      (*(volatile uint32_t *)PMURTC_RTC_REG_ADDR(n))
#define WG015_BOOT_FLAG_STAY  0xB00710ADu /* stay in bootloader after reset  */
#define WG015_BOOT_FLAG_APP   0x0AFF10ADu /* fast-path: jump to app at once  */

#endif /* !__ASSEMBLER__ */

#endif /* _WG015_CH32FUN_SHIM_H */
