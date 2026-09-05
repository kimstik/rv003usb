/* engine16_native.c — competitor NATIVE, peripheral configuration.
 *
 * This is C and not assembly on purpose: it is executed once at init, nothing
 * in it is timed, and its whole content is register values. Writing it in
 * assembly would hide the one thing that matters — which bit goes where and on
 * whose authority.
 *
 * Every constant below carries its source. Headers are
 * IOsetting/py32f0-template @289ffc8,
 * Libraries/CMSIS/Device/PY32F0xx/Include/py32f030x8.h; RM is PY32F030
 * Reference Manual V1.7. Register offsets are identical on F003x4/x6 and
 * F030x6/x8 (BUILD_FACTS.md §7 verified the GPIO block; the DMA/TIM/SYSCFG
 * bases are compared here across py32f003x6.h and py32f030x8.h).
 *
 * PY32F002Bx5 cannot run this: py32f002bx5.h defines neither DMA1_BASE nor
 * TIM3_BASE. That part takes the software engine. See engine16_native.md §8.
 *
 * Compiles with:
 *   arm-none-eabi-gcc -mcpu=cortex-m0plus -mthumb -Os -Wall -c
 */

#include <stdint.h>

/* --------------------------------------------------------------- addresses */
/* py32f030x8.h:512-513, py32f003x6.h:496-497 */
#define DMA1_BASE               0x40020000u
#define DMA1_CH1_BASE           (DMA1_BASE + 0x08u)
/* py32f030x8.h:487 (APBPERIPH_BASE 0x40000000 + 0x400) */
#define TIM3_BASE               0x40000400u
/* py32f030x8.h:498 */
#define SYSCFG_BASE             0x40010000u
/* RM Table 3-2: GPIOB on the I/O PORT bus */
#define GPIOB_BASE              0x50000400u
#define RCC_BASE                0x40021000u

#define REG(a)                  (*(volatile uint32_t *)(a))

/* RCC: py32f030x8.h:3252 (AHBENR.DMAEN bit 0), :3266 (APBENR1.TIM3EN bit 1),
 * :3295 (APBENR2.SYSCFGEN bit 0). Offsets per RM RCC register map. */
#define RCC_AHBENR              REG(RCC_BASE + 0x38u)
#define RCC_APBENR1             REG(RCC_BASE + 0x3Cu)
#define RCC_APBENR2             REG(RCC_BASE + 0x40u)
#define RCC_AHBENR_DMAEN        (1u << 0)
#define RCC_APBENR1_TIM3EN      (1u << 1)
#define RCC_APBENR2_SYSCFGEN    (1u << 0)

/* GPIO, BUILD_FACTS.md §7: MODER 0x00, AFR[2] 0x20-0x24, IDR 0x10 */
#define GPIO_MODER(b)           REG((b) + 0x00u)
#define GPIO_AFR(b, i)          REG((b) + 0x20u + 4u * (i))

/* TIM, py32f030x8.h TIM_TypeDef field order */
#define TIM_CR1(b)              REG((b) + 0x00u)
#define TIM_SMCR(b)             REG((b) + 0x08u)
#define TIM_DIER(b)             REG((b) + 0x0Cu)
#define TIM_SR(b)               REG((b) + 0x10u)
#define TIM_CCMR1(b)            REG((b) + 0x18u)
#define TIM_CCER(b)             REG((b) + 0x20u)
#define TIM_PSC(b)              REG((b) + 0x28u)
#define TIM_ARR(b)              REG((b) + 0x2Cu)
#define TIM_CCR1(b)             ((b) + 0x34u)

/* DMA channel, py32f030x8.h DMA_Channel_TypeDef */
#define DMA_CCR(c)              REG((c) + 0x00u)
#define DMA_CNDTR(c)            REG((c) + 0x04u)
#define DMA_CPAR(c)             REG((c) + 0x08u)
#define DMA_CMAR(c)             REG((c) + 0x0Cu)

/* SYSCFG_CFGR3, py32f030x8.h:3700-3735 */
#define SYSCFG_CFGR3            REG(SYSCFG_BASE + 0x1Cu)
#define SYSCFG_CFGR3_DMA1_MAP   (0x1Fu << 0)

