/* K1921VG015_min.h — minimal, self-written register header for the rv003usb
 * WG015 (NIIET K1921VG015) port.
 *
 * Deliberately NOT the vendor K1921VG015.h (12.4k lines, license unverified —
 * PLAN.md Р9).  Contains ONLY what this port needs.  Every block cites its
 * source in РП К1921ВГ015 (19.02.2025) once; per-field facts come from
 * doc/wg015/research/*.md which carry the manual.txt line references.
 *
 * Conventions follow ch32fun: plain C structs of volatile uint32_t, base
 * address macros, _Pos/_Msk field defines.  All macros shared with assembly
 * carry no integer suffixes (this header is included from .S files through
 * the wg015 ch32fun.h shim — keep it __ASSEMBLER__-clean).
 */

#ifndef _K1921VG015_MIN_H
#define _K1921VG015_MIN_H

/*===========================================================================
 * Memory map (РП табл. 6.1/6.2; research_core_irq.md §6)
 *===========================================================================*/
#define WG015_FLASH_MEM_BASE  0x80000000  /* main flash, 1 MB, 256 x 4K pages */
#define WG015_FLASH_MEM_SIZE  0x00100000
#define WG015_RAM0_BASE       0x40000000  /* 256K; TCM-A = low 128K, TCM-B = high 128K */
#define WG015_TCMA_BASE       0x40000000
#define WG015_TCMA_SIZE       0x00020000
#define WG015_TCMB_BASE       0x40020000
#define WG015_TCMB_SIZE       0x00020000
#define WG015_RAM1_BASE       0x10000000  /* 64K, battery domain */

/*===========================================================================
 * RCU — reset & clock unit @ 0x3000_E000 (РП Приложение А.1;
 * offsets per research_clocks.md §1.1/§4.1, research_usb_power.md §3)
 *===========================================================================*/
#define RCU_BASE              0x3000E000

/* register offsets (asm-visible) */
#define RCU_CGCFGAHB_OFFSET   0x00
#define RCU_CGCFGAPB_OFFSET   0x08
#define RCU_RSTDISAHB_OFFSET  0x10
#define RCU_RSTDISAPB_OFFSET  0x18
#define RCU_RSTSTAT_OFFSET    0x20
#define RCU_SYSCLKCFG_OFFSET  0x30
#define RCU_CLKSTAT_OFFSET    0x3C
#define RCU_PLLSYSCFG0_OFFSET 0x50
#define RCU_PLLSYSCFG1_OFFSET 0x54
#define RCU_PLLSYSCFG2_OFFSET 0x58
#define RCU_PLLSYSCFG3_OFFSET 0x5C
#define RCU_PLLSYSSTAT_OFFSET 0x60
#define RCU_RSTSYS_OFFSET     0xC0

/* CGCFGAHB / RSTDISAHB: GPIO clock gate / reset disable, same bit layout
 * (РП §11: ports unclocked+in reset after power-up, enable both) */
#define RCU_CGCFGAHB_GPIOAEN  0x00000100
#define RCU_CGCFGAHB_GPIOBEN  0x00000200
#define RCU_CGCFGAHB_GPIOCEN  0x00000400
#define RCU_RSTDISAHB_GPIOAEN 0x00000100
#define RCU_RSTDISAHB_GPIOBEN 0x00000200
#define RCU_RSTDISAHB_GPIOCEN 0x00000400

/* RSTSTAT (+20h): reset cause, research_usb_power.md §3 */
#define RCU_RSTSTAT_POR       0x00000002
#define RCU_RSTSTAT_WDT       0x00000004
#define RCU_RSTSTAT_SYSRST    0x00000010

/* SYSCLKCFG (+30h): SRC[1:0]; reset 0 = HSI 1 MHz (research_clocks.md §4.1) */
#define RCU_SYSCLKCFG_SRC_Pos        0
#define RCU_SYSCLKCFG_SRC_Msk        0x3
#define RCU_SYSCLKCFG_SRC_HSICLK     0
#define RCU_SYSCLKCFG_SRC_HSECLK     1
#define RCU_SYSCLKCFG_SRC_SYSPLL0CLK 2
#define RCU_SYSCLKCFG_SRC_LSICLK     3

/* CLKSTAT (+3Ch): SRC[1:0] = currently active source */
#define RCU_CLKSTAT_SRC_Pos   0
#define RCU_CLKSTAT_SRC_Msk   0x3

