# engine16_native — the peripheral engine

Competitor NATIVE. Deliberately not descended from V-USB, Grainuum, rv003usb or
any other software bit-bang. The question asked was: *what is optimal for an ARM
microcontroller that has DMA, timers with input capture and SPI, rather than for
an 8-bit CPU that has only GPIO?* This is the answer, including — mostly — the
parts where the answer is "that direction dies here, for this reason".

Files, both built in this container with the installed `arm-none-eabi-gcc`
13.2.1 (§11):

* `engine16_native.S` — the decoder. Assembly, because the entire result of this
  document is its cycle count.
* `engine16_native.c` — the peripheral configuration. C, because it is forty
  register writes executed once, nothing in it is timed, and writing it in
  assembly would hide the only thing that matters: which bit, and on whose
  authority.

---

## 0. Verdict up front

| direction | verdict |
|---|---|
| 1. Timer-triggered DMA from `GPIOx->IDR` into RAM | **DEAD.** GPIO is not a bus-matrix slave; the DMA cannot reach it. §2 |
| 2. Input capture on D± edges + DMA (acquisition) | **WORKS, and is excellent.** §3 |
| 2b. …followed by a software decode of the intervals | **DOES NOT FIT AT 24 MHz.** Costs 23–24 cycles per NRZI transition against a 16-cycle worst-case arrival rate. §6 |
| 3. SPI as a receive shift register | **DEAD.** No resynchronisation path; ±1.5 % LS rate tolerance defeats a free-running bit clock inside one packet. §4 |
| 4. Transmit by output compare + DMA | **WORKS, costs zero CPU cycles per bit, and is the strongest thing in this file.** §7 |

**The headline, stated so it cannot be misread: peripheral assistance does not
rescue reception at 24 MHz.** It removes phase lock, sampling, entry latency,
dribble and cycle-exactness — all of them, completely — and then loses on raw
throughput, because the USB turnaround deadline converts decode latency into a
throughput requirement and the decode is 23–24 cycles per transition where the
wire allows 16.

The crossover is quantified in §6.5: **this engine needs ≈ 24 cycles per bit
time, i.e. ≥ 36 MHz, and is comfortable at 48 MHz with ~35 % of slack.** At the
competition's 24 MHz it does not work, and the other four competitors are right
to be solving "16 cycles per bit, forever". That is a real result and it
strengthens their case rather than mine.

What survives and should be taken anyway: §3 (acquisition), §7 (transmission),
and §10.

---

## 1. Hardware facts this design rests on

Sources: vendor CMSIS headers from IOsetting/py32f0-template `289ffc8`
(`Libraries/CMSIS/Device/PY32F0xx/Include/`), the LL driver headers from the
same tree, the PY32F030 Reference Manual V1.7 and the PY32F030 Datasheet V1.8
(both already in this project's document set). Line numbers are given so every
claim can be re-checked without trusting me.

| # | fact | source |
|---|---|---|
| H-1 | DMA1, 3 channels, exists on F003x4/x6 and F030x6/x8: `DMA1_BASE = AHBPERIPH_BASE`, `DMA1_Channel1_BASE = DMA1_BASE + 8` | `py32f003x6.h:496-497`, `py32f030x8.h:512-513` |
| H-2 | F002Bx5 has **no** `DMA1_BASE` and **no** `TIM3_BASE`; it has only `SYSCFG_BASE`, `TIM1_BASE`, `SPI1_BASE` | `py32f002bx5.h:422-428` |
| H-3 | DMA request routing is a 5-bit selector per channel in `SYSCFG_CFGR3`: `DMA1_MAP[4:0]`, `DMA2_MAP[12:8]`, `DMA3_MAP[20:16]`, each with a `DMAx_ACKLVL` bit | `py32f030x8.h:3700-3735`; RM §SYSCFG_CFGR3 |
| H-4 | Selector values include `TIM1_CH1..CH4`, `TIM1_UP`, `TIM1_TRIG`, `TIM3_CH1/CH3/CH4`, `TIM3_TRG`, `TIM3_UP`, `SPI1_RX/TX`. `TIM3_CH1 = 0x12` | `py32f0xx_ll_system.h:168-194` (`:186` for TIM3_CH1) |
| H-5 | **The bus matrix has three slaves — SRAM, Flash, and the AHB-to-APB bridge. GPIO is not one of them.** Figure 3-1 hangs GPIOA/B/F off `IOPORT`, connected to the core. Table 3-2's bus column reads `I/O PORT` for GPIO and `AHB`/`APB` for everything else | RM §3.1 and Table 3-2 |
| H-6 | Input capture supports **both edges on one channel**: `CCxNP:CCxP = 11` = "non-inverted/both edges … sensitive to both TIxFP1 rising and falling edges (capture or trigger operations in reset, external clock or trigger mode)". Documented separately for TIM1 and TIM3 | RM TIM1 `CCER` and TIM3 `CCER` bit descriptions; `py32f030x8.h:4133-4153` |
| H-7 | Slave reset mode: `SMS=100`, `TS=101` (TI1FP1) clears the counter on the trigger edge | RM §18.3.13 |
| H-8 | **PWM-input mode proves the capture latches the pre-reset value on the resetting edge**: `CCR1` holds the *period*. Figure 18-27 — `CNT` reaches 0004, `CCR1` = 0004, counter resets | RM §"PWM input mode" |
| H-9 | Per-channel DMA request enables `CC1DE` (bit 9), `CC2DE` (bit 10) in `TIMx_DIER` | `py32f030x8.h:3890-3895` |
| H-10 | DMA reaches "Flash, SRAM, APB and AHB peripherals"; independent PSIZE/MSIZE with documented truncation | RM §11.2, §11.3.4 Table 11-1 |
| H-11 | Clock enables: `RCC_AHBENR.DMAEN` b0, `RCC_APBENR1.TIM3EN` b1, `RCC_APBENR2.SYSCFGEN` b0, `RCC_APBENR2.TIM1EN` b11 | `py32f030x8.h:3252-3298` |
| H-12 | D− must sit on a pin with a timer-channel AF. On F030: PA2/PA6 → `TIM3_CH1`, PA3 → `TIM1_CH1`, PB3 → `TIM1_CH2`, PB4 → `TIM3_CH1`, PB5 → `TIM3_CH2` | PY32F030 DS V1.8 pin/AF table |

Operating point per `ENGINE16_SPEC` §1: 24 MHz, LS 1.5 Mbit/s, **1 bit = 16
cycles = 666.7 ns**. Every cycle number below is at 24 MHz unless it says
otherwise. Cost model is the spec's §2 **RAM column** (ordinary instruction 1,
`LDR/STR` to RAM 2, `PUSH/POP` 2+1, taken branch 2–3), because everything timed
here is RAM-resident.

