/* py32_hsical.c - crystal-less HSI trim against the USB host keep-alive.
 * Public domain / CC0.
 *
 * ORIGIN.  The method is micronucleus firmware/osccal.S, header line
 * "Ralph Doncaster 2020 - optimized OSCCAL tuning from low-speed USB SOF
 * every 1ms", public domain, plus the discard-the-first-measurement rule
 * from firmware/osccalASM.S (cpldcpu, 2014-01-4 changelog entry).  Doncaster
 * measures the gap between end-of-frame events with a fixed-cycle poll loop
 * whose fractional accumulator is scaled so the counter's high byte *is* the
 * OSCCAL delta, then applies it with unit gain three times.  Nothing of that
 * code is reused: it is 8-bit AVR against a one-byte OSCCAL register.  What
 * transfers is the technique -- the host's frame interval is the only
 * accurate reference available before enumeration, one proportional
 * correction per frame converges in a handful of frames, the first
 * measurement is thrown away, and a dead band stops the loop hunting.
 *
 * WHY.  Low speed is 1.5 Mbit/s +-1.5% (USB 2.0 s7.1.11).  The engine needs
 * an integer 16 cycles per bit, so 24.000 MHz exactly, and +-1.5% of the
 * host's 1.000 ms +-0.05% frame is +-360 cycles out of 24000.
 *   - PY32F002B *needs* this.  Its factory 48 MHz word [0x1FFF0104] = 0xB3A2
 *     measures 43.12 MHz, -10.2% (doc/py32/CHIP_FACTS_XIAMATSU.md s2, from
 *     xm_002b.md:172-175,209-210) -- enumeration from it is impossible.
 *   - PY32F003/F030 do not need it at reset: the 24 MHz factory word at
 *     0x1FFF0F10 measures 23.99 MHz, -0.04% (xm_030.md:15).  They need it for
 *     drift: DS030 Table 5-15 gives -4/+2% over -40..85 C.  Build those with
 *     PY32_HSICAL_ENABLE=1 and skip py32_hsical_wait().
 *
 * MEASUREMENT.  SysTick VAL, free-running, 24-bit, down-counting, clocked
 * from HCLK (core_cm0plus.h SysTick_Type; CLKSOURCE/ENABLE at
 * core_cm0plus.h:485,491; RELOAD mask 0xFFFFFF at :495).  Present on this
 * part: RM002B p97 s11.1.2.  At 24 MHz it wraps every 0.70 s, so a 24000-
 * cycle interval never aliases.  Interval = (prev - now) & 0xFFFFFF.
 *
 * ACTUATOR.  RCC->ICSCR (offset 0x04, py32f002bx5.h:310).  HSI_TRIM is
 * bits [12:0] (RCC_ICSCR_HSI_TRIM_Pos 0, _Msk 0x1FFF -- py32f002bx5.h:
 * 2241-2242, identically py32f003x4.h:2840-2841 and py32f030x6.h:2978-2979);
 * HSI_FS is [15:13] (:2257-2258).  CMSIS documents HSI_TRIM as one flat
 * field.  The split it is actually used as -- TRIM_H = bits 12:9, TRIM_L =
 * bits 8:0, TRIM_L 0x000..0x1FF sweeping min..max1 monotonically and TRIM_H
 * adding a coarse percentage on top -- is a measurement, not a datasheet
 * fact: xm_002b.md:393-420 (F002B) and xm_030.md:393-420 (F003/F030).  Only
 * TRIM_L is servoed; FS and TRIM_H are left as SystemInit loaded them, so
 * the factory band is preserved, except for the saturation escape below.
 *
 * GAIN.  Absolute step size from the same measurements, at 24 MHz:
 *   F002B  FS=101, TRIM_H=0:  (33.4-21.7) MHz / 511 = 22.9 kHz = 22.9 cyc/ms
 *   F002B  FS=100 factory band (TRIM_H=5, +26%):     ~19.0 cyc/ms
 *   F003   FS=100 band scaled to 24 MHz:             ~17.7 cyc/ms
 * PY32_HSICAL_CYC_PER_STEP defaults to 20, giving loop gain 0.87..1.15 for
 * all three.  A proportional loop converges for any gain in (0,2), so the
 * constant only has to be within 2x of the truth; it is within 15%.  One
 * step is ~0.08% against a 1.5% budget, and the residual after lock is
 * +-half a step, ~0.04%.
 *
 * CONVERGENCE (simulated against the step sizes above, not measured).  One
 * correction per frame, so one per millisecond.  From F002B's -10.2%: 3 to 4
 * frames to inside the dead band.  From the edge of the acceptance window
 * (-33% / +50%, the widest error this can start from): 8 to 12 frames.  The
 * per-frame cap of 64 steps is what stretches the far cases; it is there so
 * a single bad measurement cannot throw the clock across the band.  There is
 * at most one overshoot, of well under one percent, because the loop gain is
 * near 1 and never above 1.15.  All of that fits inside the host's reset-to-
 * first-SETUP gap, and it works while the part is still far too far off to
 * receive a packet -- the reference is an edge, not a decoded packet, which
 * is the whole reason micronucleus's approach works at all.
 *
 * COST (counted from the -Os disassembly, arm-none-eabi-gcc, F002B build).
 * py32_hsical_event: 12 instructions on the reject path, ~20 when the error
 * is inside the dead band, ~40 on the path that actually writes ICSCR --
 * once per 24000 cycles, and only between packets.  No divide (Cortex-M0+
 * has none): err/CYC_PER_STEP is a compile-time reciprocal multiply,
 * (err * 65536/N) >> 16, which gcc folds into one MULS against the raw
 * interval.  Nothing from libgcc is linked in.  Whether PY32's M0+ carries
 * the one-cycle or the 32-cycle multiplier is a core build option and is
 * not stated in RM002B; even 32 cycles is 0.13% of a frame.
 * 348 B of text and 8 B of bss at -Os; 0 B with PY32_HSICAL_ENABLE=0.
 *
 * ============================ THE SEAM ==================================
 * The engine's USB ISR (rv003usb.S / the PY32 port of it) must do exactly
 * two things.  It is the only part of this that is not in these files.
 *
 *   1. AT ISR ENTRY, before any packet decode:
 *          ldr rS, [rT]          @ rT = 0xE000E018 (PY32_HSICAL_TICK_ADDR)
 *      Two instructions if the address is already in a register, three
 *      otherwise.  This must be the *first* thing after the EXTI_PR ack and
 *      must be at a fixed position in the code path, because measurement
 *      jitter is exactly the variation in the delay from the D- edge to this
 *      load.  Every cycle of jitter is 0.004% of frequency error; the dead
 *      band absorbs 16 cycles of it.
 *
 *   2. AT ISR EXIT, after the packet (if any) is handled, and only there:
 *          bl py32_hsical_event  @ r0 = rS saved from step 1
 *      Never between SYNC and EOP -- this routine takes hundreds of cycles
 *      and would destroy the bit timing.  Call it on EVERY USB interrupt,
 *      including the ones that turn out not to be packets; a low-speed
 *      keep-alive EOP is exactly such an interrupt and is the reference.
 *      No filtering is wanted from the engine: this routine does its own,
 *      by accepting only intervals near one frame.
 *
 * The engine must also leave SysTick alone (project rule R9: SysTick
 * free-running, always).  If the port already owns it, build with
 * PY32_HSICAL_OWN_SYSTICK=0.
 *
 * Caller side, once, before the D- pull-up is asserted:
 *      py32_hsical_init();
 *      usb_pullup_on();                        // host now sends keep-alives
 *      py32_hsical_wait(50);                   // F002B only; bounded
 * F003/F030 skip the wait: they enumerate from the factory word and this
 * runs afterwards, from the ISR, purely as a drift loop.
 *
 * NOT VERIFIED ON SILICON -- every one of these is a bench item:
 *   - TRIM_L monotonicity and sign inside the factory band (assumed +1;
 *     PY32_HSICAL_TRIM_SIGN flips it).  This is the project's OQ3.
 *   - The absolute step sizes above are read off a 3-significant-figure
 *     range table, and only for FS=101/TRIM_H=0 on F002B and FS=100 on one
 *     F003L16S6.  The factory-band values are interpolated, not measured.
 *   - HSI settling time after an ICSCR write.  The AVR original needs one
 *     NOP after writing OSCCAL; nothing equivalent is documented for PY32.
 *     A correction is applied immediately after an event, so a whole frame
 *     of settling is available before the next packet -- but a large jump
 *     could still corrupt a packet in flight, which is why the per-frame
 *     step is capped.
 *   - ISR-entry-to-SysTick-read jitter, which sets the usable dead band.
 *   - That the host emits a keep-alive on a frame with no traffic for us on
 *     every host controller of interest.  micronucleus assumes it; so do we.
 * ======================================================================== */

