# Branch notes: py32 port + branchless CH32V003 improvements

All claims referenced as `commit:file`. Branches inspected read-only (no checkout).

## Part A — origin/py32: ARM Cortex-M0+ (PUYA PY32F0xx) port, commit 0ad3c42

Single commit "Port demo_gamepad to Arm Cortex M0+ (PUYA PY32)". Only demo_gamepad ported.

### Files touched (0ad3c42, `git show --stat`)
- **Added**: `rv003usb/rv003usb-arm.S` (573 lines, new Thumb asm engine), `Makefile.py32`
  (standalone top-level makefile), `py32f0-template` (git submodule = vendor CMSIS/LL/HAL +
  startup + linker scripts), `.vscode/{launch,settings}.json`, `.gitmodules`, `.gitignore`.
- **Modified (shared, `#if __riscv` forks)**: `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`,
  `demo_gamepad/demo_gamepad.c`, `demo_gamepad/usb_config.h`, top `Makefile`.
- **Untouched**: `rv003usb/rv003usb.S` (RISC-V asm) — zero changes.

### Directory structure for a 2nd target (0ad3c42:Makefile, Makefile.py32)
- No per-target subdirectories. Second asm file sits NEXT TO the first in `rv003usb/`
  (`rv003usb.S` vs `rv003usb-arm.S`); the build system picks which one.
- `Makefile.py32` invoked per-demo as `make -C demo_gamepad -f ../Makefile.py32`
  (0ad3c42:Makefile `PROJECTS_PY32:=demo_gamepad`, target `build_py32`).
- Startup files and .ld are NOT copied into the repo: taken from the `py32f0-template`
  submodule (`AFILES += .../startup_py32f002b.s`, `LDSCRIPT = ...LDScripts/$(PYOCD_DEVICE).ld`
  in 0ad3c42:Makefile.py32). Good pattern: vendor scaffolding stays vendored.

### What stayed shared vs duplicated
- **Shared**: entire protocol layer — `rv003usb.c` descriptor/EP0 machine, `usb_config.h`
  descriptors, demo code. Portability seams are `#if __riscv / #else` blocks
  (0ad3c42:rv003usb/rv003usb.c: headers, RCC clock enable, GPIO/EXTI init;
  0ad3c42:demo_gamepad/usb_config.h: pin mapping; 0ad3c42:demo_gamepad/demo_gamepad.c:
  clock bring-up `BSP_RCC_HSI_48MConfig()` / `BSP_RCC_HSE_PLLConfig()`).
- **Duplicated (fork-copy)**: the whole time-critical engine. `rv003usb-arm.S` is a
  hand-rewritten Thumb translation of `rv003usb.S`, NOT parameterized reuse. Same
  algorithm/labels (`preamble_loop`, `packet_type_loop`, `bit_process`, `rx_stuffed`,
  `usb_send_data`, `insert_stuffed_bit`), every cycle re-annotated by hand.
- **Verdict: fork-copy of the asm; parameterized reuse of everything above it.**

### Timing re-derivation (0ad3c42:rv003usb/rv003usb-arm.S)
- Same clock (48 MHz) as CH32V003 → same **32-cycle bit slot** (RX loop annotated
  `// 0`..`// 31`). Timing changed only because M0+ instruction costs differ, not budget.
- IRQ-entry skew handled by `DELAY_CYCLES(96)` with comment "90 to 117 would work ...
  use less than the mean so it'll work with a delayed interrupt" — window re-measured for
  Cortex-M exception entry latency.
- Hot code runs **from RAM**: `.pushsection .datacode,"ax"` for the RX slot, with
  trampolines (`ldr r0, =se0_complete_flash; bx r0`) back to flash for non-critical tails
  ("continue in flash to conserve RAM"). Flash wait states made XIP timing unusable.
- **Alignment-sensitive branch timing**: `.balign 4` before every loop plus a build-time
  assert `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2; .error "...must be
  unaligned"` — M0+ prefetch makes taken-branch cost depend on target alignment.
- **Per-variant cycle deltas**: `#if PY32F002Bx5` vs other PY32 parts insert/remove single
  `nop`s in TX paths (`pre_and_tok_send_inner_loop`, `insert_stuffed_bit`) — even chips in
  the same vendor family needed different padding.