/* PLLSYSCFG0 (+50h), field layout per research_clocks.md §1.1 */
#define RCU_PLLSYSCFG0_PLLEN      0x00000001
#define RCU_PLLSYSCFG0_BYP_Pos    1        /* [2:1] per-output bypass */
#define RCU_PLLSYSCFG0_BYP_Msk    0x00000006
#define RCU_PLLSYSCFG0_DACEN      0x00000008
#define RCU_PLLSYSCFG0_DSMEN      0x00000010
#define RCU_PLLSYSCFG0_FOUTEN_Pos 5        /* [6:5] per-output enable; bit5 = Fout0 */
#define RCU_PLLSYSCFG0_FOUTEN_Msk 0x00000060
#define RCU_PLLSYSCFG0_FOUTEN0    0x00000020
#define RCU_PLLSYSCFG0_REFDIV_Pos 7        /* [12:7] */
#define RCU_PLLSYSCFG0_REFDIV_Msk 0x00001F80
#define RCU_PLLSYSCFG0_PD0A_Pos   13       /* [15:13] */
#define RCU_PLLSYSCFG0_PD0A_Msk   0x0000E000
#define RCU_PLLSYSCFG0_PD0B_Pos   16       /* [21:16] */
#define RCU_PLLSYSCFG0_PD0B_Msk   0x003F0000
#define RCU_PLLSYSCFG0_PD1A_Pos   22       /* [24:22] */
#define RCU_PLLSYSCFG0_PD1B_Pos   25       /* [30:25] */

/* PLLSYSCFG2 (+58h): FBDIV[11:0] */
#define RCU_PLLSYSCFG2_FBDIV_Msk  0x00000FFF

/* PLLSYSCFG3 (+5Ch): REFSEL[24] "0 REFCLK / 1 SRCCLK" + DSKEW* calibration.
 * РП/SDK contradiction — SDK never writes it and works (research_clocks.md
 * §1.2).  Do not touch outside the P0 REFSEL experiment. */
#define RCU_PLLSYSCFG3_REFSEL     0x01000000

/* PLLSYSSTAT (+60h) */
#define RCU_PLLSYSSTAT_LOCK       0x00000001

/* RSTSYS (+C0h): soft reset, write KEY|RSTEN (research_usb_power.md §3) */
#define RCU_RSTSYS_RSTEN          0x00000001
#define RCU_RSTSYS_KEY_Pos        16
#define RCU_RSTSYS_KEY            0xA55A0000
#define RCU_RSTSYS_MAGIC          0xA55A0001  /* KEY | RSTEN: one write = reset */

/*===========================================================================
 * GPIO — ports A/B/C @ 0x2800_0000/1000/2000, identical 16-bit blocks
 * (РП §11 + А.6; ALL offsets per research_gpio.md §1)
 *===========================================================================*/
#define GPIOA_BASE            0x28000000
#define GPIOB_BASE            0x28001000
#define GPIOC_BASE            0x28002000