#include "py32_hsical.h"

#if PY32_HSICAL_ENABLE

#include "py32f0xx.h"

/* --- derived constants -------------------------------------------------- */

/* Acceptance window: an interval is taken as one frame only if it lands
 * within -33%/+50% of target.  It must be this wide because a part that
 * needs calibration can be 10% off before the loop starts, and F002B's
 * whole FS=101 band is 21.7..33.4 MHz.  It is still narrow enough to reject
 * everything else on the bus: a complete low-speed control transaction is
 * under ~100 us (~2400 cycles) and a two-frame gap is at least 2x21700. */
#define HSICAL_MIN_INTERVAL (PY32_HSICAL_TARGET - PY32_HSICAL_TARGET / 3u)
#define HSICAL_MAX_INTERVAL (PY32_HSICAL_TARGET + PY32_HSICAL_TARGET / 2u)

/* 1/CYC_PER_STEP in Q16, rounded. */
#define HSICAL_RECIP \
	((int32_t)((65536 + PY32_HSICAL_CYC_PER_STEP / 2) / PY32_HSICAL_CYC_PER_STEP))

#define HSICAL_TICK_MASK 0x00FFFFFFu   /* core_cm0plus.h:495, :499 */

/* HSI_TRIM[12:0] split, from xm_002b.md:393-401 / xm_030.md:393-401. */
#define HSICAL_TRIM_L_POS  RCC_ICSCR_HSI_TRIM_Pos     /* 0  */
#define HSICAL_TRIM_L_MSK  (0x1FFu << HSICAL_TRIM_L_POS)
#define HSICAL_TRIM_H_POS  (RCC_ICSCR_HSI_TRIM_Pos + 9u)
#define HSICAL_TRIM_H_MSK  (0x00Fu << HSICAL_TRIM_H_POS)
#define HSICAL_TRIM_L_MID  0x100u

