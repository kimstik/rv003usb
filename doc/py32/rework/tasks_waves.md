# §9 rework — replacement blocks for `doc/py32/PLAN.md`

Splice rule: each `## REPLACES …` line names the PLAN.md region (at 88d1229) the block body
replaces; the body runs to the next `## REPLACES`/`## PATCH`/`## NOTES` line. Cite forms are
PLAN.md's (`arm.S:<n>` = `rv003usb/rv003usb-arm.S` at 0ad3c42, 573 lines; `S:<n>` =
`rv003usb/rv003usb.S`; `c:<n>`/`h:<n>` = `rv003usb.c`/`.h`; `PA S-n` = `PRIOR_ART.md`;
`RM002B p<n>`) plus, for this fragment:

* `CF §n` = `doc/py32/CHIP_FACTS_XIAMATSU.md` section; `xm_030.md:<n>` / `xm_002b.md:<n>` = the
  Xiamatsu README lines quoted there.
* `BF §n` = `doc/py32/BUILD_FACTS.md` — facts produced by **building in this container**
  (arm-none-eabi-gcc 13.2.1). Cited, never re-derived.
* `DV D-n` = `doc/py32/DEFECTS_VERIFIED.md` defect n.
* `LG §n` = `doc/py32/rework/ledger.md`; `RV R<n>`/`RV G<n>` = `rework/risks_verdict.md` risk /
  bring-up gate; `TC §n` = `rework/target_clock.md`.

---

## REPLACES §9 — Work breakdown

### 9.0 What changed against v2 §9

Six structural changes; everything else in v2 §9 that is not restated below stands.