---

## 2. Direction 1 — timer-triggered DMA from `GPIOx->IDR`. Dead.

This was the highest-value direction, and it dies on one sentence of the
reference manual.

RM §3.1 lists the system as "Two masters: Cortex-M0+, General-purpose DMA" and
"**Three Slaves: Internal SRAM, Internal Flash memory, AHB with AHB-APB
Bridge**". Figure 3-1 draws GPIO ports A, B and F hanging off **IOPORT**, which
connects to the Cortex-M0+ core and not to the bus matrix. Table 3-2 says the
same thing in the register map itself: the bus column reads `I/O PORT` for
GPIOA/GPIOB/GPIOF and `AHB` or `APB` for every other peripheral.

**The same architectural decision that makes `ldr rd,[gpio,#IDR]` a one-cycle
access from the CPU takes GPIO off the DMA's address map.** The IOPORT is a
core-private bus. On an STM32F0, where GPIO sits on AHB2 as a bus-matrix slave,
DMA-from-`IDR` is a standard trick; on this part it is not available. This is
exactly the kind of assumption a port from another platform brings with it and
gets wrong — the concern that created this competitor, landing on the very first
idea.

**How sure am I.** The RM is explicit and consistent in three places, but this
is a negative claim, and negative claims about silicon deserve a measurement.
The settling test is ten lines: `DMA_CCR1.MEM2MEM = 1`, `CPAR1 = 0x50000010`
(`GPIOA->IDR`), `CMAR1 = &buf`, `CNDTR1 = 1`, `EN = 1`, then look at `TEIF1`.
I have not run it. I am reporting the manual's architecture, not silicon.

Two further problems the direction has even if the bus matrix were kinder,
recorded because they transfer to whoever tries this on a different part:

* **One sample per bit is not enough.** LS is 1.5 Mbit/s **±1.5 %** (USB 2.0
  §7.1.11). Over a 96-bit DATA packet a fixed 1.5 MHz sampler slips up to
  ±1.44 bit times relative to the host — far past half a bit. Sampling DMA
  therefore needs ≥ 2× oversampling plus a software resynchroniser: a DMA
  request every 8 cycles, and ~200 B of ring for one packet.
* **RAM.** `BUILD_FACTS` §9 measures 432 B free on F003x4 with the demo linked.

## 3. Direction 2 — input capture. This part works, and it is the mechanism worth taking.

### 3.1 The idea

USB is NRZI: a 0 is a transition, a 1 is no transition, and bit stuffing
guarantees a transition at least every 7 bit times. **All of the data is in the
transition timing.** A timer channel that timestamps every edge captures the
packet losslessly, in hardware, with the CPU switched off.

