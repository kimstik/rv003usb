/* startup_wg015.S — tiny startup for the rv003usb WG015 (K1921VG015) port.
 * PLAN.md Р8 "Свой tiny-boot": sp, mtvec, zero .bss, copy .data (incl.
 * relocated USB descriptors) and .timecrit, PLL to 48.000 MHz, call main.
 * Replaces the WCH boot block of rv003usb.S:1032-1085.
 *
 * RV32IMC + Zicsr only — no F instructions (эррата №5, PLAN Р9).
 * Reset state: SYSCLK = HSI 1 MHz, FLASH LAT = 1 (research_flash.md §5).
 * Reset PC = 0x8000_0000 is SDK-inferred, РП-unconfirmed (F7) — verified by
 * the P0 cold-start checklist item.
 */

#include "K1921VG015_min.h"

/*---------------------------------------------------------------------------
 * Integer-only 48.000 MHz PLL configs, one per supported crystal
 * (research_clocks.md §2, all rows verified against РП §4.2 ranges):
 * fOUT0 = HSE/REFDIV * FBDIV / ((1+PD0A)*(1+PD0B)); REFDIV=1 everywhere.
 * Default HSE = 16 MHz (PLAN §7).
 *---------------------------------------------------------------------------*/
#ifndef HSE_VALUE
#define HSE_VALUE 16000000
#endif

#if HSE_VALUE == 10000000
	#define WG015_PLL_FBDIV 48   /* VCO 480 MHz,  /(5*2)  = 48 */
	#define WG015_PLL_PD0A  4
	#define WG015_PLL_PD0B  1
#elif HSE_VALUE == 12000000
	#define WG015_PLL_FBDIV 96   /* VCO 1152 MHz, /(8*3)  = 48 */
	#define WG015_PLL_PD0A  7
	#define WG015_PLL_PD0B  2
#elif HSE_VALUE == 16000000
	#define WG015_PLL_FBDIV 96   /* VCO 1536 MHz, /(8*4)  = 48 */
	#define WG015_PLL_PD0A  7
	#define WG015_PLL_PD0B  3
#elif HSE_VALUE == 20000000
	#define WG015_PLL_FBDIV 48   /* VCO 960 MHz,  /(5*4)  = 48 */
	#define WG015_PLL_PD0A  4
	#define WG015_PLL_PD0B  3
#elif HSE_VALUE == 24000000
	#define WG015_PLL_FBDIV 48   /* VCO 1152 MHz, /(8*3)  = 48 */
	#define WG015_PLL_PD0A  7
	#define WG015_PLL_PD0B  2
#elif HSE_VALUE == 25000000
	#define WG015_PLL_FBDIV 48   /* VCO 1200 MHz, /(5*5)  = 48 */
	#define WG015_PLL_PD0A  4
	#define WG015_PLL_PD0B  4
#else
#error "HSE_VALUE must be 10/12/16/20/24/25 MHz: integer 48.000 MHz PLL only (research_clocks.md par.2; 8 MHz cannot legally feed the PLL, fREF >= 10 MHz)"
#endif

/* Initial PLLSYSCFG0: PLLEN=1, both outputs bypassed (BYP=3) during config,
 * FOUTEN=0, DSMEN/DACEN=0 (integer mode), PD1x=0 (Fout1 stays bypassed —
 * SDK precedent system_k1921vg015.c:238). */
#define WG015_PLLSYSCFG0_INIT ( (WG015_PLL_PD0B << RCU_PLLSYSCFG0_PD0B_Pos) | \
                                (WG015_PLL_PD0A << RCU_PLLSYSCFG0_PD0A_Pos) | \
                                (1 << RCU_PLLSYSCFG0_REFDIV_Pos) | \
                                (3 << RCU_PLLSYSCFG0_BYP_Pos) | \
                                RCU_PLLSYSCFG0_PLLEN )

	.section .init,"ax",@progbits
	.globl _start
	.balign 4
_start:
	/* Stack at the top of TCM-B (data bank; code bank is TCM-A — Р6). */
	la sp, __stack_top
	.option push
	.option norelax
	la gp, __global_pointer$
	.option pop

	/* Mask everything, then mtvec (direct mode — F11: direct-only assumed)
	 * to the weak trap stub; the rv003usb ISR port replaces it (Р7). */
	csrw mie, zero
	la t0, wg015_trap_entry
	csrw mtvec, t0

	/* Zero .bss (TCM-B). */
	la a0, __bss_start
	la a1, __bss_end
1:	bgeu a0, a1, 2f
	sw zero, 0(a0)
	addi a0, a0, 4
	j 1b
