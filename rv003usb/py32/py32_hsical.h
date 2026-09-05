/* py32_hsical.h - crystal-less HSI trim against the USB host keep-alive.
 *
 * Public domain / CC0, as is the AVR original it is modelled on
 * (micronucleus firmware/osccal.S, "Ralph Doncaster 2020").
 *
 * See py32_hsical.c for the method, the seam contract and the open items.
 */
#ifndef PY32_HSICAL_H
#define PY32_HSICAL_H

/* ---- build switches ---------------------------------------------------- */

/* 0 => every entry point below becomes an empty inline and py32_hsical.c
 * compiles to an empty translation unit.  Nothing is linked in. */
#ifndef PY32_HSICAL_ENABLE
#define PY32_HSICAL_ENABLE 1
#endif

/* Target core clock.  The engine needs an integer 16 cycles per low-speed
 * bit, so 24 MHz exactly; a 48 MHz build sets this to 48000000. */
#ifndef PY32_HSICAL_FCPU
#define PY32_HSICAL_FCPU 24000000u
#endif

/* Cycles the host's 1.000 ms +-0.05% frame interval should take. */
#define PY32_HSICAL_TARGET (PY32_HSICAL_FCPU / 1000u)

/* Cycles-per-frame moved by one HSI_TRIM_L step at PY32_HSICAL_FCPU.
 * Only the loop gain depends on this; see py32_hsical.c "gain". */
#ifndef PY32_HSICAL_CYC_PER_STEP
#define PY32_HSICAL_CYC_PER_STEP 20
#endif

/* Dead band, in cycles of error, inside which no trim write happens. */
#ifndef PY32_HSICAL_DEADBAND
#define PY32_HSICAL_DEADBAND 16
#endif

/* Largest trim change applied from a single frame measurement. */
#ifndef PY32_HSICAL_MAX_STEP
#define PY32_HSICAL_MAX_STEP 64
#endif

/* +1 if HSI_TRIM_L rising raises the frequency (measured: it does, see .c). */
#ifndef PY32_HSICAL_TRIM_SIGN
#define PY32_HSICAL_TRIM_SIGN (+1)
#endif

/* 1 => when TRIM_L saturates, step the coarse TRIM_H field and re-centre. */
#ifndef PY32_HSICAL_COARSE
#define PY32_HSICAL_COARSE 1
#endif

/* 1 => py32_hsical_init() may program SysTick.  Set 0 if the port already
 * runs SysTick free-running from HCLK with LOAD = 0xFFFFFF. */
#ifndef PY32_HSICAL_OWN_SYSTICK
#define PY32_HSICAL_OWN_SYSTICK 1
#endif

/* ---- state ------------------------------------------------------------- */

#define PY32_HSICAL_IDLE    0   /* no usable measurement yet (host absent?) */
#define PY32_HSICAL_ACQUIRE 1   /* measuring and correcting */
#define PY32_HSICAL_LOCKED  2   /* error has been inside the dead band */

#if PY32_HSICAL_ENABLE

#include <stdint.h>

/* SysTick VAL, for engine .S files that cannot include CMSIS.
 * SCS_BASE 0xE000E000 + SysTick_BASE offset 0x10 (core_cm0plus.h:649)
 * + VAL offset 0x08 (SysTick_Type: CTRL, LOAD, VAL, CALIB). */
#define PY32_HSICAL_TICK_ADDR 0xE000E018u

/* One 24-bit down-count sample.  From C this is ldr-literal + ldr. */
static inline uint32_t py32_hsical_stamp(void)
{
	return *(volatile uint32_t *)PY32_HSICAL_TICK_ADDR;
}

void py32_hsical_init(void);

/* THE SEAM.  Call once per USB interrupt, at ISR *exit*, passing the SysTick
 * sample taken at ISR *entry*.  See py32_hsical.c for the full contract. */
void py32_hsical_event(uint32_t stamp);

/* PY32_HSICAL_IDLE / _ACQUIRE / _LOCKED. */
int py32_hsical_state(void);

/* Block until LOCKED or until about timeout_ms have passed at the current
 * (uncalibrated, hence approximate) clock.  Returns 1 if locked, 0 on
 * timeout.  Never call from an ISR. */
int py32_hsical_wait(uint32_t timeout_ms);

#else /* !PY32_HSICAL_ENABLE */

#include <stdint.h>
static inline uint32_t py32_hsical_stamp(void) { return 0u; }
static inline void py32_hsical_init(void) {}
static inline void py32_hsical_event(uint32_t s) { (void)s; }
static inline int py32_hsical_state(void) { return PY32_HSICAL_LOCKED; }
static inline int py32_hsical_wait(uint32_t t) { (void)t; return 1; }

#endif /* PY32_HSICAL_ENABLE */

#endif /* PY32_HSICAL_H */
