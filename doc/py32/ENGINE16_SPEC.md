# Engine-16: contract for the 24 MHz / 16-cycles-per-bit competition

Four independent implementations are written against this contract, then a
referee merges them into one. This file is the only thing they share. It fixes
what must be true so the results are comparable; everything it does not fix is
deliberately free, because that is where the competition lives.

## 1. Operating point

* Part: PY32, Cortex-M0+ (Thumb, `-mcpu=cortex-m0plus`). Low registers r0-r7 for
  most instructions; r8-r12 reachable only via `mov`.
* SYSCLK **24 MHz**, from HSI, no PLL. Datasheet-clean on F003, F002A, F030 and
  (after trimming) F002B.
* USB low speed, 1.5 Mbit/s, fixed by USB 2.0 §7.1.11. Therefore the bit cell is
  **exactly 16 cycles**. Not 15, not 17 — a design that needs a 17th cycle in any
  path has failed, and one that finishes early must pad deterministically.
* Flash latency is **0** at 24 MHz (vendor spec — not something to verify).

## 2. Cost model

Measured on live silicon at Flash Latency = 0, i.e. exactly this operating point
(`CHIP_FACTS_XIAMATSU.md` §1). Costs depend on where the code executes from and
**the columns swap**:

| operation | code in FLASH | code in RAM |
|---|---|---|
| most instructions | 1 | 1 |
| branch taken / not taken | **2-3** / 1 | **2-3** / 1 |
| LDR/STR to a GPIO port | 1 | full speed |
| LDR of a literal from flash via PC | 2 | **4** |
| LDR/STR to RAM | **4** | **2** |
| LDM/STM/PUSH/POP | 4 first reg, +1 each | 2 first reg, +1 each |
| B / BX / BL | 2-3 / 3 / 4 | 2-3 / 3 / 4 |

Three consequences that shape the design, and they are the interesting part:

* **A taken branch costs 2-3 and the source says which depends on alignment and
  on the preceding instruction.** At a 16-cycle budget an unresolved ±1 is 6 % of
  a bit cell. Designs that keep taken branches out of the per-bit path are
  structurally favoured. This is the single strongest pressure in the spec.
* **Placement still matters at LAT=0, but only for data.** Instruction fetch is
  single-cycle from flash either way. What changes is that a RAM store costs 4
  cycles from flash-resident code and 2 from RAM-resident code. At 16 cycles a
  4-cycle `rxbuf` store is a quarter of the budget. Both placements are allowed;
  state which you chose and pay for it honestly in the ledger.
* From RAM-resident code a **flash literal-pool load costs 4**. Bit-cell
  constants belong in registers, not in a pool.

For reference, the existing 32-cycle RISC-V-derived engine spends roughly 26
cycles of real work per bit (about 14 non-branch instructions plus 5 branches at
2-3 each). Fitting 16 is not a re-pad. Something structural has to go.

## 3. What the engine must do

Receive path, from the interrupt on D- through to a decoded packet:

1. Detect SYNC and lock the bit phase.
2. Sample the bus once per bit cell at a stable point.
3. NRZI decode (no transition = 1, transition = 0).
4. Bit unstuffing: after six consecutive 1s the next bit is a stuffed 0 and is
   removed from the data stream.
5. Assemble bytes LSB-first into a buffer.
6. Detect SE0 / EOP and terminate.
7. Validate CRC — CRC5 for tokens, CRC16 for data. **Whether CRC is computed
   inside the bit cell or deferred to the untimed tail is your choice**, and it
   is probably the most consequential one available. Justify it.
8. Do not overrun the receive buffer. The existing engine does not bound this
   store and it is reachable from the bus (`DEFECTS_VERIFIED.md` D-2). A design
   that reintroduces the hole has failed, and "add a check" costs cycles inside
   the timed path — solving it structurally is worth more than solving it late.

Transmit path may be sketched rather than finished, but if you sketch it, say
what its bit cell costs and whether it fits 16.

## 4. External contract (fixed — the referee merges on this seam)

* Entry: the D- pin-change interrupt handler.
* Output: received bytes in a buffer, length, and the packet's PID, then a call
  into the existing C layer. The C layer, the descriptors, the protocol state
  machine and DFU are shared and **must not change** — this competition is about
  the timed engine only.
* Register conventions, buffer layout, internal labels, unrolling strategy and
  file structure are all **free**.

## 5. Deliverable

In your own worktree, on your own branch:

1. `engine16_<name>.S` — assembles cleanly with
   `arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c`.
   It does not have to link into a full image, but it must assemble, and
   `arm-none-eabi-objdump -d` on it must show the instruction stream you claim.
2. `engine16_<name>.md` — the design note. It must contain:
   * the idea in one paragraph, and its lineage;
   * **a cycle ledger per path**: for every distinct route through one bit cell
     (data 1, data 0, stuffed bit, byte boundary, SE0), the instruction sequence
     with a running cycle count, summing to 16. Show the arithmetic. An
     unaccounted cycle is a defect, not a rounding error;
   * where the taken branches are, and what their 2-vs-3 ambiguity costs;
   * placement chosen (flash or RAM) and the data-access cost that follows;
   * register allocation, and whether it fits the low-register file honestly;
   * what you gave up. Every design at this budget gives something up. A note
     that claims no cost is not finished.
3. Do not edit any shared file. Your `.S` and your `.md`, nothing else.

## 6. How the referee will judge

In this order:

1. **Does it fit 16 cycles on every path, with the arithmetic shown?** A design
   that fits with slack beats one that fits exactly.
2. **Is it correct?** NRZI, unstuffing, byte alignment, EOP, CRC, buffer bound.
3. **Is it robust to the 2-vs-3 branch ambiguity?** Fewer timed taken branches
   is better; a design whose timing does not depend on the ambiguity at all is
   better still.
4. Register pressure, code size, RAM footprint.
5. Clarity, and how well the idea can be combined with another.

The referee does not pick a winner and discard the rest. It reads all four and
builds one engine from the best mechanism in each, so a design that contributes
one excellent idea and loses overall is still valuable. Write for that.
