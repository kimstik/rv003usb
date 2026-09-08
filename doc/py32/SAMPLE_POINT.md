# Where the receive engine samples the bit cell

`doc/py32/engine16_merged.S` samples each 16-cycle low-speed bit cell exactly
once, at a point fixed by the SYNC phase lock.  That point is the whole of the
receiver's timing margin: everything after the lock is a rigid 16-cycle chain,
so an offset that sits badly in the cell is an offset that sits badly for all
~89 remaining bits of the longest packet.

Every number below is produced by

    python3 tools/engine16_rx_sweep.py [--jitter C] [--k ...] [--polls ...]
    python3 tools/engine16_rx_sweep.py --verify --jitter 0.6   # committed source
    python3 tools/engine16_rx_bus.py                           # the two gates

which assembles each candidate with `arm-none-eabi-gcc`, links it, and runs it
on a unicorn emulator against a synthesized low-speed waveform with the GPIO
IDR driven from an emulated cycle count.  Nothing here is read off a comment.

## 0. Result

|                      | committed before | this change |
|----------------------|------------------|-------------|
| poll period of the J->K hunt | 7 cycles | **3 cycles** |
| sample offset band, over entry latency 7..37 x 8 sub-cycle phases | 9.00 .. 15.88 of 16 | **9.00 .. 11.88 of 16** |
| band width | 6.88 | **2.88** |
| guaranteed symmetric clock tolerance, no jitter | +-0.007 % | **+-0.203 %** |
| guaranteed symmetric clock tolerance, +-25 ns jitter | 0 (40 of 248 grid points do not decode at all) | **+-0.155 %** |
| largest worst-case per-transition jitter with zero failures | 0.00 cycles (0 ns) | **1.00 cycle (42 ns)** |
| entry-latency window | 6..37 | **4..37** |
| `.text` of the two-engine link | 6506 B | 6526 B (+20) |

## 1. What the measurement means, and two corrections to the instrument

**"Decoded" now means ACCEPTED.**  `tools/engine16_rx_bus.py` used to check the
ten bytes at `usb_rxbuf+2`.  The engine writes that buffer as the packet
arrives and only tests the CRC16 residue afterwards, so a frame that gained or
lost a bit still leaves `80 c3 01..08` in RAM.  At +6000 ppm the payload reads
back perfectly and the engine rejects the packet.  Both tools now require that
the engine reached the C dispatch - `usb_pid_handle_data`, which is downstream
of the residue test - with `r3 = payload + 3` (F-4).  Most of the old
"+1.00 %" was this leniency, not margin.

**The sign of ppm was backwards.**  `period` is *device cycles per host bit*,
so `period > 16` means more device cycles inside one bit time: the device clock
is **FAST**.  The docstring said SLOW.  The direction is visible in the drift -
at +ppm the sample offset falls through the cell (11.60 -> 10.65 -> ... at
+1 %) and runs into the EOP dribble; at -ppm it climbs and falls off the end of
the cell.  So:

* a **fast** device clock is limited by the floor:  `e+ = (o_min - floor) / (16 N)`
* a **slow** device clock is limited by the ceiling: `e- = (16 - o_max) / (16 N)`

with `N ~ 89` bits from the lock to the EOP of an 8-byte DATA0.  One cycle of
margin is worth **700 ppm**, measured: `out6r` K=16 -> K=17 moves the fast limit
+0.203 % -> +0.268 % and the slow limit -0.287 % -> -0.212 %.  The two limits are equal - the tolerance is
symmetric and maximal - when the band is centred in `[floor, ceiling]`, which
is the entire optimisation.

## 2. The floor and the ceiling, measured rather than cited

The floor was folklore ("7 cycles").  It is measurable: hold the last data
level `D` cycles into the first SE0 cell and ask how large `D` can get.

| engine | sample band | largest dribble that still decodes |
|---|---|---|
| committed before | 9.00..15.88 | 9.00 cycles (375 ns) |
| this change, `USB_RX_LOCK_NOPS=13` | 6.00..8.88 | 6.00 cycles (250 ns) |
| this change, `USB_RX_LOCK_NOPS=17` | 10.00..12.88 | 10.00 cycles (417 ns) |

The tolerated dribble is **exactly `o_min`**, in every case.  So the engine does
not absorb a held last bit at all: a sample that lands inside the hold reads it
as a data bit, the EOP is detected one cell late, and the frame is rejected.
USB 2.0 §7.1.9 allows 260 ns = 6.24 cycles at 24 MHz, so `o_min >= 6.24` is a
hard floor and the fast-clock limit really is `(o_min - 6.24)/(16 N)`.