/* register offsets (asm-visible) */
#define GPIO_DATA_OFFSET        0x00  /* read after non-bypassable 2-clk sync */
#define GPIO_DATAOUT_OFFSET     0x04
#define GPIO_DATAOUTSET_OFFSET  0x08  /* W1 atomic set    */
#define GPIO_DATAOUTCLR_OFFSET  0x0C  /* W1 atomic clear  */
#define GPIO_DATAOUTTGL_OFFSET  0x10  /* W1 atomic toggle (write 0 = no-op)   */
#define GPIO_PULLMODE_OFFSET    0x20  /* 1 bit/pin, pull-up only              */
#define GPIO_OUTMODE_OFFSET     0x24  /* 2 bits/pin: 00 PP, 01 OD, 10 OS      */
#define GPIO_OUTENSET_OFFSET    0x2C  /* W1 output driver enable              */
#define GPIO_OUTENCLR_OFFSET    0x30  /* W1 output driver disable             */
#define GPIO_ALTFUNCSET_OFFSET  0x34
#define GPIO_ALTFUNCCLR_OFFSET  0x38
#define GPIO_ALTFUNCNUM_OFFSET  0x3C  /* 2 bits/pin: 0..3 = none/AF1/AF2/AF3  */
#define GPIO_SYNCSET_OFFSET     0x44  /* 0 = 2-clk sync (base), 1 = 4-clk     */
#define GPIO_SYNCCLR_OFFSET     0x48
#define GPIO_QUALSET_OFFSET     0x4C
#define GPIO_QUALCLR_OFFSET     0x50
#define GPIO_QUALMODESET_OFFSET 0x54
#define GPIO_QUALMODECLR_OFFSET 0x58
#define GPIO_QUALSAMPLE_OFFSET  0x5C
#define GPIO_INTENSET_OFFSET    0x60
#define GPIO_INTENCLR_OFFSET    0x64
#define GPIO_INTTYPESET_OFFSET  0x68  /* 0 = level, 1 = edge                  */
#define GPIO_INTTYPECLR_OFFSET  0x6C
#define GPIO_INTPOLSET_OFFSET   0x70  /* 0 = low/falling, 1 = high/rising     */
#define GPIO_INTPOLCLR_OFFSET   0x74
#define GPIO_INTEDGESET_OFFSET  0x78  /* both-edge mode (INTPOL ignored)      */
#define GPIO_INTEDGECLR_OFFSET  0x7C
#define GPIO_INTSTATUS_OFFSET   0x80  /* W1C, never cleared by hardware       */
#define GPIO_DMAREQSET_OFFSET   0x84
#define GPIO_DMAREQCLR_OFFSET   0x88
#define GPIO_ADCSOCSET_OFFSET   0x8C
#define GPIO_ADCSOCCLR_OFFSET   0x90
#define GPIO_LOCKKEY_OFFSET     0x9C  /* W: key; R: LOCKSTAT                  */
#define GPIO_LOCKSET_OFFSET     0xA0  /* NEVER on D+/D-: locked pins refuse   */
#define GPIO_LOCKCLR_OFFSET     0xA4  /*   DATAOUTx/OUTENx writes (PLAN Р5)   */
#define GPIO_MASKLB_OFFSET      0x400 /* +4*mask, bits 7:0 masked access      */
#define GPIO_MASKHB_OFFSET      0x800 /* +4*mask, bits 15:8 masked access     */

#define GPIO_LOCKKEY_UNLOCK     0xADEADBEE  /* research_gpio.md §1 (РП §11.6) */

/*===========================================================================
 * FLASH controller @ 0x3000_D000 (РП Приложение А.4; research_flash.md §1)
 *===========================================================================*/
#define FLASH_CTL_BASE        0x3000D000  /* "FLASH_BASE" of vendor header; renamed
                                           * to avoid clashing with WCH's FLASH_BASE
                                           * (flash memory base) in shared code */

#define FLASH_ADDR_OFFSET     0x00  /* 16-byte aligned (auto-aligned)  */
#define FLASH_DATA0_OFFSET    0x04  /* DATA0..3 = one 128-bit program unit */
#define FLASH_DATA1_OFFSET    0x08
#define FLASH_DATA2_OFFSET    0x0C
#define FLASH_DATA3_OFFSET    0x10
#define FLASH_TACCR_OFFSET    0x1C  /* clk per 20 ns  (reset 2 = 100 MHz base) */
#define FLASH_TNVSR_OFFSET    0x20  /* clk per 5 ms   */
#define FLASH_TERSR_OFFSET    0x24  /* clk per 100 ms (erase timebase) */
#define FLASH_TNVHR_OFFSET    0x28  /* clk per 5 us   */
#define FLASH_TNVH1R_OFFSET   0x2C  /* clk per 100 us */
#define FLASH_TRCVR_OFFSET    0x30  /* clk per 10 us  (recovery) */
#define FLASH_TPGSR_OFFSET    0x34  /* clk per 10 us  (program pulse) */
#define FLASH_CMD_OFFSET      0x44
#define FLASH_STAT_OFFSET     0x48
#define FLASH_CTRL_OFFSET     0x4C
#define FLASH_LP_OFFSET       0xC8

/* CMD (+44h): KEY[31:16] = C0DEh, one op bit at a time; sequence
 * ADDR -> DATA -> CMD -> >=5 NOP -> poll STAT.BUSY (research_flash.md §4) */
#define FLASH_CMD_KEY         0xC0DE0000
#define FLASH_CMD_RD          0x00000001
#define FLASH_CMD_WR          0x00000002
#define FLASH_CMD_ERSEC       0x00000004
#define FLASH_CMD_ALLSEC      0x00000008  /* full erase — address-guarded blobs
                                           * must never set this (PLAN Р8/R14) */