The refinement that makes it cheap is H-6 + H-7 + H-8 together: put the channel
in **slave reset mode triggered by its own input, on both edges**. The counter
is then cleared at every transition, and the capture register holds *the
interval since the previous transition*, not an absolute timestamp. This is
PWM-input mode generalised to both edges, and H-8 is the RM's own proof that the
capture latches before the reset.

Four separate problems disappear at once:

* no timestamp subtraction in software — three instructions per event;
* **no 16-bit wrap arithmetic, ever**, which otherwise costs either a mask per
  event or one corrupted packet every ~68 ms;
* intervals are ≤ 128 counts in-packet, so `MSIZE=8` makes the ring **one byte
  per transition**;
* the byte value 0 is unreachable in-packet (the shortest legal interval is 16),
  so a zero-filled ring **doubles as an "not captured yet" sentinel at zero
  cost**.

And the property that matters most: intervals are quantised against the *local*
interval, never against an accumulated phase, so **clock offset does not
accumulate**. Every transition is a fresh reference. That is the structural
advantage over every sampling design, hardware or software.

### 3.2 Peripheral configuration, in register terms

Full source in `engine16_native.c`; the essentials:

```
RCC->AHBENR   |= DMAEN(0)   APBENR1 |= TIM3EN(1)   APBENR2 |= SYSCFGEN(0)   (H-11)

GPIOx->MODER   D− := 10b (alternate function)
GPIOx->AFR[]   D− := TIM3_CHx AF index for that pin                          (H-12)

TIM3->PSC   = 0                      24 MHz, 1 count = 1 cycle
TIM3->ARR   = 0xFFFF
TIM3->CCMR1 = CC1S = 01              IC1 on TI1, IC1F = 0000 (no filter)
TIM3->CCER  = CC1E | CC1P | CC1NP    = 0b1011 → both edges                   (H-6)
TIM3->SMCR  = TS = 101 | SMS = 100   TI1FP1, reset mode                      (H-7)
TIM3->DIER  = CC1DE                  DMA request per capture                 (H-9)
TIM3->CR1   = CEN

SYSCFG->CFGR3.DMA1_MAP = 0x12        TIM3_CH1 → DMA1 channel 1          (H-3, H-4)

DMA1_Ch1->CPAR  = &TIM3->CCR1        APB — reachable, unlike GPIO
DMA1_Ch1->CMAR  = &native_ring
DMA1_Ch1->CNDTR = 112
DMA1_Ch1->CCR   = MINC | CIRC | PL=very-high | PSIZE=16 | MSIZE=8 | EN
```

`PSIZE=16, MSIZE=8` is the documented truncating transfer of RM §11.3.4
Table 11-1: the low byte of `CCR1` is stored, and that is the whole interval
because the interval is always ≤ 128 in-packet.

`ACKLVL` (H-3) is left at 0. The RM documents it only as "response speed enable
bit"; setting it is a bench experiment, not a design assumption.

### 3.3 What this buys — and these are real, independent of §6's failure

* **Interrupt entry latency stops mattering.** The M0+ worst case is 15 cycles
  plus prologue (TRM §3.6.1). In every software engine that latency, and worse
  its jitter, comes straight off the phase budget of the first bits — it is why
  `PLAN` has an entry-spread window at all. Here the hardware has already
  timestamped those edges; the handler can arrive 100 cycles late and lose
  nothing.
* **Phase acquisition costs zero cycles and has no error term.** No preamble
  spin, no `USB_RX_SYNC_DELAY`, no sample-point choice, and `PRIOR_ART` D-9's
  whole argument about sampling at offset 14–18/32 to survive up to 260 ns of
  dribble evaporates: there is no sample point to place.
* **The 2-vs-3-cycle taken-branch ambiguity — `ENGINE16_SPEC` §2 calls it "the
  single strongest pressure in the spec" — stops being a correctness risk.** In
  §6.3 it is worth ±1 cycle per transition and it changes only the *rate* at
  which the decoder falls behind. Nothing decodes differently because a branch
  took 3 cycles.
* **Quantisation margin, per transition, non-accumulating.** An interval of
  n bit times is nominally 16n counts. Error sources at the worst case n = 7:
  host rate tolerance ±1.5 % → ±1.7 counts; capture synchroniser and 24 MHz
  quantisation ±1; LS source jitter to next transition ±25 ns = ±0.6 (USB 2.0
  Table 7-8). Total **±3.3 counts against a decision boundary at ±8**, i.e.
  4.7 counts of margin, and it never accumulates because the counter is reset
  at every edge.

### 3.4 What the DMA has to sustain — the top unmeasured risk

Worst case is one transition per bit time: **one DMA transfer every 16 cycles**.
One transfer is an APB read of `CCR1` plus an SRAM byte write.

