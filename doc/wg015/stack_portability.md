# rv003usb stack portability inventory (for K1921VG015 port)

Branch `claude/wg015-bitbang-usb-port-bxuu7w` (= kimstik master, HEAD 75d926a). Every claim cites file:line. Paths relative to repo root.

## 1. File map

| File | Lines | Role |
|---|---|---|
| `rv003usb/rv003usb.S` | 1092 | Bit-level engine. RX ISR `EXTI7_0_IRQHandler` (S:64–590), asm PID handlers ack/setup/out (S:608–670), SE0 keepalive + HSI auto-trim (S:673–728), TX `usb_send_data`/`usb_send_empty` (S:740–1026), optional minimal startup `USE_TINY_BOOT` (S:1032–1085) |
| `rv003usb/rv003usb.c` | 497 | Protocol layer: `usb_setup()` GPIO/EXTI init (c:55–154), `usb_pid_handle_in/data` + control transfers, descriptors, SET_ADDRESS (c:162–469), C fallback ack/setup handlers (c:477–495) |
| `rv003usb/rv003usb.h` | 218 | Config knobs (h:28–57), struct field offsets shared C↔asm (h:124–144), `struct usb_endpoint`/`rv003usb_internal` (h:160–196), `USB_BUFFER_SIZE 12` (h:120), `USB_DMASK` (h:122) |
| `rv003usb/notes_on_porting_to_v00x.md` | 30 | Lessons from CH32V002/5/6 port: flash 1.5–2x slower than v003, bit loops must run from RAM (notes:10–17) |
| `lib/tinyusb_hid.h`, `lib/cdc.h` | – | Chip-neutral descriptor helpers |
| `lib/swio_self.h` | – | WCH debug-module self-access, only for `RV003USB_USB_TERMINAL` (c:14) |
| `bootloader/` (CH32V003) | boot.c 481, ld 166 | USB HID bootloader in the 1920 B boot zone (bootloader/README.md:3); FLASH LENGTH = 1916 + 4 B SECRET (ld:6–7) |
| `bootloader_v006/` (v002/5/6) | boot.c 251, 3 ld × 169 | Same idea, FLASH LENGTH 3324 (v006 ld:6); the three .ld differ **only** in RAM LENGTH 4K/6K/8K (diff, line 8) |
| `ch32fun/` submodule | **EMPTY** (git submodule status: `-1e4887e`) | Expected to provide: `ch32fun.h` (register defs, `XW_C_*` macros, `NVIC_EnableIRQ`, SysTick struct), `ch32fun.mk` build system (bootloader/Makefile:15), default startup + vector table + linker script, `minichlink` flasher (bootloader/Makefile:13) |

Flash footprint: bootloader "almost 1920 Bytes and thus fills the available space almost completely" (bootloader/README.md:16).

## 2. Timing architecture

- 48 MHz / 32 cycles per 1.5 Mbps bit is implicit everywhere; the only explicit number: keepalive expects 48000 SysTick(HCLK) counts per 1 ms frame (`li a1, 48000` S:685), sanity window ±4000 (S:690–698).
- Loops are hand-unrolled instruction slots padded to 32 cycles with `c.nop`, `VOOXDELAY`, `j 1f; 1:` ("4 cycles?" S:144,147), and macro `nx6p3delay(n)` = 6n+3 cycles (S:20).
- Cycle-count assumptions written into comments (all measured on CH32V003):
  - `c.bnez` slot "takes 6 cycles or 8 cycles, depending" (S:155–157) — i.e. taken vs not-taken branch differs by 2.
  - `HANDLE_NEXT_BYTE` "4 cycles for this section. (Checked) (Sometimes 5)?" (S:313–317).
  - Tolerance at `done_preamble`: "8 extra cycles here cause errors. −5 cycles is too much. −4 to +6 cycles is OK" (S:186–188).
  - TX SE0 timing "off by 2 clock cycles. Probably OK" (S:1016–1017).
- Two timing profiles selected by `CH32V00x` (S:18–26):
  - **v003**: runs from flash; flash is 1-wait-state at 48 MHz (`FLASH->ACTLR = FLASH_ACTLR_LATENCY_1`, S:1068–1069), timing calibrated to v003 flash fetch; `VOOXDELAY` empty (S:22).
  - **v00x**: time-critical code linked into `.srodata,"ax"` = copied to and run from RAM (S:59, S:735); `VOOXDELAY` = one uncompressed nop `.word 0x00000013` inserted after nearly every branch (S:24–25); flash↔RAM calls need `lui/addi + c.jr` because offsets exceed ±1 MB `j` range (S:465–470, 498–514, 725–727; notes:17).
