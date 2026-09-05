# PY32 port — state of the work

Mirrors doc/wg015/STATE.md: what is done, what is in flight, what is next.
Kept current so a session that dies mid-run can be resumed from this file alone.

Last updated: 2026-09-05. All four fragments complete and spliced into PLAN.md.

## Where the plan stands

`PLAN.md` (2675 lines) is the single authoritative document. The four rework
fragments have been **spliced into it** (the splice half of task T0); read
PLAN.md, not the fragments. `rework/` is kept unedited as the provenance record —
where each block came from, and what it looked like before its cross-references
were retargeted.

| fragment | landed in PLAN.md as |
|---|---|
| `rework/ledger.md` (491 l) | new §2.0 (the cost model, cited by the other blocks as their "§0"), §2.1, the cycle-cost annotations of §2.5, Appendix A, Appendix B, Appendix D (the fragment's own "§5" — renamed, because that number is taken by §5 Gaps versus WG015) |
| `rework/risks_verdict.md` (234 l) | §0, §10, new §10A bring-up gates G0-G12, §12 |
| `rework/target_clock.md` (472 l) | §3.1, §3.2, §6, §11 |
| `rework/tasks_waves.md` (1141 l) | §9 in full, including §9.0-§9.6 |

**Splice done.** All 12 `## REPLACES` blocks, both `## NEW` blocks and ledger's
§0 landed; every substantive line of all 15 blocks was checked present in
PLAN.md. Two things deliberately did **not** come across: the fragments' own
preambles (splice scaffolding), and ledger's closing "Requests to owners of
sections I do not own" — process residue, dispositioned item by item in §9.6.
Section numbering did not shift: §2.0, §10A and Appendix D are suffixed
additions, so existing citations into §1-§8 and Appendices A-C still resolve.
Cross-references were retargeted: shorthand for material now inside PLAN.md
(`LG §`, `RV R`/`RV G`, `TC §`) points at PLAN section numbers; shorthand for
documents that stay standalone (`BF §`, `CF §`, `XF`, `DV D-`) now names the
file. Recorded in PLAN.md §12 item 67.

## What the rework changes, in one place

1. **Primary part flips** to F030/F003 (HSI 24 MHz x PLL2 = 47.98 MHz, −0.04 %,
   inside USB tolerance with **no servo**). F002B drops to second target and
   needs HSI self-calibration against LSI before enumeration, because its
   factory 48 MHz constant measures **43.12 MHz** (−10.2 %) — enumeration is
   impossible at that error. Source: `CHIP_FACTS_XIAMATSU.md` §2.
2. **The cost model is two-column and the columns swap with execution
   location.** Code in RAM: RAM data 2 cycles, PUSH/POP 2+1, ports at full
   speed, no wait states, flash literal-pool load **4**. Code in flash: RAM data
   4, flash literal 2. Running timed code from RAM is therefore **confirmed, not
   overturned**. The one real trap is a flash literal-pool load from
   RAM-resident code. Source: `CHIP_FACTS_XIAMATSU.md` §1.

An earlier pass read one column of that table as if it were the whole thing and
concluded RAM was expensive. It was not, and the conclusion would have reversed
a correct decision. The rule that came out of it is now PLAN.md
§10.3: do not adopt an overturn of someone else's
engineering decision until the whole source behind it has been re-read.

## Facts established by building, not by reading

`BUILD_FACTS.md` — every claim carries its reproduction command.

* Toolchain: `arm-none-eabi-gcc` 13.2.1 installed; both per-part variants of the
  engine assemble (rc=0). No task may be gated on hardware.
* **RX runs from RAM, TX runs from FLASH**: `.datacode` 252 B holds the whole
  real-time RX path; `.text` 512 B holds the token tail *and the entire TX
  engine*. The ledger needs both columns, not one.
* `.datacode` reaches RAM **incidentally** — no `.datacode` rule exists
  anywhere; it is absorbed by the stock script's `*(.data*)` wildcard (verified
  by linking: VMA 0x20000000, LMA 0x08000200). A script spelling that rule
  `*(.data.*)` would place the RX engine in flash **silently** — no error, no
  warning, successful build, all timing wrong. Highest-risk item found.
* Register map is **byte-identical** across F002B and F030/F003 (GPIOB
  0x50000400, EXTI 0x40021800, IDR 0x10, BSRR 0x18, same `GPIO_TypeDef` order),
  so the target flip changes not one address in timed code.
* The five `#if PY32F002Bx5` sites are **pure cycle padding**; none touches a
  register. The `#else` arm carries an alignment assertion the branch's build
  has never evaluated — assembling the F003 variant shows it passes.
* `py32f0-template` is an empty submodule, so the branch cannot link as
  published. Upstream pins cleanly at 289ffc8. Open: vendor the few files needed
  or carry the submodule.
* **The branch builds, for all three candidate parts** once the template is
  supplied: F030x8 (RAM 2128/8K, flash 2908/64K), F003x4 (RAM 1616/2K =
  **78.91 %**, flash 2132/16K), F002Bx5 (RAM 1168/3K, flash 2696/24K). The
  placement split is confirmed in the linked image: `EXTI2_3_IRQHandler`
  0x200000c8, `usb_send_data` 0x0800022c.
* Build-system defects found by being bitten: objects land **outside** `Build/`
  and carry no `MCU_TYPE`, so `rm -rf Build` does not clean and a part switch
  silently relinks another part's objects — this produced a false "F030 does not
  build" during this work. And an F003 build takes neither arm of the demo's
  clock `#if`, linking a healthy-looking image that never reaches 48 MHz.