/* Consecutive in-dead-band frames required to declare lock. */
#define HSICAL_LOCK_N 2

/* --- state (12 bytes) --------------------------------------------------- */

/* All written from the USB ISR; 8- and 32-bit accesses are single-copy
 * atomic on Cortex-M0+, so no critical section is needed to read them. */
static volatile uint32_t hsical_prev;   /* last SysTick sample             */
static volatile uint8_t  hsical_state;  /* PY32_HSICAL_*                   */
static volatile uint8_t  hsical_good;   /* consecutive in-dead-band frames */
static volatile uint8_t  hsical_armed;  /* 0 => hsical_prev not meaningful */

/* --- init --------------------------------------------------------------- */

void py32_hsical_init(void)
{
	hsical_state = PY32_HSICAL_IDLE;
	hsical_good  = 0;
	hsical_armed = 0;

#if PY32_HSICAL_OWN_SYSTICK
	/* Free-running, HCLK-sourced, no interrupt.  Configure only if it is
	 * not already running the way we need; the port may own it (R9). */
	if ((SysTick->CTRL & (SysTick_CTRL_ENABLE_Msk | SysTick_CTRL_CLKSOURCE_Msk))
	    != (SysTick_CTRL_ENABLE_Msk | SysTick_CTRL_CLKSOURCE_Msk) ||
	    SysTick->LOAD < (PY32_HSICAL_TARGET * 2u)) {
		SysTick->LOAD = SysTick_LOAD_RELOAD_Msk;
		SysTick->VAL  = 0u;
		SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
	}
#endif
}

int py32_hsical_state(void)
{
	return hsical_state;
}

/* --- actuator ----------------------------------------------------------- */

/* Move TRIM_L by delta steps.  On saturation, step the coarse TRIM_H field
 * by one and re-centre TRIM_L: TRIM_H's smallest documented increment is
 * ~+4% while a full TRIM_L sweep is ~+50% of band minimum, so dropping
 * TRIM_L from 0x1FF to 0x100 while raising TRIM_H by one is roughly
 * continuous (xm_002b.md:403-418).  Without this escape a part whose
 * factory word sits near a band edge -- which is F002B's whole problem --
 * could never reach 24 MHz. */
