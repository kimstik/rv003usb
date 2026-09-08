/* ch32fun.h - HAL SHIM for tools/usb_enum_sim.py ONLY.
 *
 * rv003usb.c is a CH32V003 source: it includes <ch32fun.h> for the vendor
 * register map and calls into it from usb_setup() and from the reboot
 * feature-report path.  Neither is on any path this simulation exercises -
 * usb_setup() configures GPIO/EXTI on a part that does not exist here, and
 * the reboot path needs ist->reboot_armed == 2, which only a host that sends
 * the 0xFD feature report can produce.
 *
 * So this header supplies exactly the declarations those two blocks need,
 * and NOTHING ELSE.  Every peripheral is a struct over a scratch page in the
 * emulator's RAM: a write lands somewhere harmless and a read gives zero.
 * The FIVE functions the simulation actually exercises - usb_pid_handle_
 * {setup,data,in,out,ack} - touch none of it.  If a future change makes them
 * touch a register, this file will not silently absorb it: the address is a
 * plain RAM page, and the harness prints any write to it.
 */
#ifndef _CH32FUN_SHIM_H
#define _CH32FUN_SHIM_H
#include <stdint.h>

#define SIM_PERIPH_BASE 0x20003000u

typedef struct { volatile uint32_t r[64]; } SIM_BLOCK;

#define RCC     ((SIM_RCC_T *)(SIM_PERIPH_BASE + 0x000))
#define AFIO    ((SIM_AFIO_T *)(SIM_PERIPH_BASE + 0x100))
#define EXTI    ((SIM_EXTI_T *)(SIM_PERIPH_BASE + 0x200))
#define GPIOA   ((SIM_GPIO_T *)(SIM_PERIPH_BASE + 0x300))
#define GPIOC   ((SIM_GPIO_T *)(SIM_PERIPH_BASE + 0x340))
#define GPIOD   ((SIM_GPIO_T *)(SIM_PERIPH_BASE + 0x380))
#define FLASH   ((SIM_FLASH_T *)(SIM_PERIPH_BASE + 0x400))
#define PFIC    ((SIM_PFIC_T *)(SIM_PERIPH_BASE + 0x500))
#define TIM1    ((SIM_TIM_T *)(SIM_PERIPH_BASE + 0x600))

typedef struct { volatile uint32_t APB2PCENR, CFGR0, RSTSCKR; } SIM_RCC_T;
typedef struct { volatile uint32_t EXTICR; } SIM_AFIO_T;
typedef struct { volatile uint32_t INTENR, FTENR; } SIM_EXTI_T;
typedef struct { volatile uint32_t CFGLR, CFGHR, INDR, OUTDR, BSHR, BCR, LCKR; } SIM_GPIO_T;
typedef struct { volatile uint32_t BOOT_MODEKEYR, STATR, CTLR; } SIM_FLASH_T;
typedef struct { volatile uint32_t SCTLR; } SIM_PFIC_T;
typedef struct { volatile uint32_t PSC, ATRLR, CH3CVR, SWEVGR, CCER, CHCTLR2, BDTR, CTLR1; } SIM_TIM_T;

#define RCC_APB2Periph_GPIOA 0x04
#define RCC_APB2Periph_GPIOC 0x10
#define RCC_APB2Periph_GPIOD 0x20
#define RCC_APB2Periph_AFIO  0x01
#define GPIO_PortSourceGPIOA 0
#define GPIO_PortSourceGPIOC 2
#define GPIO_PortSourceGPIOD 3
#define GPIO_Speed_In        0
#define GPIO_CNF_IN_FLOATING 4
#define GPIO_CFGLR_OUT_50Mhz_PP 3
#define GPIO_CFGLR_OUT_50Mhz_AF_PP 0xB
#define FLASH_KEY1 0x45670123
#define FLASH_KEY2 0xCDEF89AB
#define CR_LOCK_Set 0x00008080
#define EXTI7_0_IRQn 20
static inline void NVIC_EnableIRQ(int n) { (void)n; }
static inline void SystemInit(void) {}
static inline void Delay_Ms(int n) { (void)n; }

#endif