1. **Primary part flipped** (CF §2, TC §3.1). `MCU ?= PY32F030x8`. F002B clock work is off the
   critical path on its own track (T12, T13's `acquire` mode, T16). No task in waves 0–3 waits
   on an F002B decision.
2. **The linker script is a first-class task, not a line in T1's content list** (BF §4, DV D-5).
   The branch's RAM-resident RX engine reaches RAM by accident — it is swallowed by the stock
   script's `*(.data*)` wildcard. A script spelling that rule `*(.data.*)` drops the engine into
   flash with no error and no warning. The remedy is our own script with a named RAM-code
   section, `ASSERT`s, and `--orphan-handling=error`.
3. **"The engine runs from RAM" is retired as a phrase.** BF §3 measured the split:
   `.datacode` 252 B = the whole real-time RX path, RAM-resident; `.text` 512 B = the
   token-dispatch tail **and the entire TX path**, flash-resident. Every task says which side it
   is on.
4. **No task is gated on hardware.** arm-none-eabi-gcc 13.2.1 is installed and the engine
   assembles and links (BF §1, §2). Every acceptance criterion below is a command that runs in
   this container. T10/T16 exist to *record* silicon, and nothing in waves 0–3 depends on them.
5. **v2's T2 was too big for one session.** Split into T2 (RX path, correctness, placement) and
   T2T (TX path, per-part `#if` review, re-pad), serialised on `rv003usb-arm.S`. The walker and
   the trim actuator came out of T2 as T14 and T13 and moved to wave 1, because T2's own
   acceptance needs them.
6. **Every task carries a model marking** (Sonnet / Opus) with its reason. The executing fleet is
   mixed; the marking is a routing instruction, not a compliment.

### 9.1 Conventions

v2's conventions stand, amended:

* Branch = the T0 result. Builds run from the repo root. `ARMCC=arm-none-eabi-` (gcc ≥ 13, BF §1);
  RISC-V via the `ch32fun` submodule.
* One Makefile variable `DEFS` is passed **identically to the `.c` and `.S` rules** (§2.6):
  `-DRV003USB_PY32=1`, exactly one family (`-DPY32F030=1` / `-DPY32F002B=1`), `-D$(MCU)=1`
  (part), `-DPY32_FLASH_KB= -DPY32_SRAM_KB=`.
* Timing constants are **cycles**, from `usb_port_py32_tune.h`, never microseconds (PA S-3).
* "walker" = `tools/py32_cyc.py` (T14; LG §"REPLACES Appendix B" is its seed).
* No task edits a file it does not own. A task that needs a change elsewhere appends to the
  `requests` section of `doc/py32/STATE.md` (T8's file — the single shared exception, append-only).
* Every commit touching the Р10 files carries a `Provenance:` trailer; `[MIT-attrib]` /
  `[GPL-ideas-only]` tags below mark the tasks concerned.
* Acceptance criteria are mechanical and **hardware-free**: a command that must exit 0, a size
  limit, a symbol at an address, a grep that must or must not match.

**Vendor-versus-submodule — decided here, not deferred.** `py32f0-template` is removed and the
handful of files we need are **written**, not copied: linker scripts, startup, and a minimal
device header, exactly as the WG015 port writes `K1921VG015_min.h`. Reasons, both from BF §6:
the submodule is empty on the branch so the branch cannot link as published, and the upstream
files carry their own licence into a repo that would rather not inherit one. Consequence for the
fleet: T0 removes the submodule; T1 writes the replacements from RM/DS page cites; nothing in
the tree ever refers to `../py32f0-template/…`. Upstream at 289ffc8 stays a *reference* for
memory geometry and register offsets (that is where BF §6's RAM/flash table came from), not a
build input.

### 9.2 Ownership matrix — strictly disjoint at file granularity

`Serialised after` means the two tasks share a file and must never run concurrently; every other
pair is free to run in parallel. Nothing else in this table shares a path.

| Task | Model | Wave | Owns (exact paths) | Serialised after |
|---|---|---|---|---|
| T0 | Sonnet | 0 | the merge — every file it touches, alone | — |
| T1 | **Opus** | 1 | `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f003x6.ld, py32f030x6.ld, py32f030x8.ld, py32f002bx5.ld, Makefile.py32, py32_stdio_stub.c, selftest_main.c, README.md}` | — |
| T3 | Sonnet | 1 | `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h`, `rv003usb/wg015/usb_port_wg015.h`, `rv003usb/py32/usb_port_py32.h` | — |
| T8 | Sonnet | 1 | `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | — |
| T13 | **Opus** | 1 | `rv003usb/py32/usb_port_py32_trim.h`, `rv003usb/py32/selftest_trim.S` | — |
| T14 | **Opus** | 1 | `tools/py32_cyc.py`, `tools/py32_cyc_costs.json`, `tools/py32_cyc_selftest/*` | — |
| T2 | **Opus** | 2 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h` | — |
| T4 | Sonnet | 2 | `demo_gamepad/{usb_config.h, funconfig.h, demo_gamepad.c, README.md}`, `demo_hidapi/{usb_config.h, funconfig.h, demo_hidapi.c, README.md}` | — |
| T6 | Sonnet | 2 | `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S, bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`, `tools/wg015_vcd/*` | — |
| T12 | **Opus** | 2 | `rv003usb/py32/py32_hsical.c`, `rv003usb/py32/py32_hsical.h`, `py32_bench/bench8_hsical.c` | — |
| T2T | **Opus** | 3 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h` | **T2** |
| T5 | **Opus** | 3 | `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/*`, `bootloader_dfu/README.md`, `tools/wg015mkdfu.py` | — |
| T7 | Sonnet | 3 | `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top) | — |
| T9 | **Opus** | 3 | `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | — |
| T11 | Sonnet | 3 | `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}` | — |
| T15 | Sonnet | 3 | `rv003usb/py32/engine_pid_handlers.S` | — |
| T10 | **Opus** | 4 | `doc/py32/calibration.md`, `rv003usb/py32/py32_cal_f030.mk` | — |
| T16 | **Opus** | 4 | `doc/py32/calibration_f002b.md`, `rv003usb/py32/py32_cal_f002b.mk` | — |

**Verification — every file the plan touches, listed once, with its single owner.** Read this as
the proof obligation, not as decoration: if a path appears twice the fleet is not parallel-safe.

| Path | Owner |
|---|---|
| `rv003usb/rv003usb-arm.S` | T2, then T2T (serialised — never concurrent) |
| `rv003usb/py32/usb_port_py32_asm.h`, `usb_port_py32_tune.h` | T2, then T2T (same serialisation) |
| `rv003usb/rv003usb.c`, `rv003usb.h`, `usb_port_ch32.h`, `wg015/usb_port_wg015.h`, `py32/usb_port_py32.h` | T3 |
| `rv003usb/py32/py32_min.h`, `ch32fun.h`, `startup_py32.S`, `*.ld`, `Makefile.py32`, `py32_stdio_stub.c`, `selftest_main.c`, `README.md` | T1 |
| `rv003usb/py32/usb_port_py32_trim.h`, `selftest_trim.S` | T13 |
| `rv003usb/py32/py32_hsical.{c,h}` | T12 |
| `rv003usb/py32/engine_pid_handlers.S` | T15 |
| `rv003usb/py32/py32_cal_f030.mk` | T10 |
| `rv003usb/py32/py32_cal_f002b.mk` | T16 |
| `demo_gamepad/*`, `demo_hidapi/*` | T4 |
| `py32_bench/{Makefile, main.c, bench_common.*, bench_kernels.S, bench1..bench6}` | T6 |
| `py32_bench/bench7_loopback.c`, `loopback_vectors.h`, `gen_loopback_vectors.py` | T11 |
| `py32_bench/bench8_hsical.c` | T12 |
| `tools/py32_cyc.py`, `py32_cyc_costs.json`, `py32_cyc_selftest/*` | T14 |
| `tools/wg015_vcd/*` | T6 |
| `tools/wg015mkdfu.py` | T5 |
| `bootloader_dfu/*` | T5 |
| `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | T9 |
| `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top) | T7 |
| `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | T8 |
| `doc/py32/calibration.md` | T10 |
| `doc/py32/calibration_f002b.md` | T16 |
| `doc/py32/PLAN.md`, `doc/py32/rework/*` | **no task** — this rework's splice step, before T0 |

**Four seams that make the above disjoint** (each replaces a "task X edits task Y's file"
edge that v2 had; the pattern is v2's own, from T6's weak-symbol bench menu):

| Seam | Owner of the mechanism | Who drops in without touching it |
|---|---|---|
| `SOURCES += $(wildcard $(PY32_DIR)/engine_*.S)` in `Makefile.py32` | T1 | T15 (`engine_pid_handlers.S`) |
| `SOURCES += $(wildcard $(PY32_DIR)/py32_*.c)` | T1 | T12 (`py32_hsical.c`); T1's own `py32_stdio_stub.c` also matches. `selftest_main.c` deliberately does not match — it belongs to one target only, and `selftest_trim.S` (T13) does not match `engine_*.S` |
| `-include $(PY32_DIR)/py32_cal_*.mk` (a glob that matches nothing is silent in make) | T1 | T10 (`py32_cal_f030.mk`), T16 (`py32_cal_f002b.mk`) — measured constants arrive as `DEFS +=` overrides, so no hardware task ever edits a header |
| `py32_bench/Makefile` globs `bench[0-9]_*.c`; `main.c`'s menu binds keys `1`…`9` to weak `benchN_run` | T6 | T11 (`bench7_loopback.c`), T12 (`bench8_hsical.c`) |

Consequence for T10/T16: they write **no** header and no source. v2 had them writing "values only"
into `usb_port_py32_tune.h`, which is T2's file — that is the one ownership violation v2 shipped,
and the `.mk` seam removes it.

### 9.3 Dependency edges and waves

Edges, each with the reason it exists. An edge is a real artefact one task consumes from another,
not a courtesy.

| Edge | Why |
|---|---|
| everything ← T0 | the branch does not exist before it |
| T1 ← T0 | — |
| T3 ← T0; T3 ← T1 (soft) | T3's PY32 compile check needs T1's `Makefile.py32` and `py32_min.h`; its V003/WG015 bit-identity gates need nothing. Wave-1 exit item |
| T8 ← T0 | — |
| T13 ← T0; T13 ← T14 (soft) | the actuator's cycle cost is a walker path. Wave-1 exit item |
| T14 ← T0 | its fixtures are self-contained `.S` files it writes and assembles itself |
| T2 ← T1 | linker script with the named RAM-code section, `py32_min.h`, build |
| T2 ← T3 | `USB_DM_IRQ` block moved out of `rv003usb.h` into the PY32 port header |
| T2 ← T13 | `USB_TRIM_ACTUATE` is called from the SE0 branch |
| T2 ← T14 | T2's acceptance *is* a walker run |
| T4 ← T1, T3 | build + seam headers |
| T6 ← T1 | build |
| T12 ← T1 | RCC/LSI/ICSCR offsets from `py32_min.h` |
| T2T ← T2 | same file |
| T5 ← T1, T2, T3 | links the engine, uses the shared boot words and the port header |
| T7 ← T1, T2, T4, T5, T14 | the top `Makefile` builds all of them and runs `check-cycles` |
| T9 ← T2, T5 | the engine, and T5's proof that the transport works |
| T11 ← T2, T6 | the engine to time, T6's menu and glob |
| T15 ← T2, T3 | `usb_port_py32_asm.h` macros; `EP_*_OFFSET` from `rv003usb.h` |
| T10 ← waves 0–3 | it flashes what they built |
| T16 ← T12, T13, T10 | it runs T10's rig on the second part with T12's calibration in the image |

| Wave | Tasks | Exit gate (run once, by whoever lands last) |
|---|---|---|
| 0 | T0 | `git submodule status` names only `ch32fun`; the four RISC-V/WG015 builds green |
| 1 | T1, T3, T8, T13, T14 | T3's PY32 compile item; T13's actuator costed by T14's walker; `tools/py32_cyc.py --selftest` exits 0 |
| 2 | T2, T4, T6, T12 | `make check-cycles` green on both demos for `MCU=PY32F030x8` and `PY32F002Bx5` |
| 3 | T2T, T5, T7, T9, T11, T15 | `make all` (= `build build_py32 check-cycles`) green; CI yaml parses |
| 4 | T10 (F030, primary), T16 (F002B track) | RV G1–G12; nothing in waves 0–3 waits on them |

T10 and T16 are **parallel and independent** — different boards, different `.mk` files, different
docs. The F002B track (T12 → T13 `acquire` mode → T16) never blocks the primary path.

### 9.4 Tasks

#### Wave 0

**T0 — Starting state (ONE agent, alone, before anyone else) — Sonnet**
*Sonnet: the conflict set is enumerated in advance and every resolution is named; nothing is
judged, only applied.*

Owns: everything the merge touches — exclusive because nobody else runs yet. This is also why
T0 may edit `.gitignore`, the top `Makefile` and `demo_gamepad/*`, which T7 and T4 own later.

Entry: none.

Does — dry-run verified at 1db45fd; the later commits touch only `doc/py32/`:
1. `git checkout -b py32-port claude/wg015-bitbang-usb-port-bxuu7w && git merge --no-edit 80b1893`
   (clean: `bootloader/usb_config.h`, 2 lines).
2. `git fetch origin py32 && git cherry-pick -x 0ad3c42` → conflicts: `.gitignore`, `.gitmodules`,
   `Makefile`, `demo_gamepad/demo_gamepad.c`, `demo_gamepad/usb_config.h`, `rv003usb/rv003usb.c`.
3. Resolve: `.gitignore` = HEAD + `*.o`, `*.d`, `Build/`; `.gitmodules` = HEAD (ch32fun only) and
   `git rm --cached py32f0-template && rm -rf py32f0-template` (§9.1: vendored, not carried —
   DV D-4); `Makefile` = HEAD (T7 adds the PY32 hook); `demo_gamepad.c` = HEAD (`#include
   "ch32fun.h"`, no BSP calls — clocks belong to startup); `demo_gamepad/usb_config.h` = HEAD's
   flag block, no pin ladder (T4 adds it); `rv003usb.c` = HEAD entirely (drop the LL includes and
   the `#if __riscv` forks); `rv003usb.h` auto-merges and keeps the `USB_DM_IRQ` block (T3 moves
   it). `git rm -r .vscode Makefile.py32` — the branch's `Makefile.py32` is deleted, not amended:
   it pins `MCU_TYPE = PY32F002Bx5` (DV D-3) and T1 writes its replacement with the flipped
   default.
4. Keep `rv003usb/rv003usb-arm.S` byte-identical. Commit listing the resolutions. Do not push.

Accept (static): `git submodule status` shows only `ch32fun`; `git status` clean;
`git diff origin/py32 -- rv003usb/rv003usb-arm.S` empty; `! grep -rn 'py32f0-template' . --exclude-dir=.git`;
`make -C demo_gamepad`, `make -C demo_hidapi`, `make -C bootloader`, `make -C bootloader_dfu/v003`,
`make -C bootloader_dfu/wg015 PREFIX=riscv64-unknown-elf-`, and
`make -C demo_hidapi -f ../rv003usb/wg015/Makefile.wg015 PREFIX=riscv64-unknown-elf-` all succeed.

Size: one session.

#### Wave 1

**T1 — Target skeleton, linker scripts, RAM budget — Opus — closes DV D-4, DV D-5**
*Opus: DV D-5 is the only defect in the set that produces no diagnostic at build time and an
obscure one at run time; and the RAM arithmetic below decides which parts the port supports.*

Owns: `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f003x6.ld,
py32f030x6.ld, py32f030x8.ld, py32f002bx5.ld, Makefile.py32, py32_stdio_stub.c, selftest_main.c,
README.md}`.

Entry: T0 landed.

Does:

*(a) The linker script — the real content of this task (BF §4, DV D-5).* There is **no
`.datacode` rule anywhere** — not in the branch, not in `py32f0-template@289ffc8`. The branch's
RX engine lands in RAM only because the stock script's rule is `*(.data*)`, a wildcard with no
dot, which absorbs it; verified by linking (VMA `0x20000000`, LMA `0x08000200`, BF §4). A script
spelling that rule `*(.data.*)` places the RX engine in flash: no error, no warning, build
succeeds, every timing figure void. Therefore:
* one named output section `.timecrit` (RAM, `AT>` flash), input rule
  `KEEP(*(.timecrit)) KEEP(*(.timecrit.*)) KEEP(*(.datacode))` — the third pattern carries the
  unmodified branch engine until T2 renames its section, and is **not** removed when T2 lands
  (an inherited object could still emit it);
* `ASSERT(SIZEOF(.timecrit) > 0, "…")` — this is the D-5 killer: if the input rule ever stops
  matching, the output section is empty and the build fails instead of running from flash;
* `ASSERT(ADDR(.timecrit) >= ORIGIN(RAM) && ADDR(.timecrit) + SIZEOF(.timecrit) <= ORIGIN(RAM) + LENGTH(RAM), "…")`;
* `-Wl,--orphan-handling=error` in `Makefile.py32`, with `.ARM.attributes`, `.ARM.exidx`,
  `.comment` and `.debug*` explicitly placed or discarded. This generalises the fix: **any**
  input section the script does not name becomes a link error rather than a silent placement.
  That is what turns D-5 from a class of accidents into an impossibility.

The remaining half of the rule — "no flash literal-pool load inside a timed bit cell" (CF §1,
RV R23) — cannot be expressed in `ld`, which does not resolve `[pc,#imm]` targets. It is T14's
walker. T1's part is to make it *fail the build*: `Makefile.py32` declares `all: … check-cycles`
so the walker runs on every build, not on request. `--orphan-handling=error` covers the case
where a pool is emitted into a section nobody placed.

*(b) Memory map, and a real RAM budget.* Geometry from BF §6 / `py32f0-template@289ffc8`
LDScripts, one script per supported part:

| part | RAM | FLASH | script |
|---|---|---|---|
| PY32F003x4 | 2 K | 16 K | — see below, out of scope |
| PY32F003x6 / PY32F030x6 | 4 K | 32 K | `py32f003x6.ld` / `py32f030x6.ld` |
| PY32F003x8 / PY32F030x8 | 8 K | 64 K | `py32f030x8.ld` |
| PY32F002Bx5 | 3 K | 24 K | `py32f002bx5.ld` |
| PY32F002Ax5 | 3 K | 20 K | out of scope (RV R25) |

The flip makes this a live constraint: the new primary family's smallest member has **less** RAM
than the demoted F002B. Fixed RAM floor for a PY32 app, all terms sourced:

| term | bytes | source |
|---|---|---|
| `.timecrit` ceiling | 960 | T2 acceptance (branch today: 252 RX + 512 TX = 764 measured, BF §3) |
| `rxbuf` | 20 | `4 + USB_BUFFER_SIZE(12) + 4`, T2 step 2 |
| fixed top-of-RAM `.noinit` block | 16 | Р6 |
| stack `ASSERT` floor | 768 | T2 acceptance |
| **floor** | **1764** | |

2048 − 1764 = **284 B** left on an F003x4 for descriptors, `.data`, `.bss` and
`rv003usb_internal_data`. `demo_hidapi` alone carries eight descriptor-list entries plus device,
config, HID report and three strings (`demo_hidapi/usb_config.h:28-152`), all of which Р4 puts in
RAM. **Plan decision: PY32F003x4 is out of scope; the minimum primary part is x6 (4 K).** T1
does not assert this by hand — the ld `ASSERT` in (c) fails on x4 and passes on x6, and T4's
`--print-memory-usage` prints the number that closes the question.

*(c)* `ASSERT(__noinit_top - __bss_end >= PY32_STACK_MIN, "stack below PY32_STACK_MIN")`,
`PY32_STACK_MIN = 768`. Section order: `.isr_vector` (flash) → `.data` + `.rodata.usbdesc`
(RAM `AT>` flash) → `.timecrit` (as (a)) → `.text`/`.rodata` (flash) → `.bss` → `.noinit`
(NOLOAD) → fixed block `__noinit_top = ORIGIN(RAM)+LENGTH(RAM)-16` with
`PROVIDE(py32_boot_flag = __noinit_top); PROVIDE(py32_boot_count = __noinit_top+4);
PROVIDE(py32_dbltap = __noinit_top+8); PROVIDE(py32_noinit_spare = __noinit_top+12);` → stack top
= `__noinit_top`. `PROVIDE(__timecrit_lma/_start/_end, __data_*, __bss_*)`.

*(d)* `py32_min.h` — structs, offsets and bit masks from §3.3-3.4, one `_Static_assert` per
struct size and offset (`offsetof(GPIO_TypeDef,BSRR)==0x18`, `RCC.CSR==0x60`, `EXTI.PR==0x0C`,
`EXTICR[0]==0x60`, `IMR==0x80`, FLASH `CR==0x14`, `SR==0x10`, `TS0==0x100`,
`SCB.ICSR==0xE000ED04`…); family switches (`PY32F030`: ports A/B/F, PLL, `HSI_FS=100` @24 MHz;
`PY32F002B`: ports A/B/C, no PLL, `HSI_FS=101`); OSPEEDR encoding with its RM002B p78 cite (Р8);
LSI and `RCC->CSR` bits, needed by T12; every block cites its RM page; assembler-clean (no `UL`).
Written from RM/DS, not copied from CMSIS or the LL drivers (§9.1).

*(e)* `ch32fun.h` shim, mirroring `rv003usb/wg015/ch32fun.h`: `NVIC_EnableIRQ`/`SetPriority`
(2-bit `IPR`)/`SystemReset`, `__disable_irq`/`__enable_irq`, SysTick struct +
`PY32_systick_freerun()` (`LOAD=0xFFFFFF`, `CLKSOURCE=HCLK`, `ENABLE`, no IRQ — **the only writer
of `LOAD` in the tree**, Р9), wrap-safe `Delay_Ms`/`Delay_Us` (`Delay_Ms` chunked ≤ 100 ms),
`SystemInit()` no-op, `#error` on `RV003USB_USB_TERMINAL`/`RV003USB_DEBUG_TIMING`,
`extern uint32_t py32_boot_flag, py32_boot_count, py32_dbltap;` and
`static inline void py32_app_alive(void){ py32_boot_count = 0; }`.

*(f)* `startup_py32.S`: 48-word vector table, weak `Default_Handler`, EXTI symbols exactly as
`startup_py32f002b.s:133-135`; `NMI_Handler` = `NVIC_SystemReset` when `PY32_HSE=1` (a silent CSS
fallback to the untrimmed HSI drops the link, §3.2) else weak default; `Reset_Handler`: SP =
`__noinit_top`, copy `.data` (incl. `.rodata.usbdesc`), copy `.timecrit` (LMA→VMA), zero `.bss`
(never `.noinit`), then clock init per family —
**F030/F003 (primary):** `HSI_FS=100` trim word from `0x1FFF0F10`, `HSION`, `PLLCFGR.PLLSRC=HSI`,
`PLLON`, wait `PLLRDY`, `ACR=LATENCY_1`, `CFGR.SW=PLL`, wait `SWS` (RM030 p77, p83). 24 × 2 =
**47.98 MHz, −0.04 %** (CF §2, `xm_030.md:15`, `:336`) — inside the USB ±1.5 % tolerance and the
sampling margin **with no trim step and no servo at reset** (TC §3.1).
**F002B (second track):** `ACR=LATENCY_1`, `HSION`, wait `HSIRDY` — and **never** load
`[0x1FFF0104]` blind: the factory 48 MHz word runs the chip at a measured 43.12 MHz, −10.2 %
(`xm_002b.md:172-175`, `:209-210`, RV R19). The clock is brought to 48 MHz by T12's calibration,
called from `main()` before `usb_setup()`, not from startup.
Then `VTOR = __vector_table`, `bl main`. Bring-up builds (`PY32_SWD_DELAY=1`) hold ≈100 ms before
reconfiguring clocks or pins (RV R24, `xm_030.md:376-378`) — without it a probe cannot re-attach
and F002B has no ROM loader to recover through.

*(g)* `Makefile.py32`, mirroring `Makefile.wg015`: **`MCU ?= PY32F030x8`** (the flip; also
`PY32F030x6`, `PY32F003x6`, `PY32F002Bx5`); `SOURCES := $(TARGET).c rv003usb/rv003usb-arm.S
rv003usb/rv003usb.c startup stub $(wildcard $(PY32_DIR)/engine_*.S) $(wildcard $(PY32_DIR)/py32_*.c)`;
`-include $(PY32_DIR)/py32_cal_*.mk`; `DEFS` per §9.1 on **both** the `%.o: %.c` and `%.o: %.S`
rules (`-x assembler-with-cpp`); `-mcpu=cortex-m0plus -mthumb -Os -ffunction-sections
-fdata-sections -nostartfiles -specs=nano.specs`, `-Wl,--gc-sections
-Wl,--orphan-handling=error -Wl,--print-memory-usage`; targets
`all size lst bin flash clean check-cycles`, `all: … check-cycles`; `flash` = `pyocd load
--target py32f030x8 …` (Puya DFP, §3.5) with a `JLINK=1` alternative, **no OpenOCD**.

*(h)* `README.md`: pins (D+=PB0, D−=PB3, DPU=PB2, as the branch, same on F030), clock options,
the RAM budget table above, IRQ policy (Р7), **D± drive: lowest OSPEEDR + 33 Ω series, why (Р8),
and "22 pF on D± is not a fix" (PA A-8)**, `USB_DPU_DELAY_MS` (PA A-10), the SysTick rule (Р9),
probes (§3.5), and the one-line statement that the primary part needs no clock servo at reset.

Accept (static, all in this container):
1. `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` and `MCU=PY32F002Bx5`
   link `selftest_main.c` against the **unmodified branch** `rv003usb-arm.S` and master
   `rv003usb.c` (a T1-local weak `usb_port_hw_setup` stub is allowed until T3 lands).
2. `arm-none-eabi-objdump -h selftest.elf`: `.timecrit` VMA in SRAM, LMA in flash, size > 0;
   `.noinit` outside `.bss`; `.isr_vector` at `0x08000000` with word0 = `__noinit_top`,
   word1 = `Reset_Handler|1`.
3. **D-5 regression, both directions.** Temporarily narrowing the `.timecrit` input rule to
   `*(.data.*)` must make the link **fail** on the `SIZEOF(.timecrit) > 0` assert (record the
   command and the message in the commit); restoring it must make it pass. This is the single
   check that distinguishes this port from the branch.
4. `nm selftest.elf | grep py32_boot_flag` = `0x20001FF0` on F030x8 (8 K), `0x20000FF0` on x6
   (4 K), `0x20000BF0` on F002Bx5 (3 K).
5. `MCU=PY32F003x4` fails to link on the stack `ASSERT` with `demo_hidapi` — the budget decision
   of (b) is enforced, not asserted in prose.
6. `make -n … | grep rv003usb-arm.S` contains `-DRV003USB_PY32=1 -DPY32F030=1 -DPY32F030x8=1`;
   the engine objects of the two `MCU` builds differ (`cmp` exits 1) — the `#if PY32F002Bx5`
   arms are finally both selected by the build system (DV D-3, half closed here, half in T7).
7. `grep -c 'SysTick->LOAD' rv003usb/py32/ch32fun.h` = 1 and `grep -rn 'SysTick->LOAD'
   rv003usb/py32/*.S rv003usb/py32/py32_*.c` empty (Р9).
8. `_Static_assert`s compile; `! grep -rn 'py32f0-template' rv003usb/py32/`.

Size: one session. The linker script and `py32_min.h` are the bulk; (e)–(h) are transcription.

**T3 — C-layer seams: per-target `usb_port_<chip>.h` — Sonnet**
*Sonnet: a mechanical code move whose correctness is proved by two byte-identity gates, not by
judgement.*

Owns: `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h` (new),
`rv003usb/wg015/usb_port_wg015.h` (new), `rv003usb/py32/usb_port_py32.h` (new).

Entry: T0 landed. (Item 3 of Accept additionally needs T1 — wave-1 exit item.)

Does: Р2 exactly. `rv003usb.h` gets the single selector and declares the seam API. `usb_setup()`
in `rv003usb.c` becomes `rv003usb_internal_data.se0_windup = 0; usb_port_hw_setup();`, with the
V003/V00x body (`c:59-153`, incl. `DEBUG_TIMING`) moved **verbatim** into `usb_port_ch32.h` and
the WG015 body into `usb_port_wg015.h`; the reboot block (`c:173-186` and the WG015 variant)
becomes `USB_PORT_REBOOT_TO_BOOTLOADER()`; the `USB_DM_IRQ` block from 0ad3c42 moves into the
PY32 header (this is the edge T2 depends on); `RV003USB_USB_TERMINAL` and `RV003USB_DEBUG_TIMING`
become `#error` under `RV003USB_PY32`.
PY32 `usb_port_hw_setup()`: `RCC->IOPENR |= GPIOxEN`; DP/DM `MODER=00, PUPDR=00,
OSPEEDR=USB_PORT_OSPEED` (**default 0 = lowest**, Р8; overridable from `usb_config.h`); DPU
`MODER=01` then `BSRR` high after `Delay_Ms(USB_DPU_DELAY_MS)` (default 0; charger-detect ICs
sharing D± need ≈2 s, rv003usb #137, PA A-10); `EXTI->EXTICR[DM>>2]` port select (field widths as
`py32f002b_ll_exti.h:153-160` — lines 0-4 are 3-bit, 5-7 are 1-bit); `EXTI->IMR |= 1<<DM;
EXTI->FTSR |= 1<<DM; EXTI->PR = 1<<DM`; `NVIC_SetPriority(USB_DM_IRQn, 0)`; `NVIC_EnableIRQ`;
`py32_app_alive()`.
`USB_PORT_REBOOT_TO_BOOTLOADER()`: `py32_boot_flag = 0xB00710AD; NVIC_SystemReset()` —
`py32_boot_flag` is T1's ld-provided top-of-RAM word, **not** a `.noinit` variable.

Accept (static): (1) `make -C demo_gamepad` (CH32V003) `.bin` **byte-identical** to the T0 build
(`cmp`); (2) WG015 `demo_hidapi` and `bootloader_dfu/wg015` build and their `.bin` is identical or
the diff is explained in the commit message; (3) `rv003usb.c` compiles for `MCU=PY32F030x8` and
`PY32F002Bx5` with `-Wall -Werror` (wave-1 exit item — needs T1); (4) `grep -n 'OSPEEDR'
rv003usb/py32/usb_port_py32.h` shows only the `USB_PORT_OSPEED` use, default `0`;
(5) `grep -c 'py32_app_alive' rv003usb/py32/usb_port_py32.h` ≥ 1; (6) `grep -c 'USB_DM_IRQ'
rv003usb/rv003usb.h` = 0.

Size: one session.

**T8 — Documentation set — Sonnet**
*Sonnet: transcription against a source-per-fact rule; no design decisions are taken here.*

Owns: `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}`.

Entry: T0 landed.

Does: `chip_info.md` = §3 expanded with page refs, incl. §3.5 probes; `ledger_arm.md` = LG's
Appendix A (RAM-execution column) + the TX ledger + the staircase costs, carried over verbatim
with LG's cites, **not re-derived**; `STATE.md` = fleet progress table + the append-only
`requests` section (§9.1's single shared exception) + the provenance table (one row per task:
source, licence class, `Provenance:` trailers seen, Р10); `TODO.md` in the house format of
`doc/wg015/TODO.md`.

Accept (static): every fact carries a source; `STATE.md` lists **every** task T0–T16 with its
owner, model and wave; `grep -c '^| T' doc/py32/STATE.md` ≥ 18; the provenance table names
Grainuum (MIT), joyboot (MIT), LemcUSB (GPLv3, ideas only), stm32f030-vusb (GPL-3.0, ideas only),
V-USB (GPLv2, ideas only); `grep -c 'RAM-execution' doc/py32/ledger_arm.md` ≥ 1 (the ledger must
state which column it is in — LG §0).

Size: one session.

**T13 — Keepalive trim actuator: what the servo becomes after the flip — Opus**
*Opus: this task decides the default behaviour of the only closed loop in the firmware; a wrong
default either hunts on the primary part or silently disables drift correction on both.*

Owns: `rv003usb/py32/usb_port_py32_trim.h`, `rv003usb/py32/selftest_trim.S`.

Entry: T0 landed. (The cost item of Accept needs T14 — wave-1 exit item.)

Does. The branch ships a **stub**: `handle_se0_keepalive:` at `arm.S:217` is `// TODO` followed by
`ldr r0, =interrupt_complete; bx r0` — it acknowledges nothing and measures nothing. DV's "Not
verified here" is right that this is a design gap, not a source defect. After the flip its shape
changes, and the honest statement is what it **becomes**, in three modes selected at compile time
by `USB_TRIM_MODE`:

| mode | for | what runs on every keepalive |
|---|---|---|
| `off` | HSE builds on F030 | nothing — `USB_TRIM_ACTUATE` expands to zero instructions |
| `drift` | **default; F030/F003 HSI, the primary path** | ack `EXTI->PR` first, measure the `USB_TICK` delta `(last − now) & 0xFFFFFF`, sanity ±4000 (as `S:762-772`; an out-of-window delta also resets the lock counter), store `last_se0`/`delta_se0`/`se0_windup` (`h:190-192`), then a single saturating slow term `trim = trim0 − USB_TRIM_SIGN · sat(windup >> USB_TRIM_SLOW_SHIFT, ±USB_TRIM_SAT)` |
| `acquire` | F002B, after T12 has already brought the clock into range | `drift` plus the two-rate acquisition arm: while `lock < USB_TRIM_LOCK_N`, `trim −= USB_TRIM_SIGN · (dev >> USB_TRIM_FAST_SHIFT)`, `lock++`; `trim0` captured at the first keepalive |

**What the flip deletes from the primary path.** HSI 24 × PLL2 = 47.98 MHz, −0.04 % (CF §2,
`xm_030.md:15`, `:336`) is inside the USB ±1.5 % tolerance and inside the engine's ≈0.44 %
sampling margin (§2.4.5) at reset, so `drift` is the default and the following v2 code is **not
compiled on the primary path**: the fast-acquisition arm, the `lock` counter, `USB_TRIM_LOCK_N`,
`USB_TRIM_FAST_SHIFT`, and the capture of `trim0` at the first keepalive. With them goes the
constraint that set their values — the servo no longer has to reach 0.25 % inside the host's
reset→first-SETUP window (RV R15, OQ9), because it starts inside it. Concretely, the keepalive
path shrinks from acquisition + drift to: ack, delta, sanity, one shift, one saturate, one
`ICSCR` write.
What does **not** go away: temperature. DS030 T5-15 gives ±2 % over 0–85 °C and −4/+2 % over
−40…85 °C — both outside the sampling margin (RV R2). So the servo is not deleted, it is demoted
from *enumeration precondition* to *drift compensator*, and it stops being on the critical path
of bring-up.
Actuation constraints, both from measurement: on F002B the servo moves `TRIM_L` **only** —
`TRIM_H` scales the range in coarse steps (+41 % at 0b0111, +50 % at 0b1000, `xm_002b.md:232-246`)
and a step across a band is ≈9 %, which throws the delta out of the sanity window and loses lock
(RV R20). `USB_TRIM_SAT` (default ±64 LSB) must keep the excursion inside the band T12 selected.
`USB_TRIM_SIGN` is a build constant, measured by bench6/T16 — the sign of the HSI trim LSB is not
documented.
The header expands to Thumb-1 with **no** literal-pool load: the register base and the masks
arrive in registers from the caller's frame, because the actuator runs in `.timecrit` and a
flash-resident pool costs 4 cycles there (CF §1, RV R23).
`selftest_trim.S` instantiates `USB_TRIM_ACTUATE` alone in `.timecrit` between two global labels,
so the walker can cost the actuator in isolation without linking the engine.

Accept (static):
1. Each of `USB_TRIM_MODE = off|drift|acquire` assembles `selftest_trim.S` for both `MCU`s;
   an unset or unknown `USB_TRIM_MODE` is `#error`; `acquire` on a `PY32F030` build is `#error`
   ("the primary path does not acquire at reset — see §9.4 T13").
2. `off` produces a zero-byte body: `arm-none-eabi-nm --print-size` shows
   `__trim_actuate_end - __trim_actuate_begin == 0`.
3. `arm-none-eabi-objdump -d selftest_trim.o | grep -c 'ldr.*\[pc'` = 0.
4. Wave-1 exit item: `tools/py32_cyc.py --path trim` reports the `drift` body ≤ 40 cycles and the
   whole keepalive path (first instruction → exception return) ≤ 96 cycles, the budget LG sets
   because a token may follow a keepalive EOP after 2 bit-times of idle (USB 2.0 §7.1.18-19).
5. `grep -c 'TRIM_H' rv003usb/py32/usb_port_py32_trim.h` ≥ 1 **and** no write to `TRIM_H` in the
   actuator body (`objdump` of the `acquire` build): RV R20 enforced, not documented.

Size: one session.

**T14 — Cycle walker, cost table, literal-pool check — Opus**
*Opus: this file encodes the cost model the entire ledger is padded against; a walker that passes
for the wrong reason is worse than no walker.*

Owns: `tools/py32_cyc.py`, `tools/py32_cyc_costs.json`, `tools/py32_cyc_selftest/*`.

Entry: T0 landed. Self-contained otherwise — it writes its own fixtures and assembles them with
the installed gcc (BF §1).

Does: LG's Appendix-B seed, finished. Two-column model, keyed on **where the instruction
executes**, from `py32_cyc_costs.json`:

* `exec: {RAM, FLASH}` with the CF §1 rows — RAM: ordinary 1, `b<cc>` taken 2-3 / not-taken 1,
  `bl` 4, `bx` 3, GPIO `ldr/str` 1, RAM data **2**, flash literal pool **4**, `push/pop` **2 + 1·(n−1)**;
  FLASH: RAM data **4**, flash literal pool 2, `push/pop` 4 + 1·(n−1).
* The region of every address comes from the **section map of the ELF**, never from a symbol name
  or a `.req` alias (LG's rule) — so a mis-placed section shows up as a cost change, not a silent
  pass.
* `--cost-table FILE` override (RV R4/G1: no pad constant is final before the bench).
* Point values give the gate; `ranges` maxima give the exposure; report
  `name cycles [min..max] budget PASS|FAIL|EXPOSED`, exit 1 on any FAIL.
* **The literal-pool rule as a hard error, not a report** (CF §1, RV R23, BF §5): every
  `ldr rN,[pc,#imm]` reached from a path marked time-critical has its pool address resolved and
  its region looked up; a pool outside RAM is a fatal error. BF §5 measured that today the rule
  holds **by construction, not by enforcement** — gcc happened to emit `.datacode`'s pool inside
  `.datacode` (`.word`s at 0xdc..0xf8 of a 0xfc section), with three of the eight loads inside
  timed loops. That is a property of one compiler invocation, not of the source. T1 makes
  `all: … check-cycles`, so this check fails the build.
* `--selftest`: assemble the `tools/py32_cyc_selftest/*.S` fixtures with the installed
  arm-none-eabi-gcc, link them at known VMAs with a fixture linker script, and compare against
  the counts written in each fixture's header comment. Fixtures must cover, at minimum: a
  straight-line block in each region; a `push`/`pop` pair in each region; a `[pc,#imm]` load with
  the pool in RAM and the same load with the pool in flash (they must differ by 2 in the RAM
  column); a taken and a not-taken `b<cc>`; a `bl`/`mov pc,lr` staircase entry; and one fixture
  that **must be rejected** — a flash pool inside a time-critical path (`--selftest` fails if it
  is accepted).

Accept (static): `python3 tools/py32_cyc.py --selftest` exits 0; the negative fixture makes a
normal run exit non-zero with a message naming the address and the region;
`python3 -c "import json; json.load(open('tools/py32_cyc_costs.json'))"` exits 0 and the file
carries both `exec` columns and a `source` field per row citing `xm_030.md:<n>`;
`grep -c 'ranges' tools/py32_cyc.py` ≥ 1; `! grep -n 'startswith(.rv003usb_wait' tools/py32_cyc.py`
(no cost may be taken from a symbol name).

Size: one session. The walk function and the fixtures are the bulk; the cost table is transcription
from LG §0.

#### Wave 2

**T2 — Engine: RX path, correctness, placement — Opus — closes DV D-1, DV D-2**
*Opus: this is the engine everything else in waves 2-4 builds on; a wrong cycle count here is
wrong everywhere downstream, and nobody catches it by inspection.*

Owns: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h`.

Entry: T1 landed (`.timecrit` section, `py32_min.h`, build). T3 landed (`USB_DM_IRQ` moved into
the PY32 port header — wave-1 exit item). T13 landed (`USB_TRIM_ACTUATE` callable from the SE0
branch — wave-1 exit item). T14 landed (`tools/py32_cyc.py --selftest` exits 0 — wave-1 exit item;
T2's own acceptance is a walker run).

Does — RX path and correctness only; the TX re-pad and the per-part `#if` review are T2T's job
(§9.0 point 5), serialised on this file:

0. **Guard** (v2 §2.6, PA S-11): first lines `#ifndef RV003USB_PY32 #error … #endif` and
   `#if !defined(PY32F030) && !defined(PY32F002B) #error … #endif`. File header carries the MIT
   notice for Grainuum's staircase idea (Р10, `[MIT-attrib]`) and the [11,74]-cycle entry-window
   citation. `usb_port_py32_asm.h` `#error`s on an unset `USB_PORT`.
1. Replace every hand-written literal in the RX region with the §7.1-style macros from
   `usb_port_py32_asm.h` (GPIO base, `IDR_OFFSET`/`BSRR_OFFSET` — `0x10`/`0x18` in either family,
   BF §7, so the macro carries no per-part arm).
2. **Placement (Р4, half of it — RX only).** `.pushsection .datacode,"ax"` becomes
   `.section .timecrit,"ax"`; `.ltorg` after each RX block so every literal pool it emits resolves
   inside `.timecrit` (RAM) — **not a preference, a hard rule now enforced by T14's walker**
   (RV R23: a pool that lands in flash costs 4 from RAM code, `xm_030.md:490`, and today it holds
   only "by construction", BF §5). `rxbuf: .space 4 + USB_BUFFER_SIZE + 4` (20 B, T1's floor
   table) — the leading 4 is the limit word D-2's bound check reads, the trailing 4 keeps the
   struct word-aligned. TX stays in `.text` (flash) until T2T moves it; the linker's third
   `KEEP(*(.datacode))` pattern (T1) stays live for exactly this reason. **Consequence for the
   wave-2 exit gate:** the walker's path list carries the TX paths against the flash column
   (their pre-existing, unchanged budgets) until T2T retargets them to the RAM column in wave 3
   — `make check-cycles` is scored against mixed columns at the end of wave 2 by design, not by
   oversight. Placement is corroborated a second time, in a fully linked image rather than a
   synthetic object: BF §9 shows `EXTI2_3_IRQHandler` at `0x200000c8`, `preamble_loop` at
   `0x200000e6`, `bit_process` at `0x20000142`, `rxbuf` at `0x2000023c` (all RAM) against
   `usb_send_data` at `0x0800022c` (flash) — the vendor-toolchain build BF §9-§10 exercised, not
   this port's own linker script, but the same mechanism D-5 describes.
3. **D-1 — endpoint bound, `bhi`→`bhs`** (`arm.S:277`, DV D-1). One-instruction fix in the
   dispatch tail, which stays in flash and is not cycle-counted (Appendix A; RV R3/OQ14 —
   "dispatch in flash reads RAM data at 4/access — not timed, acceptable", LG's request, noted,
   no action needed beyond this fix). Same encoding size, same cycle count, zero effect on any
   RX or TX cell.
4. **D-2 — RX overrun, bound the store** (`arm.S:145-148`, DV D-2). At `is_end_of_byte`:
   `cmp r2, r8; bhs done_usb_message` before the `strb`, with r8 = `rxbuf + 4 + USB_BUFFER_SIZE`
   loaded once at ISR entry (r8 is free through the RX path). This sits **inside the
   cycle-counted path**, so it is not free: `cmp lo,hi` costs 1 cycle (RAM, ordinary instruction,
   LG §0) and must be paid for out of the existing 32/32/32/32/64 budget, not added on top. Pay
   for it by removing 1 cycle from `DELAY_CYCLES(6)` (arm.S:151) on the mid-byte path and by
   shortening the EOB tail's `nop` pad by 1; the walker's path list carries both variants and
   must still report exactly 32 — a bound check that changes the budget is a bug, not a feature.
5. **F9 — bounded preamble spin** (PA A-16): `preamble_loop` gives up after
   `USB_RX_PREAMBLE_LIMIT` (≈512 cycles = 16 bit-times, `usb_port_py32_tune.h`) instead of
   spinning forever on a stuck or shorted line; counter in `SCRATCH` (r4, free there), 4×-unrolled
   poll so sample spacing is 4/4/4/7 (worst-case detect jitter 0…6 instead of 0…4 — RV R18; the
   walker reports the wider spacing as an `EXPOSED` range, not a silent pass). `USB_RX_SYNC_DELAY`
   (F5) is re-derived on paper against the wider jitter so the 14–18/32 sample band still holds;
   RV gate G7 is the hardware check that it actually does on silicon, out of scope here.
6. **F3 — early exit on `rx_stuffed`.** Sample the delay once and `beq done_usb_message` when no
   bus transition has occurred, instead of spinning the full `DELAY_CYCLES(24)`; the 4-cycle test
   comes out of that delay's own budget so the slot stays 32/64.
7. **F6 — `RV003_ADD_EXTI_MASK`/`HANDLER` hook**: on ISR entry, if `EXTI->PR & USB_DMASK` is
   zero, jump to a user hook in flash before touching any RX state; ack the extra mask bits at
   exit (mirrors the RISC-V `S:113-129/645-650` pattern) — how an app that also needs EXTI on
   lines 4-15 shares the vector (RV R11) without T2 knowing about it.
8. **Marker** (§7.3, zero-intrusion debug pulse): r10 = mask loaded from the RAM word
   `usb_dbg_mask`, one `str`/pulse per RX slot boundary; production mask is `0`, so the pulse
   compiles to nothing measurable when disabled — ported once here because `usb_port_py32_asm.h`
   is this task's file.
9. **RX-side staircase entries.** The two RX entry pads (after `DELAY_CYCLES(96)` and
   `DELAY_CYCLES(71)`) move off inline `nop` padding onto `bl rv003usb_wait_N` (§7.4) for N ≥ 9
   (below that, inline `nop` stays cheaper than a `bl`+return). The label table itself
   (`rv003usb_wait_5`…`rv003usb_wait_N`, `N − C` `nop`s then a return) is generated in this file
   because RX needs it first; T2T extends the same table for TX's larger N (up to 64) in wave 3.
   `C` is **not assumed**: it comes from `usb_port_py32_tune.h`'s `USB_STAIRCASE_C`, wired to
   T14's default cost table (`bl`:4 + `mov_pc`:2 = 6, the seed values in `py32_cyc_costs.json`),
   overridable from a `py32_cal_*.mk` (T10/T16's seam, §9.2) once bench K10 measures whether the
   return is `mov pc,lr` (C=6) or `bx lr` (C=7) — no header edit, ever, for a measured constant.
10. **In-slot RX pads stay inline** (LG's structural finding): the loop-invariant literal loads
    `ldr CRC,=0xffff` / `ldr SCRATCH,=0xa001; mov POLY_RX,SCRATCH` hoist out of the packet-type
    loop into the `DELAY_CYCLES(71)` pad (zero extra registers — CRC/POLY_RX already hold the
    values, LG §2.1) — this is what keeps that loop's literal load out of the timed cell
    entirely, rather than merely making it cheap.
11. Every pad above is a **formula in `usb_port_py32_tune.h`, parameterised on `USB_B_TAKEN`**
    (default 2 — a taken branch/`b<cc>` from RAM, LG §0), not a baked integer: e.g. the mid-byte
    `bit_process` pad is `4 − 2·USB_B_TAKEN` `nop`s (≥0 only at `USB_B_TAKEN`≤2; at 3 the fix is
    structural — fewer taken branches per cell, LG's Appendix A reading — out of scope here since
    B=2 is what T14's default cost table and every criterion below assume; a `USB_B_TAKEN=3`
    build is a research build for T10/T16, not a wave-2 deliverable). Same shape as the WG015
    house ledger (LG's honoured request; T2T applies the same style to the TX pads).

Accept (static):
1. `arm-none-eabi-gcc -x assembler-with-cpp -c rv003usb/rv003usb-arm.S -o /dev/null` with no `-D`
   exits non-zero, stderr matches `#error` (item 0's guard).
2. Assembles clean (`-Wa,--fatal-warnings`) for `MCU=PY32F030x8` and `PY32F002Bx5`.
3. `python3 tools/py32_cyc.py <elf>` exits 0: every RX path reports exactly 32 and the stuffed
   path 64; the keepalive path (T13) ≤ 96.
4. `arm-none-eabi-objdump -d rv003usb-arm.o | grep -c 'bhi.*done_usb_message'` = 0 and the
   disassembly at the old D-1 site shows `bhs`/`bcs` instead.
5. `arm-none-eabi-nm rv003usb-arm.o`: the RX-ISR-through-`done_usb_message` symbols resolve to
   `.timecrit` (SRAM); the TX symbols are still in `.text` (T2T has not moved them yet).
6. `arm-none-eabi-objdump -d --no-show-raw-insn rv003usb-arm.o` shows every `ldr rN,[pc,#imm]`
   reached from a `.timecrit` label resolving inside `[0x20000000, 0x20000000+PY32_SRAM_KB·1024)`
   (T14's literal-pool rule, RV R23) — zero exceptions.
7. `grep -c 'USB_B_TAKEN' rv003usb/py32/usb_port_py32_tune.h` ≥ 1; the assembled `.text`/`.timecrit`
   byte count changes if `USB_B_TAKEN` is overridden to 1 at build time (the formulas actually
   drive the pad count, not a comment beside a fixed integer).
8. `grep -q 'xobs/grainuum' rv003usb/rv003usb-arm.S` (Р10 attribution present).

Size: one session; hard. The D-2 rebalance and the RX staircase migration are the parts that need
care — everything else is mechanical against LG's tables.

**T4 — Demos conditioned for PY32 — Sonnet**
*Sonnet: a conditional-compile pass against gates T1/T3 already fixed; no engineering judgement
left to exercise.*

Owns: `demo_gamepad/{usb_config.h, funconfig.h, demo_gamepad.c, README.md}`,
`demo_hidapi/{usb_config.h, funconfig.h, demo_hidapi.c, README.md}`.

Entry: T1 landed (`Makefile.py32`, `py32_min.h`). T3 landed (`usb_port_py32.h`, `USB_PORT_OSPEED`).

Does: pins under `#if defined(RV003USB_PY32)` — `USB_PORT B, DP 0, DM 3, DPU 2` (the branch's own
numbering, unchanged on F030). `funconfig.h` untouched for V003 (T4 must not break the
byte-identity gate — new flags only inside `#if RV003USB_PY32`). Descriptors carry the `USBDESC`
section attribute exactly as `bootloader_dfu/wg015/usb_config.h:39` when `RV003USB_PY32` (Р4's
RAM-placement rule — descriptors are a `usb_send_data` source and must resolve inside SRAM, the
same rule T2's literal-pool check enforces for code). `demo_hidapi.c`'s WS2812/GPIOD block guarded
`#if !defined(RV003USB_PY32) && !(defined(WG015)&&WG015)`. `Delay_Ms(1)` before `usb_setup()` kept
(h TDDIS note, unaffected by the flip). PY32 builds never call a clock-configuration function from
either demo — T1's `startup_py32.S` brings the clock up before `main()` runs, unlike the vendor
branch's `demo_gamepad.c:15-23`, which calls `BSP_RCC_HSI_48MConfig()`/`BSP_RCC_HSE_PLLConfig()`
per part and silently configures **no clock at all** for any part neither `#if` arm names — a
build-system defect this port's architecture avoids structurally rather than by adding a third
arm (BF §10.3). README build lines updated to the flipped default:
`make -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` first, `MCU=PY32F002Bx5` second.

Accept (static): both demos build for `MCU=PY32F030x8`, `PY32F030x6`, `PY32F002Bx5`;
`arm-none-eabi-size --print-memory-usage` prints RAM ≤ 1900 B on F030x6 and ≤ 2200 B on F002Bx5
(T1's floor table, §9.4 T1(b) — the vendor-toolchain build of the same demo lands at 2128 B/8K on
F030x8 and 1168 B/3K on F002Bx5, BF §9, a different header stack from ours but the right order of
magnitude to sanity-check against); V003 `demo_gamepad.bin` unchanged vs the T0 build (`cmp`);
descriptor placement gate (PA S-4): `arm-none-eabi-nm --numeric-sort demo_hidapi.elf | grep -iE
'descriptor|string|report' | awk '$1 !~ /^2000/' | wc -l` = 0; `grep -c 'BSP_RCC\|SystemClock_Config'
demo_gamepad/demo_gamepad.c demo_hidapi/demo_hidapi.c` = 0 under `RV003USB_PY32` (clock init is
T1's job, never the demo's); `grep -rn 'SysTick->LOAD' demo_gamepad demo_hidapi` empty (Р9).

Size: one session.

**T6 — Calibration bench firmware: K1-K11 kernel superset + VCD gates — Sonnet**
*Sonnet: the kernel shapes and the two gates are fully specified by `rework/ledger.md` §5; this
task assembles what is already designed.*

Owns: `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S,
bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`,
`tools/wg015_vcd/*`.

Entry: T1 landed (build).

Does: **v2's bench1/bench2 are retired and replaced by the K1-K11 kernel superset of
`rework/ledger.md` §5** (LG's request, honoured in full):
1. `bench_kernels.S`: each kernel is a 1000×-unrolled straight-line block assembled twice — once
   into `.timecrit` (RAM copy), once into `.text` (flash copy) — timed with the free-running
   SysTick (`VAL` before/after, HCLK source, Р9), empty-kernel overhead subtracted, 16 repeats
   reported as min/max (LG §5: "the spread is a result", per the source's own alignment caveat).
2. `bench1_ioport.c` now covers K1 (`ldr [r1,#0x10]`, GPIOA/B/F on F030 — closes OQ7), K2
   (`str [r1,#0x18]` alternating, LA-checked pin toggle), K3 (SRAM load, expect 2 RAM / 4 flash),
   K6 (flash-data load via register base, sets `Df` for R3/OQ14's "dispatch reads RAM data at
   4/access — not timed, acceptable" note).
3. `bench2_branch.c` now covers K4/K5 (literal load, pool in SRAM vs flash — resolves whether
   `L`=2 is real or T2's hoist is mandatory), K7/K8 (taken `b .+2`, aligned vs the odd-halfword
   case — **the direct test of R4/OQ4**: "taken branch 2 (TRM) vs 3 (Grainuum)" becomes a measured
   2-or-3 with an explicit alignment split, not a guess), K9 (`DELAY_CYCLES` shape, resolves
   A3/A6's 96-vs-127 question), K10 (`bl`/`mov pc,lr` vs `bl`/`bx lr` — sets `C` for T2/T2T's
   staircase), K11 (push/pop pairs without `pc`, sets the A2 entry constant).
4. `bench5_slot.c`, `bench4_flash.c` carried over from v2 unchanged in shape (isomorphic RX slot
   with PRBS+evictor; flash fetch profile for OQ14/R8's turnaround question).
5. `bench6_trim.c` unchanged in shape (HSI_TRIM LSB weight and **sign**, OQ3) — needed by T13's
   `USB_TRIM_SIGN` and, on the F002B track, by T12/T16.
6. `main.c`: menu binds keys `1`-`9` to weak `benchN_run` symbols (`__attribute__((weak))`,
   "n/a" printed when null); `Makefile` globs `bench[0-9]_*.c` — the seam T11's
   `bench7_loopback.c` and T12's `bench8_hsical.c` drop into without touching this task's files
   (§9.2's seam table).
7. `tools/wg015_vcd/wg015vcd.py`: `--marker-edge rise|both` (§7.3); **`--gate-se0 LO:HI`**
   (default `60:72` cycles = 1.25-1.5 µs) applied to the already-computed `eop_se0_cyc` inside
   `eval_gates` — the EOP width was reported but never gated before (PA T-2, A-14, L-2).

Accept (static): `make -C py32_bench -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` and
`MCU=PY32F002Bx5` build; `nm bench.elf | grep -cE 'bench[1-6]_run'` = 6;
`tools/wg015_vcd/selftest/run_selftest.sh` reports `0 failed` **and** contains a new case
exercising `--gate-se0` on a too-short EOP capture (must FAIL) and a 64-cycle one (must PASS);
`grep -c 'gate_se0' tools/wg015_vcd/wg015vcd.py` ≥ 3; `grep -rn 'SysTick->LOAD' py32_bench` empty
(Р9); `grep -c 'K1[01]\?' py32_bench/bench2_branch.c` ≥ 1 (K10/K11 present, not silently dropped).

Size: one session.

**T12 — F002B HSI self-calibration against LSI — Opus — the F002B track's precondition**
*Opus: RV R19-R21's non-linear trim field and the ±3 % LSI datasheet-vs-measurement gap mean a
naive linear search either saturates or crosses a `TRIM_H` band; getting the search shape wrong
strands the whole F002B track behind an untunable servo.*

Owns: `rv003usb/py32/{py32_hsical.c, py32_hsical.h}`, `py32_bench/bench8_hsical.c`.

Entry: T1 landed (RCC/ICSCR/LSI/CSR offsets from `py32_min.h`).

Does: TC Р5.4 / RV R19-R21 / RV gate G4. The factory 48 MHz word (`[0x1FFF0104]`) is **never
loaded** on this part (R19 — it measures 43.12 MHz, −10.2 %, outside the servo's ±8.3 % sanity
window, so the servo could never engage from it):
1. Enable LSI (`RCC->CSR.LSION`, wait `LSIRDY`) — the reference available at reset, measured
   −0.18 % on one unit (`xm_002b.md:204-206`) but ±3 % by DS002B T5-14 (RV R21's disagreement,
   closed only by RV gate G4 on ≥5 units — out of scope here).
2. Count HSI cycles per LSI reference period (SysTick or a free-running counter clocked by HSI,
   gated by an LSI-derived edge; exact register sequence from `py32_min.h`'s RCC/LSI bits, T1).
3. Search `TRIM_L` (0x000-0x1FF, linear across 21.7-33.4 MHz at `TRIM_H=0`, `xm_002b.md:249-257`)
   with `TRIM_H` **fixed for the whole search** at the band containing 48 MHz (`0b0111` or
   `0b1000`, RV R20 — a step across a `TRIM_H` boundary is a ≈9 % jump the search must never
   take). Binary or proportional search — a pure function, no MMIO, lives in `py32_hsical.h` so
   it is unit-testable in isolation.
4. Write `ICSCR.HSI_TRIM` with the winning `TRIM_L`, leave `HSI_FS=101`, `TRIM_H` untouched from
   step 3's fixed value; hand control to `main()`'s clock-ready point before `usb_setup()` (T1's
   clock-init spec: T12 runs from `main()`, not `startup_py32.S`).
5. Runs entirely before `USB_PORT_OSPEED`/DPU are asserted (Р5.4) — no bus activity, no timed
   cell, so none of T2's cycle-budget rules apply; this code lives in `.text` (flash).
6. `bench8_hsical.c`: on-target harness that runs the same search and reports the winning
   `TRIM_L`, the LSI reference count, and the resulting HSI frequency via MCO/2 (R21 — "MCO tops
   out at ≈35 MHz, must divide", `xm_002b.md:261`) — a **measurement** tool, run in T16 (Wave 4),
   not this task.

Accept (static, hardware-free — the search's correctness, not the silicon's, is what this task
can prove without a board):
1. `rv003usb/py32/py32_hsical.c`/`.h` compile for `MCU=PY32F002Bx5` with `-Wall -Werror`;
   **compiling for `MCU=PY32F030x8` is a build error** (`#error "F002B-only — F030 needs no
   calibration, TC §3.1"`), so the F030 wave-4 rig never links this file.
2. `py32_hsical.h`'s search function has no register access
   (`grep -c 'RCC->\|ICSCR' rv003usb/py32/py32_hsical.h` = 0) — MMIO stays in the `.c` wrapper
   only, so the algorithm is host-testable.
3. **Native unit test** (host `gcc`, not `arm-none-eabi-gcc`, compiled and run in this container):
   feed the search a synthetic LSI-vs-HSI ratio corresponding to (a) the measured −0.18 % LSI /
   −10.2 % factory HSI case and (b) the DS002B T5-14 ±3 % LSI extremes; assert the search
   converges to a `TRIM_L` whose synthetic HSI lands within ±1.5 % of 48 MHz in every case, in
   ≤ `USB_HSICAL_MAX_STEPS` iterations, and never selects a `TRIM_L`/`TRIM_H` pair outside the
   fixed band chosen in step 3. Exit code 0 is the gate.
4. `grep -c 'TRIM_H' rv003usb/py32/py32_hsical.c` shows exactly one write to `TRIM_H` (the fixed
   value from step 3) and zero writes to it from inside the search loop (RV R20 enforced the same
   way T13's item 5 enforces it on the actuator side).
5. `grep -c '0x1FFF0104' rv003usb/py32/py32_hsical.c` = 0 (the factory word is never read, R19).
6. `py32_bench/bench8_hsical.c` builds for `MCU=PY32F002Bx5` and links against `py32_hsical.h`'s
   pure search (no duplicated logic).

Size: one session. The search-shape unit test is the real content; the MMIO wrapper is
transcription from `py32_min.h`.

#### Wave 3

**T2T — Engine: TX path onto RAM, re-pad, per-part `#if` review — Opus — closes the RAM-placement
half of Р4**
*Opus: moving 512 B of flash-resident TX code into the same `.timecrit` region T2 already
budgeted changes literal-pool costs in both directions at once (LG note 60) — the arithmetic has
to be redone, not copied from T2's RX numbers.*

Owns: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`,
`rv003usb/py32/usb_port_py32_tune.h`. Serialised after T2 (same files, never concurrent).

Entry: T2 landed.

Does — TX only; RX is untouched:
1. **Placement, the other half of Р4.** Move `usb_send_empty` … `no_really_done_sending_data`
   (512 B, BF §3) from `.text` into `.timecrit`. `.ltorg` after each TX block so its literal pool
   — today at `.text+0x1b8..0x1fc`, in flash, costing 2 from the flash-resident code that reads it
   (BF §5) — resolves in SRAM instead. The one load inside a timed cell today, `.text+0xda` inside
   `pre_and_tok_send_one_bit`, must be **hoisted into a register before the cell** (T2's §2.1
   hard-rule pattern, reused verbatim) — otherwise it becomes a 4-cycle RAM-code-reads-flash-pool
   fault the moment the section moves and a stale `.ltorg` is missed.
2. **The trade the move buys** (LG note 60, BF §5): `load_next_byte`'s packet-byte read,
   `ldrb SHIFT_BUF,[r0]`, was 4 cycles (RAM data from flash-resident code); from RAM-resident code
   it drops to 2. This is the arithmetic v2 never ran; it is why Р4's "one rule for both halves"
   nets out favourably rather than merely being simpler.
3. **Re-pad every TX cell to the Appendix A/B targets (32/64)**, using the staircase T2 seeded,
   extended to N up to 64 for the SE0 pad (B11's target, `--gate-se0 60:72`). **Every pad is a
   formula in `usb_port_py32_tune.h` parameterised on `USB_B_TAKEN` and `USB_L_LITERAL`** (LG's
   honoured request for "T2 step 4" — the TX re-pad is where that request actually lands, since
   T2's RX pads at `USB_B_TAKEN=2` already fit with zero pad room to spare): e.g. B1's pad is
   `18 − 2·USB_B_TAKEN − USB_L_LITERAL` `nop`s or a staircase entry, not the integer 12.
   Store-index invariants from LG's pad-site map (pre_and_tok zero/one equal store index;
   send_inner zero-path store index stays 10; stuffed store index = 32 + 10) are asserted by the
   walker's path list, not by eyeballing the table.
4. **Per-part `#if` review, corrected from v2.** All five `#if PY32F002Bx5` sites
   (`arm.S:402, 415, 444, 490, 530`) are, by BF §8, pure cycle padding with no register or address
   difference — re-derive their pad counts under the moved-to-RAM cost model (they were tuned
   against the flash column; RAM changes `push/pop` and RAM-data costs under them).
   **The `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2 / .error` alignment assert at
   the `#else` (F003/F030) arm is preserved, not deleted** — reversing v2's step-4 instruction to
   delete it. BF §8 found it has never been evaluated by the branch's own build and passes today;
   deleting a correctness guard just because bench2 (T6, K7/K8) might show RAM alignment doesn't
   matter would convert a silent timing break into a silent build success — exactly backwards
   from D-5's lesson. If K7/K8 show alignment does matter, `.balign 4` on loop heads is the fix
   (RV R4); the assert stays regardless, mirrored onto whichever arm needs it once both are
   re-padded.
5. Extend the walker's path list (`usb_port_py32_asm.h`) with the TX paths B1-B12, C1-C4, and the
   trailing-stuff-bit case (OQ11) so `tools/py32_cyc.py` covers RX and TX in one run.

Accept (static):
1. Assembles for both `MCU`s; `arm-none-eabi-objdump -h` shows `.timecrit` now containing every
   RX **and** TX symbol, `.text` reduced to the dispatch tail only (D-1's fix site).
2. `python3 tools/py32_cyc.py <elf>` exits 0: every TX path (B1-B12) = 32 or 64, C1-C4
   (turnaround) reported, keepalive still ≤ 96, no regression on any RX path from T2.
3. Every `ldr rN,[pc,#imm]` reached from `.timecrit` resolves inside SRAM (T14's rule), including
   the relocated `.text+0xda` site.
4. `grep -c '.ifeq' rv003usb/rv003usb-arm.S` ≥ 1 **and** the F003/F030 arm still assembles clean —
   the assert is present and still passes, not removed (reversal of v2, noted in the commit
   message).
5. `grep -c '#if PY32F002Bx5' rv003usb/rv003usb-arm.S` = 5 (all five sites retained, re-padded,
   not deleted); the two `MCU` engine objects still differ (`cmp` exits 1, D-3's selection stays
   exercised).
6. `grep -c 'USB_L_LITERAL\|USB_B_TAKEN' rv003usb/py32/usb_port_py32_tune.h` ≥ 2 (both pad
   parameters present).
7. `.timecrit` total size ≤ 960 B (T1's ceiling; RX 252 + TX 512 measured today, ≈196 B of
   staircase/pad headroom).

Size: one session, hard — the literal-pool bookkeeping in both directions (step 1/2) is the part
that must not be rushed.

**T5 — DFU bootloader for PY32 — Opus — [MIT-attrib: joyboot boot counter] [GPL-ideas-only:
micronucleus write-sleep idea only]**
*Opus: the fixed boot-word contract (Р6) and the flash-timing-register set (RV R6/G12) both have
to match T1's ld exactly or DFU bricks a board that the app side never would.*

Owns: `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/{Makefile, bootloader.c, dfu_chip.h,
dfu_transport.h, usb_config.h, funconfig.h, py32-dfu-bootloader.ld}`, `bootloader_dfu/README.md`,
`tools/wg015mkdfu.py`.

Entry: T1, T2, T3 landed. (The transport's turnaround budget is only exact once TX is also in
`.timecrit`, i.e. after T2T — both are wave 3, so T2T lands before this task's acceptance is
scored regardless; no new dependency edge, since T2T is serialised on T2's files, not a separate
input.)

Does: TC Р6 (mechanism unchanged, per-part numbers move with the flip). `usb_config.h` = copy of
`bootloader_dfu/wg015/usb_config.h` with PY32 pins (T1's README pinout), `wTransferSize 0x80`,
`bcdDevice 0x0210`, serial `"P32D"`, `USBDESC` to RAM (same rule as T4). `Makefile` wraps
`Makefile.py32` (`TARGET=bootloader`, `LDSCRIPT=py32-dfu-bootloader.ld`, hard `SIZE_BUDGET` via ld
`FLASH LENGTH`: 4096 on F030x6/F003x6, 4096 on F002Bx5, soft-warn at 3800, printed like
`bootloader_dfu/wg015/Makefile:14-22`). `py32-dfu-bootloader.ld` includes `py32_common.ld` with
`FLASH ORIGIN 0x08000000 LENGTH 4096`, RAM per-part unchanged from the app scripts (T1) so
`py32_boot_flag` etc. resolve to the **same address** in loader and app.
`dfu_port_flash_timebase_init()` writes the flash-timing register set matching the HSI mode
actually in use (RV R6/G12 — F030 at `LATENCY=1`/HCLK=2×HSI is a set the datasheet does not
explicitly enumerate for that path, flagged not guessed) and enables `TICKINT` on the
free-running SysTick; `SysTick_Handler` (priority 3, `dfu_wraps++`) in `bootloader.c`.
`DFU_ENABLE_BOOTCOUNT 1` on F030 / `0` on F002B in `dfu_chip.h` (R17's false-STAY risk stays
smaller on the primary part, whose apps call `py32_app_alive()` from T3's default hook).
`tools/wg015mkdfu.py` gets `--bcddevice`, `--pid`, `--vid` options, defaults unchanged (V003/WG015
output is bit-identical unless a flag is passed).

Accept (static): builds for `MCU=PY32F030x8` and `PY32F002Bx5`; `sizecheck` passes both; `nm
bootloader.elf | grep ' py32_boot_flag$'` prints the **same address** as T1's `selftest.elf` for
the same `MCU` (shared boot words, Р6 — the whole point of the ld-`PROVIDE` scheme); `nm` shows
`dfu_status`/`dfu_upload_buf` at SRAM addresses; `grep -n 'DFU_POLL_ERASE_MS\|DFU_POLL_PROG_MS'
bootloader_dfu/dfu_py32.h` shows `12` for both, `DFU_CYCLES_PER_MS` shows `47980` on F030 (Р9's
corrected constant, TC §3.2 — "not 48000") and `48000` on F002B (post-T12 calibration target);
`! grep -rn 'OPTR\|RDP' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/`; `grep -rn
'SysTick->LOAD' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/` empty; `python3
tools/wg015mkdfu.py --selfcheck` and `--bcddevice 0x0210` produce a suffix with the new value;
V003/WG015 DFU builds unchanged.

Size: one session, hard.

**T7 — Build integration, CI, top-level docs — Sonnet — closes the build-system half of DV D-3
and BF §10.1**
*Sonnet: wiring existing targets together — except item 3 below, which exists because a sibling
build already got bitten by exactly the failure mode it guards against.*

Owns: `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top).

Entry: T1, T2, T4, T5, T14 landed (§9.3's edge table — `check-cycles` needs T14's walker, which is
a wave-1 artefact; T2T is not a separate input, see T5's note).

Does:
1. `PROJECTS_PY32 := demo_gamepad demo_hidapi bootloader_dfu/py32`; `build_py32:` loops
   `$(MAKE) -C $$d -f $(abspath rv003usb/py32/Makefile.py32) MCU=$$mcu` for `MCU in PY32F030x8
   PY32F002Bx5`; `check-cycles:` runs `tools/py32_cyc.py` on every PY32 ELF; `all: build
   build_py32 check-cycles`.
2. **Per-part build hygiene (BF §10.1).** The build this port replaces (vendor `rules.mk`, via
   `py32f0-template`) reaches sources through `../`, so objects land outside `Build/`, carry no
   `MCU_TYPE` in their path, and `rm -rf Build` does not clean them — a part switch can silently
   relink objects compiled for a different part. That build is gone (T0 removes the submodule,
   T1 writes `Makefile.py32` from scratch), but `build_py32`'s own MCU loop is the one place in
   this port's build where the same failure mode could still reappear if `Makefile.py32`'s object
   directory is not keyed by part. T7 does not own `Makefile.py32` (T1's file, already landed), so
   the fix at this layer is defensive rather than structural: `build_py32` runs
   `$(MAKE) -C $$d -f … clean` immediately before each per-`MCU` build in the loop, guaranteeing
   no cross-`MCU` object survives from a previous iteration regardless of how `Makefile.py32`
   names its build directory. T7 also appends a request to `doc/py32/STATE.md` (T8's file, the
   one shared append-only exception, §9.1) asking `Makefile.py32` to key `BDIR` by `$(MCU)`
   directly, since a clean-before-build in the CI loop is a mitigation, not the permanent fix.
3. CI installs `gcc-arm-none-eabi` (no OpenOCD/pyOCD — nothing here needs hardware) and runs
   `make build_py32 check-cycles`. README gets a "PY32 / Cortex-M0+" section (targets, pins,
   clock options incl. the no-servo-at-reset F030 default, D± drive per Р8, loader, limits, IRQ
   policy, SysTick rule, the RAM budget table from T1).

Accept (static): `make all` green locally; CI yaml parses (`python3 -c "import yaml,sys;
yaml.safe_load(open('.github/workflows/build.yml'))"`); `grep -c 'check-cycles' Makefile
.github/workflows/build.yml` ≥ 2; `grep -c 'PY32F030x8' .github/workflows/build.yml Makefile` ≥ 1
each (the flipped default is exercised in CI, not only locally); **stale-object regression**:
`make build_py32` for `MCU=PY32F030x8` alone, record the RAM/flash totals reported by
`--print-memory-usage`, then run the full `build_py32` loop (both MCUs) without an intervening
manual `clean` — the F030x8 totals in the second run must be byte-identical to the first (proves
the loop's own `clean` step, not luck, produced correct per-part objects); `grep -c 'requests' -A5
doc/py32/STATE.md` shows the `Makefile.py32` `BDIR` request with a citation to `BF §10.1`.

Size: one session.

**T9 — HID blob loader for PY32 — Opus**
*Opus: the blob table and the address guard are the one place a bug bricks a board with no
recovery path faster than a full re-flash.*

Owns: `bootloader_py32/{bootloader.c, Makefile, usb_config.h, funconfig.h,
py32-usb-bootloader.ld, blobs/Makefile, blobs/blob_erase_page.S, blobs/blob_program_page.S,
blobs/blob_read_chunk.S, blobs/blob_boot_app.S, blobs/blob_rescale_timings.S}`; modify
`bootloader_wg015/wg015hostcli/wg015bflash.c` + its `README.md` (accept `bcdDevice 0x0210`, a
Thumb blob table, page = 128 B, unit = page).

Entry: T5, T2 landed (the engine, and T5's proof that the DFU transport works over it).

Does: port of `bootloader_wg015/bootloader.c` with RV32-specific mechanisms swapped for their
PY32 equivalents — `RTC_REG` boot words → the ld-`PROVIDE`d top-of-RAM block (Р6, same scheme as
T5), `rdcycle` → free-running SysTick (Р9), PLIC teardown → `NVIC_DisableIRQ` + `EXTI->PR` ack —
with the shared-C `RV003USB_BOOTLOADER` hooks unchanged; scratchpad at `0x20000000`, sized to the
smallest supported part's headroom (T1's floor table) rather than WG015's flat 1152 B. Blobs are
hand-written PIC Thumb (no `-fPIC`, entry at +4, address guard `< APP_BASE` refused before any
flash-controller register is touched).

Accept (static): builds for both `MCU`s; blobs ≤ 284 B each (`arm-none-eabi-size`); the CLI
refuses a loader image whose `bcdDevice` is not `0x0210`; `grep -rn 'SysTick->LOAD'
bootloader_py32` empty.

Size: one session, hard.

**T11 — Writer→reader loopback bench — Sonnet — [MIT-attrib if Pico `test_ll.c` vector logic is
copied]**
*Sonnet: the vector set and the protocol are fully specified by v2/PA; this is assembly against
an existing design, not new design.*

Owns: `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}`.

Entry: T2 landed (the engine — effectively after T2T too, same note as T5/T9). T6 landed (bench
framework, weak-symbol menu, Makefile glob — the seam this task drops into without touching T6's
files).

Does: two-board setup (D+↔D+, D−↔D−, common ground; board B's DPU asserted, board A's D± as
inputs with internal pull-downs so idle state is J); writer/reader roles from the UART menu.
Writer: `usb_send_data` every 1 ms over the vector set — all-0x00, all-0xFF, 0x7E/0xFE runs
(PA A-7), payloads whose CRC16 tail ends in six ones (trailing stuff bit, OQ11 — the generator
asserts ≥4 such vectors exist), one deliberate seven-ones violation (must be rejected), one
8-cycle SE0 glitch (optional), LFSR random. Reader: links the engine with its own PID handler
stubs, counts CRC-valid packets per vector id, answers ACK so the LA also measures turnaround.
`gen_loopback_vectors.py --check` regenerates and diffs the header — CI-safe, no hardware.

Accept (static): `make -C py32_bench MCU=PY32F002Bx5` and `MCU=PY32F030x8` build with `nm
bench.elf | grep -q bench7_run`; `python3 py32_bench/gen_loopback_vectors.py --check` exits 0,
reports ≥4 six-ones-tail vectors and exactly 1 stuffing-violation vector; `grep -rn
'SysTick->LOAD' py32_bench` empty. The hardware run itself and its numbers land in T10's
`calibration.md` (Wave 4).

Size: one session.

**T15 — Thumb PID handlers under `RV003USB_OPTIMIZE_FLASH` — Sonnet — closes v2's F8**
*Sonnet: a mechanical translation of two existing C handlers into hand-written Thumb using an
offset table T3 already fixed; no new design.*

Owns: `rv003usb/py32/engine_pid_handlers.S`.

Entry: T2 landed (`usb_port_py32_asm.h` macros). T3 landed (`EP_*_OFFSET` constants in
`rv003usb.h`).

Does: v2's F8 (`rv003usb-arm.S` always `blx`es the C `usb_pid_handle_ack`/`usb_pid_handle_setup`,
which are compiled out under `RV003USB_OPTIMIZE_FLASH=1` — a link error the branch never hit
because it never builds with that flag). Thumb reimplementations of both handlers, ≈40 B each,
reading `EP_*_OFFSET` (T3, `h:133-138`) directly instead of through the C struct layout, guarded
`#ifdef RV003USB_OPTIMIZE_FLASH`. Lands in `.text` (flash) — dispatch-tail code, not a timed cell
(same classification as D-1's fix site), so none of T2/T2T's cycle rules apply. Picked up
automatically by T1's `engine_*.S` Makefile glob — no `Makefile.py32` edit needed (§9.2's seam
table).

Accept (static): `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8
RV003USB_OPTIMIZE_FLASH=1` links (the link error F8 named no longer occurs); the same build with
`RV003USB_OPTIMIZE_FLASH=0` (default) shows neither handler in `nm` (the C versions are used
instead, unchanged behaviour); `arm-none-eabi-size` on the object shows ≤ 45 B per handler; DFU
configs (T5, T9 — both already build with the flag) are unaffected in address layout
(`EP_*_OFFSET` sourced from the one place, T3).

Size: one session.

#### Wave 4

**T10 — F030 bring-up rig, procedure, and calibration record — Opus**
*Opus: getting the gate order or the fallback wrong here wastes bench time on real hardware,
which nothing else in this fleet can undo.*

Owns: `doc/py32/calibration.md`, `rv003usb/py32/py32_cal_f030.mk`.

Entry: waves 0-3 landed (everything it flashes and measures). Runs before T16 in practice (T16
reuses this rig's document format) but does not block it — they are independent per §9.3.

Does — **the deliverable is the procedure and the rig, not a measurement** (per the governing
rule: no task in this fleet is gated on hardware, and this wave is no exception to that, only to
the "hardware-free acceptance" part of it):
1. `calibration.md` is a filled-in template, one section per RV/G gate in order — G0, G1, G6, G7,
   the F030 leg of G9, G10, G11, the F030 leg of G12 — each section quoting the exact yes/no
   question, board, measurement method and pass/fail line from `rework/risks_verdict.md` §10A and
   the K1-K11 procedure from `rework/ledger.md` §5 **verbatim** (both are already-verified
   documents this fleet cites, not re-derives), with a blank result field and an explicit `TBD —
   requires hardware, out of scope for this task's own static acceptance` marker next to every
   field until the rig actually runs.
2. `py32_cal_f030.mk` is the seam target (T1's `-include $(PY32_DIR)/py32_cal_*.mk`, §9.2): today
   it contains only commented-out `DEFS += -DUSB_RX_SYNC_DELAY=… # from G7`-style placeholders,
   one per constant this gate sequence is expected to produce (`USB_RX_SYNC_DELAY`,
   `USB_TRIM_LOCK_N`, `USB_TRIM_FAST_SHIFT`, `USB_TX_SE0_PAD`, a `USB_STAIRCASE_C` override) —
   every one commented out, so the file changes no build until a real measurement fills it in.
3. Notes, not gates: BF §10.2 found the vendor CMSIS library builds no PLL path at all for F003
   (`RCC_PLL_SUPPORT` undefined for that part), which directly disagrees with the measured claim
   in `CHIP_FACTS_XIAMATSU.md` §2 that the PLL locks at 48 MHz on F003 silicon. This port never
   calls the vendor library (T1 writes the PLL bring-up as direct register writes, identically for
   the whole PY32F030 family — BF §10.3's "no owning task", "which parts get benched" gap does not
   reopen this port's clock-init design), so the vendor header's `#ifdef` does not constrain us
   either way; what remains unresolved is whether the **registers themselves** lock on F003
   silicon, which is unmeasured and out of this document's scope — F030 is the only part T10
   benches (RV R25/TC §3.1 already restrict F003/F002A to an explicit
   `PY32_OUT_OF_SPEC=1` opt-in), and an F003 register-level PLL measurement is recorded as an open
   item in `calibration.md` rather than invented as a new gate this rework does not own.

Accept (static): `calibration.md` names every one of G0, G1, G6, G7, G9, G10, G11, G12 by ID and
quotes its pass condition (cross-checked against `rework/risks_verdict.md`'s `| G` heading rows);
`rv003usb/py32/py32_cal_f030.mk` exists and, entirely commented out, changes nothing:
`make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` succeeds identically with
and without the file present (`cmp` on the two `.bin`s); `grep -c '^#' rv003usb/py32/py32_cal_f030.mk`
equals its non-blank line count (no live override ships before a real gate runs); every
unmeasured field in `calibration.md` says `TBD`, not a fabricated number (`grep -c 'TBD'
doc/py32/calibration.md` ≥ the gate count); `grep -q 'RCC_PLL_SUPPORT\|BF §10' doc/py32/calibration.md`
(the F003-PLL open item is recorded, not silently dropped).

Size: one session for the document and the seam file; the gates themselves are hardware work,
explicitly out of this task's scope.

**T16 — F002B bring-up rig, procedure, and calibration record — Opus**
*Opus: the F002B leg carries the one open question (RV gate G4, the 15× LSI
datasheet-vs-measurement gap) that decides whether the second target ships at all.*

Owns: `doc/py32/calibration_f002b.md`, `rv003usb/py32/py32_cal_f002b.mk`.

Entry: T12 landed (self-calibration routine to flash). T13 landed (`acquire` mode built). T10
landed (the rig's format and procedure to mirror on the second board — a documentation
dependency, not a build one; T16 does not read T10's files, it follows the same template).

Does — same discipline as T10, on the F002B-specific gate subset: G3 (like-for-like sanity at
24 MHz/LAT0 against T10's G1 result), **G4** (the self-calibration spread across ≥5 units — the
gate that decides whether R19/R21's disagreement is survivable, or whether F002B needs per-board
OTP, or is dropped), G5 (RAM column post-calibration at 48 MHz/LAT1), the F002B leg of G9 (servo
lock from up to ±3 % start, using T13's `acquire` mode) and of G12 (48 MHz flash-timing set,
B-C silicon only, RMBC p24). `calibration_f002b.md` additionally records `DBG_IDCODE` per unit
(R1's retired-risk residual) and T12's `bench8_hsical` output (winning `TRIM_L`, LSI reference
count) per unit — the artefact G4 is actually scored against. `py32_cal_f002b.mk` mirrors T10's
seam shape: commented-out `DEFS +=` placeholders for the F002B-specific constants
(`USB_TRIM_SIGN`, F002B's own `USB_RX_SYNC_DELAY` if G3/G5 diverge from F030's, a
`PY32_HSICAL_TRIM_H` override if G4 needs a different band than T12's compile-time default).

Accept (static): `calibration_f002b.md` names G3, G4, G5, G9(F002B), G12(F002B) and quotes each
pass condition from `rework/risks_verdict.md` §10A; explicitly states G4's ≥5-unit requirement
and its two fallbacks (per-board OTP constant; F002B dropped as a target) so a reader cannot
mistake "ran once" for "gate passed"; `rv003usb/py32/py32_cal_f002b.mk` exists, fully commented
out, and `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F002Bx5` is
byte-identical with and without it present; every unmeasured field says `TBD`.

Size: one session for the document and seam file; the gates are hardware work, explicitly out of
scope here, same as T10.

### 9.5 Defect map — every verified defect has an owning task

None of DEFECTS_VERIFIED.md's five defects, and none of the design gaps the plan tracks
alongside them, is left without a task that closes it. Three (D-3, D-4, D-5, and the stub servo)
are already closed by Waves 0-1's finished prose; this rework's job was the remaining two (D-1,
D-2) plus one build-system defect surfaced mid-rework by BUILD_FACTS.md §10.1.

| Defect | Source | Owning task(s) | Wave | How it is closed |
|---|---|---|---|---|
| D-1 endpoint bound off-by-one (`bhi` accepts `endp == ENDPOINTS`) | DV D-1 | **T2** | 2 | `bhi`→`bhs`, one instruction in the untimed dispatch tail; Accept item 4 |
| D-2 RX byte store has no bound check | DV D-2 | **T2** | 2 | bound check inside the cycle-counted path, paid for out of the existing budget, not added on top; Accept items via the walker |
| D-3 per-part `#if` variant never *selected* by the build | DV D-3 | T1 (build matrix, already landed) + **T7** (CI matrix, stale-object regression) | 1, 3 | T1 Accept item 6 shows the two `MCU` objects differ; T7 closes the build-hygiene half BF §10.1 exposed (per-`MCU` `clean`, STATE.md request for a permanent `BDIR` keying fix) |
| D-4 branch cannot link as published (empty `py32f0-template`) | DV D-4 | T0 (removal) + T1 (replacement files, already landed) | 0, 1 | submodule dropped; own linker/startup/header written from RM/DS cites, not vendored |
| D-5 RAM placement of the RX engine is incidental | DV D-5 | T1 (already landed) | 1 | named `.timecrit` section + `ASSERT` on its VMA + `--orphan-handling=error`; regression-tested in both directions (T1 Accept item 3); corroborated a second time in a fully linked image, BF §9 |
| Stub keepalive servo (`// TODO`, acks and measures nothing) | DV "Not verified here" | T13 (already landed) | 1 | three-mode actuator (`off`/`drift`/`acquire`) replacing the stub, sized to what the target flip actually needs |
| New: build objects escape `Build/`, not keyed by part (a build-system near-D-3) | BF §10.1 | **T7** | 3 | per-`MCU` `clean` inside `build_py32`'s loop, plus a request routed to T1's file via STATE.md for the permanent fix |

No defect is orphaned: every row names a task, and every task named above appears in this
rework's Wave 2-4 prose or in Waves 0-1's already-finished prose.

### 9.6 Requests honoured from `rework/ledger.md`'s "Requests to owners of sections I do not own"

LG's final section names five items addressed to §9's tasks. All five are honoured explicitly,
not by silent coincidence:

| LG's request | Honoured in | How |
|---|---|---|
| T2 step 2 (`.ltorg`): "now a hard rule, walker-enforced" | T2 step 2, Accept item 6 | every literal pool reached from `.timecrit` must resolve inside SRAM or the build fails (T14's rule, applied); not a style note |
| T2 step 4: "pads as formulas in `usb_port_py32_tune.h` with B as a parameter (`USB_B_TAKEN` default 2), not integers" | T2 step 11 (RX pads) and **T2T step 3** (TX pads, where "step 4" actually landed after the RX/TX split) | `USB_B_TAKEN`/`USB_L_LITERAL`-parameterised formulas in both tasks' Accept criteria (T2 item 7, T2T item 6); a build with a different `USB_B_TAKEN` measurably changes the assembled pad, proving the formula drives the number rather than documenting it |
| T6 bench1/bench2: "replaced by K1-K11 above (superset); adopt the kernel list and the two gates" | T6 (Wave 2), entirely rewritten around it | bench1/bench2 no longer exist as separate concepts — K1-K11 are distributed across `bench1_ioport.c` (K1-K3, K6) and `bench2_branch.c` (K4-K5, K7-K11); Gate 1/Gate 2 themselves are T10/T16's job (Wave 4), consistent with LG's own "T10 runs them first" |
| R4/OQ4: "'taken branch 2 (TRM) vs 3 (Grainuum)' is now '2-3 measured from RAM, alignment-dependent per the source'; K7/K8/K9 close it" | T6 step 3 | K7/K8 explicitly named as "the direct test of R4/OQ4"; the reworded risk text itself lives in `rework/risks_verdict.md` (not this fragment's file to edit), but the evidence that closes it is produced here |
| R3/OQ14: "dispatch in flash reads RAM data at 4/access — not timed, acceptable; note only" | T2 step 3 (D-1's fix site) | stated as a note at the one place in this rework where it is directly relevant (the dispatch tail staying in flash while RX/TX move to RAM), with no task action beyond what D-1 already required |

Size: n/a — this section records disposition, not work.
