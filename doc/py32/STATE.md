# PY32 port — state of the work

Mirrors doc/wg015/STATE.md: what is done, what is in flight, what is next.
Kept current so a session that dies mid-run can be resumed from this file alone.

Last updated: 2026-09-04. Three of four fragments complete; §9 waves 2-4 in flight.

## Where the plan stands

`PLAN.md` (1194 lines) is the v2 document. It is **superseded in four places** by
fragments in `rework/`, which have not yet been spliced into it. Read the
fragments as authoritative where they overlap PLAN.md.

| fragment | replaces | state |
|---|---|---|
| `rework/ledger.md` (491 l) | §2.1, §2.5, App A, App B, + new bench-gate §5 | complete |
| `rework/risks_verdict.md` (234 l) | §0, §10, §12, + new §10A bring-up gates G0-G12 | complete |
| `rework/target_clock.md` (472 l) | §3.1, §3.2, §6, §11 | complete |
| `rework/tasks_waves.md` (531 l) | §9 | Waves 2-4 prose, defect map, ledger requests outstanding |

**Splice not yet done.** Each block is headed `## REPLACES §N — …` so the merge
is mechanical. Task T0 in `rework/tasks_waves.md` owns it.

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
a correct decision. The rule that came out of it is recorded in
`rework/risks_verdict.md`: do not adopt an overturn of someone else's
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

1. Finish `rework/tasks_waves.md` Waves 2-4, the defect map, and the requests
   `rework/ledger.md` addresses to §9. (target_clock.md is done.)
2. **Splice** the four fragments into `PLAN.md` (task T0).
3. Review the spliced plan — the three critics (executability, technical,
   completeness) have never cleared a post-correction version.
4. Run the implementer fleet over §9's waves with disjoint file ownership.

## Process note that has earned its place

Three limit hits across two model families have cost **zero** work, because every
agent wrote into an isolated git worktree that outlives it. Files were salvaged
from worktrees even where the agent died before committing — a 491-line ledger
among them. Any future fleet run on this repo should keep that arrangement.
