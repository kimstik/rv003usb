# V-USB's engine, measured — and what it costs us to match it

Everything below is **measured** unless a line says "estimated". Every number
carries the command that produced it.

## 0. The measurement nobody had done

We had counted our engines (2692 B for the pair) and micronucleus's *shipped
bootloaders* (1342–1548 B, from release `.hex` data records). Those are not the
same kind of object: one is two bare engines, the other is a whole bootloader.
Nobody had ever built **V-USB's driver alone** and weighed it.

Toolchain: `gcc-avr 1:7.3.0+Atmel3.7.0-1`, `binutils-avr 2.26.20160125`
(Ubuntu noble), installed for this task. Target `attiny85`.

Sources on disk:
* V-USB upstream, git `8012420` (2026-08-26)
* micronucleus, vendored `usbdrv/` + its own stripped `usbconfig.h`

`usbdrv/usbdrvasm.S` is the whole assembly driver and nothing else: CRC16
helper, ISR entry, sync detect, receive engine, packet dispatch, transmit
engine. No descriptors, no `usbdrv.c` state machine, no application. It is
exactly the object our `engine16_*.S` files are.

### Command

```
avr-gcc -x assembler-with-cpp -c usbdrv/usbdrvasm.S -o out.o \
        -mmcu=attiny85 -DF_CPU=<hz> -I. -Iusbdrv -Iconfiguration/t85_default
avr-size out.o
```

### Result — V-USB assembly driver, total `.text`

| build | clock | recv CRC | bytes |
|---|---:|:--:|---:|
| micronucleus `usbdrv` + micronucleus `usbconfig.h` | 12 MHz | no | 668 |
| micronucleus `usbdrv` + micronucleus `usbconfig.h` | 16 MHz | no | 578 |
| micronucleus `usbdrv` + micronucleus `usbconfig.h` | **16.5 MHz** | no | **700** |
| micronucleus `usbdrv` + micronucleus `usbconfig.h` | 20 MHz | no | 578 |
| upstream `usbdrv` + `usbconfig-prototype.h` | 16.5 MHz | no | 724 |
| upstream `usbdrv` + prototype | 18 MHz | no | 804 |
| upstream `usbdrv` + prototype, `USB_CFG_CHECK_CRC=1` | **18 MHz** | **yes** | **1536** |

16 MHz and 20 MHz coincidentally tie at 578 B; the objects differ
(`md5sum` distinct, and the preprocessor confirms `usbdrvasm20.inc` is used
for the 20 MHz build). The stripped micronucleus config buys only 24 B over
upstream's defaults (700 vs 724) — **the config knobs barely touch the
assembly module.** The size is the engine, not the options.

## 1. The finding that reframes the whole argument

`doc/py32/VUSB_MICRONUCLEUS.md` says V-USB "uses no tables at all". That is
true **only of the variants that do not check the receive CRC.**

V-USB *can* check the receive CRC — `USB_CFG_CHECK_CRC=1`, which is legal at
exactly one clock, 18 MHz (`usbdrvasm18-crc.inc`; `usbdrvasm.S:369` errors out
at any other rate). Symbols from that build:

```
$ avr-nm -n up18crc.o | tail -2
00000400 t usbCrcTableLow
00000500 t usbCrcTableHigh
$ avr-objdump -h up18crc.o
  0 .text  00000600  ...  2**8
```

Two 256-byte lookup tables, `.balign 256` (`usbdrvasm18-crc.inc:637`).

**When V-USB decides to verify the receive CRC, it reaches for exactly the
tool we did: a byte-indexed lookup table.** It needs 512 B of it, it needs a
higher clock than any of its other variants, and the driver goes from 804 B to
1536 B at the same 18 MHz.

Measured cost of adding a receive CRC check, in V-USB's own idiom, same clock,
same config, one flag changed:

| | bytes |
|---|---:|
| 18 MHz, `USB_CFG_CHECK_CRC=0` | 804 |
| 18 MHz, `USB_CFG_CHECK_CRC=1` | 1536 |
| **delta** | **+732** |

Of that +732: 512 B is tables, 184 B is `.balign 256` padding in an isolated
object (partly recoverable when linked into a real image), and ~36 B is code.