* **Open and consequential**: `RCC_PLL_SUPPORT` is defined only for F030, so the
  vendor library compiles no PLL path for F003 — against Xiamatsu's measured
  claim that the PLL locks on F003. F003's `RCC` struct has a reserved word at
  0x0C exactly where F030 has `PLLCFGR`, which raises the prior but does not
  settle it (F002B has the same hole and its PLL is reportedly absent). If F003
  has no PLL, "the primary family needs no servo" narrows to F030 alone.

`DEFECTS_VERIFIED.md` — defects located in source, with two claims re-shaped:

* **D-1** endpoint bound is off by one and **branch-introduced**: `arm.S:277`
  uses `bhi`, admitting `endp == ENDPOINTS`, where the RISC-V original correctly
  uses `bgeu` (`rv003usb.S:528`). `eps[]` is the last struct member, so the
  access runs off the end of the struct and is reachable from the wire. Fix is
  one instruction, `bhs`, in non-timing-critical flash code.
* **D-2** RX store has no bound check (`arm.S:145-148`, author's own TODO on
  :145), but it sits inside the cycle-counted path — a bound check is not free,
  so this is a design task with a cycle-budget criterion, not a one-liner.
* **D-3** "the per-part variant is never assembled" was too strong: both arms
  assemble; only the build system's *selection* is missing (`MCU_TYPE` pinned).

## Next steps, in order

1. Review the spliced plan — the three critics (executability, technical,
   completeness) have never cleared a post-correction version. This is the first
   version they can be run against, since PLAN.md is now self-contained.
2. Run the rest of T0 (§9.4 Wave 0): the branch, the merge of master 80b1893, the
   cherry-pick of the PY32 branch and the removal of the vendor scaffolding. Only
   the splice half of T0 is done; that half is not started.
3. Run the implementer fleet over §9's waves with disjoint file ownership
   (§9.2's matrix, §9.3's edges).
4. On silicon, run §10A's gates in order — G1 first, since no pad constant in
   Appendix A/B is final before it (Appendix D carries the kernels).

## Process note that has earned its place

Three limit hits across two model families have cost **zero** work, because every
agent wrote into an isolated git worktree that outlives it. Files were salvaged
from worktrees even where the agent died before committing — a 491-line ledger
among them. Any future fleet run on this repo should keep that arrangement.


## The 24 MHz / 16-cycle engine competition — CLOSED, merged engine exists

Six independent engines were written against `ENGINE16_SPEC.md`, then a referee
merged them. Full record with every claim re-checked: `ENGINE16_RESULTS.md`.

**Result: `engine16_merged.S` receives at exactly 16 cycles per bit on every
data path, with zero exposure to the 2-vs-3 taken-branch ambiguity.** Verified
independently with `tools/engine16_cyc.py` and in raw `objdump`. A bit-exact
model of it decodes 305 host-encoded packets with no failures.

Mechanisms and their origins: VUSB's register-offset table and wire-domain
unroll; DESCENT's combined D+/D- sample-and-mask, which yields SE0 detection
free from the read that performs the NRZI test; CLEANSHEET's carry-chain `adcs`
capture; GRAINUUM's per-byte unroll granularity; BALANCE's structural buffer
bound, which closes `DEFECTS_VERIFIED.md` D-2 by construction with no runtime
check.

What the competition settled that had been open:
* **Per-byte unrolling is required**, established independently from four
  directions, and one byte is the right granularity — unrolling a whole packet
  fails because a short conditional branch cannot keep an SE0 escape in range.
* **A shared byte-boundary handler cannot work at 16 cycles**, for an arithmetic
  reason: it inherits 1-4 cycles of its caller's remaining budget while a byte
  store needs 7.
* **DMA cannot reach GPIO on this part** — GPIO is on a core-private IOPORT bus.
  The same decision that makes a port read cost one cycle puts it out of DMA's
  reach.
* A table lookup beats mask arithmetic as the decoder here: 8 cycles/bit without
  unstuffing, store or CRC, against 76 cycles/byte with all three.

## What the merge left open — the current front

1. **Turnaround: 203..229 cycles, 12.7..14.3 bit times from SE0 to the C call.**
   USB 2.0 §7.1.18 asks 6.5; the host timeout is 16-18. Inside what the host
   tolerates, outside what the specification asks. This is a property of 24 MHz,
   not of the merge. The answer already exists in the project as ACK-first
   (`PRIOR_ART.md` R8, `doc/wg015/TODO.md` item 3), where it was explicitly
   deferred *pending a measured turnaround*. That measurement now exists, so the
   item is live.
2. **No transmit.** The merged engine receives only. A 16-cycle TX bit cell is
   unwritten. `NATIVE` sketched a hardware alternative worth evaluating:
   output-compare toggle mode plus DMA is a hardware NRZI transmitter, because
   NRZI *is* "toggle on 0" and the toggle list is always precomputable — zero
   CPU cycles per bit, 26 cycles to arm. Its breakages are listed in
   `engine16_native.md` and it does not exist on F002B.
3. Phase lock is +/-3.5 cycles and unbenched; there is no mid-packet resync.
4. 1812 B of RAM-resident code and tables, of which 512 B is the CRC16 table —
   significant on a 3 KB part, and a candidate for trading back against cycles.

## Integration status

The engine and the six entries live in `doc/py32/` as design artifacts. They are
**not yet integrated** into the port skeleton that `PLAN.md` §9 describes
(`rv003usb/py32/`). §9's task T2 is substantially answered by the merge but its
file ownership and acceptance criteria have not been reconciled with what the
competition actually produced.