static void hsical_actuate(int32_t delta)
{
	uint32_t icscr = RCC->ICSCR;
	uint32_t h = icscr & HSICAL_TRIM_H_MSK;
	int32_t  l = (int32_t)((icscr & HSICAL_TRIM_L_MSK) >> HSICAL_TRIM_L_POS);

	l += delta;

#if PY32_HSICAL_COARSE
	if (l < 0) {
		if (h != 0u) {
			h -= (1u << HSICAL_TRIM_H_POS);
			l = (int32_t)HSICAL_TRIM_L_MID;
		} else {
			l = 0;
		}
	} else if (l > 0x1FF) {
		if (h != HSICAL_TRIM_H_MSK) {
			h += (1u << HSICAL_TRIM_H_POS);
			l = (int32_t)HSICAL_TRIM_L_MID;
		} else {
			l = 0x1FF;
		}
	}
#else
	if (l < 0)          l = 0;
	else if (l > 0x1FF) l = 0x1FF;
#endif

	/* HSI_FS is preserved: only HSI_TRIM[12:0] is rewritten. */
	RCC->ICSCR = (icscr & ~(HSICAL_TRIM_L_MSK | HSICAL_TRIM_H_MSK))
	           | h | ((uint32_t)l << HSICAL_TRIM_L_POS);
}

/* --- the seam ----------------------------------------------------------- */

void py32_hsical_event(uint32_t stamp)
{
	uint32_t interval;
	int32_t  err, delta;

	/* SysTick counts down, so previous minus current, modulo 24 bits. */
	interval = (hsical_prev - stamp) & HSICAL_TICK_MASK;
	hsical_prev = stamp;

	/* Discard the first sample: it has no predecessor.  This is
	 * osccalASM.S's 2014-01-4 sync state, and it is what makes the routine
	 * safe to call during the SE0 bus reset. */
	if (!hsical_armed) {
		hsical_armed = 1;
		return;
	}

	/* Reject anything that is not a frame boundary.  Data traffic lands far
	 * short of the window; because hsical_prev is updated unconditionally,
	 * a packet mid-frame costs exactly one rejected frame and the loop
	 * re-acquires on the next keep-alive. */
	if (interval < HSICAL_MIN_INTERVAL || interval > HSICAL_MAX_INTERVAL)
		return;

	if (hsical_state == PY32_HSICAL_IDLE)
		hsical_state = PY32_HSICAL_ACQUIRE;

	err = (int32_t)interval - (int32_t)PY32_HSICAL_TARGET;

	/* Dead band.  One trim step is ~20 cycles, so correcting an error
	 * smaller than this could only make it worse: the loop would hunt
	 * +-1 step forever. */
	if (err >= -(int32_t)PY32_HSICAL_DEADBAND &&
	    err <=  (int32_t)PY32_HSICAL_DEADBAND) {
		if (hsical_good < HSICAL_LOCK_N && ++hsical_good >= HSICAL_LOCK_N)
			hsical_state = PY32_HSICAL_LOCKED;
		return;
	}

	hsical_good = 0;

	/* Interval longer than target => more of our cycles per host
	 * millisecond => clock too fast => lower the trim.  No divide on
	 * Cortex-M0+: this is one MULS and one ASRS. */
	delta = -(int32_t)((err * HSICAL_RECIP + 32768) >> 16);

	if (delta >  PY32_HSICAL_MAX_STEP) delta =  PY32_HSICAL_MAX_STEP;
	if (delta < -PY32_HSICAL_MAX_STEP) delta = -PY32_HSICAL_MAX_STEP;
	if (delta == 0)
		return;

	hsical_actuate(PY32_HSICAL_TRIM_SIGN * delta);
	/* The clock changes here.  hsical_prev was sampled before the write, so
	 * the next interval is measured entirely at the new rate -- there is no
	 * split-rate measurement. */
}

/* --- bounded pre-enumeration convergence -------------------------------- */

/* Doncaster's routine and cpldcpu's both spin forever when no host answers
 * (cpldcpu's changelog says so explicitly and defers to the watchdog).  We
 * do not: no host means no interrupts, which means no measurements, which
 * means this returns 0 after timeout_ms and the caller decides.  The
 * timeout is counted in cycles of the clock we are trying to fix, so it is
 * only as accurate as that clock -- worst case on F002B, 10% long.
 * timeout_ms must be under 178000 at 24 MHz for the cycle count to fit. */
int py32_hsical_wait(uint32_t timeout_ms)
{
	uint32_t left = timeout_ms * PY32_HSICAL_TARGET;
	uint32_t last = py32_hsical_stamp();

	while (hsical_state != PY32_HSICAL_LOCKED) {
		uint32_t now = py32_hsical_stamp();
		uint32_t d   = (last - now) & HSICAL_TICK_MASK;

		last = now;
		if (d >= left)
			return 0;
		left -= d;
	}
	return 1;
}

#endif /* PY32_HSICAL_ENABLE */