/* ------------------------------------------------------------- bit values  */
/* py32f030x8.h:3974-4035, :4133-4153 */
#define TIM_CCMR1_CC1S_TI1      (1u << 0)       /* CC1S = 01                  */
#define TIM_CCER_CC1E           (1u << 0)
#define TIM_CCER_CC1P           (1u << 1)
#define TIM_CCER_CC1NP          (1u << 3)
/* RM TIM CCER: CC1NP:CC1P = 11 -> "non-inverted/both edges ... sensitive to
 * both TIxFP1 rising and falling edges (capture or trigger operations in
 * reset, external clock or trigger mode)". This is the fact the whole design
 * rests on: one channel timestamps every NRZI transition. */
#define TIM_CCER_CC1_BOTH_EDGES (TIM_CCER_CC1P | TIM_CCER_CC1NP)

/* py32f030x8.h:3819-3835 */
#define TIM_SMCR_SMS_RESET      (4u << 0)       /* SMS = 100, reset mode      */
#define TIM_SMCR_TS_TI1FP1      (5u << 4)       /* TS  = 101                  */
/* py32f030x8.h:3890-3895 */
#define TIM_DIER_CC1DE          (1u << 9)
#define TIM_CR1_CEN             (1u << 0)

/* py32f030x8.h:992-1028 */
#define DMA_CCR_EN              (1u << 0)
#define DMA_CCR_CIRC            (1u << 5)
#define DMA_CCR_MINC            (1u << 7)
#define DMA_CCR_PSIZE_16        (1u << 8)       /* PSIZE = 01                 */
#define DMA_CCR_MSIZE_8         (0u << 10)      /* MSIZE = 00                 */
#define DMA_CCR_PL_VERYHIGH     (3u << 12)

/* py32f0xx_ll_system.h:186 —
 * LL_SYSCFG_DMA_MAP_TIM3_CH1 = SYSCFG_CFGR3_DMA1_MAP_4 | SYSCFG_CFGR3_DMA1_MAP_1
 * = 0x10 | 0x02 = 0x12 */
#define SYSCFG_DMA_MAP_TIM3_CH1 0x12u

/* --------------------------------------------------------------- the port  */
/* D- must sit on a pin carrying a TIM3_CHx alternate function. On F030 the
 * candidates are PA2/PA6 (TIM3_CH1), PB4 (TIM3_CH1), PB5 (TIM3_CH2), PA3
 * (TIM1_CH1), PB3 (TIM1_CH2) — PY32F030 DS V1.8 pin/AF table. This is a real
 * new board constraint that the software engines do not impose. */
#ifndef USB_DM_PIN
#define USB_DM_PIN              4               /* PB4 = TIM3_CH1            */
#endif
#ifndef USB_DM_AF
#define USB_DM_AF               1               /* AF index from the DS table
                                                 * for THIS pin — check it per
                                                 * package before believing it */
#endif

extern uint8_t native_ring[];
#define NATIVE_RING_LEN         112

