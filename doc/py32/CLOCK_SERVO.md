# The clock servo, executed

`rv003usb/py32/py32_hsical.c` had never been run. `tools/usb_enum_sim.py`
linked a stub in its place (`py32_hsical_event: bx lr`), so every result the
port had about it came from reading it. `doc/py32/COVERAGE.md` records what
that costs: on 2026-09-08 an inverted branch in the receiver's SYNC phase lock
survived four bit-exact models because every one of them started downstream of
it. The servo was in the same position, and it is the component the whole
crystal-less design rests on — the receiver's measured window is **±0.203 %**
(`SAMPLE_POINT.md:169`, asymmetric −0.287 %/+0.203 %) and the part's HSI moves
−4/+2 % over temperature (DS030 Table 5-15, `PLAN.md`).

`tools/hsical_sim.py` executes it, closed-loop, against a clock it actually
controls.

```
python3 tools/hsical_sim.py               # every experiment
python3 tools/hsical_sim.py --selftest    # plant, hookup and build switch
python3 tools/hsical_sim.py E6            # one experiment
python3 tools/usb_enum_sim.py --hsical    # the whole enumeration, real servo
```

---

## VERDICT

**On an idle bus the servo is excellent, and on a bus carrying traffic it
destroys the clock it is supposed to hold.**

* Idle bus: converges from ±1 %, ±2 %, ±5 %, ±10 % in **4–6 frames** to a
  residual of **−0.007 %**, which is 29× inside the ±0.203 % window. It then
  never writes the trim again — 0 writes in 1800 frames. Capture range
  **−33 % … +49.5 %**, against a part whose worst documented error is −10.2 %.
* Traffic: one ordinary interrupt-IN transaction per frame drives a
  **perfectly trimmed 24.000 MHz clock to +11 % … +25 %** within fifteen
  frames, and the servo reports `PY32_HSICAL_LOCKED` while it does it. That is
  not a drift; it is a stable false lock at the wrong frequency, and at +11 %
  the receiver cannot decode a single packet.

The failure is not in the loop. The arithmetic, the gain, the dead band and
the state machine are all sound and are all confirmed by measurement below.
The failure is in **what the loop is fed**: the seam contract says "call it on
EVERY USB interrupt … No filtering is wanted from the engine", and that turns
the gap from the last packet of a frame to the next keep-alive into a
measurement.

**As it stands, `PY32_HSICAL_ENABLE=1` is safe only before enumeration and
must be stopped before the host starts moving data.** The `.c`'s own
instruction for F003/F030 — "Build those with `PY32_HSICAL_ENABLE=1` and skip
`py32_hsical_wait()`", i.e. run it forever as a drift loop — is the exact
configuration that breaks it.

Three further defects are measured below (§7, §8, §5d). None of them is fatal
on its own; the traffic one is.

---

## 1. What is real here and what is modelled

**Real.** The device is the compiled, unmodified `rv003usb/py32/py32_hsical.c`
(`arm-none-eabi-gcc -Os -mcpu=cortex-m0plus`), executed instruction by
instruction on a Unicorn Cortex-M0+ with the cycle-cost table
`tools/engine16_cyc.py` — the same one `usb_enum_sim.py` uses. No part of the
servo's arithmetic, dead band, acceptance window, saturation escape or state
machine is reimplemented in Python. Experiment **EA** goes further and runs it
through the real `doc/py32/engine16_merged.S` ISR.

**Modelled — the actuator.** `RCC->ICSCR` at 0x40021004 is MMIO; a write
changes the frequency at which the emulated SysTick advances, and SysTick is
the servo's only input. **That is the closed loop.** An open-loop run would
prove nothing.

Every number in the plant is a measurement on live silicon by Xiamatsu, quoted
in `CHIP_FACTS_XIAMATSU.md §2`:

| quantity | value | citation |
|---|---|---|
| `HSI_TRIM` split: `TRIM_H` = bits 12:9, `TRIM_L` = bits 8:0 | — | `xm_002b.md:224-227` |
| `TRIM_L` 0x000 = band min, 0x1FF = "max1", `TRIM_H`=0 | — | `xm_002b.md:228-232` |
| `TRIM_H` adds +4/+9/+19/+25/+33/+41/+50/+60/+70/+100/+119/+137/+165 % | — | `xm_002b.md:233-247` |
| F002B `HSI_FS`=100 (24 MHz) band | 12.9 → 20.6 MHz | `xm_002b.md:255-263` |
| F002B `HSI_FS`=101 (48 MHz) band | 21.7 → 33.4 MHz | `xm_002b.md:255-263` |
| F003 `HSI_FS`=100 band (one PY32F003L16S6) | 10.7 → 17.1 MHz | `xm_030.md:428-433` |
| `RCC_ICSCR` offset 0x04, `RCC_BASE` 0x40021000, `HSI_TRIM`[12:0] | — | `py32f002bx5.h:310, :433, :2241` |
| SysTick VAL at 0xE000E018, 24-bit down-counter | — | `core_cm0plus.h`, `py32_hsical.h:78` |

`tools/sim_shim/py32f0xx.h` carries the same citations; the vendor CMSIS tree
is not vendored into this repository.

**The one assumption that is the harness's, not the datasheet's:** two measured
endpoints do not describe a curve. The default `TRIM_L` law is linear between
them. `Plant(shape='sqrt'|'quad')` puts a 1.4×/0.7× LSB weight through the same
two points, and **E8** shows the servo converges on all three — so nothing
below depends on that choice. Monotonicity and LSB weight remain the project's
OQ3 bench item (`rework/target_clock.md:443`).

**Modelled — the reference.** USB 2.0 §7.1.7.6: a hub emits a low-speed
keep-alive EOP "at the beginning of the frame … at least once in each frame in
which no other low-speed traffic exists". §7.1.11 fixes the frame at
1.000 ms ±0.05 %. Both the "keep-alive every frame" and the "keep-alive only
in idle frames" readings are simulated (§6, §6b), because the spec permits
both and they fail differently.