The RM publishes no per-transfer cycle cost. **This is the largest unquantified
assumption in the design.** The comparable ST DMA on an F0 costs roughly 5–9 AHB
cycles for a peripheral-to-memory transfer, which would leave ample margin in
16, but I am not going to present an ST number as a PY32 number.

The failure mode if it is too slow is at least honest: `TIM3->SR.CC1OF`
(over-capture) is set, one interval is lost, the packet fails CRC, the host
retries. `native_overcapture()` in the `.c` exposes it. **Bench item, gating.**

Second unmeasured item, and it bears directly on §6: the bus matrix arbitrates
CPU and DMA round-robin (RM §3.1). Every DMA transfer may stall a CPU SRAM
access. The decode loop does two SRAM reads per event. **If contention costs one
cycle per transition, every margin in §6 moves by 6 %.**

## 4. Direction 3 — SPI as a receive shift register. Dead, and not marginally.

SPI in slave mode needs SCK. There is no 1.5 MHz clock in the system that is
phase-locked to the host, and no internal route from a timer output to SPI SCK,
so the best construction available is a board-level trace from a `TIM1_CHx`
output to `SPI1_SCK`, with the timer started by a hardware trigger on the first
SYNC edge.

It still dies, for the reason direction 1 needed oversampling: **a free-running
receive clock has no resynchronisation path.** LS is ±1.5 % (USB 2.0 §7.1.11);
over a 96-bit packet that is up to ±1.44 bit times of slip, so the shift
register samples the wrong bit long before the packet ends. NRZI plus bit
stuffing exists *precisely* so a receiver can re-lock on a transition at least
every 7 bits — and a shift register clocked by a local oscillator is the one
receiver architecture that cannot use that guarantee. No SPI configuration on
this part re-times SCK from the data.

Secondary reasons, so nobody reopens it: SPI hands you raw wire symbols, so NRZI
decode and destuffing remain bit-serial software work with the extra misery of
stuff bits crossing byte boundaries; and it costs an external wire, a pin and a
timer to buy nothing.

## 5. EOP is a level, not an edge — and this is where the elegance runs out

I had this wrong for most of the design and it is worth writing down, because it
is the trap in the whole "the data is in the transitions" idea.

At EOP both lines are driven low (SE0) for 2 bit times, then the bus returns to
idle J. Consider what D− does:

* last symbol was **K** (D− low): D− stays low through SE0, then rises to J. The
  interval from the previous transition is (1..7 bit times) + 2.
* last symbol was **J** (D− high): D− falls at SE0 start and rises at SE0 end.
  Interval = 2 bit times exactly.

Either way the EOP shows up in the capture stream as **an ordinary 2..7 bit-time
interval, indistinguishable from data.** SE0 is a level — both lines low
simultaneously — and no amount of edge timestamping expresses a coincidence of
levels. The capture front end is blind to the one event the response deadline is
measured from.

So EOP costs a `GPIOx->IDR` read (1 cycle, IOPORT) plus a test, and — this is
the damaging part — **it can only be read when the CPU gets around to it.** In
`engine16_native.S` the read lives in `.Lwait`, the path taken when the ring is
empty, so it costs nothing on the hot path. But a decoder that is behind is
never in `.Lwait`, and the SE0 window is only 32 cycles wide. A decoder more
than 32 cycles behind **misses SE0 entirely** and must fall back to the slow
test: ring empty and `TIM3->CNT` > 7.5 bit times, i.e. end-of-EOP + 120 cycles,
which is the deadline itself.

The obvious hardware alternative was evaluated and misses by about four cycles:
TIM1 in slave reset mode as a retriggerable monostable, `ARR` = the idle
threshold, giving a "no edge for N cycles" interrupt. `ARR` must exceed the
maximum in-packet gap (7 bit times = 112, +1.7 for rate tolerance, plus jitter
≈ 116), and the update then fires at end-of-EOP + ARR ≥ 116 against a 120-cycle
deadline — before the ISR has even been entered. **It is a useful watchdog for a
lost packet. It is not a response trigger.** Worth recording because it is the
first idea everyone has, and it fails by arithmetic rather than by taste.

## 6. The decoder, and the honest result

### 6.1 Why "deferring the decode removes the timing constraint" is false

USB 2.0 §7.1.18: a device must begin its response **2 to 7.5 bit times** after
the EOP of the packet it answers (`PRIOR_ART` L-1); the host's bus turn-around
timeout is 16–18 bit times. At 24 MHz:

```
1 bit time                        =  16 cycles
device response deadline, 7.5 bt  = 120 cycles     ← the spec limit
host turn-around timeout, 16 bt   = 256 cycles     ← the practical limit
```