So the CRC decision costs V-USB roughly as much as its entire non-checking
driver. Our 1024 B of `T_CRC16` across two engines is not an outlier — it is
the same order as what the authors of V-USB paid the one time they took the
same contract, and they only paid it once because they only have one table
pair.

## 2. Normalisation — what is in and what is out

A comparison whose normalisation is not stated is worthless, so here it is.

**In, on both sides:** the interrupt handler from its first instruction to
the point where a decoded packet is handed to C — entry, SYNC detection,
the unrolled bit engine, the bit-stuffing escapes, end-of-packet, the byte
store, PID decode, device-address filtering, and the return path.

**Out, on both sides:** descriptors, the enumeration state machine, the
control-transfer logic, `usbdrv.c` / our C layer, the transmit engine
(counted separately), and the application.

**Asymmetries that remain, and which way they cut:**

| | V-USB | ours (`engine16_merged.S`) | `engine16_minimal.S` |
|---|---|---|---|
| receive CRC16 | **no** | yes, folded into the bit cell | **no** |
| token CRC5 | no | yes | no |
| device-address filter | **yes** (`asmcommon.inc:65-70`) | yes | yes |
| buffer bound | runtime (`subi cnt / brcs`) | structural (masked index) | structural |
| SE0 test | once per byte | every bit | once per byte |
| unstuffing | flags, no table | 256 B table, branchless | flags, no table |
| oscillator trim | separate file (`osccal.S`) | hooks in the ISR | no |
| tables | 0 B (512 B if CRC on) | 800 B chargeable to receive | **0 B** |

The `engine16_minimal.S` column exists so that the last two rows can be
compared without arguing: it is a real object on this core making V-USB's
choices, not a projection.

**Correction to `VUSB_MICRONUCLEUS.md`.** That note said the address filter
was ours alone. It is not. `asmcommon.inc:65-70` does
`lds shift,usbDeviceAddr / ldd x2,y+1 / lsl x2 / cpse x2,shift / rjmp
ignorePacket` — the same test, in the dispatch rather than the engine. It is
not a source of size difference and must not be claimed as one.

## 3. The like-for-like table