#define FLASH_CMD_NVRON       0x00000100

/* STAT (+48h): reads during BUSY return garbage, no fault */
#define FLASH_STAT_BUSY       0x00000001
#define FLASH_STAT_IRQF       0x00000002

/* CTRL (+4Ch, reset 1_0000h): РП documents ONLY LAT[18:16]; LAT=1 required
 * and sufficient @48 MHz/1.2 V and is the reset default (РП табл. 7.1). */
#define FLASH_CTRL_LAT_Pos    16
#define FLASH_CTRL_LAT_Msk    0x00070000  /* РП: 3 bits. SDK/SVD claim 4 bits
                                           * (0xF0000) — TODO(port): resolve on
                                           * hardware if LAT>7 ever needed. */
/* CEN/CFLUSH exist only in SDK/SVD, absent from РП (research_flash.md §1);
 * semantics unknown — P1.4 experiment material, do not set blindly. */
#define FLASH_CTRL_CEN        0x00000002  /* undocumented in РП */
#define FLASH_CTRL_CFLUSH     0x00000100  /* undocumented in РП, write-only */

#define FLASH_LP_LPEN         0x00000001

#define WG015_FLASH_PAGE_SIZE 4096

/*===========================================================================
 * PLIC (РП §9.2-9.5, табл. 9.2; research_core_irq.md §4)
 * claim = lw MICC, complete = sw MICC; EIP only when prio > threshold.
 *===========================================================================*/
#define PLIC_BASE             0x0C000000
#define PLIC_PRI_BASE         0x0C000000  /* PRI[n] = base + 4*n, n = 1..31, prio 0..7 (0 = off) */
#define PLIC_IPM0_ADDR        0x0C001000  /* pending, sources 1..31, RO */
#define PLIC_MIEM0_ADDR       0x0C002000  /* M-mode per-source enable mask */
#define PLIC_MTHR_ADDR        0x0C200000  /* M-mode priority threshold */
#define PLIC_MICC_ADDR        0x0C200004  /* M-mode claim/complete */

/* Vector numbers (РП табл. 9.1) — only what this port touches. */
#define WG015_IRQ_GPIO        5   /* ONE shared line for ports A+B+C */

/*===========================================================================
 * CLINT (РП §9.1 табл. 9.1; research_core_irq.md §2)
 *===========================================================================*/
#define CLINT_BASE            0x02000000
#define CLINT_MTIMECMP_ADDR   0x02004000
#define CLINT_MTIME_ADDR      0x0200BFF8  /* frequency undocumented in РП; SDK
                                           * implies = SYSCLK. Prefer rdcycle. */

/*===========================================================================
 * PMURTC user registers (РП Приложение А.3; research_usb_power.md §3)
 * RTC_REG[0..15] @ 0x38011000 + 0x20 + 4n survive soft/WDT/pin reset.
 *===========================================================================*/
#define PMURTC_BASE           0x38011000
#define PMURTC_RTC_REG_OFFSET 0x20
#define PMURTC_RTC_REG_ADDR(n) (PMURTC_BASE + PMURTC_RTC_REG_OFFSET + 4 * (n))
/* PLAN F8: RTC_REG[14] is known-bad on tested silicon — do not use it. */

/*===========================================================================
 * C-only part: register structs + peripheral pointers
 *===========================================================================*/
#ifndef __ASSEMBLER__

#include <stdint.h>
#include <stddef.h>

/* RCU (offsets per research_clocks.md §1.1/§4.1; UART/SPI/ADC/WDOG/CLKOUT
 * config registers between +64h and +BCh intentionally omitted) */
