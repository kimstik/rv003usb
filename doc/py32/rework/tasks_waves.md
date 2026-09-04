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