`doc/py32/SYNCLOCK_PRIOR_ART.md` §3.3 concludes the opposite - "holding the last
data level for a full 16 cycles (666 ns) into the first SE0 cell decodes
correctly ... the dribble floor does not bind on this engine at any sample
offset it can produce" - and retires the floor as a constraint.  That is the
buffer-compare leniency of §1.  At entry latency 16 on the engine as committed
here, one held cell at a time:

| hold | `usb_rxbuf+2` (12 bytes) | verdict |
|---|---|---|
| 0.00 c | `80c301020304050607084f30` | dispatch, r3 = 11 |
| 6.24 c | `80c301020304050607084f30` | dispatch, r3 = 11 |
| 9.00 c | `80c301020304050607084f30` | dispatch, r3 = 11 |
| 10.00 c | `80c301020304050607084f30` | **NOT CALLED - frame rejected** |
| 16.00 c | `80c301020304050607084f30` | **NOT CALLED - frame rejected** |

The buffer is byte-identical in all five.  A sample inside the hold reads it as
a data bit, so the EOP arrives one cell late, the data field is no longer a
whole number of wire bytes, a thirteenth byte is emitted and the residue is
computed over it.  The trailing-partial-byte handling §3.3 cites does not save
it; it is what makes the *count* wrong.  Reproduce with the snippet in §8.

The ceiling is 16 - the sample must not cross into the next cell - less
whatever the transitions themselves move.  Transition jitter is modelled
directly rather than argued from a table: `Bus(jitter=C)` displaces every cell
boundary by `+-C` in the **worst-case alternating pattern**, which is what
actually squeezes a cell (a displacement common to all boundaries is only a
phase shift, and each boundary is shared by two cells, so the worst a
transmitter can do to a receiver that samples every cell is to shrink every
other one to `16 - 2C`).  USB 2.0 Table 7-5's low-speed **source** jitter for
consecutive transitions is +-25 ns = 0.6 cycles at 24 MHz, and that is the
figure used for the headline.

A note on the +-141 ns sometimes quoted as a "safe band of 3.4..12.6 of 16":
applied as per-transition jitter it leaves `16 - 6.8 = 9.2` usable cycles, and
with the 6.24-cycle dribble floor **no** candidate in this sweep survives it -
`orig`, `out6r` and `back4r` all fail every grid point at `--jitter 3.4`.  141 ns
is a *total receiver budget* that already contains the sampling uncertainty
this document is about; adding it on top double-counts.  Measured, at
`--jitter 1.7` (70 ns) every candidate already fails somewhere.  The band that
can actually be defended is the one at +-25 ns.

**The two criteria pick the same winner, so the question does not have to be
adjudicated.**  Read as a hard band, §7.1.15.1 + Table 7-5 say the sample must
stay in cycles 3.4..12.6 of 16.  Of the candidates with a usable entry window:

| candidate | band | inside 3.4..12.6? | above the 6.24 floor? | symmetric @25 ns |
|---|---|---|---|---|
| committed before | 9.00..15.88 | **no** (3.3 cycles outside) | yes | 0 |
| out6r K=14 | 7.00..9.88 | yes | yes | 0.015 % |
| out6r K=15 | 8.00..10.88 | yes | yes | 0.090 % |
| **out6r K=16** | **9.00..11.88** | **yes** | **yes** | **0.155 %** |
| out6r K=17 | 10.00..12.88 | **no** (0.28 over) | yes | 0.133 % |
| out6r K=18 | 11.00..13.88 | **no** | yes | 0 |

K=16 is the largest fixed delay whose whole band still fits inside 3.4..12.6,
and it is also the best measured tolerance among those that fit.  K=17, which
wins by 0.009 % on the no-jitter number alone, fails the band and loses on the
jitter number - the two criteria agree.

## 3. The sweep

Grid: entry latency 7..37 (the committed engine's own window, less its bottom
entry - a non-zero sub-cycle packet phase is the same thing as half a cycle
less entry latency and pushes it out) x 8 sub-cycle packet phases; dribble
6.24 cycles; longest low-speed packet (DATA0, 8 bytes).  `K` is `.Lk_edge`'s
nop run, `P` the padding at the tail of `.Lprime`; both are pure fixed delay
and only the sum matters.

Poll shapes:

* `orig` - the committed loop, `subs / beq / ldr / lsls / taken b<cond>` = **7 cycles**.
* `outM` - M samples in a straight line, each `ldr / lsls / b<cond>` where the
  branch is **taken only when the edge is found**, so the not-found cost is an
  untaken branch: **3 cycles**.  Every exit branches to the same `.Lk_edge`.
* `backM` - the shape the brief sketched: keep the loop-back conditional and
  check the timeout once per M polls instead of every poll.  Not-found is
  `ldr / lsls / TAKEN b<cond>` = **5 cycles**.
* trailing `r` - the lock relocated in front of the EOP stubs (see §5).

### 3.1 No jitter

    python3 tools/engine16_rx_sweep.py --jitter 0 --polls orig,out4r,out5r,out6r,out8r,back4r,back6r --k 14,16,18,20

| shape | K | bytes | entry window | offset band | width | slow .. fast | symmetric | fails |
|---|---|---|---|---|---|---|---|---|
| orig | 14 | 6494 | 1..3 | 3.00..9.88 | 6.88 | - | 0 | 121 |
| orig | 16 | 6498 | 7..11 | 5.00..11.88 | 6.88 | - | 0 | 41 |
| orig | 18 | 6502 | 6..37 | 7.00..13.88 | 6.88 | -0.147 % .. +0.058 % | 0.058 % | 0 |
| **orig** | **20** | **6506** | **6..37** | **9.00..15.88** | **6.88** | **-0.007 % .. +0.195 %** | **0.007 %** | **0** |
| out4r | 14..20 | 6510-6522 | 26..37 | 0.00..15.88 | 15.88 | - | 0 | 96 |
| out5r | 14..20 | 6518-6530 | 23..37 | 1.00..15.88 | 14.88 | - | 0 | 24 |
| out6r | 14 | 6522 | 0..37 | 7.00..9.88 | 2.88 | -0.427 % .. +0.058 % | 0.058 % | 0 |
| out6r | 15 | 6526 | 0..37 | 8.00..10.88 | 2.88 | -0.360 % .. +0.130 % | 0.130 % | 0 |
| **out6r** | **16** | **6526** | **0..37** | **9.00..11.88** | **2.88** | **-0.287 % .. +0.203 %** | **0.203 %** | **0** |
| out6r | 17 | 6530 | 0..37 | 10.00..12.88 | 2.88 | -0.212 % .. +0.268 % | 0.212 % | 0 |
| out6r | 18 | 6530 | 6..37 | 11.00..13.88 | 2.88 | -0.147 % .. +0.195 % | 0.147 % | 0 |
| out6r | 19 | 6534 | 6..37 | 12.00..14.88 | 2.88 | -0.072 % .. +0.195 % | 0.072 % | 0 |
| out6r | 20 | 6534 | 6..37 | 13.00..15.88 | 2.88 | -0.007 % .. +0.195 % | 0.007 % | 0 |
| out8r | 14..20 | 6534-6546 | identical to out6r | | | | | 0 |
| back4r | 14 | 6518 | 0..37 | 8.00..12.88 | 4.88 | -0.220 % .. +0.130 % | 0.130 % | 0 |
| back4r | 16 | 6522 | 3..37 | 10.00..14.88 | 4.88 | -0.072 % .. +0.268 % | 0.072 % | 0 |
| back6r | 14..20 | 6534-6546 | identical to back4r | | | | | |

### 3.2 With USB 2.0 Table 7-5's +-25 ns of source jitter (`--jitter 0.6`)

| shape | K | offset band | slow .. fast | symmetric | fails / 248 |
|---|---|---|---|---|---|
| orig | 18 | 7.00..13.88 | - | 0 | 4 |
| **orig** | **20** | **9.00..15.88** | **-** | **0** | **40** |
| out6r | 14 | 7.00..9.88 | -0.345 % .. +0.015 % | 0.015 % | 0 |
| out6r | 15 | 8.00..10.88 | -0.273 % .. +0.090 % | 0.090 % | 0 |
| **out6r** | **16** | **9.00..11.88** | **-0.205 % .. +0.155 %** | **0.155 %** | **0** |
| out6r | 17 | 10.00..12.88 | -0.133 % .. +0.228 % | 0.133 % | 0 |
| out6r | 18 | 11.00..13.88 | - | 0 | 4 |
| back4r | 14 | 8.00..12.88 | -0.133 % .. +0.090 % | 0.090 % | 0 |
| back4r | 16 | 10.00..14.88 | - | 0 | 5 |