The response depends on the packet, so the decode has to be finished inside that
window — unless the decoder ran *during* the packet, which it can, because the
ring is filling in hardware. So the constraint is not latency at all: it is
**whether the decoder keeps up with the wire**, and the wire delivers a
transition every 16 cycles in the worst case.

**That is how 16 cycles comes back.** Same number, different reason. It is a
genuinely weaker constraint — an average upper bound on a free-running loop
instead of an exact per-path equality on a phase-locked one — and it is still
the constraint that decides the outcome.

### 6.2 The decode as arithmetic

A transition marks the start of a data 0; the bits between transitions are 1s.
So an interval of n bit times means **one 0 followed by (n−1) 1s**, and both the
pattern and its length are functions of n alone. Bit stuffing adds exactly one
bit of state: after six consecutive 1s — which happens exactly when n = 7 — the
next transition's leading 0 is the stuffed bit and is dropped.

`engine16_native.S` therefore carries a **two-row table** indexed by
`(stuffed, n)`, 17 intervals per row, 136 bytes, built by the assembler:

| interval | normal row emits | stuffed row emits |
|---|---|---|
| 1 bt (≈16) | `0` | nothing |
| 2..6 bt | `0` + (n−1) ones | (n−1) ones |
| 7 bt (≈112) | `0` + six ones, next row = stuffed | six ones, next row = stuffed |
| ≥ 8 bt, or 0 | position += 31 → falls out of the loop into the malformed path | same |

One lookup per transition does the NRZI decode, the destuffing, the stuffed-slot
validation (`PRIOR_ART` D-7 — an interval that quantises outside 1..7 is
rejected before CRC, for free) and the next stuff state. The table also carries
the next row offset, so **the stuff state costs no branch and no compare**.

### 6.3 Cycle ledger — the streaming decode loop

Code and data RAM-resident. Verified by running
`tools/engine16_cyc.py --exec ram --ioport r10` on the assembled object; the
numbers below are that tool's, not mine.

| # | instruction | cyc | Σ | note |
|---|---|---|---|---|
| 1 | `ldrb r0,[r5,#0]` | 2 | 2 | interval byte; 0 = not captured yet |
| 2 | `cmp r0,#0` | 1 | 3 | sentinel |
| 3 | `beq .Lwait` | 1 | 4 | not taken on the hot path |
| 4 | `adds r5,#1` | 1 | 5 | ring advance |
| 5 | `adds r0,#8` | 1 | 6 | round to nearest bit time |
| 6 | `lsrs r0,r0,#4` | 1 | 7 | n = (interval+8)/16, 0..16 |
| 7 | `lsls r0,r0,#2` | 1 | 8 | n·4 |
| 8 | `adds r0,r0,r4` | 1 | 9 | + stuff row offset |
| 9 | `ldr r3,[r6,r0]` | 2 | 11 | table entry |
| 10 | `lsrs r4,r3,#24` | 1 | 12 | next stuff row — no branch |
| 11 | `lsls r2,r3,#8` | 1 | 13 | drop the row byte |
| 12 | `lsrs r2,r2,#24` | 1 | 14 | count |
| 13 | `uxth r3,r3` | 1 | 15 | pattern |
| 14 | `lsls r3,r1` | 1 | 16 | pattern << p |
| 15 | `orrs r7,r3` | 1 | 17 | acc \|= |
| 16 | `adds r1,r1,r2` | 1 | 18 | p += count |
| 17 | `cmp r1,#8` | 1 | 19 | byte boundary? |
| 18 | `blt .Lloop` | 2–3 | **21–22** | taken on the hot path |

**21–22 cycles per transition.** One taken branch, and its 2-vs-3 ambiguity is
worth ±1 cycle — it changes the rate of falling behind, never the result.

Off the hot path, `.Lbyte` stores the byte, checks the buffer bound and
renormalises: **14–15 cycles**, taken once per 8 bits. On the worst-case path
(every interval = 1 bit time) that is once per 8 transitions, so

```
effective cost = 21..22 + 14..15/8 = 22.75 .. 23.9 cycles per transition
```

Call it **23–24 cycles**, against **16 cycles** of wire time in the worst case.

### 6.4 The deadline arithmetic, done honestly

Worst realistic packet: an 8-byte DATA packet with an all-zero payload — legal,
reachable, and ordinary (a DFU download block of zeros). 8 SYNC + 8 PID +
64 payload + 16 CRC16 = **96 bit times = 1536 cycles**. Transitions: SYNC gives
7 (`KJKJKJKK`), the PID field always contains ones by construction (PID + its
complement) and gives ≈ 4, the zero payload gives 64, CRC16 up to 16 →
**E ≈ 91**, hard upper bound 96.