typedef struct
{
	volatile uint32_t CGCFGAHB;       /* +0x00 AHB clock gates  */
	volatile uint32_t _r0;
	volatile uint32_t CGCFGAPB;       /* +0x08 APB clock gates  */
	volatile uint32_t _r1;
	volatile uint32_t RSTDISAHB;      /* +0x10 AHB reset disable */
	volatile uint32_t _r2;
	volatile uint32_t RSTDISAPB;      /* +0x18 APB reset disable */
	volatile uint32_t _r3;
	volatile uint32_t RSTSTAT;        /* +0x20 reset cause      */
	volatile uint32_t _r4[3];
	volatile uint32_t SYSCLKCFG;      /* +0x30 sysclk source select */
	volatile uint32_t SECCNT0;        /* +0x34 */
	volatile uint32_t SECCNT1;        /* +0x38 */
	volatile uint32_t CLKSTAT;        /* +0x3C actual sysclk source */
	volatile uint32_t INTEN;          /* +0x40 */
	volatile uint32_t INTSTAT;        /* +0x44 */
	volatile uint32_t _r5[2];
	volatile uint32_t PLLSYSCFG0;     /* +0x50 */
	volatile uint32_t PLLSYSCFG1;     /* +0x54 FRAC[23:0] */
	volatile uint32_t PLLSYSCFG2;     /* +0x58 FBDIV[11:0] */
	volatile uint32_t PLLSYSCFG3;     /* +0x5C REFSEL[24] + DSKEW* */
	volatile uint32_t PLLSYSSTAT;     /* +0x60 LOCK[0] */
	volatile uint32_t _r6[23];
	volatile uint32_t RSTSYS;         /* +0xC0 KEY A55Ah | RSTEN */
} RCU_TypeDef;

/* GPIO (РП А.6; research_gpio.md §1) */
typedef struct
{
	volatile uint32_t DATA;           /* +0x00 */
	volatile uint32_t DATAOUT;        /* +0x04 */
	volatile uint32_t DATAOUTSET;     /* +0x08 */
	volatile uint32_t DATAOUTCLR;     /* +0x0C */
	volatile uint32_t DATAOUTTGL;     /* +0x10 */
	volatile uint32_t _r0[3];
	volatile uint32_t PULLMODE;       /* +0x20 */
	volatile uint32_t OUTMODE;        /* +0x24 */
	volatile uint32_t _r1;
	volatile uint32_t OUTENSET;       /* +0x2C */
	volatile uint32_t OUTENCLR;       /* +0x30 */
	volatile uint32_t ALTFUNCSET;     /* +0x34 */
	volatile uint32_t ALTFUNCCLR;     /* +0x38 */
	volatile uint32_t ALTFUNCNUM;     /* +0x3C */
	volatile uint32_t _r2;
	volatile uint32_t SYNCSET;        /* +0x44 */
	volatile uint32_t SYNCCLR;        /* +0x48 */
	volatile uint32_t QUALSET;        /* +0x4C */
	volatile uint32_t QUALCLR;        /* +0x50 */
	volatile uint32_t QUALMODESET;    /* +0x54 */
	volatile uint32_t QUALMODECLR;    /* +0x58 */
	volatile uint32_t QUALSAMPLE;     /* +0x5C */
	volatile uint32_t INTENSET;       /* +0x60 */
	volatile uint32_t INTENCLR;       /* +0x64 */
	volatile uint32_t INTTYPESET;     /* +0x68 */
	volatile uint32_t INTTYPECLR;     /* +0x6C */
	volatile uint32_t INTPOLSET;      /* +0x70 */
	volatile uint32_t INTPOLCLR;      /* +0x74 */
	volatile uint32_t INTEDGESET;     /* +0x78 */
	volatile uint32_t INTEDGECLR;     /* +0x7C */
	volatile uint32_t INTSTATUS;      /* +0x80 W1C */
	volatile uint32_t DMAREQSET;      /* +0x84 */
	volatile uint32_t DMAREQCLR;      /* +0x88 */
	volatile uint32_t ADCSOCSET;      /* +0x8C */
	volatile uint32_t ADCSOCCLR;      /* +0x90 */
	volatile uint32_t _r3[2];
	volatile uint32_t LOCKKEY;        /* +0x9C W: key ADEADBEEh; R: LOCKSTAT */
	volatile uint32_t LOCKSET;        /* +0xA0 */
	volatile uint32_t LOCKCLR;        /* +0xA4 */
	volatile uint32_t _r4[214];
	volatile uint32_t MASKLB[256];    /* +0x400 masked access, bits 7:0
	                                   * (read semantics undocumented — GAP) */
	volatile uint32_t MASKHB[256];    /* +0x800 masked access, bits 15:8 */
} GPIO_TypeDef;