- RX resync: preamble loop re-times on each observed transition ("TRICKY: This helps retime the USB sync" S:152–158); 8-slot unrolled edge catcher after ISR entry (S:103–117).
- `FUNCONF_SYSTICK_USE_HCLK 1` is mandatory (compile error otherwise, h:17–19); set in every funconfig (bootloader/funconfig.h:6, demo_hidapi/funconfig.h).
- Clock trimming: keepalive measures SE0-to-SE0 frame delta and servo-trims the HSI via RCC.CTLR HSITRIM field (S:701–719) — this is how the device tolerates running on internal RC.

## 3. Hardware touchpoints

Assembly (`rv003usb.S`):
- **GPIO** at `USB_GPIO_BASE` (from `USB_PORT`, h:24). Hardcoded WCH register offsets: `CFGLR=0`, `INDR=8`, `BSHR=16` (S:4–6). Reads `c.lw INDR` (S:69,87,103…308…), atomic set/reset via `BSHR` (S:768, 837, 948, 993, 998), full direction flip by rewriting CFGLR nibbles for TX bus turnaround (S:754–771) and back to floating input (S:1000–1007). Output-speed nibble differs: `0b0010` (2 MHz PP) on v003 vs `0b0001` on v00x (S:760–764).
- **EXTI**: pending flag `EXTI_INTFR` at `EXTI_BASE+20`, ack by writing `1<<USB_PIN_DM` (S:578–584); hardcoded literal `0x40010414` when EXTI is shared (S:79).
- **SysTick**: hardcoded `0xE000F000`, `CNT` at offset 8 (S:12–13), read in keepalive (S:677–683).
- **RCC**: hardcoded `0x40021000` (RCC.CTLR) for HSI trim write (S:706–719).
- **TIM1** `TIM1_BASE+0x24` (debug pulses) or `+0x58` (dummy sink) (S:30–38) — debug only.

C (`rv003usb.c`):
- `RCC->APB2PCENR` clock enable for GPIO port + AFIO (c:60).
- GPIO CFGLR/BSHR pin config incl. DPU pull-up drive (c:128–150).
- `AFIO->EXTICR` pin-to-EXTI-line mux, `EXTI->INTENR`, `EXTI->FTENR` falling edge on D− (c:143–145).
- `NVIC_EnableIRQ(EXTI7_0_IRQn)` — WCH PFIC (c:153).
- Reboot-to-bootloader: `FLASH->BOOT_MODEKEYR` (FLASH_KEY1/2), `FLASH->STATR = 1<<14`, `FLASH->CTLR = CR_LOCK_Set`, `RCC->RSTSCKR |= 0x1000000`, `PFIC->SCTLR = 1<<31` (software system reset) (c:179–184).
- TIM1 PWM + MCO on PC4 for `RV003USB_DEBUG_TIMING` only (c:62–125).

Bootloader adds: SysTick->CTLR=5 manual enable (boot.c:121), `RCC->RSTSCKR == 0x10000000` soft-reset detection (boot.c:240–248), option bytes via minichlink `+a55aff00 option` (bootloader/Makefile:17–22), and `configurebootloader` programs option bytes on-chip via `FLASH->OBKEYR/KEYR/MODEKEYR/CTLR`, `OB->RDPR` (configurebootloader.c:29–48).

## 4. Configuration surface

`usb_config.h` (per project):
- `ENDPOINTS` (bootloader/usb_config.h:5), `USB_PORT` [A,C,D], `USB_PIN_DP`/`USB_PIN_DM` **[0–4]**, optional `USB_PIN_DPU` [0–7] + `USB_DPU_PORT` override (bootloader/usb_config.h:19–23; DPU port override handled boot.c:227–230). Pin range 0–4 because `USB_DMASK` must fit `c.andi`'s 6-bit immediate (S:70; removal discussed notes:25–27).
- Feature flags `RV003USB_OPTIMIZE_FLASH / BOOTLOADER / HANDLE_IN_REQUEST / OTHER_CONTROL / HANDLE_USER_DATA / HID_FEATURES / USB_TERMINAL / USE_REBOOT_FEATURE_REPORT / SUPPORT_CONTROL_OUT / USER_DATA_HANDLES_TOKEN / EVENT_DEBUGGING / DEBUG_TIMING / CUSTOM_C` (h:28–43; bootloader_v006/usb_config.h:20–30).
- All USB descriptors live here (bootloader/usb_config.h:33–166).