**Sanity of the plant.** Xiamatsu's own factory words, run through this model:
`[0x1FFF0100] = 0x8BA7` → 24.092 MHz (the author's arithmetic says 24.68);
`[0x1FFF01A0] = 0x8F11` → 23.989 MHz (author 24.42, and the author notes this
word "turned out closer to 24 MHz" — the model agrees with the ranking). The
~2 % disagreement with the author's own formula is a gap in the measured data,
not a tuned parameter; it is exactly the LSB-weight uncertainty E8 sweeps.

**The hookup, verified rather than assumed** (E0): after one `init` and two
events the servo has touched 0x40021004, 0xE000E010, 0xE000E014 and
0xE000E018 — the actuator and SysTick — and nothing else. 79 distinct
instructions executed on that path; 91 across the full battery.
`PY32_HSICAL_ENABLE=0` still builds to text=0 data=0 bss=0, as
`BUILD_FACTS.md` requires. At `=1` it is 348 B text + 8 B bss, matching the
`.c`'s own count.

---

## 2. Convergence on an idle bus (E1)

Reference: keep-alive EOP every 1.000 ms, nothing else on the wire. F002B,
`HSI_FS`=100, `TRIM_H`=5 (the band the factory 24 MHz word sits in). "Frames"
counts keep-alives delivered; the first is always discarded, as
`osccalASM.S`'s sync state requires.

| asked | actual start | frames to LOCKED | final err | ICSCR writes | inside ±0.203 % |
|---|---|---|---|---|---|
| +0.5 % | +0.464 % | 4 | −0.007 % | 1 | yes |
| +1 % | +1.013 % | 5 | −0.007 % | 2 | yes |
| +2 % | +2.033 % | 5 | −0.007 % | 2 | yes |
| −1 % | −1.027 % | 5 | −0.007 % | 2 | yes |
| −2 % | −1.969 % | 5 | −0.007 % | 2 | yes |
| +5 % | +5.016 % | 5 | −0.007 % | 2 | yes |
| −5 % | −5.030 % | 5 | −0.007 % | 2 | yes |
| +10 % | +7.292 %¹ | 6 | −0.007 % | 3 | yes |
| −10 % | −9.974 % | 6 | −0.007 % | 3 | yes |

¹ +10 % is above the top of the `TRIM_H`=5 band (25.75 MHz), so the run starts
at the highest reachable point.

**Residual: −0.007 %.** That is the distance from 24.000 MHz to the nearest
`TRIM_L` code (23.9983 MHz), not a loop error — the loop reaches the trim
resolution. One trim step is 18.84 cycles/ms = 0.078 %, so the worst residual
the actuator can produce is ±0.039 %, five times inside the window.

**Steady state (E2), 2000 frames:**

| run | min err | max err | trim writes after settling | distinct ICSCR values |
|---|---|---|---|---|
| from +2 % | −0.007 % | −0.007 % | **0 in 1800 frames** | 1 |
| from −2 % | −0.007 % | −0.007 % | **0 in 1800 frames** | 1 |
| from dead on | −0.007 % | −0.007 % | **0 in 1800 frames** | 1 |

It does not oscillate and it does not walk. With a perfect reference it stops
writing entirely.

---

## 3. Capture range (E3)

Swept in 0.5 % steps across every frequency the `HSI_FS`=100 band can reach,
200 frames each:

**Converges to inside ±0.203 % over initial error [−33.0 %, +49.5 %].**

That is the servo's own acceptance window almost exactly: it accepts an
interval in [16000, 36000] cycles, i.e. a clock in [16.0, 36.0] MHz, i.e.
[−33.3 %, +50.0 %] of 24 MHz. Below −33.5 % every interval is rejected and the
servo sits in `IDLE` forever; above +50 % likewise. Both are correct
behaviour — there is no reference outside the window.

Against the part: F002B's factory 48 MHz word measures **−10.2 %**
(`CHIP_FACTS_XIAMATSU.md §2`), and HSI drift over −40…85 °C is **−4/+2 %**.
The capture range is 3× and 8× those. Capture range is not this servo's
problem.

| initial err | frames to LOCKED | final err | inside ±0.203 % |
|---|---|---|---|
| −30 % | 21 | −0.025 % | yes |
| −20 % | 7 | +0.046 % | yes |
| −10 % | 13 | −0.025 % | yes |
| −5 % | 5 | +0.044 % | yes |
| 0 % | 3 | −0.007 % | yes |
| +5 % | 5 | −0.045 % | yes |
| +10 % | 6 | +0.026 % | yes |
| +20 % | 8 | +0.026 % | yes |
| +30 % | 10 | −0.037 % | yes |
| +40 % | 18 | +0.055 % | yes |
| +50 % | **NEVER** | +50.005 % | no |

The non-monotonic frame counts (13 from −10 %, 21 from −30 %) are the
`TRIM_H` escape of §7 costing detours. The `.c` predicts "8 to 12 frames" from
the edge of the window; measured is up to 21.

**Inside that range there is a hole.** Eight of the swept start points — +32.0,
+37.5, +39.0, +43.0, +44.5, +46.0, +47.5, +49.0 % — do not converge. They run
*away*, to +52.913 % (36.699 MHz), which is above the acceptance ceiling, and
stop there permanently. §7 has the mechanism.

---

## 4. Does it ever make things worse? (E4)

Twenty-one start points spread across ±0.2 % — inside the receiver's window —
run for 40 frames:

Every one ends at −0.007 %. **No start point inside tolerance is pushed
outside it, and no trajectory overshoots its own starting error.** The dead
band is ±16 cycles of 24000 = ±0.067 %, so anything inside ±0.067 % is left
alone by construction, and outside it the loop gain of 0.94 cannot overshoot.

That answer holds **only on an idle bus**. §6 is the same question with traffic
on the wire and the answer there is the opposite.

---

## 5. A missing or irregular reference (E5)

**(a) A 30-frame gap with no keep-alive at all.** Error before the gap
−0.007 %, at the first keep-alive after it −0.007 %, at frame 80 −0.007 %.
Zero trim writes during or after the gap. The 31 ms interval is 744 000 cycles,
far outside [16000, 36000], so it is rejected; `hsical_prev` is updated
unconditionally, so the *next* interval is a clean frame. **Correct.**

One note: `hsical_state` never leaves `LOCKED` once set — it stays 2 across the
entire outage. `py32_hsical_state()` is a latch, not a live indicator, and
nothing should read it as "the clock is good now".

**(b) A burst.** Five EOPs 20 µs apart after the gap: ICSCR unchanged
(0x8BA2 → 0x8BA2), error unchanged. 20 µs is 480 cycles, below the window.
**Correct** — provided the burst is tight. Bursts whose spacing lands inside
[16000, 36000] cycles are §6's problem.

**(c) Jitter, over 500 frames.** The dead band is ±16 cycles, so:

| stamp jitter | frame tolerance | min err | max err | trim writes in 400 frames | stays inside ±0.203 % |
|---|---|---|---|---|---|
| 0 | 0 | −0.007 % | −0.007 % | 0 | yes |
| ±4 cyc | 0 | −0.007 % | −0.007 % | 0 | yes |
| ±16 cyc | 0 | −0.086 % | +0.071 % | 170 | yes |
| 0 | ±0.05 % | −0.007 % | −0.007 % | 0 | yes |
| ±16 cyc | ±0.05 % | −0.164 % | +0.150 % | 222 | yes |

USB's own ±0.05 % frame tolerance is ±12 cycles and is absorbed by the dead
band. ISR-entry jitter of ±16 cycles is not: the loop then hunts, writing the
trim in more than half of all frames, and the combined worst case is ±0.164 %
— inside ±0.203 % with **19 % margin**. The engine puts the SysTick load in
the first two instructions of the handler precisely to keep that jitter small
(`engine16_merged.S:813-828`); this measurement says how small it has to be.
**Measuring the real entry jitter is a bench item and it is not optional.**

**(d) A missed keep-alive is only rejected while the clock is fast enough.**
The acceptance ceiling is 36000 cycles, so a two-frame gap is inside the window
for any clock below 18 MHz — and 18 MHz is inside the servo's own capture
range. With every second keep-alive dropped:

| initial err | start | err after 400 frames | state |
|---|---|---|---|
| −5 % | 22.80 MHz | −5.002 % (no reference, never corrects) | IDLE |
| −15 % | 20.40 MHz | −14.997 % (no reference) | IDLE |
| −25 % | 18.00 MHz | **−12.721 %**, stuck | ACQUIRE |
| −30 % | 16.80 MHz | **−46.250 %** = 12.90 MHz, the band floor, dead | ACQUIRE |

A slow clock plus an irregular reference walks *further* off and can reach the
absorbing floor of §7b. Above 18 MHz the same stimulus is harmless.

---

## 6. THE DEFECT: traffic on the same wire (E6)

The seam contract (`py32_hsical.c`, "THE SEAM", step 2) says:

> Call it on EVERY USB interrupt, including the ones that turn out not to be
> packets … **No filtering is wanted from the engine**: this routine does its
> own, by accepting only intervals near one frame.

and the acceptance window's justification says:

> a complete low-speed control transaction is under ~100 µs (~2400 cycles) and
> a two-frame gap is at least 2×21700.

Both halves are about the wrong interval. The intervals in a frame with
traffic are: keep-alive → first packet, packet → packet, and **last packet of
frame *n* → keep-alive of frame *n+1*.** That last one is a frame minus the
transaction's position, it is not short, and it is accepted whenever it exceeds
16000 cycles — two thirds of a frame.

One interrupt-IN transaction per frame (IN token, DATA1, host ACK — the
device's own reply raises no second interrupt, since the engine acks `EXTI_PR`
after it transmits, `engine16_merged.S:2245-2250`), starting from an exactly
trimmed 24.000 MHz clock, 300 frames:

| transaction starts at | gap to next keep-alive | err at frame 1 | **err at frame 300** | state | trim writes |
|---|---|---|---|---|---|
| 10 µs | 0.900 ms | −0.007 % | **+11.069 %** | LOCKED | 7 |
| 50 µs | 0.860 ms | −0.007 % | **+16.245 %** | LOCKED | 10 |
| 100 µs | 0.810 ms | −0.007 % | **+23.382 %** | LOCKED | 14 |
| 200 µs | 0.710 ms | −0.007 % | **−7.135 %** | ACQUIRE | 2 |
| 400 µs | 0.510 ms | −0.007 % | −0.007 % | IDLE | 0 |
| 600 µs | 0.310 ms | −0.007 % | −0.007 % | IDLE | 0 |
| 800 µs | 0.110 ms | +5.016 % | **+24.983 %** | LOCKED | 15 |
| 900 µs | 0.010 ms | +5.016 % | **+11.069 %** | LOCKED | 7 |

The two rows that survive are the ones where *every* interval in the frame
falls below 16000 cycles — the transaction sits in the middle third. Everywhere
else the servo converges, stably and while reporting `LOCKED`, on

&nbsp;&nbsp;&nbsp;&nbsp;`f = 24 MHz × (1 ms / gap)`

which is 26.67 MHz for a 0.9 ms gap and 29.63 MHz for 0.81 ms. It is a
*correct* lock onto the wrong interval, so nothing in the loop ever objects.

**(b) With the keep-alive omitted in frames that carry traffic** — all that
§7.1.7.6 actually requires — the reference is the ACK-to-next-IN interval and
the result is the same equilibrium regardless of where in the frame the
transaction sits:

| transaction at | err at frame 1 | err at frame 300 | state |
|---|---|---|---|
| 10 µs | −0.007 % | **+9.900 %** | LOCKED |
| 100 µs | −0.007 % | **+9.900 %** | LOCKED |
| 400 µs | −0.007 % | **+9.900 %** | LOCKED |
| 800 µs | −0.007 % | **+9.900 %** | LOCKED |

**(c) Enumeration traffic** (SETUP, DATA, IN, DATA, ACK early in the frame):
−0.007 % → **+23.382 %**, 29.612 MHz, in 300 frames with 14 trim writes.

Why this is worse than a number: at +11 % the 16-cycle bit cell is 53× outside
±0.203 %, so the device stops decoding. It does not stop *interrupting* — the
host keeps sending tokens, the edges keep arriving, the servo keeps measuring
the same truncated interval, and the clock stays pinned. **The failure is
self-sustaining and there is no path out of it while the host is polling.**

This is the whole reason the AVR original works and this one does not:
micronucleus's `osccal` runs during the bus reset, before there is any traffic,
and then stops. `py32_hsical_event` is wired into both ISR exits and runs
forever.

---

## 7. The saturation escape moves the clock the wrong way (E7)

`PY32_HSICAL_COARSE=1` says: when `TRIM_L` saturates, step `TRIM_H` by one and
re-centre `TRIM_L` at 0x100, because that is "roughly continuous
(`xm_002b.md:403-418`)". Against the measured band it is not:

| TRIM_H | top of band | after up-escape | up-escape jump | bottom of band | after down-escape | down-escape jump |
|---|---|---|---|---|---|---|
| 0 | 20.60 MHz | 17.43 MHz | **−15.4 %** | 12.90 MHz | — | — |
| 1 | 21.42 | 18.27 | −14.7 % | 13.42 | 16.76 | **+24.9 %** |
| 2 | 22.45 | 19.10 | −14.9 % | 14.06 | 17.43 | +23.9 % |
| 3 | 23.48 | 19.94 | −15.1 % | 14.71 | 18.27 | +24.2 % |
| 4 | 24.51 | 20.95 | −14.6 % | 15.35 | 19.10 | +24.4 % |
| 5 | 25.75 | 22.29 | −13.4 % | 16.12 | 19.94 | +23.7 % |
| 6 | 27.40 | 23.63 | −13.8 % | 17.16 | 20.95 | +22.1 % |
| 7 | 29.05 | 25.14 | −13.5 % | 18.19 | 22.29 | +22.5 % |
| 8 | 30.90 | 26.81 | −13.2 % | 19.35 | 23.63 | +22.1 % |

**Both escapes move the clock in the direction opposite to the correction that
triggered them.** The escape is reached only when the servo wants to go further
up (or down) than `TRIM_L` allows, and then it goes 13–15 % down (or 22–25 %
up). Continuity would need `TRIM_H` steps of ≈+23 %; the measured steps are
+4 to +8 % apart, so re-centring at 0x100 can never be continuous.

Traced instance, the +32 % runaway of §3:

```
start   ICSCR 0x9C1F  TRIM_H=14 TRIM_L=0x01F   31.680 MHz  (+32.00 %)
frame 2 ICSCR 0x9B00  TRIM_H=13 TRIM_L=0x100   36.699 MHz  (+52.91 %)
frame 3..N  every interval rejected (36.699 MHz > the 36 MHz ceiling)
```

Asked to go 64 steps down, `TRIM_L` 0x01F − 64 underflows; the escape takes
`TRIM_H` down one and `TRIM_L` up to 0x100, and the clock goes **up 5 MHz**,
past the acceptance ceiling, permanently.

**(a) Recovery is possible when the escape lands back inside the window.**
From `TRIM_H`=4/5 with `TRIM_L`=0x1F0 the servo takes the up-escape, drops
13–15 %, and climbs back to −0.025 %/−0.007 % within 200 frames using 2–3
writes. So the escape is not always fatal — it is fatal exactly when it throws
the clock outside [16, 36] MHz.

**(b) There is an absorbing state at the bottom.** `TRIM_H`=0, `TRIM_L`=0 is
12.90 MHz, below the servo's own 16 MHz acceptance floor. Every interval is
then rejected: 500 frames, 0 writes, still 12.90 MHz, state `IDLE`. A part
whose `SystemInit` leaves it there, or a servo pushed there by §5d, never
recovers.

**(EB) The header already offers the branch that removes this.**
`PY32_HSICAL_COARSE` is a build switch (`py32_hsical.h:52`). Sweeping the same
range with each setting:

| setting | converges over | start points that end further off than they began |
|---|---|---|
| `COARSE=1` (default) | −33.0 % … +49.5 % | **8** (+32.0, +37.5, +39.0, +43.0, +44.5, +46.0, +47.5, +49.0 %) |
| `COARSE=0` | −28.5 % … +49.5 % | **0** |

4.5 % of capture range buys the removal of every runaway. This is a
build-switch measurement, not an algorithm change.

---

## 8. Loop gain and the dead band (E8)

`PY32_HSICAL_CYC_PER_STEP` is a compile-time 20. The loop gain is the real LSB
weight divided by it; a proportional loop is stable for gain in (0, 2).

| configuration | cyc/ms per step | loop gain | frames to LOCK | residual |
|---|---|---|---|---|
| F002B 24 MHz, `TRIM_H`=5 | 18.8 | 0.94 | 5 | −0.007 % |
| F002B 24 MHz, `TRIM_H`=6 | 20.0 | 1.00 | 4 | −0.037 % |
| F002B 24 MHz in the 48 MHz band | 22.9 | 1.14 | 5 | +0.052 % |
| **F002B 48 MHz build, `TRIM_H`=8** | **34.3** | **1.72** | 13 | +0.010 % |
| F003 24 MHz, `TRIM_H`=7 | 17.7 | 0.88 | 4 | +0.021 % |
| F003 24 MHz, `TRIM_H`=8 | 18.8 | 0.94 | 5 | −0.013 % |
| F002B, sqrt trim curve (LSB 1.4×) | 11.5 | 0.58 | 7 | +0.044 % |
| F002B, quadratic trim curve (LSB 0.7×) | 34.1 | 1.70 | 12 | −0.031 % |

**The gain constant is not a risk.** Across two parts, three bands and a
±40 % error in the assumed LSB weight, gain stays in 0.58 … 1.72 and every
configuration converges. The `.c`'s claim that "the constant only has to be
within 2× of the truth" is confirmed.

One caveat, and it belongs to the 48 MHz build only. The dead band is a fixed
16 cycles; a limit cycle exists whenever the dead band is narrower than half a
trim step. At 24 MHz the ratio is 1.7 (safe). At 48 MHz it is 0.9:

| configuration | dead band ÷ half step | start points that hunt | worst \|err\| after settling |
|---|---|---|---|
| F002B 24 MHz, `TRIM_H`=5 | 1.7 (band 16) | 0/60 | 0.039 % |
| **F002B 48 MHz build** | **0.9 (band 16)** | **3/60** | 0.038 % |
| F003 24 MHz, `TRIM_H`=8 | 1.7 (band 16) | 0/60 | 0.038 % |

5 % of starting points hunt forever in the 48 MHz build — one trim write per
frame, ±0.04 % of clock movement between packets. The amplitude is harmless;
the writes are not free and the clock moving during a packet is not obviously
harmless.

**FIXED.** `PY32_HSICAL_DEADBAND`'s default now follows `PY32_HSICAL_FCPU`:
16 at or below 32 MHz, 18 above. A trim LSB moves a fixed *fraction* of the
frequency, so its weight in cycles-per-frame scales with the clock while a
constant does not — that mismatch is the whole defect. Confirmed by the
preprocessor, not by reading:

```
FCPU=24000000 -> PY32_HSICAL_DEADBAND = 16
FCPU=48000000 -> PY32_HSICAL_DEADBAND = 18
```

and re-measured:

| configuration | dead band ÷ half step | start points that hunt | worst \|err\| |
|---|---|---|---|
| F002B 24 MHz, `TRIM_H`=5 | 1.7 (band 16) | 0/60 | 0.039 % |
| **F002B 48 MHz build** | **1.0 (band 18)** | **0/60** | 0.035 % |
| F003 24 MHz, `TRIM_H`=8 | 1.7 (band 16) | 0/60 | 0.038 % |

24 MHz is deliberately untouched: 16 measured clean there, and a validated
configuration should not move for a defect it does not have.

E8's ratio column used to be computed from a hardcoded 16, so it kept printing
0.9 for the 48 MHz build after the default had already become 18. It now reads
the constant the configuration actually compiled.

---

## 9. Cost, and the seam (E9, EA)

Cycles per `py32_hsical_event` call, priced with `engine16_cyc.py`:

| path | cycles | the `.c`'s claim |
|---|---|---|
| interval rejected (out of window) | **42** | "12 instructions" |
| inside the dead band | **61** | "~20 instructions" |
| writes ICSCR | **104** | "~40 instructions" |

The `.c` counts instructions; these are cycles, and the difference is the
Cortex-M0+ price list — 4 cycles per RAM access and 2 per flash literal from
flash-resident code (`CHIP_FACTS_XIAMATSU.md §1`). No `libgcc` symbol is
linked; the reciprocal multiply folds as the `.c` says.

**EA — the seam, through the real engine.** `tools/usb_enum_sim.py --hsical`
links the real servo in place of the stub; `hsical_sim.py EA` then drives
keep-alives through `usb_rx_engine16` itself: exception entry, the two
`ldr` instructions that sample SysTick VAL, the SE0 fork to
`usb_rx_keepalive`, the `EXTI_PR` ack, and `bl py32_hsical_event` across the
`push {r1, lr}` / `pop {r1, pc}` frame.

| keep-alive | ICSCR | MHz | err | ICSCR writes | ISR cycles | SP balance |
|---|---|---|---|---|---|---|
| 1 | 0x8BBC | 24.4880 | +2.033 % | 0 | 56 | ok |
| 2 | 0x8BA4 | 24.0360 | +0.150 % | 1 | **127** | ok |
| 3 | 0x8BA2 | 23.9983 | −0.007 % | 1 | 124 | ok |
| 4–10 | 0x8BA2 | 23.9983 | −0.007 % | 0 | 84–102 | ok |

The keep-alive path is the one taken, SP is balanced on every exit, and the
loop converges in three keep-alives through the real ISR. Two things to record:

* The **whole enumeration is byte-identical** with the real servo linked
  instead of the stub (`diff` of `usb_enum_sim.py --no-ksweep` with and
  without `--hsical` is empty apart from the temp path). The seam does not
  disturb the engine.
* Worst-case keep-alive ISR is **127 cycles**, i.e. 7.9 bit times. The `.c`
  says the servo is "well under the 128 cycles of a SYNC field"; measured, the
  ISR ends at 127. It is inside, but "well under" is not the right word, and
  the margin is one cycle.

---

## 10. The defect list

| # | defect | severity | what it costs to fix |
|---|---|---|---|
| **D1** | The servo is fed every USB interrupt, so the last-packet-to-next-keep-alive gap is measured as a frame. A trimmed clock is driven to +10…+25 % within ~15 frames and stays there, reporting `LOCKED`. §6 | **fatal in normal operation** | see below |
| **D2** | `PY32_HSICAL_COARSE`'s saturation escape moves the clock 13–25 % in the direction opposite to the correction that triggered it, and can throw it past the acceptance ceiling permanently. 8 of 383 swept start points. §7 | **serious**, reachable only near band edges | see below |
| **D3** | A missed keep-alive is accepted as a frame whenever the clock is below 18 MHz, walking a slow clock further off and into the absorbing floor at 12.9 MHz. §5d | serious, only below 18 MHz | folded into D1's fix |
| ~~D4~~ | 48 MHz build: dead band (16) is below half a trim step (17.2), so ~5 % of starting points hunt forever. §8 | **FIXED** | the default now follows FCPU: 16 / 18. 3/60 hunting → 0/60 |
| **D5** | `hsical_state` never leaves `LOCKED`; it is a latch, not a live health flag. §5a | documentation | one line |
| **D6** | `.c` cost comments are in instructions and read as cycles; the reject path is 42 cycles not 12, the write path 104 not 40, and the keep-alive ISR peaks at 127 of a claimed-comfortable 128. §9 | documentation | one table |

**Costing the fixes. This section proposes; it changes nothing, and nothing
below has been simulated.**

* **D1, the cheap and structural fix — feed the servo only from the keep-alive
  path.** The engine already separates them: `usb_rx_keepalive` is entered when
  D± are SE0 at ISR entry, the packet path is not. Deleting the second call
  site (`engine16_merged.S:2270-2276`: `pop {r0}` / `bl` / `add sp,#4`) and the
  stamp's share of the entry `push` **removes** instructions from the packet
  ISR — the hot path — rather than adding any. The servo then sees only frame
  boundaries and D1 and D3 both disappear, because every interval it is offered
  is an integer number of frames. Contradicts the `.c`'s "No filtering is
  wanted from the engine", which is the sentence that has to change.
  *Estimated: −5 instructions in the engine, 0 in the servo, plus a rewrite of
  the seam contract.*
* **D1, the residual after that.** With keep-alive-only input, a hub that omits
  the keep-alive in busy frames (§7.1.7.6 permits it) offers 2-, 3- and
  4-frame intervals. Either reject them — the present ±33/+50 % window already
  rejects 2 frames above 18 MHz, and a tighter post-lock window would reject
  all of them — or accept `N × TARGET` and divide. The first costs nothing; the
  second costs a compare chain, ~10 instructions.
* **D1, the alternative that needs no engine change — narrow the window once
  locked.** Keep ±33/+50 % while `state != LOCKED` (pre-enumeration, no
  traffic) and switch to ±3 % after. A ±3 % window rejects every partial-frame
  gap. *Estimated: one `hsical_state` compare and a second pair of bounds,
  ~8 instructions, ~24 B.* Weaker than the structural fix — it depends on the
  device never being polled before it locks.
* **D2:** `PY32_HSICAL_COARSE=0` today, at a cost of 4.5 % of capture range and
  a *saving* of ~14 instructions (measured, EB). Doing it properly — mapping
  the current frequency into the new band instead of re-centring — needs a
  divide the M0+ does not have. If F002B needs to cross bands, that belongs in
  the LSI pre-calibration `PLAN.md` already requires, not in the frame servo.
* **D4:** `PY32_HSICAL_DEADBAND` 16 → 18 for `FCPU`=48000000. Zero cost.

---

## 11. Does this stack hold ±0.203 % without a crystal?

**Yes on the reference, no on the wire, as the servo is wired today.**

The measurement is good enough: one keep-alive per millisecond, a 24-bit
counter, and a ±16-cycle dead band resolve the clock to ±0.067 %, and the
actuator's own resolution (0.078 % per step) puts the achievable residual at
±0.039 %. Both are inside ±0.203 % with 3–5× margin, and the loop reaches that
residual in 4–6 frames from anything the part can plausibly power up at. The
gain constant tolerates a ±40 % error in the LSB weight it was derived from.
Nothing in this stack needs a crystal to hold ±0.203 % **if the only thing the
servo ever measures is a frame boundary.**

The margin that is not there is jitter. ±16 cycles of ISR-entry jitter already
consumes 80 % of the window (§5c). The dead band is 16 cycles; the entry jitter
has never been measured; and the two numbers are the same size. That is the
single measurement that decides the design, and it is a bench measurement.

And the servo as wired today does not measure only frame boundaries. It
measures whatever gap happens to precede a keep-alive, and on a device that is
being polled that gap is not a frame. **Until D1 is fixed, `PY32_HSICAL_ENABLE`
must be treated as a pre-enumeration-only feature — run it, lock it,
`PY32_HSICAL_ENABLE=0` or detach it before the first SETUP — and the drift-loop
use the `.c` recommends for F003/F030 must not be shipped.** F003/F030 do not
need it to enumerate (factory word −0.04 %, `xm_030.md:15`), so nothing is lost
by leaving it off there until D1 lands.

---

## 12. What this does not prove

Everything in `py32_hsical.c`'s own "NOT VERIFIED ON SILICON" list stands, and
the simulation cannot touch any of it:

* `TRIM_L` monotonicity and sign inside the factory band (OQ3). The harness
  assumes monotone and `+1`; E8 shows the *magnitude* does not matter much, and
  says nothing about the sign. A wrong sign makes the loop diverge on the first
  correction, and the acceptance window would then trap it exactly as §7 does.
* The absolute step sizes come from a three-significant-figure range table on
  one part per family. Part-to-part band spread is documented as large
  (`xm_030.md:437-441`: min 1.8–2.3 MHz, max 43–50 MHz — roughly ±15 %).
* HSI settling time after an ICSCR write. Modelled as instantaneous. The `.c`
  flags it; nothing here measures it.
* ISR-entry-to-SysTick-read jitter — see §11.
* That a host actually emits the keep-alive on every frame of interest.
  §6 shows both readings of §7.1.7.6 fail, so this one no longer matters for
  the verdict, but it still matters for the fix.
* The device clock is modelled as exactly `Plant.freq(ICSCR)` with no noise,
  no temperature ramp and no supply dependence. A real drift loop has to track
  a moving target; §2 only shows it holds a stationary one.
