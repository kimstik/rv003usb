# §9 rework — replacement blocks for `doc/py32/PLAN.md`

Splice rule: each `## REPLACES …` line names the PLAN.md region (at 88d1229) the block body
replaces; the body runs to the next `## REPLACES`/`## PATCH`/`## NOTES` line. Cite forms are
PLAN.md's (`arm.S:<n>`, `S:<n>`, `c:<n>`, `PA S-n`, `RM002B p<n>`) plus `CF §n` =
`doc/py32/CHIP_FACTS_XIAMATSU.md` section and `xm_030.md:<n>` / `xm_002b.md:<n>` = the
Xiamatsu README lines quoted there.

## REPLACES §9 — Work breakdown

(skeleton — task ids, ownership; prose per wave follows in later commits)

Tasks: T0 merge · T1 skeleton/build · T2 engine · T3 C seams · T4 demos · T5 DFU ·
T6 bench+VCD · T7 CI · T8 docs · T9 HID loader · T10 HW validation (F030) · T11 loopback ·
T12 F002B HSI↔LSI self-calibration (new) · T13 keepalive actuator header (new, split from T2) ·
T14 cycle walker + literal-pool check (new, split from T2) · T15 F8 Thumb handlers (new, split
from T2) · T16 HW validation F002B track (new, split from T10).

### Ownership matrix (must stay disjoint)

| Task | Owns | Serialised after |
|---|---|---|
| T0 | merge only | — |
| T1 | `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f002b.ld, py32f030x6.ld, py32f030x8.ld, Makefile.py32, py32_stdio_stub.c, README.md, selftest_main.c}` | — |
| T2 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h` | — |
| T3 | `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h`, `rv003usb/wg015/usb_port_wg015.h`, `rv003usb/py32/usb_port_py32.h` | — |
| T4 | `demo_gamepad/{usb_config.h,funconfig.h,demo_gamepad.c,README.md}`, `demo_hidapi/{usb_config.h,funconfig.h,demo_hidapi.c,README.md}` | — |
| T5 | `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/*`, `bootloader_dfu/README.md`, `tools/wg015mkdfu.py` | — |
| T6 | `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S, bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`, `tools/wg015_vcd/*` | — |
| T7 | `Makefile`, `.github/workflows/build.yml`, `.gitignore`, `README.md` | — |
| T8 | `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | — |
| T9 | `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | — |
| T10 | `doc/py32/calibration.md`; values only in `rv003usb/py32/usb_port_py32_tune.h` | T2 |
| T11 | `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}` | — |
| T12 | `rv003usb/py32/py32_hsical.c`, `rv003usb/py32/py32_hsical.h`, `py32_bench/bench8_hsical.c` | — |
| T13 | `rv003usb/py32/usb_port_py32_trim.h`, `rv003usb/py32/selftest_trim.S` | — |
| T14 | `tools/py32_cyc.py`, `tools/py32_cyc_costs.json`, `tools/py32_cyc_selftest/*` | — |
| T15 | `rv003usb/rv003usb-arm.S` (F8 handlers only) | T2 |
| T16 | `doc/py32/calibration_f002b.md`; values only in `rv003usb/py32/usb_port_py32_trim.h` | T13, T12 |

### Wave order (skeleton)

| Wave | Tasks |
|---|---|
| 0 | T0 |
| 1 | T1, T3, T8, T13, T14 |
| 2 | T2, T4, T6, T12 |
| 3 | T5, T7, T9, T11, T15 |
| 4 | T10, T16 |