`funconfig.h` (per project): `CH32V003 1` or MCU via Makefile `TARGET_MCU` (bootloader/funconfig.h:5, demo_hidapi/Makefile:6), `FUNCONF_SYSTICK_USE_HCLK 1` (mandatory, h:17–19), optional `RV003_ADD_EXTI_MASK`/`RV003_ADD_EXTI_HANDLER` asm hook to share the EXTI ISR with user pins (demo_exti/funconfig.h:6–7; S:78–83, 554–563).

Makefile knobs: `TARGET_MCU`, `ADDITIONAL_C_FILES += rv003usb.S [rv003usb.c]`, `-DUSE_TINY_BOOT` for bootloader (bootloader/Makefile:5–9).

## 5. Interrupt entry/exit

- Vector: handler symbol placed in `.text.vector_handler` for the ch32fun vector table (S:57), or, with `USE_TINY_BOOT`, its address is planted directly at flash offset 0x50 (EXTI7_0 slot) via `. = 0x52` + `.word EXTI7_0_IRQHandler` (S:1077–1079). WCH PFIC jumps straight to it in vectored mode (`csrw mtvec, 3` S:1050).
- Not naked-with-HW-stacking: plain machine-mode handler, manually builds an 80-byte frame and saves a0–a5, s0, s1, t0–t2, ra lazily as it goes (S:65–76, 119–122, 161–162); exits with `mret` (S:590). t0 noted unused (S:121).
- First D− sample is the 5th instruction after entry — `addi sp / sw a0 / sw a5 / la a5 / c.lw INDR`, "MUST check SE0 immediately" (S:65–69). SE0 branch decided at S:91–100. Then the 8-slot edge-catcher (S:103–117) absorbs entry-latency jitter by re-syncing on the first D+/D− transition; no explicit entry-latency cycle number exists in the repo.
- Contract: the pin-change interrupt "**must** be the highest priority… **must never** be preempted"; other critical sections ≤ ~40 cycles (README.md:94).
- Interrupt acked at exit by writing EXTI INTFR (S:576–584), with a small deliberate delay "to make sure we don't accidentally false fire" (S:576).

## 6. Bootloader specifics

- Flash layout (v003): bootloader lives in the dedicated 1920 B boot zone, physically 0x1FFFF000, mapped to 0x00000000 at boot (ld:5–6); user code in main flash is untouched. Which image boots is selected by option bytes (`+a55aff00` enables bootloader, bootloader/Makefile:19) and at runtime by `FLASH->BOOT_MODEKEYR` unlock + `FLASH->STATR` bit 14 + `PFIC->SCTLR = 1<<31` reset (boot.c:110–115 to user code; c:179–184 back to bootloader).
- Entry decision: power-up timeout (75 ms units, boot.c:41–51), optional boot button (boot.c:32–39, 237–251), optional USB-host-detect timeout (boot.c:45–47, 283–293 with `reset_timeout` set from USB traffic, boot.c:373–382 / c:251–254), soft-reboot detect via `RCC->RSTSCKR == 0x10000000` (boot.c:240–248).
- Self-flashing mechanism: bootloader itself contains **no flash-write code**. Host (minichlink) uploads ≤120 B code blobs into a RAM `scratchpad` via HID feature reports; on trailing magic `0x1234abcd` the blob is executed as a function (boot.c:300–311, 453–473). The chip-specific flash programming lives in those host-supplied blobs → must be rewritten host-side for a new chip.
- Linker tricks: `SECRET` region — 4 bytes at fixed 0x77C (v006: 0xCFC) holding an XOR-encoded address of `boot_usercode` so the host can call it (ld:7, 25, 116–120; boot.c:90–97); fixed RAM addresses `scratchpad = 0x20000100`, `runwordpad = 0x20000580` (ld:156–157); v006 uses a `.scratchpad (NOLOAD)` section instead (v006 ld:156–163) sized 2K/4K/6K by chip (v006 boot.c:51–59).
- `USE_TINY_BOOT` startup replacing ch32fun's (S:1032–1085): set sp=`_eusrstack`, `mstatus=0x80`, `mtvec=3`, zero all RAM, `RCC->CTLR = HSION|PLLON|trim`, `FLASH->ACTLR = LATENCY_1`, `RCC->CFGR0 = SW_PLL` (48 MHz from HSI×2 PLL), then `csrw mepc, main; mret` (S:1047–1075). No .data section support (S:1058).

