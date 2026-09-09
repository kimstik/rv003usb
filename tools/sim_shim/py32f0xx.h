/* py32f0xx.h - HAL SHIM for tools/hsical_sim.py ONLY.
 *
 * rv003usb/py32/py32_hsical.c includes <py32f0xx.h> for exactly two things:
 * the RCC->ICSCR actuator and the SysTick reference counter.  The vendor
 * CMSIS tree is not vendored into this repository, so this file supplies
 * those two register blocks and NOTHING ELSE.  Every value below is copied
 * from the vendor headers, with the file and line it came from, so that a
 * mismatch is a review question and not a silent difference between what the
 * simulation runs and what the firmware runs:
 *
 *   Puya PY32F0xx Firmware Library (py32f0-template),
 *   Libraries/CMSIS/Device/PY32F0xx/Include/py32f002bx5.h
 *     :309-310  RCC_TypeDef { CR @0x00; ICSCR @0x04; ... }
 *     :433      RCC_BASE = AHBPERIPH_BASE + 0x1000 = 0x40021000
 *     :472      #define RCC ((RCC_TypeDef *)RCC_BASE)
 *     :2241-42  RCC_ICSCR_HSI_TRIM  bits [12:0]
 *     :2257-58  RCC_ICSCR_HSI_FS    bits [15:13]
 *   (py32f003x4.h:2840-2841 and py32f030x6.h:2978-2979 are identical for
 *   HSI_TRIM, which is why py32_hsical.c cites all three.)
 *
 *   Libraries/CMSIS/Core/Include/core_cm0plus.h
 *     SysTick_Type { CTRL, LOAD, VAL, CALIB }, SysTick_BASE = SCS_BASE+0x10,
 *     SCS_BASE = 0xE000E000, so VAL is 0xE000E018 - which is the literal
 *     py32_hsical.h:PY32_HSICAL_TICK_ADDR and engine16_merged.S both use.
 *     ENABLE = bit 0, TICKINT = bit 1, CLKSOURCE = bit 2, RELOAD = 0xFFFFFF.
 *
 * These addresses are what tools/hsical_sim.py maps as MMIO.  If this file
 * and the harness ever disagree the harness's self-check fails: it asserts
 * that the servo's first ICSCR read lands on 0x40021004.
 */
#ifndef _PY32F0XX_SHIM_H
#define _PY32F0XX_SHIM_H
#include <stdint.h>

#define __IO volatile

/* ---- RCC (py32f002bx5.h:305-330, truncated at ICSCR - nothing past it is
 * referenced by py32_hsical.c, and a short struct cannot mis-address a
 * field that does not exist). */
typedef struct {
	__IO uint32_t CR;          /* 0x00 */
	__IO uint32_t ICSCR;       /* 0x04 */
} RCC_TypeDef;

#define AHBPERIPH_BASE   0x40020000UL
#define RCC_BASE         (AHBPERIPH_BASE + 0x00001000UL)   /* 0x40021000 */
#define RCC              ((RCC_TypeDef *)RCC_BASE)

#define RCC_ICSCR_HSI_TRIM_Pos   (0U)
#define RCC_ICSCR_HSI_TRIM_Msk   (0x1FFFUL << RCC_ICSCR_HSI_TRIM_Pos)
#define RCC_ICSCR_HSI_TRIM       RCC_ICSCR_HSI_TRIM_Msk
#define RCC_ICSCR_HSI_FS_Pos     (13U)
#define RCC_ICSCR_HSI_FS_Msk     (0x7UL << RCC_ICSCR_HSI_FS_Pos)
#define RCC_ICSCR_HSI_FS         RCC_ICSCR_HSI_FS_Msk

/* ---- SysTick (core_cm0plus.h) */
typedef struct {
	__IO uint32_t CTRL;        /* 0x00 */
	__IO uint32_t LOAD;        /* 0x04 */
	__IO uint32_t VAL;         /* 0x08 */
	__IO uint32_t CALIB;       /* 0x0C */
} SysTick_Type;

#define SCS_BASE                 0xE000E000UL
#define SysTick_BASE             (SCS_BASE + 0x0010UL)
#define SysTick                  ((SysTick_Type *)SysTick_BASE)

#define SysTick_CTRL_ENABLE_Pos      0U
#define SysTick_CTRL_ENABLE_Msk      (1UL << SysTick_CTRL_ENABLE_Pos)
#define SysTick_CTRL_TICKINT_Pos     1U
#define SysTick_CTRL_TICKINT_Msk     (1UL << SysTick_CTRL_TICKINT_Pos)
#define SysTick_CTRL_CLKSOURCE_Pos   2U
#define SysTick_CTRL_CLKSOURCE_Msk   (1UL << SysTick_CTRL_CLKSOURCE_Pos)
#define SysTick_CTRL_COUNTFLAG_Pos   16U
#define SysTick_CTRL_COUNTFLAG_Msk   (1UL << SysTick_CTRL_COUNTFLAG_Pos)
#define SysTick_LOAD_RELOAD_Pos      0U
#define SysTick_LOAD_RELOAD_Msk      (0xFFFFFFUL << SysTick_LOAD_RELOAD_Pos)
#define SysTick_VAL_CURRENT_Msk      (0xFFFFFFUL)

#endif /* _PY32F0XX_SHIM_H */