`K=16` is the winner: best at 25 ns of jitter and within 0.009 % of the best at
zero jitter, where `K=17` edges it.  `K=17` is 0.212 % with no jitter but 0.133 %
with it - the extra cycle it spends chasing the fast-clock limit is spent
against the ceiling, where jitter also eats.

## 4. Negative results, which are most of the value

* **Re-centring the committed loop is not enough.**  `orig K=18` moves the band
  down to 7.00..13.88 and takes the tolerance from 0.007 % to 0.058 % - an
  8x gain for two nops - but at 25 ns of jitter it *fails outright* on 4 of 248
  grid points.  A 6.88-cycle band cannot be centred well enough to fit inside a
  9.76-cycle window that jitter shrinks from both ends.  The width is the
  problem; the constant is only half of it.
* **The brief's `back` shape costs 5 cycles per sample, not 4-5, and the `out`
  shape costs 3, not 4-5.**  The estimate priced the loop-back branch on every
  poll.  Turning the loop inside out - "found" is the taken branch - makes the
  common path an *untaken* conditional, which is 1 cycle.  `ldr / lsls /
  untaken b<cond>` = 3 is the ARMv6-M floor for a poll: no load instruction on
  this core sets flags, so no sample can be fewer than three instructions.
  Measured band widths: 6.88 (7-cycle loop), 4.88 (`back`), 2.88 (`out`).
* **Per-exit padding is exactly the wrong idea.**  An earlier version of this
  work padded each unrolled exit so that all of them reached `.Lk_edge` at the
  same delay after the START OF THE BLOCK.  That is a fixed point with no
  relation to the edge, and it spread the lock over the whole block: measured
  band 4.88 instead of 2.88, entry window collapsed to 30..35.  What must be
  constant is the delay from the sample that DETECTED the edge - which is
  automatic when every exit branches to one target, and needs no padding at all.
* **Fewer than six samples loses the packet.**  `.Lwait_j` hands over 3 cycles
  after the J it saw, and the J->K edge can be a full 16-cycle cell away, so
  the straight-line block must cover 3 + 3(M-1) >= 16, i.e. M >= 6.  M=5 covers
  15: entry window 23..37, 24 grid failures, band 14.88 because the lock now
  sometimes falls through and re-hunts.  M=4: window 26..37, 96 failures.  M=8
  is identical to M=6 and 12 bytes larger.
* **A counter between `.Lwait_j` and `.Lwait_k` costs its own width.**  The two
  cycles a `subs/beq` spends there are a blind window between the last J seen
  and the first K looked for; with it, the measured band was 4.88 rather than
  2.88 whatever the poll period.  `.Lwait_j` now owns the timeout for both.
* **`.balign 4` was quantising the fixed delay to 2 cycles.**  In the old
  layout the alignment directive sat between the confirming sample and
  `.Lprime`, so adding one nop to `.Lk_edge` removed one nop of alignment fill:
  `orig K=18` and `K=19` assemble to different sources and measure identically,
  as do `K=20` and `K=21`.  The delay was only adjustable in even steps, and
  the executed delay was one cycle more than the "20 nops" comment said.
* **`out2r`/`out3r`, and `back` with M>4, buy nothing** over the smallest M that
  covers the cell; the loop-back is never taken during a packet.

## 5. Why the lock moved, and what it cost

The unrolled hunt did not fit where the lock used to sit.  `usb_rx_cell0`'s
`beq rx_eop0` reaches backward 232 bytes of a Thumb `B<cond>`'s 256, so only
**24 bytes** of growth were available between the EOP stubs and the chain head,
against the 28 the six samples need.  The assembler refuses it outright.

Moving the hunt in front of the stubs would have needed a branch back to
`.Lprime` and a register to hold its address; loading that register in the
entry setup costs two cycles of the *entry-latency ceiling*, which is "the hunt
must start before SYNC cell 5 ends" - measured, the window dropped from 37 to
35.  Moving the **priming block out as well** solves both: the chain is entered
through `bx r14`, which already holds `usb_rx_chain + 1` because that is the
chain's own back edge, so no register and no literal are needed, and the
`beq rx_eop0` branch gets 44 bytes *shorter* instead of 28 longer.

Cost: one `bx` (3 cycles, flat - not the 2-3 of a `B`) of fixed delay, absorbed
by `USB_RX_LOCK_NOPS`.  `.Lprime` is now a branch target rather than a
fall-through, and `.balign 4` moved to `usb_rx_chain`, where its fill is never
executed.

