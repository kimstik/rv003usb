# RAM budget on F003x4 — measured, and correcting an earlier wrong claim

Everything here was produced by building in this container. It **retracts** a
claim made earlier in `STATE.md`: that the RX+TX pair "cannot fit F003x4's 2048 B
before any C layer, descriptors, stack or DFU". That claim was made without
measuring the C layer, and it is wrong in two separate ways.

## 1. The C layer is not the problem — it is about 144 B

Section map of the real `demo_gamepad` build on F003x4, stock linker script:

| section | bytes | what it is |
|---|---|---|
| `._user_heap_stack` | **1028** | linker-script default, not a measured need |
| `.ram_vector` | **192** | `uint32_t vectors[48]` in `system_py32f0xx.c`, relocated by `SCB->VTOR = SRAM_BASE` |
| `.data` | 260 | of which 252 is the engine's own `.datacode` |
| `.bss` | 136 | buffers and state |
| **total** | **1616** of 2048 (78.91 %) | |

So the C layer, descriptors and all non-engine state come to roughly **144 B**.
Gesturing at them as a reason the pair would not fit was empty.

## 2. Two thirds of that 1616 B is default padding, and it goes away

**Stack.** Measured, not guessed. Largest C frames in `rv003usb.c` are
`push {r3,r4,r5,r6,r7,lr}` and `push {r0,r1,r2,r4,r5,lr}` (24 B) plus one
`sub sp, #8`. Both engines push at most `{r4,r5,r6,r7,lr}` (20 B). Worst-case
chain:

| | bytes |
|---|---|
| M0+ exception entry (r0-r3, r12, lr, pc, xPSR) | 32 |
| engine ISR saves | 28 |
| C handler frame (24 + 8) | 32 |
| margin | 36 |
| **budget** | **128** |

`_Min_Stack_Size` 0x200 → 0x80, `_Min_Heap_Size` 0x200 → 0 (this stack calls no
allocator).

**Vector table.** `system_py32f0xx.c:144-151` copies 48 vectors into RAM and
points `VTOR` at SRAM, but the same file already has the flash branch —
compiling with **`-DFORBID_VECT_TAB_MIGRATION`** sets `VTOR` to `FLASH_BASE` and
the 192 B array disappears.

Measured, cumulative:

| build | RAM | of 2048 |
|---|---|---|
| stock | 1616 B | 78.91 % |
| heap 0, stack 128 | **720 B** | 35.16 % |
| + vectors in flash | **528 B** | **25.78 %** |

**1088 B recovered, none of it from the engine.** Non-engine RAM is then 276 B
(`.data` 8 + `.bss` 136 + stack 132).

## 3. Code versus tables, and why 24 MHz changes the answer

Measured from the symbol sizes of the two engines:

| | total | code | tables |
|---|---|---|---|
| RX (`engine16_merged.S`) | 1812 B | 986 B | ~826 B |
| TX (`engine16_tx.S`) | 1368 B | 556 B | ~812 B |
| **pair** | **3180 B** | **1542 B** | **1638 B** |

The earlier "does not fit" assumed all of that must be RAM-resident. **That is a
48 MHz constraint, not a 24 MHz one.** At 24 MHz the flash is at LAT = 0, so
instruction fetch from flash is single-cycle and deterministic — which is the
whole reason the operating point was chosen. CLEANSHEET's competition entry was
flash-resident and hit exactly 16 cycles, so this is demonstrated, not assumed.

With the code in flash:

| | RAM needed | of 2048 | verdict |
|---|---|---|---|
| tables in RAM + 276 B other | 1914 B | 93 % | **fits, 134 B spare** |
| tables in flash too | 276 B | 13 % | fits, 1772 B spare |

## 4. The condition, stated honestly

This is not free, and the catch is specific. The merged RX cell does
`ldrh r1, [r4, r1]` once per bit. From **RAM-resident** code that costs 2 cycles
and the cell is exactly 16. From **flash-resident** code a RAM access costs 4,
which makes the cell 18 and blows the budget.

So flash residency requires one of:

* **the table also in flash, and a flash data read costing ≤ 2 cycles** — the
  measured cost table (`CHIP_FACTS_XIAMATSU.md` §1) prices a PC-relative literal
  from flash at 2, but says nothing about a register-offset load from a flash
  address. **This is unmeasured and it is the single number that decides the
  question.** It is a bench item, not a static one; or
* **an engine with no per-bit table read** — which is exactly CLEANSHEET's
  capture-and-defer structure, rejected on turnaround grounds, and whose
  transferable idea is recorded as R-1 in `ENGINE16_CATALOG.md`.

That is a real coupling and it deserves stating plainly: the table-driven engine
and flash residency pull against each other, and which wins depends on a
measurement nobody has made.

## 5. What this retracts

`STATE.md` says the pair "does not fit even with the table shared" on F003x4,
and `ENGINE16_CATALOG.md` R-6 says the nibble table is "necessary but not
sufficient" there. Both were computed against an all-RAM layout and a 1028 B
stack that no measurement supports. With the stack and vectors corrected, and
the code in flash, **F003x4 fits with room** — subject to §4.