2:
	/* Copy .data (incl. .rodata.usbdesc descriptors — PLAN Р3) to TCM-B. */
	la a0, __data_lma
	la a1, __data_start
	la a2, __data_end
	call wg015_copy
	/* Copy .timecrit to TCM-A; no-op in the default flash-resident variant
	 * (there LMA == VMA and wg015_copy exits immediately). */
	la a0, __timecrit_lma
	la a1, __timecrit_start
	la a2, __timecrit_end
	call wg015_copy

	/*-------------------------------------------------------------------
	 * Clocks: HSE -> integer PLL -> 48.000 MHz SYSCLK.
	 * Sequence per research_clocks.md §4.2 (РП §4.2 + SDK ClkInit,
	 * system_k1921vg015.c:144-263).
	 *-------------------------------------------------------------------*/
	li a5, RCU_BASE

	/* 1) Run from HSE while touching the PLL (no HSE enable bit exists —
	 * the oscillator free-runs unless PMU stops it, research_clocks.md
	 * §3.1).  Bounded poll like the SDK (100 iterations, then proceed). */
	li a0, RCU_SYSCLKCFG_SRC_HSECLK
	sw a0, RCU_SYSCLKCFG_OFFSET(a5)
	li a2, 100
1:	lw a1, RCU_CLKSTAT_OFFSET(a5)
	andi a1, a1, RCU_CLKSTAT_SRC_Msk
	beq a1, a0, 2f
	addi a2, a2, -1
	bnez a2, 1b
	/* TODO(port): on timeout (dead crystal) we fall through like the SDK
	 * and hang at the LOCK poll below; P0 decides a real failure policy. */
2:
	/* 2) Program the PLL: integer mode (DSMEN=0, FRAC=0). */
	li a0, WG015_PLLSYSCFG0_INIT
	sw a0, RCU_PLLSYSCFG0_OFFSET(a5)
	sw zero, RCU_PLLSYSCFG1_OFFSET(a5)          /* FRAC = 0 */
	li a1, WG015_PLL_FBDIV
	sw a1, RCU_PLLSYSCFG2_OFFSET(a5)
	/* REFSEL: deliberately NOT touched.  РП fig. 4.1 says REFSEL=1 selects
	 * HSE, but the SDK never writes PLLSYSCFG3 and works on real boards
	 * (research_clocks.md §1.2 contradiction).  SDK precedent wins here.
	 * TODO(REFSEL experiment P0): if the PLL refuses to lock from HSE, try:
	 * li a1, RCU_PLLSYSCFG3_REFSEL; sw a1, RCU_PLLSYSCFG3_OFFSET(a5) */

	/* 3) Enable Fout0, settle, poll LOCK (no numeric lock time in РП —
	 * SDK-style dummy delay + poll forever). */
	ori a0, a0, RCU_PLLSYSCFG0_FOUTEN0
	sw a0, RCU_PLLSYSCFG0_OFFSET(a5)
	li a2, 1000
1:	addi a2, a2, -1
	bnez a2, 1b
1:	lw a1, RCU_PLLSYSSTAT_OFFSET(a5)
	andi a1, a1, RCU_PLLSYSSTAT_LOCK
	beqz a1, 1b

	/* 4) Un-bypass Fout0 (BYP 3 -> 2; Fout1 stays bypassed). */
	andi a0, a0, ~(1 << RCU_PLLSYSCFG0_BYP_Pos)
	sw a0, RCU_PLLSYSCFG0_OFFSET(a5)

	/* 5) FLASH LAT: reset default 1 is required AND sufficient @48 MHz,
	 * 1.2 V (РП табл. 7.1) — deliberately no write; the SDK's LAT=3+CEN=1
	 * is not copied (working hypothesis R11, integrity-gated in P1.4). */

	/* 6) Switch SYSCLK to PLL, bounded poll (SDK-style). */
	li a0, RCU_SYSCLKCFG_SRC_SYSPLL0CLK
	sw a0, RCU_SYSCLKCFG_OFFSET(a5)
	li a2, 100
1:	lw a1, RCU_CLKSTAT_OFFSET(a5)
	andi a1, a1, RCU_CLKSTAT_SRC_Msk
	beq a1, a0, 2f
	addi a2, a2, -1
	bnez a2, 1b
2:
	call main
1:	j 1b                     /* main returned: park */

/* copy words [a0..) -> [a1..a2); skips instantly when a0 == a1 (VMA==LMA) */
wg015_copy:
	beq a0, a1, 2f
1:	bgeu a1, a2, 2f
	lw a3, 0(a0)
	sw a3, 0(a1)
	addi a0, a0, 4
	addi a1, a1, 4
	j 1b
2:	ret

/* Weak default trap handler — replaced by the rv003usb asm ISR (Р7). */
	.weak wg015_trap_entry
	.balign 4
wg015_trap_entry:
	j wg015_trap_entry