## 7. RV32E / compressed / WCH extensions

- Registers: only x0–x15 used; explicit map "zero, ra, sp, gp, tp, t0, t1, t2 / Compressed: s0, s1, a0, a1, a2, a3, a4, a5" (S:49–53). x4/tp abused as debug pointer (S:30–38). RV32E-clean.
- Compressed (C extension) instructions are load-bearing for timing throughout (`c.lw/c.sw/c.andi/c.beqz/c.xor/…`, e.g. S:103–117, 229–256). A few deliberately *uncompressed* encodings emitted as `.word` for timing/alignment: `0x00000013` nop (S:25, 282), `0x00138393` = `addi t2,t2,1` (S:291–293).
- **XW extension (WCH custom compressed byte/half ops)**: `XW_C_LBU` (S:439, 485, 890), `XW_C_LHU` (S:477), `XW_C_SB` (S:665); macros come from ch32fun.h; "CH32v003 has the XW extension. this replaces: lb s0, 0(a0)" (S:888–890). 5 sites total; the S:890 one sits inside the cycle-counted TX byte loop.
- CSRs used: `mstatus`, `mtvec`, `mepc` (tiny boot only, S:1049–1073); `mret` (S:590, 1075). No `intsyscr`/0x804 or other WCH CSRs anywhere in this repo (grep of *.S/*.c/*.h).

## 8. Portability checklist (ordered)

1. **Clock**: produce exactly 48 MHz (32 cyc/bit) via PLL from crystal, or re-pad every loop for another integer multiple of 1.5 MHz; fix the `48000`/±4000 frame constants (S:685–698). K1921 max 50 MHz → 48 works, 33⅓ cyc/bit does not.
2. **Deterministic fetch**: characterize cycle cost of GPIO load, taken/untaken branch, and flash wait-state/prefetch jitter on the new core. If flash fetch is not cycle-deterministic, copy the RX/TX loops to RAM as the CH32V00x variant does (S:59, notes:10–17) — the flash-resident goal must be validated first.
3. **GPIO block**: D+ and D− on one port, readable in one load, mask ≤ 0x1F (c.andi limit, S:70; or widen to `andi`, notes:25–27); atomic set/reset register (BSHR equivalent); fast whole-nibble direction flip for bus turnaround (S:754–771, 1000–1007). Rewrite the three offset defines (S:4–6) and TX CFGLR sequences for K1921 DATAOUT/DATAOUTSET/CLR + OUTENSET layout.
4. **Edge IRQ on D−** with low, bounded latency and direct vectoring. K1921 has a single `mtvec` + PLIC software dispatch → put a pre-dispatch stub that samples GPIO first; the 8-slot sync catcher (S:103–117) absorbs some jitter but entry must stay short and constant.
5. **ISR contract**: machine mode, `mret`, never preempted (README.md:94); replace EXTI INTFR ack (S:578–584) with the K1921 GPIO/PLIC claim-complete sequence.
6. **Free-running HCLK counter** readable at a fixed address to replace SysTick CNT (S:12–13, 677–683); delete or rework HSI-trim servo (S:701–719) — with a crystal it can be dropped.
7. **Replace XW instructions** (5 sites, §7) with standard `lbu/lhu/sb`; re-balance the affected TX loop timing (S:890).
8. **Verify ISA**: C extension mandatory; RV32E-safe already; check `.option arch` needs (S:1044–1046).
9. **Startup/vector/linker**: replace `USE_TINY_BOOT` block and ch32fun startup+vector table; new linker scripts (flash at 0x80000000, not 0x0); provide `funconfig.h`/`ch32fun.h` shims (register structs, `NVIC_EnableIRQ` equivalent) since asm+C include `ch32fun.h` (S:1, c:10).
10. **Bootloader redesign**: K1921 has no separate boot zone and no ROM loader (chip_info.md §10) → bootloader at flash base, jump-to-app instead of option-byte/`BOOT_MODEKEYR`/PFIC-reset switching (boot.c:110–115, c:179–184); write new host-side scratchpad blobs (flash unlock/erase/program for the K1921 flash controller) for minichlink or replacement tool; new SECRET/scratchpad fixed addresses in the .ld (ld:7, 156–157).
11. **Build system**: replace `ch32fun.mk` include and minichlink flash target (bootloader/Makefile:5–15) with K1921 SDK toolchain/OpenOCD flow.
