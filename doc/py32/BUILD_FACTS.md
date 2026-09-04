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