The timeout constant changed with the loop shape: one decrement now covers a
whole round (`.Lwait_j`'s poll plus, if it saw a J, `.Lwait_k`'s six samples and
the re-arm) = 28 cycles on an idle J bus against the old loop's 7.  `200` would
have made the worst-case spin 5600 cycles of ISR, four times what the old count
bought; `64` holds it at 1792, and a real packet needs at most about 16 (four
rejected SYNC edges, three `.Lwait_j` polls each).

## 6. The ledger, assembled

`engine16_merged.S` asserts the delay rather than describing it.  Every
instruction between `.Lk_edge` and `.Lprime_end` costs one cycle - the IDR read
is an IOPORT access, everything else is a nop, a `movs`, a `mov` or an untaken
branch - so that block's length in halfwords is its length in cycles:

        5   the taken BPL out of SAMPLEK (lsls, then b<cond> taken)
     + 33   .Lk_edge .. .Lprime_end   (16 nops + 3 confirm + 11 prime + 3 pad)
     +  3   the BX into the chain
     = 41 = USB_RX_LOCK_DELAY

With a detection lag of 0..2.9 cycles that is the measured 9.0..11.9 of 16.
Three `.if`s enforce it: the delay above, `.Lwait_k` being six samples and the
re-arm, and nothing having crept between `.Lwait_j` and `.Lwait_k`.  The
priming block's own 14-cycle assertion is unchanged in meaning and still sits
at `usb_rx_chain`, now spanning `.Lprime .. .Lprime_end`.

## 7. What still limits it

1. **The poll period, at the ISA floor.**  3 cycles per sample is `ldr + lsls +
   untaken b<cond>`, and ARMv6-M has no load that sets flags, so a poll cannot
   be shorter.  At 700 ppm per cycle a zero-width lock centred in
   `[6.24, 16]` would reach +-0.34 %; the 2.88-cycle band costs 0.14 % of it,
   and the committed 6.88-cycle band cost 0.24 %.
2. **The 6.24-cycle dribble floor**, which is asymmetric: it consumes 6.24 of
   the cell's 16 and leaves 9.76 for the band plus both margins.  It is not a
   property of the lock but of the EOP handling - a chain that tolerated one
   extra sampled bit before SE0 would move the floor to roughly the jitter
   figure and roughly double the tolerance.  That is a change to the EOP stubs,
   not to the phase lock, and it is the largest single lever left.
3. **Packet length.**  The tolerance is `margin / (16 N)` with N ~ 89 for an
   8-byte DATA0.  Nothing in the receiver can change N.
4. **A timer input capture on D-** would time the SYNC edge to one cycle or
   better and remove item 1 entirely, at the price of a timer channel, the
   capture read, and a computed jump into a nop ladder to apply a variable
   delay.  Not attempted here; it is the only idea that beats the ISA floor.

## 8. Reproducing

    python3 tools/engine16_rx_bus.py                      # G6 window, G7 band
    python3 tools/engine16_rx_sweep.py --verify --jitter 0.6
    python3 tools/engine16_rx_sweep.py --jitter 0.6 --polls orig,out6r,back4r --k 14,15,16,17,18
    # the dribble table of section 2
    python3 -c "
    import sys; sys.path.insert(0,'tools')
    import tempfile
    from engine16_rx_sweep import *
    wd=tempfile.mkdtemp(); elf,syms=build(ENGINE,TX,wd,tag='dr')
    b=Bus(elf,syms,wire(PID,PAY),0.0,float(CELL),0.0)
    b.watch(syms['usb_pid_handle_data'])
    for d in (0.0,6.24,9.0,10.0,16.0):
        b.configure(wire(PID,PAY),0.0,float(CELL),d); b.run(16)
        print(d, bytes(b.uc.mem_read(syms['usb_rxbuf']+2,12)).hex(),
              b.calls or 'REJECTED')"

    python3 tools/engine16_rx_model.py                    # 415 packets, 0 failures
    python3 tools/prerender_check.py                      # phases 1 and 2 PASS
    for c in 0 1 2; do for f in 0 1; do
      arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb \
        -DUSB_RX_CHECK=$c -DUSB_ENGINE16_FLASH=$f -c doc/py32/engine16_merged.S -o /tmp/x.o
      python3 tools/engine16_cyc.py /tmp/x.o --exec flash --flashdata r4 --ioport r7 --budget 16
    done; done       # --exec ram and no --flashdata for FLASH=0