t = 0 at the packet's first transition, cost 23.5 cycles/transition:

```
last data transition                          t = 1520
SE0 window                                    t = 1520 .. 1552   (32 cycles)
EOP ends, D− rises to J (captured)            t = 1552
device response deadline   1552 + 120         t = 1672
host turn-around timeout   1552 + 256         t = 1808

decoder starts (EXTI entry ≈ 20 + prologue)   t =   30
91 transitions × 23.5                         Δ = 2139
decoder drains the ring                       t = 2169
  → backlog at the SE0 window: 2169 − 1520 = 649 cycles.
  → the 32-cycle SE0 window was missed by a factor of 20 (§5).
  → EOP comes from the slow test instead, and the response cannot start
    before t = 2169 + 26 (TX arm) = 2195.
```

**2195 against a 1672 deadline and an 1808 host timeout. Late by 523 cycles —
33 bit times.** The host has already timed out and retried. This is not a
marginal miss that a few cycles of tuning recovers.

Tokens do not save it either. An IN token to address 0 endpoint 0 — the
enumeration case, which must work — is 32 bit times = 512 cycles of wire and
≈ 25 transitions = 588 cycles of decode. The decoder is 15 % slower than the
wire from the first packet onward.

| packet | bit times | wire cycles | E | decode cycles | keeps up? |
|---|---|---|---|---|---|
| token, addr 0 ep 0 | 32 | 512 | 25 | 588 | **no** (−76) |
| token, typical addr/ep | 32 | 512 | ~15 | 353 | yes (+159) |
| DATA 8 B, typical payload | 96 | 1536 | ~55 | 1293 | yes (+243) |
| DATA 8 B, all-zero payload | 96 | 1536 | 91 | 2139 | **no** (−603) |

A receiver that works on some packets is not a receiver.

### 6.5 Where the crossover is — the useful form of the result

The condition for this engine to work is one inequality:

```
cost per transition  ≤  cycles per bit time
        23.5         ≤  f / 1.5 MHz
```

→ **f ≥ 35.3 MHz.** With headroom for the SE0 window (§5) and for bus
contention (§3.4), the natural operating point is **48 MHz**, which
`CHIP_FACTS` §2 and `PRIOR_ART` §5.1 both say F030/F003 reach at 47.98 MHz from
HSI 24 × PLL2 with −0.04 % error and no servo at all.

At 48 MHz, 1 bit = 32 cycles and the same object code gives:

```
worst packet, 91 transitions × 23.5   = 2139 cycles of decode
                     96 bit times     = 3072 cycles of wire
                    → caught up with 933 cycles to spare, 30 % of the packet
                    → reaches .Lwait during the 64-cycle SE0 window
                    → EOP seen at SE0 start, 64 cycles BEFORE the EOP ends
                    → response deadline is 240 cycles after that; TX arm is 26
                    → margin ≈ 278 cycles
```

Everything inverts. Note the shape of it: **at 48 MHz the software engines need
32 *exact* cycles per bit and this one needs ≤ 32 *inexact* cycles per
transition.** The peripheral design is the one that gets easier with clock, and
it is the 24 MHz operating point — not the peripherals — that decides this
competition against it. I would change the operating point, and I am saying so
rather than pretending 24 MHz is comfortable.

The interval table is unchanged at 48 MHz: intervals become 32n counts, up to
224 for n = 7, still inside a byte, so `MSIZE=8` and the sentinel still work.
`n = (interval+16)>>5` — one different shift constant. Not verified: whether
`CHIP_FACTS` §1's RAM-execution cost table still holds at 48 MHz, where flash
latency is non-zero (`CHIP_FACTS` §3). The engine is RAM-resident so it should,
but "should" is not a measurement.

### 6.6 Buffer bound (`ENGINE16_SPEC` §3.8) — solved structurally, twice

* The ring's only writer is the DMA, in **circular** mode with
  `CNDTR = RING_LEN`. Hardware cannot write outside it. The bus-reachable
  overrun of `DEFECTS_VERIFIED` D-2 has no analogue on the capture side at all.
* On the decoded-byte side the store is on the once-per-byte path, where the
  bound check (`cmp r0,r2 / bhs .Loverrun`) is 2 cycles of **untimed** code
  rather than 2 cycles of a bit cell. That is the structural move the spec asks
  for: the check left the timed path instead of being paid for inside it.

### 6.7 Register allocation — honest about the low-register file

Hot loop, eight low registers, no `mov` to or from r8–r12 anywhere inside it:

| reg | use |
|---|---|
| r0 | interval → n → table index |
| r1 | p, bit position within the byte (0..7) |
| r2 | count |
| r3 | table entry → pattern |
| r4 | stuff row offset, 0 or 68 |
| r5 | ring read pointer |
| r6 | table base |
| r7 | bit accumulator |