- Thumb register pressure: only r0–r7 usable by most 16-bit ops; r8/r9/r12/r14 used as
  slow spill homes via `mov` (GPIO_BASE=r9, FLIP_MASK=r8, POLY_RX=r14), costing slot cycles.

### GPIO/EXTI/startup/linker replacement
- Asm uses raw hardcoded addresses/offsets: `GPIOA..GPIOF`, `MODER/IDR/BSRR` offsets, `EXTI`
  base + `EXTI_PR_OFFSET`, `GPIO_MODER_*` masks (0ad3c42:rv003usb/rv003usb-arm.S top).
  TX bus-turnaround = rewrite MODER (input→output push-pull) then BSRR writes, mirroring
  the CH32 CFGLR/BSHR scheme.
- C init uses vendor LL calls (`LL_GPIO_SetPinMode`, `LL_EXTI_SetEXTISource/EnableFallingTrig`)
  instead of direct registers (0ad3c42:rv003usb/rv003usb.c).
- IRQ selection abstracted once: `USB_DM_IRQ` macro maps D- pin → `EXTI0_1/EXTI2_3/EXTI4_15`
  (ARM) or `EXTI7_0` (RISC-V) (0ad3c42:rv003usb/rv003usb.h); handler symbol built as
  `LOCAL_EXP(USB_DM_IRQ, Handler)`, NVIC enable as `LOCAL_EXP(USB_DM_IRQ, n)`.
- Startup/vector table/linker: entirely vendor template's (submodule), zero custom code.

### Lessons / anti-patterns for a K1921VG015 (RISC-V) port
Lessons (do these):
1. Keep the C layer single-source; isolate chip code behind exactly three seams:
   headers+clock init, GPIO/EXTI setup, IRQ name macro (`USB_DM_IRQ` pattern in rv003usb.h).
2. Reuse the vendor SDK/template as a submodule for startup + linker + HAL; don't copy.
3. Separate makefile per target family invoked over the same demo dirs; don't fork demos.
4. K1921VG015 is RISC-V: unlike the ARM port, `rv003usb.S` source can be reused nearly
   verbatim — only cycle *counts* (clock, flash wait states, branch cost) and peripheral
   addresses (GPIO/EXTI/IRQ) change. The ARM port had to translate the ISA; we don't.
5. Plan for RAM execution up front if flash has wait states (the ARM port retrofitted it
   with trampolines; runfromram branch shows CH32V003 RAM-exec broke timing the other way).
Anti-patterns (avoid):
1. Hand-re-annotating every cycle in a forked asm file — no automated timing check; the
   PY32 file is full of "TODO"/"// 4 cycles?" uncertainty. Build or reuse a cycle-accurate
   sim harness first (Part B commits prove one exists for CH32V003).
