# PY32 — build facts verified by actually building (not by reading)

Everything here was produced by running the toolchain in this container on
2026-09-04, against the `py32` branch at 0ad3c42 ("Port demo_gamepad to Arm
Cortex M0+ (PUYA PY32)"). Commands are given so any claim can be re-run.
Nothing in this file is inferred from documentation.

## 1. Toolchain present in the container

| tool | version | note |
|---|---|---|
| `arm-none-eabi-gcc` | 13.2.1 20231009 (Ubuntu 15:13.2.rel1-2) | sufficient for M0+ Thumb work |
| `arm-none-eabi-gdb` | MISSING | not needed for static work |
| `riscv64-unknown-elf-gcc` | 13.2.0 | the V003/WG015 side |
| `riscv-none-elf-gcc` (xpack 15.2) | MISSING | lost in the container rollback, see doc/wg015/TODO.md item 22 |
| `clang-21` (+ `xwchc`) | MISSING | same rollback; V003-track only, not needed for PY32 |

The ARM side is complete enough that every PY32 implementation task can be
compiled, linked and disassembled statically. No task may be gated on hardware.

## 2. The engine assembles — both per-part variants

```
cd <py32 branch worktree>
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb \
    -D<PART>=1 -Idemo_gamepad -Irv003usb -Ilib \
    -c rv003usb/rv003usb-arm.S -o /tmp/arm_<PART>.o
```

`-DPY32F002Bx5=1` -> rc=0. `-DPY32F003x4=1` -> rc=0.

So the `#if PY32F002Bx5` variant in `rv003usb-arm.S` is syntactically live in
both directions. What is dead is the *selection* of it: `Makefile.py32` pins
`MCU_TYPE = PY32F002Bx5`, so the non-F002B arm of every `#if` has never been
built by the branch's own build system. State the defect that way — "never
selected", not "never assembles".

Sizes differ by 4 bytes (`.text` 0x1fc vs 0x200), which is the whole footprint
of the per-part difference.

## 3. Section split — RX runs from RAM, TX runs from FLASH

From `arm-none-eabi-objdump -h` on the object:

| section | size | contents |
|---|---|---|
| `.datacode` | 0xfc (252 B) | `EXTI2_3_IRQHandler`, `preamble_loop`, `packet_type_loop`, `pt_got_one/zero`, `is_end_of_byte`, `bit_process`, `pl_got_one`, `after_rx_stuffed`, `rx_stuffed`, `se0_complete`, `handle_se0_keepalive`, `done_usb_message` — the whole hard-real-time RX sampling path |
| `.text` | 0x200 (512 B) | `se0_complete_flash` and the token-dispatch tail, **plus the entire TX path**: `usb_send_empty`, `usb_send_data`, `pre_and_tok_send_inner_loop`, `pre_and_tok_send_one_bit`, `load_next_byte`, `send_inner_loop`, `flip_bus`, `send_one_bit`, `send_end_bit_complete`, `done_sending_data`, `insert_stuffed_bit`, `no_really_done_sending_data` |
| `.bss.rxbuf` | 0xf | RX buffer, RAM |

This asymmetry is the single most important input to the cycle ledger: the
RX path must be costed with the RAM-execution column and the TX path with the
flash-execution column. They are not one number.

## 4. How `.datacode` reaches RAM — a naming trick, not a linker rule

There is **no `.datacode` rule anywhere** — not in the branch, not in the
`py32f0-template` submodule (IOsetting/py32f0-template, pinned 289ffc8):

```
grep -rln 'datacode' <branch> --include=*.ld --include=*.mk --include=Makefile*
grep -rln 'datacode' <py32f0-template>
# both empty
```

It works anyway. Linking the engine object against the stock
`Libraries/LDScripts/py32f003x4.ld`:

```
arm-none-eabi-gcc -mcpu=cortex-m0plus -mthumb -nostdlib -nostartfiles \
    -T py32f003x4.ld arm_PY32F003x4.o -o link_test.elf \
    -Wl,--defsym=_estack=0x20000800 -Wl,--unresolved-symbols=ignore-all
```

gives

```
 1 .text   00000200  VMA 08000000  LMA 08000000   CODE      <- TX path, flash
 3 .data   000000fc  VMA 20000000  LMA 08000200   CODE      <- .datacode, RAM
 4 .bss    00000010  VMA 200000fc
```

`.datacode` is absorbed by the script's `*(.data*)` wildcard (py32f003x4.ld:118),
so it gets VMA in RAM, LMA in flash, and is copied out by the standard startup
`_sidata`/`_sdata`/`_edata` loop. The design is correct; the mechanism is
**incidental**.

Consequences the port must handle explicitly rather than inherit:
* A linker script that spells the rule `*(.data) *(.data.*)` — with a dot —
  does NOT match `.datacode`. The section would then fall through as an orphan
  and be placed in flash, silently, with no error and no warning. The RX engine
  would run XIP and every timing figure in the ledger would be wrong.
* Our port must therefore carry its own linker script with an explicit,
  named RAM-code rule and a `ASSERT`/symbol check that the section's VMA is in
  the RAM region. That is a build-time check, not a bench check.

## 5. Literal pools — measured, and why the "no flash literal" rule needs care

Counting `ldr rN, [pc, #imm]` and resolving the targets:

* `.datacode`: 8 PC-relative loads; gcc emitted the pool **inside `.datacode`
  itself** (`.word`s at 0xdc..0xf8, within the 0xfc section). The section is
  copied to RAM, so those loads read RAM = 2 cycles, not 4. Three of them are
  inside timed loops (0x2c in `preamble_loop`, 0x3e and 0x40 in
  `packet_type_loop`). Pool: `0x50000400` (GPIOB base), `0x00000009`,
  `0x00000003`, `0x0000ffff`, `0x0000a001`.
* `.text`: 17 PC-relative loads, pool at 0x1b8..0x1fc, in flash = 2 cycles from
  flash-resident code. One (at 0xda) is inside `pre_and_tok_send_one_bit`, i.e.
  inside a timed TX bit cell. Pool includes `0x40021800`, `0x50000400`,
  `0x00080001`, `0xffffff3c`, `0x00000041`, `0x0000a001`, `0x00090009`,
  `0x0000ffff`.

So the rule "no flash literal-pool load inside a timed bit cell" is satisfied
today **by construction, not by enforcement** — it depends on where gcc chose to
put the pool. It must become a mechanical check: disassemble, resolve every
`ldr rN, [pc, #imm]` target, assert it lands in a section that is RAM-resident
at run time. That check is exactly reproducible with the commands above.

Note the trap in the other direction: `.text+0xda` becomes a 4-cycle load if the
TX path is ever relocated to RAM, and `load_next_byte` in flash-resident TX
reads packet bytes from RAM at 4 cycles today. Whether moving TX to RAM is a net
win is an arithmetic question for the ledger, not a matter of taste.

## 6. Dependency availability

`py32f0-template` is an empty submodule on the branch, so the branch **cannot
link as published**. Upstream is reachable and was cloned at the pinned commit:

```
git clone https://github.com/IOsetting/py32f0-template.git
git checkout 289ffc80de237c46171635b0f03b76b1a7f765be
```

It provides `Libraries/LDScripts/{py32f002ax5,py32f002bx5,py32f003x4,py32f003x6,
py32f003x8,py32f030x6,py32f030x8}.ld`, CMSIS device headers, LL drivers and the
`startup_py32f00{2a,2b,3,30}.s` files.

Memory geometry from those scripts (verified, not from a datasheet):

| part | RAM | FLASH |
|---|---|---|
| PY32F003x4 | 2K @ 0x20000000 | 16K @ 0x08000000 |
| PY32F002Bx5 | 3K @ 0x20000000 | 24K @ 0x08000000 |

2K of RAM on the F003x4 must hold the 252 B RAM-resident engine, its literal
pool, `rxbuf`, `.data`, `.bss` and the stack. Tight but not close to the limit;
the number belongs in the plan rather than an assumption that it is fine.

Open decision, not settled here: whether to vendor the handful of files we need
(linker script, startup, a minimal device header) the way the WG015 port vendors
`K1921VG015_min.h`, or to carry the submodule. The WG015 precedent and the
licence question both argue for vendoring a minimal self-written header.

## 7. Register map — verified against the vendor headers, both families

Checked in `Libraries/CMSIS/Device/PY32F0xx/Include/{py32f030x8.h, py32f002bx5.h}`
from the pinned template, against the constants the engine actually uses.

| symbol | F030/F003 | F002B | engine uses |
|---|---|---|---|
| `IOPORT_BASE` | 0x50000000 | 0x50000000 | — |
| `GPIOA_BASE` | 0x50000000 | 0x50000000 | — |
| `GPIOB_BASE` | 0x50000400 | 0x50000400 | literal `0x50000400` in **both** `.text` and `.datacode` pools |
| `AHBPERIPH_BASE` | 0x40020000 | 0x40020000 | — |
| `RCC_BASE` | 0x40021000 | 0x40021000 | — |
| `EXTI_BASE` | 0x40021800 | 0x40021800 | literal `0x40021800` in the `.text` pool |

`GPIO_TypeDef` field order is byte-for-byte identical in the two headers:
MODER 0x00, OTYPER 0x04, OSPEEDR 0x08, PUPDR 0x0C, **IDR 0x10**, ODR 0x14,
**BSRR 0x18**, LCKR 0x1C, AFR[2] 0x20-0x24, BRR 0x28. The engine's
`#define IDR_OFFSET 0x10` and `#define BSRR_OFFSET 0x18` (arm.S:14-15) are
correct for both families.

Two conclusions worth carrying into the plan:

* **The target flip costs nothing in the engine's register layer.** Every base
  address and every register offset the bit-bang path touches is identical
  across F002B and F030/F003. Whatever else the flip changes — clock bring-up,
  calibration, RAM budget — it does not change a single address in the timed
  code. Any argument against the flip on portability grounds is unfounded.
* GPIO sits at `IOPORT_BASE` = 0x5000_0000, i.e. on the M0+ **IOPORT** bus
  rather than APB. That is the architectural reason behind the measured "доступ
  к портам на полной скорости", and it is why port access stays cheap from both
  flash-resident and RAM-resident code. This is corroboration of the measurement
  from a second, independent source, not a restatement of it.

Not checked here, and still open: whether the F002B *clock* tree exposes the
same fields at the same offsets (it does not — the F002B HSI trim path is the
whole reason for the flip), and whether the five `#if PY32F002Bx5` sites in the
engine (arm.S:402, 415, 444, 490, 530) differ for register reasons or for
timing reasons. Since the register map is identical, timing is the likelier
explanation and someone must read those five sites and say which.

## 8. What the five per-part `#if` sites actually do — read, not guessed

§7 left open whether the `#if PY32F002Bx5` sites differ for register reasons or
timing reasons. Reading all five (arm.S:402, 415, 444, 490, 530) settles it:
**every one is cycle padding in the TX path. Not one touches a register, an
address, or a bit position.**

| site | F002B | F003/F030 (the `#else`) |
|---|---|---|
| arm.S:402 | `b .+2` + `.balign 4` | two `nop` |
| arm.S:415 | `bcs pre_and_tok_delay_one_bit` then `mov SCRATCH, FLIP_MASK` | the two reordered, plus an assembler alignment assertion |
| arm.S:444 | one extra `nop` | — |
| arm.S:490 | one extra `nop` | — |
| arm.S:530 | — | one extra `b .+2` inside `insert_stuffed_bit` |

Net: F002B carries two extra `nop`, F003/F030 one extra `b .+2`, which is
exactly the 4-byte `.text` difference measured in §2 (0x1fc vs 0x200).

This is a direct corroboration of the two-column cost model: the two variants
exist *because the same instruction sequence does not take the same number of
cycles on the two dice*. It is the branch author having hit, empirically, the
same thing the Xiamatsu measurements describe.

### The alignment assertion in the never-selected arm — and it passes

The `#else` arm at arm.S:415 carries a build-time guard that has never been
evaluated by the branch's own build system, because `Makefile.py32` pins
`MCU_TYPE = PY32F002Bx5`:

```
.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2
	.error "pre_and_tok_send_inner_loop must be unaligned"
.endif
```

Assembling with `-DPY32F003x4=1` succeeds (§2, rc=0), so the assertion does
**not** fire: the label really does sit at an odd halfword offset, as the code
requires. Since `.text` is 4-byte aligned and the offset of `usb_send_data`
within the section is fixed, the property survives linking.

That is a small but real piece of good news for the target flip: the arm that
becomes primary carries a correctness constraint its author could not test, and
it holds. It also means the constraint is live — anyone inserting or removing a
halfword ahead of `pre_and_tok_send_inner_loop` will now break the build rather
than the timing, which is the desirable failure mode and worth preserving
deliberately rather than by luck.

The open item that remains is narrower than before: not "what do the variants
differ over" (answered: padding) but "are F002B's pad counts still right, and
are F003/F030's, under the corrected cost model" — which is ledger and bench
work, not archaeology.

## 9. The branch builds end to end — for all three candidate parts

With `py32f0-template` supplied at its pinned commit (§6), `demo_gamepad` builds
to a linked image for every part the port cares about. From a genuinely clean
tree (see the warning in §10 — this matters):

| MCU_TYPE | RAM used | RAM total | flash used | flash total | RAM % |
|---|---|---|---|---|---|
| PY32F030x8 | 2128 B | 8K | 2908 B | 64K | 25.98 % |
| PY32F003x4 | 1616 B | 2K | 2132 B | 16K | **78.91 %** |
| PY32F002Bx5 | 1168 B | 3K | 2696 B | 24K | 38.02 % |

So there is a working static baseline for the implementer fleet: not a plan to
build, an image that links today.

**The RAM budget question is answered with a number.** On the smallest member of
the newly-primary family, F003x4, the demo already occupies 78.91 % of 2K,
leaving about 432 B. That is the figure the plan should carry rather than an
assumption that 2K "should be enough". It includes a 192 B RAM vector table
(`.ram_vector` at 0x20000000) and a 1028 B heap/stack reservation, both of which
are tunable — but it also does not yet include anything the port adds.

### The placement split, confirmed in a real link

Symbol addresses from the linked `demo_gamepad.elf` (F003x4), not from a
synthetic object:

```
200000c8 T EXTI2_3_IRQHandler      <- RAM
200000e6 t preamble_loop           <- RAM
20000142 t bit_process             <- RAM
2000023c b rxbuf                   <- RAM
0800022c T usb_send_data           <- FLASH
```

This settles §3 and §4 in the strongest available form: the RX engine really does
execute from RAM in a fully linked image, and the TX engine really does execute
from flash.

## 10. Two build-system defects found by being bitten by them

### 10.1 Objects escape the build directory and are not keyed by part

`rules.mk` maps a source at `$(TOP)/<path>.c` to `$(BDIR)/<path>.o`, and since
sources are reached through `../`, the objects land *outside* `Build/`:

```
demo_gamepad/rv003usb/rv003usb-arm.o
demo_gamepad/py32f0-template/Libraries/CMSIS/.../startup_py32f003.o
demo_gamepad/py32f0-template/Libraries/PY32F0xx_LL_Driver/Src/*.o
```

Two consequences, both real:
* `rm -rf Build` does **not** clean the tree. Most of the objects survive it.
* Object paths carry no `MCU_TYPE`, so changing part silently reuses objects
  compiled with a different `-D<PART>` and a different device header.

This is not theoretical. It produced a wrong result during this very
investigation: after building F003x4, an `rm -rf Build` and a rebuild as
PY32F030x8 failed to link with `undefined reference to BSP_RCC_HSE_PLLConfig`,
and the obvious reading — "F030 does not build" — was **wrong**. The F003-compiled
BSP objects had been reused, and F003 does not compile that function (§10.2).
From a properly clean tree F030x8 builds fine. Any conclusion drawn from a
part-to-part rebuild on this build system is untrustworthy unless the tree was
cleaned with `find . -name '*.o' -delete` first.

The port's own build must key object paths by part, or the build matrix (D-3)
will produce confident nonsense.

### 10.2 `RCC_PLL_SUPPORT` is defined only for F030 — and this touches the flip

In the vendor CMSIS headers from the pinned template:

| part | `RCC_PLL_SUPPORT` |
|---|---|
| py32f030x6, py32f030x8 | **defined** |
| py32f003x4, x6, x8 | not defined |
| py32f002ax5, py32f002bx5 | not defined |

`BSP_RCC_HSI_PLL48MConfig()` — which is exactly the HSI 24 MHz x PLL2 = 48 MHz
path the target flip depends on, and which sets
`LL_RCC_HSI_SetCalibFreq(LL_RCC_HSICALIBRATION_24MHz)` then
`LL_PLL_ConfigSystemClock_HSI()` — lives inside `#if defined(RCC_PLL_SUPPORT)`
(`py32f0xx_bsp_clock.c:57-89`). **For F003 the vendor library will not compile a
PLL path at all.**

This directly contradicts the measured claim in `CHIP_FACTS_XIAMATSU.md` §2,
which cites «Проверено — PLL запускается на 48 МГц на чипах PY32F002A и
PY32F003» (xm_030.md:336). One of the two is wrong about F003, and the
disagreement is not resolvable from documents:

* If the measurement is right, the vendor header is conservative (plausible —
  these parts are widely believed to be one die with different markings), and
  the port must bring the PLL up **against registers directly**, because the LL
  library will not do it for F003.
* If the header is right, the flip's primary path is **F030 only**, and F003 is
  in the same boat as F002B.

Either way the plan must not assume it gets an F003 PLL from the vendor library.
This belongs in the open questions with a specific bench measurement: on an F003
part, write the PLL registers by hand and measure the resulting clock.

### 10.3 An F003 build silently configures no clock at all

`demo_gamepad.c:15-23` sets the clock only for two parts:

```
#if PY32F002Bx5
	BSP_RCC_HSI_48MConfig();
#elif PY32F030x8
	BSP_RCC_HSE_PLLConfig();
#endif
```

An F003 build takes neither arm. It compiles, links, and reports a healthy image
— and runs at whatever `SystemInit()` leaves, not at 48 MHz. That is why the
F003x4 build succeeded above while the F030x8 build was reaching for a BSP
symbol: F003 never asks for a clock. For a bit-banged USB stack whose entire
correctness rests on 48 MHz, a silently unclocked build is a trap worth a
build-time guard: the port should fail to compile for any part it has no clock
path for, rather than produce a plausible image.

## 11. The F003 PLL question — narrowed, not settled

§10.2 left a straight contradiction: the vendor headers do not define
`RCC_PLL_SUPPORT` for F003, while `CHIP_FACTS_XIAMATSU.md` §2 cites a
measurement that the PLL locks at 48 MHz on F002A and F003. Comparing the `RCC`
register structs across the three headers narrows it.

`RCC_TypeDef`, first six fields:

| offset | py32f030x8.h | py32f003x4.h | py32f002bx5.h |
|---|---|---|---|
| 0x00 | CR | CR | CR |
| 0x04 | ICSCR | ICSCR | ICSCR |
| 0x08 | CFGR | CFGR | CFGR |
| **0x0C** | **PLLCFGR** | **RESERVED0** | **RESERVED0** |
| 0x10 | ECSCR | ECSCR | ECSCR |
| 0x14 | RESERVED1 | RESERVED1 | RESERVED1 |

The rest of the struct is field-for-field identical across all three. F003 and
F002B carry a reserved word at exactly the offset where F030 declares
`PLLCFGR`, and neither declares any of `RCC_CR_PLLON` (bit 24),
`RCC_CR_PLLRDY` (bit 25) or the `RCC_PLLCFGR_*` field set, all of which F030
does.

**What this is evidence for, and what it is not.** A reserved word exactly where
another member of the family has a real register is consistent with the block
existing in silicon and merely being undeclared for parts it is not sold with —
which is what the Xiamatsu measurement claims and what the "one die, several
markings" view of these parts would predict. It raises the prior. It is **not**
proof, for a reason visible in the same table: F002B has the identical hole, and
F002B's own measured source states the PLL is absent there ("HSE на F002B —
только вход (1–32 МГц), PLL отсутствует", `CHIP_FACTS_XIAMATSU.md` §2). These
headers are plainly generated from one template, so a `RESERVED0` at 0x0C may
record nothing more than the template's shape. Anyone citing this table as
settling the question is overreading it.

**The bench test it makes cheap and precise.** On an F003 part, with
`RCC_BASE` = 0x40021000 (verified §7):

1. Write the PLL source and configuration to `0x4002100C` using F030's
   `RCC_PLLCFGR_*` field layout.
2. Set bit 24 (`PLLON`) in `RCC->CR` at `0x40021000`.
3. Poll bit 25 (`PLLRDY`). If it sets, the block is present and the header is
   merely conservative; if it never sets, F003 has no PLL and the flip's primary
   path is **F030 only**.
4. Independently confirm the resulting frequency rather than trusting `PLLRDY` —
   MCO on PA7 does not go above roughly 35 MHz (`CHIP_FACTS_XIAMATSU.md` §3), so
   measure with division.

The outcome is not cosmetic. If F003 has no PLL, the target flip still stands on
F030 but F003 joins F002B on the self-calibration track, and the "primary family
needs no servo" claim has to be narrowed to F030 alone. The plan should carry
both branches rather than assume the favourable one.

## 12. The RAM-placement guard — built and tested, including the version that does not work

§4 and §10 identify the port's most dangerous latent defect: `.datacode` reaches
RAM only because it is swallowed by a `*(.data*)` wildcard, and a linker script
spelling that rule with a dot would put the RX engine in flash with no
diagnostic. The plan's mitigation is a build-time assertion. That mitigation was
worth testing before an implementer builds on it, because **the obvious form of
it is defective**.

All results below are from linking the real engine object against purpose-built
scripts with `arm-none-eabi-gcc 13.2.1`.

### The hazard, reproduced

A script with `.data : { *(.data) *(.data.*) }` — the dotted form — and no
`.datacode` rule places `EXTI2_3_IRQHandler` at **0x08000200**, in flash. The
link succeeds. No error, no warning. Every cycle figure in the ledger is then
wrong, and nothing says so. This is no longer a predicted failure; it is a
reproduced one.

### The obvious guard, and why it passes when it should fail

The natural assertion bounds the section:

```
.ramcode : ALIGN(4) { __ramcode_start = .; *(.datacode) __ramcode_end = .; } > RAM AT> FLASH
ASSERT(__ramcode_start >= ORIGIN(RAM) && __ramcode_end <= ORIGIN(RAM) + LENGTH(RAM), "...")
```

It catches a section deliberately routed to flash. It does **not** catch the more
likely mistake: if the input-section name is ever changed or mistyped — say the
rule collects `*(.ramtext)` while the engine still emits `.datacode` — the
output section is empty, `__ramcode_start == __ramcode_end`, both are nominally
inside RAM, **the assertion passes**, and the engine lands in flash at
0x08000200 exactly as before. Verified: the build succeeds and the symbol is in
flash.

An emptiness check plus a symbol anchored in the engine itself closes both:

```
ASSERT(__ramcode_end > __ramcode_start,
       "FATAL: RAM-code section is EMPTY - input name mismatch")
ASSERT(EXTI2_3_IRQHandler >= ORIGIN(RAM) && EXTI2_3_IRQHandler < ORIGIN(RAM) + LENGTH(RAM),
       "FATAL: the RX engine is not in RAM - timing model invalid")
```

Tested against three scripts:

| script | result |
|---|---|
| correct rule, routed `> RAM AT> FLASH` | builds; `EXTI2_3_IRQHandler` at 0x20000000 |
| rule collects the wrong input name (section empty) | **rejected** — "RAM-code section is EMPTY" |
| correct rule, routed `> FLASH` | **rejected** — "the RX engine is not in RAM" |

The lesson generalises past this one guard: assert on a **symbol the timed code
actually defines**, not on the bounds of a section that may not have collected
anything. A guard that can be satisfied vacuously is not a guard.

One caveat worth stating rather than discovering later: the symbol-anchored
assertion names an engine symbol, so the linker script and the engine become
coupled — renaming the ISR entry breaks the link with an assertion failure
rather than a missing symbol. That is the desirable direction of failure, but it
should be a deliberate choice recorded next to the assertion, not a surprise.

### Postscript: the plan's own guard is stronger than the one tested above

After this section was written, the spliced PLAN.md §9 T1 turned out to specify
a **three-layer** guard, arrived at independently: `ASSERT(SIZEOF(.timecrit) > 0)`
— which it names outright as "the D-5 killer" — plus the VMA range assertion,
plus `-Wl,--orphan-handling=error`. That is the same hole this section found,
closed the same way, by a different route.

`--orphan-handling=error` was tested here too and does reject the orphan case.
Note what it costs, which PLAN.md §9 T1 already anticipates: it also errors on
`.glue_7`, `.glue_7t` and `.vfp11_veneer` from the linker's own stubs, so every
such section must be explicitly placed or discarded in the script or the build
will not link at all.

The summary statements in PLAN.md §10A, §9.5 and §12 item 64 originally
described the mitigation as "an `ASSERT` on its VMA", which understates it;
they were corrected to name all three layers and to record that a VMA assertion
alone passes vacuously.