void native_acquire_init(void)
{
	uint32_t i;

	/* clocks (py32f030x8.h:3252, :3266, :3295) */
	RCC_AHBENR  |= RCC_AHBENR_DMAEN;
	RCC_APBENR1 |= RCC_APBENR1_TIM3EN;
	RCC_APBENR2 |= RCC_APBENR2_SYSCFGEN;

	/* D- to alternate function, timer input */
	GPIO_MODER(GPIOB_BASE) =
		(GPIO_MODER(GPIOB_BASE) & ~(3u << (2 * USB_DM_PIN)))
		| (2u << (2 * USB_DM_PIN));                     /* MODER = 10 */
	GPIO_AFR(GPIOB_BASE, USB_DM_PIN >> 3) =
		(GPIO_AFR(GPIOB_BASE, USB_DM_PIN >> 3)
			& ~(0xFu << (4 * (USB_DM_PIN & 7))))
		| ((uint32_t)USB_DM_AF << (4 * (USB_DM_PIN & 7)));

	/* the sentinel: a zero byte means "no capture here yet". The shortest
	 * legal interval is one bit time = 16 counts, so 0 is unreachable
	 * in-packet and costs nothing to test. */
	for (i = 0; i < NATIVE_RING_LEN; i++)
		native_ring[i] = 0;

	/* TIM3: capture BOTH edges of D- on channel 1, and let that same edge
	 * reset the counter. RM "PWM input mode" (Figure 18-27) establishes that
	 * the capture latches the pre-reset value, so CCR1 holds the INTERVAL
	 * since the previous transition, not an absolute timestamp.
	 *
	 * Consequences, all of which the decoder depends on:
	 *   - no timestamp subtraction in software
	 *   - no 16-bit wrap arithmetic, ever
	 *   - values <= 128 in-packet, so MSIZE=8 makes the ring 1 byte/event
	 *   - quantisation is against the local interval, so clock offset never
	 *     accumulates: every transition is a fresh phase reference
	 */
	TIM_CR1(TIM3_BASE)   = 0;
	TIM_PSC(TIM3_BASE)   = 0;               /* 24 MHz, 1 count = 1 cycle  */
	TIM_ARR(TIM3_BASE)   = 0xFFFFu;
	TIM_CCMR1(TIM3_BASE) = TIM_CCMR1_CC1S_TI1;      /* IC1F = 0, no filter:
	                                                 * a filter would cost
	                                                 * resolution we need   */
	TIM_CCER(TIM3_BASE)  = TIM_CCER_CC1E | TIM_CCER_CC1_BOTH_EDGES;
	TIM_SMCR(TIM3_BASE)  = TIM_SMCR_TS_TI1FP1 | TIM_SMCR_SMS_RESET;
	TIM_DIER(TIM3_BASE)  = TIM_DIER_CC1DE;
	TIM_SR(TIM3_BASE)    = 0;

	/* route TIM3_CH1's DMA request to DMA1 channel 1 (H-3/H-4) */
	SYSCFG_CFGR3 = (SYSCFG_CFGR3 & ~SYSCFG_CFGR3_DMA1_MAP)
		     | SYSCFG_DMA_MAP_TIM3_CH1;

	/* DMA1 channel 1: TIM3->CCR1 (APB, reachable) -> ring (SRAM).
	 * PSIZE=16 / MSIZE=8 is the documented truncating transfer of RM
	 * §11.3.4 Table 11-1: the low byte of CCR1 is stored, which is the
	 * interval, because the interval is always <= 128 inside a packet.
	 *
	 * Circular mode with CNDTR = RING_LEN is also the buffer bound: the
	 * only writer is hardware and it cannot leave the ring. There is no
	 * analogue here of the bus-reachable overrun in DEFECTS_VERIFIED D-2. */
	DMA_CCR(DMA1_CH1_BASE)   = 0;
	DMA_CPAR(DMA1_CH1_BASE)  = TIM_CCR1(TIM3_BASE);
	DMA_CMAR(DMA1_CH1_BASE)  = (uint32_t)native_ring;
	DMA_CNDTR(DMA1_CH1_BASE) = NATIVE_RING_LEN;
	DMA_CCR(DMA1_CH1_BASE)   = DMA_CCR_MINC | DMA_CCR_CIRC
				 | DMA_CCR_PSIZE_16 | DMA_CCR_MSIZE_8
				 | DMA_CCR_PL_VERYHIGH | DMA_CCR_EN;
	/* DIR = 0: peripheral -> memory. ACKLVL in SYSCFG_CFGR3 is left at 0;
	 * the RM documents it only as "response speed enable bit", so setting
	 * it is a bench experiment, not a design assumption. */

	TIM_CR1(TIM3_BASE) = TIM_CR1_CEN;
	/* From here the bus is being timestamped with zero CPU involvement, and
	 * stays so forever. Interrupt entry latency, its jitter, and where the
	 * handler executes from all stop affecting what is received. */
}

/* How many transitions the DMA has written but the decoder has not read.
 * Used only by the tail, never in the loop — reading CNDTR inside the loop
 * costs an AHB access per event and is what made a batched decoder lose to a
 * sentinel-polled one (engine16_native.md §6.3). */
uint32_t native_pending(const uint8_t *read_ptr)
{
	uint32_t written = NATIVE_RING_LEN - DMA_CNDTR(DMA1_CH1_BASE);
	uint32_t consumed = (uint32_t)(read_ptr - native_ring);

	return written - consumed;
}

/* Over-capture: the DMA failed to read CCR1 before the next transition. One
 * interval is then lost and the packet decodes to a CRC failure, which the
 * host retries. Non-silent, and the gate for the DMA-rate risk of §3.4. */
int native_overcapture(void)
{
	return (TIM_SR(TIM3_BASE) & (1u << 9)) != 0;    /* CC1OF */
}