All eight are live. r8/r9/r10 hold the output pointer, the output limit and the
GPIO base and are touched only on the byte path and at EOP. There is no spare
register, and that is why the CRC is deferred (§6.8) rather than folded in.

### 6.8 CRC placement (`ENGINE16_SPEC` §3.7 asks for a justification)

**Deferred to the untimed tail.** With the whole packet in RAM there is no
reason to compute CRC incrementally: an incremental CRC costs a register the
loop does not have and ~10 cycles per byte inside the throughput bound that
already fails. Deferred, CRC16 over 8 bytes with a nibble table is ≈ 96 cycles —
affordable only under ACK-first (`PRIOR_ART` S-2), where the ACK does not wait
for the verdict. In a design whose loop fits (§6.5, 48 MHz), deferring is
strictly right; in one that does not, it changes nothing.

## 7. Transmit — output compare + DMA. This is what I would most want kept.

TX must be cycle-exact in every software design, including the four this
competes with. It does not have to be, on this part.

A timer in **output-compare toggle mode** — `OCxM = 011`, "OCxREF toggles when
`CNT = CCRx`" — with `CCxDE` pulling new compare values from RAM by DMA is a
hardware NRZI transmitter, because **NRZI is exactly "toggle on 0", so a packet
is a list of toggle times.** One DMA channel drives D+ from `TIM1_CH1`, a second
drives D− from `TIM1_CH2`; both selectors exist (H-4). DMA1 has exactly three
channels, so RX capture plus two TX channels fills it with nothing to spare.

The toggle list is computed by untimed software and, decisively, **is always
known before it is needed**:

* ACK, NAK and STALL are constant packets — their lists are `.word` data in
  flash and are never computed at all;
* an IN response is built when the endpoint buffer is filled, long before the IN
  token arrives.

So transmitting costs: point `CMAR` at a list, set `CNDTR`, `CNT = 0`, the first
`CCR1`/`CCR2`, `CCER`, `CR1.CEN`. Twelve stores ≈ **26 cycles** (RAM column,
2 each) against 51 cycles for "entry → first preamble store" alone in the
existing engine (`PLAN` Appendix A). And after those 26 cycles the waveform is
the hardware's problem: bit-exact, immune to interrupts, immune to the 2-vs-3
branch ambiguity, immune to flash-versus-RAM placement, immune to everything
`ENGINE16_SPEC` §2 warns about. **Its bit cell costs zero CPU cycles.**

Sketched, not finished, as `ENGINE16_SPEC` §3 permits. What breaks:

* **SE0.** Two independent channels express it directly (both lines toggle low,
  then D− back high). This is why I chose two channels over `CH1`/`CH1N`
  complementary outputs, where "both low" needs `MOE` cleared and `OSSI`/`OISx`
  to do the right thing at a software-chosen instant.
* **RAM.** Absolute 16-bit compare values, up to ~96 toggles per line, is ~200 B
  per channel. Trivial for ACK/NAK (flash constants); real RAM for an 8-byte
  DATA IN on a 2 KB part. Mitigation not designed: refill each list half on the
  DMA half-transfer flag.
* **Pin AF conflict.** D− needs a capture function for RX and an output function
  for TX. Cleanest is one timer doing both — `TIM1_CH1` captures D− during RX
  and drives it during TX, same pin, same AF, reconfiguring `CCMR1`/`SMCR`/
  `CCER` between phases (~8 stores, inside the 26 already counted). **Whether a
  single pin on a given package carries both functions is a per-package check
  against the DS pin table that I have not done**; H-12 shows the functions
  scattered across PA2/PA3/PA6/PB3/PB4/PB5 on F030, which is not encouraging.
* **The capture timer will record our own transmission.** Harmless, but the ring
  must be re-zeroed afterwards — `native_rearm` in the `.S`, ~230 cycles of
  untimed tail work between transactions. It is real and it is on the path.

## 8. Family split

| part | engine |
|---|---|
| **F003x4/x6, F030x6/x8** | this one is *possible* (H-1, H-3), and useful at ≥ 36 MHz (§6.5) |
| **F002Bx5** | **cannot run any of it.** `py32f002bx5.h` defines neither `DMA1_BASE` nor `TIM3_BASE` (H-2) |

The split is clean because the external contract (`ENGINE16_SPEC` §4) is the
seam: both engines hand the same buffer, length and PID to the same C layer, and
nothing in `bootloader_dfu` or the protocol state machine changes. It is a
one-way split — nothing here back-ports to F002B, because the missing piece is
the DMA controller, not a register bit.

