# The integrated build — both new engines, linked, measured

Not an analysis. A linked image, built in this container, of the merged RX
engine and the TX engine together with the existing C layer and descriptors.

## Result

| MCU | RAM | of total | FLASH | of total |
|---|---|---|---|---|
| **PY32F003x4** | **336 B** | **16.41 % of 2 KB** | 4520 B | 27.59 % of 16 KB |
| PY32F030x6 | 1744 B | 42.58 % of 4 KB | 4520 B | 13.79 % of 32 KB |
| PY32F002Bx5 | 976 B | 31.77 % of 3 KB | 5112 B | 20.80 % of 24 KB |

F003x4 is the meaningful number: it is the tightest part in the primary family
and the one the earlier analysis said could not hold both engines. **It holds
them with 1712 B of RAM to spare.**

The other two rows are not directly comparable — only `py32f003x4.ld` had its
heap and stack reduced, so F030x6 and F002Bx5 still carry the stock 512 + 512
default. Their real figures would be about 1024 B lower.

RAM composition on F003x4:

```
.data              8 B
.bss             176 B   (rxbuf, txbuf, rv003usb_internal_data)
._user_heap_stack 128 B   (heap 0, stack 128 - see RAM_BUDGET.md)
                 ---
                 336 B
```

Symbol placement, confirming the engines really are in flash:

```
080003b8 T usb_rx_engine16      flash
080007ac T usb_tables           flash
08000ad2 T usb_send_data        flash
200000a0 B usb_rxbuf            RAM   (a buffer, correctly)
```

## How it was built

1. `engine16_merged.S` and `engine16_tx.S` dropped into `rv003usb/` in place of
   `rv003usb-arm.S`. **The external seam is identical** — both new engines
   import exactly the same six symbols the old one did
   (`rv003usb_internal_data`, `usb_pid_handle_{ack,data,in,out,setup}`), so this
   is a drop-in replacement and the C layer was not touched.
2. One line of vector wiring, because the startup file's slot is
   `EXTI2_3_IRQHandler` while the engine entry is `usb_rx_engine16`:
   ```
   .global EXTI2_3_IRQHandler
   .thumb_set EXTI2_3_IRQHandler, usb_rx_engine16
   ```
   An alias rather than a rename, so the engine files stay byte-identical to
   what the competition produced and verified.
3. `.section .datacode` → `.section .text.engine16` in both engines, which is
   what puts them in flash.
4. Heap 0, stack 128, `-DFORBID_VECT_TAB_MIGRATION` (`RAM_BUDGET.md`).

A trap worth recording: with the vector wiring missing, the build **succeeds**
at 740 B of flash and 248 B of RAM, because `--gc-sections` discards both
engines when nothing references their entry point. A plausible, tiny, entirely
empty image. Anyone integrating this must check that `usb_rx_engine16` is
actually present in the linked ELF, not merely that the link succeeded.

## What this proves, and what it does not

**Proves the footprint.** Both engines, their tables, the C layer and the
descriptors fit the smallest part of the primary family with room to spare, and
the earlier claim that they could not is retracted.

**Does not prove the timing.** Both engines were written, ledgered and verified
as **RAM-resident**. Moving them to flash changes one cost that matters: the RX
cell reads its table once per bit with `ldrh r1, [r4, r1]`. From RAM-resident
code against a RAM table that is 2 cycles and the cell is exactly 16. Here the
code and the table are both in flash, and **the cost of a register-offset data
read from flash is not in the measured table** (`CHIP_FACTS_XIAMATSU.md` §1
prices a PC-relative literal at 2, which is a different addressing mode).

So this image has the right size and an unverified bit cell. If the flash data
read costs 2, the cell stays at 16 and this configuration is the answer. If it
costs more, the options are the tables in RAM with the code in flash — which
costs 4 per read and breaks the cell — or an engine with no per-bit table read,
which is CLEANSHEET's structure.

**That single measurement is now the most valuable bench item in the project**,
ahead of the taken-branch cost, because it decides whether this build is a
working stack or only a well-sized one.

## RAM residency is not a footprint competitor at 24 MHz — proven

Code that executes from RAM must still be *stored* in flash, as the load address
the startup copy reads from. So RAM residency costs flash **plus** RAM, never
flash instead of RAM. Measured rather than argued, same part, same sources, only
the section directive changed:

| engines in | FLASH | RAM |
|---|---|---|
| `.text.engine16` (flash) | 4520 B | **336 B** |
| `.datacode` (RAM) | 4640 B | **3600 B** |

RAM residency costs **+120 B of flash and +3264 B of RAM**. Worse on both axes.

The mechanism is visible in the section headers of the RAM-resident build:

```
.data   size 0x00000cc8   VMA 20000000   LMA 08000858
```

3272 B occupying RAM at run time and the same 3272 B occupying flash as the copy
source. Nothing about executing from RAM removes the flash cost.

So at 24 MHz, where LAT = 0 makes flash fetch single-cycle and deterministic,
RAM residency is **capable but never cheaper**. It buys exactly one thing: a
2-cycle RAM data access where the flash figure is unmeasured. At 48 MHz the
choice does not exist — LAT = 1 forces RAM residency and the footprint cost
comes with it.

## How things landed — from the linked image

```
08000244  usb_pid_handle_in         C layer, flash
080003b8  usb_rx_engine16           = EXTI2_3_IRQHandler (alias)
08000464  usb_rx_cell0 .. cell7     the unrolled chain, 0x1e/0x20 apart
080007ac  usb_tables                RX tables, flash
08000ad2  usb_send_data
08000b5c  usb_tx_cellP0 .. cellS7   the ten-cell TX chain
08000d24  usb_tx_tables             TX tables, flash
2000003c  rv003usb_internal_data    RAM
200000a0  usb_rxbuf                 RAM, 32-byte aligned
200000c0  usb_txbuf                 RAM
```

Three things checked in the dump rather than assumed:

* **`usb_rxbuf` is 32-byte aligned and it is declared, not lucky** —
  `.balign USB_RXBUF_LEN` at `engine16_merged.S:724`, carrying the comment that
  the mask depends on it. Together with the `lsls #27 / lsrs #27` index mask
  that gives the structural buffer bound, and the alignment it rests on is
  guaranteed by the source.
* **Cells 1 and 4 land at odd halfword alignment** (0x08000482, 0x080004de).
  Since the measured cost table says taken-branch cost depends on alignment,
  this would matter for most designs. It does not matter here, and the reason is
  the design's central property: the chain contains **no timed taken branches**.
  Cells fall through consecutively and only cell 7 does `bx lr`. The alignment is
  harmless by construction rather than by luck.
* **The C calling convention is correct.** The fifth argument
  `struct rv003usb_internal *ist` is passed on the stack — `push {r4}` before
  each `bl`, `add sp, #4` after — matching the prototypes at `rv003usb.c:197`,
  `:283`, `:291`, `:519`, `:527`.