/* FLASH controller (РП А.4; research_flash.md §1) */
typedef struct
{
	volatile uint32_t ADDR;           /* +0x00 */
	volatile uint32_t DATA[4];        /* +0x04..0x10, 128-bit program unit
	                                   * (SDK loops 16 words — unit 16 vs 64 B
	                                   * needs hardware test, PLAN R6) */
	volatile uint32_t _r0[2];
	volatile uint32_t TACCR;          /* +0x1C — timing regs write-locked while BUSY */
	volatile uint32_t TNVSR;          /* +0x20 */
	volatile uint32_t TERSR;          /* +0x24 */
	volatile uint32_t TNVHR;          /* +0x28 */
	volatile uint32_t TNVH1R;         /* +0x2C */
	volatile uint32_t TRCVR;          /* +0x30 */
	volatile uint32_t TPGSR;          /* +0x34 */
	volatile uint32_t _r1[3];
	volatile uint32_t CMD;            /* +0x44 */
	volatile uint32_t STAT;           /* +0x48 */
	volatile uint32_t CTRL;           /* +0x4C */
	volatile uint32_t _r2[30];
	volatile uint32_t LP;             /* +0xC8 LPEN[0] (SVD only) */
} WG015_FLASH_TypeDef;

#define RCU     ((RCU_TypeDef *)RCU_BASE)
#define GPIOA   ((GPIO_TypeDef *)GPIOA_BASE)
#define GPIOB   ((GPIO_TypeDef *)GPIOB_BASE)
#define GPIOC   ((GPIO_TypeDef *)GPIOC_BASE)
/* Named WG015_FLASH (not FLASH) so the V003 code paths in shared TUs, which
 * reference WCH's FLASH, fail loudly instead of poking the wrong chip. */
#define WG015_FLASH ((WG015_FLASH_TypeDef *)FLASH_CTL_BASE)

/* PLIC / CLINT / PMURTC accessors (plain MMIO words, no structs needed) */
#define PLIC_PRI(n)      (*(volatile uint32_t *)(PLIC_PRI_BASE + 4 * (n)))
#define PLIC_IPM0        (*(volatile uint32_t *)PLIC_IPM0_ADDR)
#define PLIC_MIEM0       (*(volatile uint32_t *)PLIC_MIEM0_ADDR)
#define PLIC_MTHR        (*(volatile uint32_t *)PLIC_MTHR_ADDR)
#define PLIC_MICC        (*(volatile uint32_t *)PLIC_MICC_ADDR)
#define CLINT_MTIME      (*(volatile uint32_t *)CLINT_MTIME_ADDR)
#define PMURTC_RTC_REG   ((volatile uint32_t *)(PMURTC_BASE + PMURTC_RTC_REG_OFFSET))

/* Struct layout self-checks against the researched offsets. */
_Static_assert( offsetof( RCU_TypeDef, RSTSTAT )    == 0x20, "RCU layout" );
_Static_assert( offsetof( RCU_TypeDef, SYSCLKCFG )  == 0x30, "RCU layout" );
_Static_assert( offsetof( RCU_TypeDef, CLKSTAT )    == 0x3C, "RCU layout" );
_Static_assert( offsetof( RCU_TypeDef, PLLSYSCFG0 ) == 0x50, "RCU layout" );
_Static_assert( offsetof( RCU_TypeDef, PLLSYSSTAT ) == 0x60, "RCU layout" );
_Static_assert( offsetof( RCU_TypeDef, RSTSYS )     == 0xC0, "RCU layout" );
_Static_assert( offsetof( GPIO_TypeDef, DATAOUTTGL ) == 0x10, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, PULLMODE )   == 0x20, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, OUTENSET )   == 0x2C, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, SYNCSET )    == 0x44, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, QUALSAMPLE ) == 0x5C, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, INTSTATUS )  == 0x80, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, LOCKKEY )    == 0x9C, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, MASKLB )     == 0x400, "GPIO layout" );
_Static_assert( offsetof( GPIO_TypeDef, MASKHB )     == 0x800, "GPIO layout" );
_Static_assert( offsetof( WG015_FLASH_TypeDef, TACCR ) == 0x1C, "FLASH layout" );
_Static_assert( offsetof( WG015_FLASH_TypeDef, TPGSR ) == 0x34, "FLASH layout" );
_Static_assert( offsetof( WG015_FLASH_TypeDef, CMD )   == 0x44, "FLASH layout" );
_Static_assert( offsetof( WG015_FLASH_TypeDef, CTRL )  == 0x4C, "FLASH layout" );
_Static_assert( offsetof( WG015_FLASH_TypeDef, LP )    == 0xC8, "FLASH layout" );

#endif /* !__ASSEMBLER__ */

#endif /* _K1921VG015_MIN_H */