2. `#if` sprinkling per chip variant inside slot code (`#if PY32F002Bx5` nops) — fragile;
   prefer a single DELAY/PAD macro parameterized per target (as master's `nx6p3delay`).
3. Magic peripheral addresses duplicated inside the .S instead of one header.
4. Growing `#if __riscv/#elif PY32.../#else` ladders in shared C — with a 3rd target this
   needs a per-port `usb_port_<chip>.h` include instead.

## Part B — origin/rx-tx-branchless-ch32v003-rebased (4 commits on 75d926a master)

Structure: b83d015 duplicates the whole EXTI RX engine inside `rv003usb/rv003usb.S` as
`#if defined(CH32V00x) && CH32V00x` (upstream path, untouched) `#else` (optimized V003
path, +827 lines); later commits edit only the `#else` half. Numbers below are from the
commit messages, measured in "the cycle-accurate CH32V003 simulator" (b83d015 msg).

### b83d015 — in-packet resync in RX bit-stuff slot (idea 5.2)
- Tech: a stuffed bit guarantees a wire edge at its cell boundary. Adds a PRE sample
  (`c.lw s0` ~7 cycles before the MAIN sample) in `handle_bit_stuff`; if the edge arrives
  between PRE and MAIN the sampling grid is early → stretch the slot +2 cycles
  (b83d015:rv003usb/rv003usb.S, "Cycle ledger (CH32V003, 1ws flash ...): healthy dt=64
  exact; correct dt=66"). Fixes accumulated clock error ("worst-case all-ones payload dies
  at ~+5000 ppm" without it).
- Claims: eye ones 9217→15038 ppm, rand 10272→10897 ppm, fw 1908B (commit msg).

### f46ed67 — RX v2: branchless unified 32-cycle slot
- Tech: removes the zero/one path split — one slot body computes bit value with
  `seqz`/`c.addi -1`/`c.or`/`c.andi 7` branchless stuff counter; CRC16 moved OUT of the
  slot to a post-SE0 nibble table (`rx_crc16_tbl`, ~26 cycles/byte); PID received in-slot
  (dedicated `packet_type_loop` deleted, PID lands in buf[0] in natural bit order, dispatch
  constants become 0xD2/0xE1/0x69/0x2D/0xC3); per-zero-bit bang-bang PRE/MAIN resync
  (subsumes 5.2); slot ledger "healthy: 32 cycles bit-exact" (f46ed67:rv003usb/rv003usb.S).
  Drops CRC5 check on tokens ("flash budget ... same trade-off V-USB ships with").
- Claims: eye ones 15038→18515, alt 10389→29960, rand 10897→26523 ppm; fw 1828B; turnaround
  temporarily 15.7 bit-times (CRC now post-slot) — out of spec until tier-b (commit msg).

### 3735518 — tier-b: CRC16 folded into the ACK's own TX slots, ACK-first
- Tech: ISR dispatch transmits the ACK itself, pipelined with the CRC check: 20 TX bit
  slots (4 idle-J lead-in + SYNC 8 + PID 8), each slot pair folds one buffer byte through
  the nibble table via `c.jal ack_tx_bit`; EOP is emitted only if residual == 0xB001 — on
  bad CRC the line just returns to J, host sees no EOP and retries
  (3735518:rv003usb/rv003usb.S `crc_for_tokens_would_be_bad_maybe_data`, `ack_tx_bit`,
  `tx_pins_cfg`). Length-aware 3..11-byte packets + zero-length DATA1 status stage.
  C-side `just_ack` `usb_send_data(0,0,2,0xD2)` compiled out for V003
  (3735518:bootloader/bootloader.c). Also flash-budget scavenging: dead HSITRIM read
  removed, `neg a5,a5`, debug-marker restore dropped.
- Claims: turnaround setup 15.7→5.3 bit-times (spec ≤7.5; final 5.3/2.8/5.4), tx cell max
  37→34, fw 1908B (commit msg).

### 9505abd — TX v2: branchless NRZI preamble/token slot
- Tech: preamble/token TX slot made branchless — `(bit-1) & flip_word` mask XORed into the
  BSHR value so the store lands on the same cycle for every bit ("the old zero/one split
  stored 8 cycles apart -> 28/36 alternation"); vestigial stuff counter dropped (SYNC+PID+
  token can never run 6 ones); byte-boundary path in the data loop padded with `c.nop`
  ("ran 1 cycle short (31) and the next slot realigned to 33")
  (9505abd:rv003usb/rv003usb.S `pre_and_tok_send_inner_loop`, `load_next_byte`).
- Claims: tx cell min 31→32, max run 132→128; combined final: eye 18515/29570/26523 ppm vs
  upstream 9217/10389/10272, tx cells 32/32.0/34, turnaround 5.3/2.7/5.4 bit-times,
  false_acks=0 wedged=0, fw 1908B < 1920B sector (commit msg).

### WHY "CH32V003 only" — the concrete assumptions
1. **32 cycles/bit exactly** (48 MHz / 1.5 Mbps). Every slot is a hand-placed cycle ledger
   pinned to E+2/E+26/E+28/E+32/E+34 with 2-cycle filler loads (`c.lwsp a0, 0(sp)`) as
   padding (f46ed67:rv003usb/rv003usb.S slot ledger). Change the budget and every ledger,
   filler and the ±2/±4 resync step sizes must be re-derived.
2. **QingKe V2A + 1-wait-state flash instruction timing.** Ledgers are annotated
   "(CH32V003, 1ws flash)" (b83d015:rv003usb/rv003usb.S); taken-branch/load costs are
   baked in. Master itself documents the fragility: CH32V00x flash is 1.5–2x slower and
   needs `nx6p3delay`/`VOOXDELAY` padding macros (master:rv003usb/rv003usb.S header
   comment) — same source, different chip, different cycle counts.
3. **RV32EC register file (16 regs, compressed subset s0/s1/a0–a5).** Allocation is
   squeezed to exhaustion: `tp` recycled as byte-count ("tp is otherwise unused"), `t0`
   noted spare, PID parked in a spare stack slot `sw a5, 44(sp)`
   (3735518:rv003usb/rv003usb.S). A c.-reg-only inner loop is a hard constraint that
   shaped the code (e.g. `bnez t0` "not a compressed reg: 32-bit form", f46ed67).
4. **1920-byte bootloader flash budget.** fw 1908B < 1920B drove real behavior changes:
   CRC5 token check dropped, dead code stripped for bytes (f46ed67, 3735518 msgs).
5. **Link-at-low-address tricks**: `addi ra, zero, %lo(done_usb_message_in)` as return
   address and "table kept below 0x800 so a single addi loads its address"
   (3735518:rv003usb/rv003usb.S) — assume code+tables in the first 2 KB.
6. **WCH/CH32 peripheral layout**: CFGLR/INDR/BSHR offsets, EXTI at 0x40010414, HSITRIM
   bang-bang trimming at RCC 0x40021000 driven from SE0 keepalive timing (rebased
   `handle_se0_keepalive`, 3735518 diff) — the clock-trim feedback loop is
   CH32V003-HSI-specific.
7. **tier-b stack surgery**: fake `usb_send_data` frame (`c.addi sp, -16`) + jumping into
   its epilogue (3735518:rv003usb/rv003usb.S) couples the ACK streamer to the exact
   register-save layout of this implementation.

### Port to a chip with 64 cycles/bit (e.g. 96 MHz)?
The *ideas* port (branchless slot, PRE/MAIN bang-bang resync, CRC out of slot, ACK-first
pipelined CRC, fixed-store-cycle TX); the *code* does not — it is a cycle-exact artifact of
32-cycle slots + V2A/1ws timing + RV32EC pressure. At 64 cycles/bit ~half the slot is idle,
so branchless balancing buys little (a branchy path with 30 spare cycles is trivially
padded), register pressure eases, and CRC could stay in-slot again. **Master's simpler
two-path code is the better porting base**: it is what the py32 port successfully
translated, it is the `#if CH32V00x` half the rebased branch itself keeps as the portable
reference, and its timing knobs are already macro-ized (`nx6p3delay`). Adopt branchless
ideas selectively afterward only if measured eye/turnaround demands it — tier-b ACK-first
(3735518) is the most portable win (protocol-level, restores turnaround), the 32-cycle
ledgers the least.

### -rebased vs non-rebased
- `origin/rx-tx-branchless-ch32v003` (head 9698e51): 4 equivalent commits (1db6fcb,
  84ea081, 6b1792e, 9698e51) on OLD master 9c8a442, fw 1916B, no `#if CH32V00x` wrap,
  no "CH32V003 only" tags.
- `origin/rx-tx-branchless-ch32v003-rebased` (head 9505abd): same work re-applied onto
  current master 75d926a (== both local and origin master today), upstream V00x path kept
  untouched under `#if`, numbers re-measured, fw 1908B, later timestamps (b83d015 msg).
- **The -rebased branch is current**; the non-rebased one is superseded.

## Other branches (one line each)
- **origin/runfromram** (ca9ff41 "RAM is a deadend that ends in madness. Do not."):
  aborted experiment running the V003 stack from RAM — relevant only as a warning that
  changing fetch timing (flash↔RAM) silently breaks the cycle ledgers.
- **origin/update_with_new_vector_method** (touches bootloader_v006, ch32fun submodule,
  rv003usb.S vector handling for CH32V006): CH32V006 bootloader vectoring — not directly
  relevant, but shows the intended pattern for adding a WCH sibling chip.
- **origin/enable_boost_for_bootloader** (1d7fb3a, bootloader/bootloader.c only): makes
  the bootloader enable BOOST by default — power config, not relevant to porting.