Every row is `size` on an object built by the command shown in §0 (AVR) or

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c <f>.S
arm-none-eabi-size -A <f>.o        # .datacode is the code section here
arm-none-eabi-nm -n <f>.o          # region boundaries
```

Regions are cut at symbol boundaries, so they add to the total exactly.

### V-USB, ATtiny85, micronucleus config

| region | 12 MHz | 16.5 MHz |
|---|---:|---:|
| `usbCrc16` / `usbCrc16Append` (transmit only) | 66 | 44 |
| ISR entry + SYNC detect | 54 | 64 |
| **receive engine** — inline bit slots | 118 | 194 |
| **receive engine** — unstuff escapes | 76 | 122 |
| packet dispatch (`asmcommon.inc`) | 154 | 140 |
| transmit engine | 168 | 136 |
| **total driver** | **668** | **700** |

### Ours, PY32 Cortex-M0+, 24 MHz, 16 cycles per bit

| region | `engine16_minimal.S` | `engine16_merged.S` |
|---|---:|---:|
| ISR entry (+ keep-alive exit, literals) | 58 | — |
| SYNC detect + pipeline priming | 94 | — |
| **receive engine** — the eight timed cells | 250 | 244 |
| **receive engine** — unstuff escapes | 224 | 0 (table) |
| branch trampolines | 4 | — |
| dispatch + ISR tail (+ literals) | 82 | — |
| entry, EOP stubs, flush path, dispatch, tail | — | 836 |
| **total code** | **712** | **1080** |
| tables chargeable to receive | **0** | 288 exclusive + 512 shared |

`engine16_merged.S` is not cut into all the same regions because its entry,
flush path and dispatch are one interleaved body; the two numbers that are
cut identically — the eight timed cells — are the ones the comparison turns
on, and they are 250 against 244. The rest of its 1080 B is the flush path
(a second, untimed copy of the pipeline), the CRC16 and CRC5 folding, the
per-bit SE0 test, and the oscillator-calibration hooks.

Table block, measured from `engine16_tx.S` (`.datacode` 1656 B, code ends at
`usb_tables` = 600 B, so 1056 B of table):

| table | bytes | used by |
|---|---:|---|
| `T_UT` unstuff + accumulate | 256 | receive |
| transmit dispatch + `T_TX` | 256 | transmit |
| `T_CRC16` | 512 | **both** |
| `T_CRC5` | 32 | receive |

Pair total as the tree stands: `engine16_merged.S` 1080 + `engine16_tx.S`
1656 = **2736 B**. (`RAM_BUDGET.md` records 3180 B; that predates the
shared-`T_CRC16` merge which this measurement includes.)

### The one number the whole argument was missing

| object | bytes | checks receive CRC |
|---|---:|:--:|
| V-USB receive engine alone, 12 MHz | **118 + 76 = 194** | no |
| V-USB receive engine alone, 16.5 MHz | **194 + 122 = 316** | no |
| our receive engine on V-USB's terms | **250 + 224 = 474** | no |
| our receive engine as it stands (cells + its table) | 244 + 800 | yes |
| V-USB whole assembly driver, 16.5 MHz | 700 | no |
| our whole receive ISR on V-USB's terms | 712 | no |
| micronucleus whole bootloader, t85 | 1514 | no |

## 4. Where the extra bytes actually go

### 4.1 The bit slot: six instructions against nine

V-USB, `usbdrvasm12.inc:175-181`, the whole of one bit:

```
in   x1, USBIN       ; sample
andi shift, 0xf9     ; six ones? mask depends on the bit position
breq unstuff0        ; escape
eor  x2, x1          ; NRZI against the previous sample
bst  x2, USBMINUS    ; that bit -> T
bld  shift, 1        ; T -> bit 1 of the shift register
```

`engine16_minimal.S`, the same bit:

```
ldr  r1, [r7, #IDR]  ; sample
eors r2, r1          ; NRZI against the previous sample
ands r2, r6          ; isolate D-        \  no bst
lsrs r3, r2, #(4-n)  ; align to bit n     > three where AVR uses two
orrs r0, r3          ; deposit           /
lsrs r2, #5          ; the bit -> C      \  no AND-immediate, so the
adcs r4, r4          ; -> history reg     > stuffing history needs its
lsls r3, r4, #26     ; six ones?         /  own register: four where
beq  .Lunstuff0      ; escape            \  AVR uses two
```

Both are 2-byte instructions, so the slot is 12 B against 18 B. Multiplied
by eight, that is **+48 B per byte-unroll** — the gross ISA charge. Some of
it comes back in the escapes (§4.2) and §5.3 nets it out. Two ISA facts
produce all of it:

* **No `bst`/`bld`.** AVR moves one bit from any position to any position in
  two instructions via the T flag. ARMv6-M has no single-bit transfer, so
  extract-and-deposit is `ands` + shift + `orrs`. **+1 per bit.**
* **No AND-immediate.** V-USB's stuffing test is free of extra state because
  `andi shift,0xf9` masks *the data register itself*, in place, with a
  different immediate per bit position — which is also the only reason
  V-USB must unroll. Thumb-1 has no AND-immediate; an in-place rotating
  window would need eight mask constants in eight registers we do not have.
  So the history goes in a second register. **+2 per bit.**

### 4.2 The escapes: the cell width, not the word width

| | per escape | eight escapes |
|---|---:|---:|
| V-USB, 12 MHz (8-cycle cell) | 8–12 B | 76 B |
| V-USB, 16.5 MHz (11-cycle cell) | 8–30 B | 122 B |
| ours, 24 MHz (16-cycle cell) | 28 B | 224 B |

An unstuff escape must consume one whole bit time and has almost nothing to
do in it. Its size is therefore governed by the cell width, and 156 of the
349 instructions in `engine16_minimal.S` — **312 of its 712 bytes** — are
`nop`. Broken down by region:

| region | nops | bytes of nop |
|---|---:|---:|
| eight unstuff escapes | 88 | 176 |
| eight timed cells | 39 | 78 |
| SYNC detect + priming | 29 | 58 |
| **total** | **156** | **312** |

This is a consequence of choosing 24 MHz, not of the word width. We chose
24 MHz *because* the per-bit instruction count is higher — at 12 MHz the
nine instructions would not fit at all. It is a self-inflicted cost, but the
alternative was not fitting.

V-USB pads too — with `nop`, `nop2` and `lpm` — but far less. Counted the
same way (`avr-objdump -d | grep -cE '\snop$|\slpm$|rjmp\s+\.\+0'`, times
2 B per AVR word):

| receive path only (entry + SYNC + engine + dispatch) | total | padding | net |
|---|---:|---:|---:|
| V-USB 12 MHz (`0x42`–`0x1f3`) | 434 | 62 (14%) | 372 |
| V-USB 16.5 MHz (`0x2c`–`0x233`) | 520 | 68 (13%) | 452 |
| `engine16_minimal.S` (whole file) | 712 | 312 (44%) | **400** |

Cutting it finer, to the **receive engine proper** — the eight inline bit
slots plus the eight unstuff escapes, nothing else:

| | slots raw | slots net | escapes raw | escapes net | engine raw | engine net |
|---|---:|---:|---:|---:|---:|---:|
| V-USB 12 MHz | 150 | 140 | 76 | 54 | 226 | **194** |
| V-USB 16.5 MHz | 194 | 184 | 122 | 96 | 316 | **280** |
| `engine16_minimal.S` | 250 | 172 | 224 | 48 | 474 | **220** |

Net of padding our engine sits *between* V-USB's two, and the reason is not
flattering to either side of a naive reading:

* our **inline slots** are bigger than the 12 MHz ones, exactly as §4.1
  predicts — 172 B net for 86 instructions against 140 B for 70;
* our **escapes** are smaller net — 48 B for 24 instructions (three each:
  break the run, sample the stuffed bit, rejoin) against V-USB's 54 B,
  because with the stuffing history in its own register there is no data bit
  to corrupt and no repair to make. V-USB's escapes must carry `andi x3,~0x80
  / ori shift,0x80` and its byte tail must carry `eor x3,shift / ser x3` to
  undo them. **That is the one place the second register gives bytes back.**
* V-USB's 16.5 MHz engine is 86 B larger than its own 12 MHz one because
  16.5 MHz is 11 cycles per bit, not a whole number of samples per cell, so
  that variant carries a software PLL (`phase`, the extra `in`/`or` pairs)
  that neither its 12 MHz version nor our 24 MHz one needs.

So the raw byte count answers the owner's question badly on its own: net of
timing filler our engine is **+13%** against the very structure it copies
(220 B against 194 B), and the filler is a consequence of the clock.

### 4.3 The ledger our features actually pay into

Tested, not assumed:

| our feature | does it pay in bytes? | mechanism |
|---|---|---|
| 32-bit registers | **once, and it is cancelled** | A 32-bit history register makes the six-bit window contiguous and never-wrapping, so the test is one shift with the **same** immediate at every bit position — no eight mask constants, and V-USB's only reason to unroll disappears. But needing a *second* register at all is what costs the +2. Net: the width pays for the thing the missing AND-immediate charged us. |
| register-offset addressing | **yes, and it is the structural bound** | `strb r0,[r3,r5]` with `r5` masked to five bits makes "cannot address outside the buffer" a property of the arithmetic. Cost: 2 instructions per byte (`lsls`/`lsrs`), both in slack that was otherwise `nop`. **Free.** V-USB gets a 1-instruction store from `st Y+` (post-increment, which Thumb-1 lacks) but then pays 2 more for a runtime check that is weaker. |
| `ldm`/`stm` | **no, not in the engine** | There is nothing to move in bulk inside a bit cell. It pays once per interrupt: `push {r4-r7,lr}` is one instruction where AVR's `PUSH_STANDARD` is five. Worth ~10 B, once, at the edges. |
| barrel shifter | **yes, small** | `lsls r3,r4,#26` isolates six bits with no mask constant and no literal load. AVR would need the constant. |
| **missing** `bst`/`bld` | costs +1 per bit | §4.1 |
| **missing** AND-immediate | costs +2 per bit | §4.1 |
| **missing** post-increment store | costs +1 per byte | §4.3 above |
| **taken branch is 2–3, not 2** | costs correctness, not bytes | §4.4 |

### 4.4 The cost that is not measured in bytes

On AVR, `breq` taken is exactly 2 cycles. On this core a taken conditional
branch is 2–3 and `ENGINE16_SPEC.md` §2 says the source does not state which.
V-USB's structure puts a data-dependent taken branch inside every bit cell —
the unstuff escape. Each stuffed bit therefore admits up to one cycle of
phase error, and a maximal DATA0 packet carries about twelve of them: up to
three quarters of a bit cell of drift, accumulated, with no mechanism to
recover it.

`tools/engine16_cyc.py --exec ram --ioport r7 --budget 16` puts all eight
cells of `engine16_minimal.S` at **exactly 16 cycles** on the fall-through
path, and every escape at 15–16. So the V-USB structure *fits* our budget.
It is the branch ambiguity, not the cycle count, that makes it unsafe here —
and removing that ambiguity is precisely what `engine16_merged.S` buys with
its 256-byte `T_UT`: a branchless bit cell.

Flash-resident, the same tool reports cell 7 at 18 cycles, because the byte
store is a 4-cycle RAM access from flash-resident code instead of 2. That is
the same finding `engine16_merged.S` records, and the same reason both files
are `.datacode`.

## 5. The three answers

### 5.1 If we adopt their approach fully, what is the byte count?

**Measured, not estimated: 712 B** for the whole receive interrupt handler —
entry, keep-alive exit, SYNC detection, the eight timed cells, the eight
unstuff escapes, end-of-packet, PID decode, address filter, hand-off and
return — with **zero bytes of table**. That is `engine16_minimal.S`, built
and weighed.

Against what it replaces on the receive side as the tree stands:

| | bytes |
|---|---:|
| `engine16_merged.S` code | 1080 |
| its exclusive tables (`T_UT` 256 + `T_CRC5` 32) | 288 |
| its half of the shared `T_CRC16` | 256 |
| **receive side today** | **1624** |
| **receive side on V-USB's terms** | **712** |
| **difference** | **−912 B (−56%)** |

What those 912 B buy today, and would be given up:

* the receive CRC16 check, and with it the ability to refuse to ACK a
  corrupted packet — V-USB ACKs first and says so (`usbdrv.c:580-586`);
* the token CRC5 check;
* a branchless bit cell, i.e. immunity to the 2-vs-3 taken-branch ambiguity
  (§4.4) — this is the one that decides the question, not the byte count;
* SE0 detected on the exact bit rather than at the next byte boundary.

The transmit side is **not** measured here. `engine16_tx.S` is 600 B of code
plus 256 B of exclusive table against V-USB's 136 B + 44 B CRC helper at
16.5 MHz, but no equivalent minimal transmit engine was built, so no number
for "ours on their terms" is claimed. Saying anything more would be an
estimate, and this document does not mix the two.

### 5.2 Is 32-bitness a help, a wash, or a handicap?

**A wash inside the engine, a small help at the edges — and the expectation
in the brief was right about the mechanism but wrong about which half
cancels which.**

The expectation was: no `bst`/`bld` costs instructions per bit; 32-bit
registers save nothing because a USB byte is 8 bits and the state is small;
`ldm`/`stm` and register-offset addressing may pay elsewhere. Tested:

* **`bst`/`bld`: confirmed, +1 instruction per bit.** No single-bit transfer
  in ARMv6-M.
* **A second, larger effect the expectation missed: no AND-immediate, +2
  instructions per bit.** V-USB's stuffing test is free because `andi` masks
  the data register in place with a per-position immediate. Thumb-1 cannot,
  so the stuffing history needs its own register. Together: 9 instructions
  per bit against 6, **+48 B per byte-unroll**.
* **32-bit registers do help, once, and it is precisely this second effect
  they mitigate.** A 32-bit history register makes the six-bit window
  contiguous and never-wrapping, so the test is one shift with the *same*
  immediate at every bit position — no eight mask constants, and no
  per-position code at all in the escape. Measured consequence: our escapes
  are 48 B net against V-USB's 54 B, because there is no faked data bit to
  repair and no `andi x3 / ori shift / eor x3 / ser x3` fixup chain (§4.2).
  **−6 B, against the +48 B.** Not "nothing", but it does not come close to
  paying for itself.
* **`ldm`/`stm`: no.** There is nothing to move in bulk inside a bit cell.
  It pays once per interrupt in the prologue and epilogue — `push {r4-r7,lr}`
  where AVR needs five pushes — worth roughly 10 B, once. The expectation
  that it "may pay elsewhere" is true but the elsewhere is small.
* **Register-offset addressing: yes, and this is the real win.**
  `strb r0,[r3,r5]` with `r5` masked to five bits makes the buffer bound a
  property of the address arithmetic rather than a runtime test. It costs
  two instructions per byte and both land in slack that was `nop`, so on
  this engine it is **free**, and it is strictly stronger than V-USB's
  `subi cnt,1 / brcs overflow`. AVR's compensating feature is post-increment
  (`st Y+`, one instruction), which Thumb-1 lacks; net, roughly even on
  cycles and better on safety.

The mechanism, stated once: **a USB bit is one bit and a USB byte is eight,
so register width buys nothing on the data path.** It buys something only
where the *history* is wider than the datum — the six-bit stuffing window,
which straddles byte boundaries. That is exactly one place, and net of
timing filler it is worth 6 B. Everything else about being 32-bit is neutral
here.

The genuine handicap is not width at all. It is that **a taken conditional
branch costs 2–3 cycles instead of a fixed 2** (§4.4), which makes V-USB's
entire escape-based structure unsound on this core regardless of size. That
is what the 256-byte `T_UT` in `engine16_merged.S` is actually buying.

### 5.3 Close to their size, or bigger? By how much?

**Bigger, by about 1.4× on the receive path — and about 15% once timing
filler is removed.** Both numbers are needed; either alone misleads.

| comparison | ours | V-USB 16.5 | V-USB 12 | ratio (vs 16.5) |
|---|---:|---:|---:|---:|
| whole receive ISR, as assembled | 712 | 520 | 434 | **1.37×** |
| receive engine proper, as assembled | 474 | 316 | 226 | 1.50× |
| receive engine proper, **net of padding** | 220 | 280 | 194 | **0.79×** |

`usbdrvasm12.inc` is the structure `engine16_minimal.S` actually copies, so
the 12 MHz column is the structural reference; the 16.5 MHz column is the
one whose clock is closest to ours. Against 12 MHz, net of padding, we are
220 against 194 — **+13%**.

The full split of the 248 B gap against the 12 MHz build. Every row is
measured (`avr-objdump`/`arm-none-eabi-objdump` over the symbol ranges), and
the rows sum exactly:

| | ours | V-USB 12 | gap |
|---|---:|---:|---:|
| inline slots, real instructions | 172 | 140 | **+32** |
| inline slots, timing filler | 78 | 10 | **+68** |
| escapes, real instructions | 48 | 54 | **−6** |
| escapes, timing filler | 176 | 22 | **+154** |
| **total** | **474** | **226** | **+248** |

Read it as two lines:

* **Real instructions: +26 B.** That is the whole irreducible difference,
  and it is smaller than the raw +48 B the ISA charges per byte-unroll
  (24 extra instructions × 2 B) because the escapes hand −6 B back and
  V-USB's 12 MHz variant carries a 32 B first-byte preamble (`haveTwoBitsK`)
  our phase lock makes unnecessary. **26 bytes** is what being this core
  instead of an AVR costs the receive engine.
* **Timing filler: +222 B.** Ours is a 16-cycle cell against their 8. This
  is our choice of clock — and it is forced by the first line, because nine
  instructions per bit cannot fit in eight cycles.

So: **bigger, and by 1.4–1.5× as assembled; but the part that is not ours to
choose is 26 bytes.** The 800 B of tables in the engine we actually ship are
not in this gap at all. They are the price of a contract V-USB declined —
checking the CRC — and V-USB itself pays **+732 B** and a forced move to
18 MHz the one time it takes that contract (§1).

**The honest summary for the owner:** we are not close to micronucleus's
whole 1342–1548 B bootloader while spending 1624 B on receive alone, and
adopting V-USB's contract would cut that to 712 B. But the engine itself is
not where we are fat — net of timing padding it is 220 B against their 280.
We are fat because we check the CRC, and V-USB's own numbers say that is
what checking the CRC costs.