This reinforces `CHIP_FACTS` §4 from an independent direction: that document
makes F030/F003 primary for clock reasons; this makes them primary for
peripheral reasons. F002B loses twice, and the software engine is not a fallback
for it — it is the only engine it will ever have.

## 9. What I gave up

* **The spec's cycle ledger.** There is no per-bit-cell ledger because there is
  no bit cell. §6.3 replaces it with an upper bound and a drift rate. A referee
  who values the equality should weigh that; I think the equality is the thing
  worth escaping, but I did not escape it.
* **The result.** §6.4. At 24 MHz this engine does not receive USB. I could have
  presented the 21–22 cycle loop next to the 16-cycle budget and let the reader
  do the subtraction; instead the arithmetic is above, with the packet that
  breaks it named.
* **RAM.** 112 B ring + 136 B table + 12 B rxbuf = **260 B**, against ~15 B for
  the software engine's `rxbuf`. On F003x4 with 432 B measured free
  (`BUILD_FACTS` §9) this is the binding resource before anything else is.
  Halving the table (folding the stuff row into a branch) costs 2 cycles per
  transition, which §6 cannot afford. **This design trades RAM for cycles and
  there is no version of it that is cheap in both.**
* **Pins.** D− stops being a free choice; it must carry a timer channel (H-12).
  The per-site `usb_port_*.h` contract absorbs it, but it is a board constraint
  the software engines do not impose.
* **Three peripherals and their clocks** — DMA1, TIM3, SYSCFG — with their reset
  and low-power interactions, none of which the software engines touch.
* **Two unmeasured hardware assumptions, both gating**: DMA per-transfer cost at
  a 16-cycle request rate (§3.4), and CPU/DMA bus contention on the decode
  loop's SRAM accesses (§3.4). Neither can be settled without silicon.
* **A wrong idea, kept in §5 rather than deleted**, because the next person to
  try this will have it too: the capture stream cannot see EOP, because SE0 is a
  coincidence of levels and capture only sees edges.

## 10. The one mechanism to take

Take **input capture in slave-reset mode as the acquisition front end** (§3.1) —
both edges on one channel, `SMS=100` / `TS=101`, `CCxNP:CCxP=11`,
`PSIZE=16`/`MSIZE=8` DMA into a zero-filled byte ring — **and use it under one
of the four software engines, not under this decoder.**

It costs three peripherals and 112 bytes, and it deletes from any of them: the
bounded preamble spin, the sample-point choice, the entry-latency budget, the
dribble argument of `PRIOR_ART` D-9, and the phase-drift term. The software
engine keeps its 16-cycle bit cell for the *payload*, where it is fast enough,
and gets its bit phase from a hardware timestamp that is exact to ±1 cycle and
does not care when the ISR arrived. That hybrid is buildable at 24 MHz today;
this decoder is not.

Two things fall out of the same ring for free, and both are already on the
project's want-list:

* **The keepalive servo.** `PRIOR_ART` §5.6 says the F030/F002B lack the CTC
  that PY32F07x uses to auto-trim HSI, and that our keepalive servo is that CTC
  in software. With this front end the 1 ms keepalive interval is measured *in
  hardware*, to one cycle, with no CPU cost and no sampling — which is a good
  deal closer to a real CTC than a software servo timed by the same engine it is
  correcting.
* **EOP width and turnaround**, measured on-device rather than only in
  `wg015vcd.py` from a logic-analyser capture.

And if the operating point is ever revisited: at 48 MHz (§6.5) the whole engine
in this directory works, with 30 % slack, and TX (§7) costs zero cycles per bit.
The peripheral optimum is real. It is just above 24 MHz.

## 11. Build verification

Run in this container, `arm-none-eabi-gcc 13.2.1`:

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb \
    -c doc/py32/engine16_native.S -o native.o                            → rc 0
arm-none-eabi-gcc -mcpu=cortex-m0plus -mthumb -Os -Wall -Wextra \
    -c doc/py32/engine16_native.c -o native_c.o                          → rc 0
python3 tools/engine16_cyc.py --exec ram --ioport r10 native.o
```

`arm-none-eabi-objdump -d native.o` shows the instruction stream of §6.3
verbatim, and the assembler-built interval table:

```
 native_ivtbl (normal row)          (stuffed row)
  n=1  0x00010000  count 1, pat 0    0x00000000  count 0, pat 0
  n=2  0x00020002  count 2, pat 10b  0x00010001  count 1, pat 1b
  n=7  0x4407007e  count 7, pat      0x4406003f  count 6, pat 111111b
                   1111110b, and next row = +68 (stuffed) in both
  n>=8 0x001f0000  count 31 → malformed exit
```

Sizes: `.datacode` 312 B (code + the 136 B table), `.bss` 128 B, `.c` 292 B of
init code that runs once.
