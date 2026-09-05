# Does the stack hold 16 cycles from flash? Not quite — and the gap is exact

Correcting two claims of mine in sequence, both made too fast.

## First correction: the flash data cost was never unmeasured

I called the cost of a flash data read "the single number that decides this" and
said it was unmeasured. It is in the source, in the same table as everything
else (`xm_030.md:470-486`):

```
код во ФЛЕШЕ:                          код в ОЗУ:
LDR,STR - при обращении к портам - 1     LDR,STR - при обращении к RAM  - 2
LDR - при обращении к Flash через PC - 2 LDR - при обращении к Flash через PC - 4
LDR,STR - при обращении к RAM  - 4
```

The rows are indexed by **which memory is touched**, not by addressing mode:
ports 1, flash 2, RAM 4 for flash-resident code, with flash and RAM swapping for
RAM-resident code. "через PC" describes how the author performed the read, not a
restriction on it. `tools/engine16_cyc.py` had the same error — it charged every
non-port load as RAM — and now takes `--flashdata` naming registers that point
into flash.

## Second correction: "exactly 16 in flash too" was checked on one cell

Having fixed the tool I measured `usb_rx_cell0`, got exactly 16, and said the
flash-resident build was timing-correct. That generalised from one cell of
eight. Measuring all of them:

| cell | flash-resident | slack (nops) |
|---|---|---|
| 0 | 16 | 4 |
| 1 | 16 | 0 |
| 2 | 16 | 2 |
| **3** | **18** | **0** |
| 4 | 16 | 2 |
| 5 | 16 | 0 |
| 6 | 16 | 1 |
| 7 | 16 | 0 |

Seven cells fit. **Cell 3 is 18 — two over budget.**

## The bottleneck, precisely

Cell 3 is the one that stores a finished byte:

```
strb r3, [r1, r2]        4 cycles     <- rxbuf is in RAM; from flash-resident
                                         code a RAM access costs 4, not 2
```

Fourteen single-cycle instructions plus that store is 18. From RAM-resident code
the same store costs 2 and the cell is exactly 16. **That is why the engines
were written RAM-resident** — the 2-cycle RAM access is not a convenience, it is
what makes the byte store fit the cell.

It is the only RAM access in the timed receive path. Everything else in a cell
touches a port (1 cycle) or the tables (2 cycles from flash), and both of those
are unaffected by residency.

## Is it recoverable? Yes in principle, not for free

Total slack across the eight cells is **9 nops per byte** — exactly the "9 cycles
of slack out of 128" the referee recorded. Cell 3 needs 2 of them, and the
obvious donor is the buffer index computation that sits immediately before the
store:

```
mov  r2, ip              1
lsls r2, r2, #27         1     BALANCE's structural bound
lsrs r2, r2, #27         1
```

Three cycles that could be computed a cell earlier, in cell 2 (2 nops spare) or
cell 0 (4 spare), and carried in a register. That would put cell 3 at 15 and let
a single nop bring it back to 16.

The cost is a register, and the referee recorded "every register live" as a
limitation of the merged design. So the fix is a redistribution under register
pressure, not a one-line edit — and it is precisely the kind of change that
must be re-verified against the object rather than reasoned about, since the
pipeline runs one byte behind and moving work across a cell boundary moves it
across that pipeline stage too.

## Where this leaves the two configurations

| | footprint | timing |
|---|---|---|
| RAM-resident | 4640 B flash **+ 3600 B RAM** — fits nothing smaller than F030x6 | **verified exactly 16 on every cell** |
| flash-resident | 4520 B flash + 336 B RAM — fits F003x4 with 1712 B spare | 7 cells at 16, **cell 3 at 18** |

So the choice is not free either way, and neither configuration is currently
both. The flash build needs 2 cycles moved out of cell 3; the RAM build needs
3264 B of RAM that F003x4 does not have.

**The flash route is the one worth finishing**, because its deficit is 2 cycles
against 9 of available slack, while the RAM route's deficit is 1552 B against a
2048 B part. Neither is settled until the redistribution is built and measured.

## Not yet checked

The transmit engine's cells are not separately labelled, so the same per-cell
audit has not been done for it. `usb_send_data` measures 146..155 cycles
flash-resident against 128..137 RAM-resident — an 18-cycle difference that is
almost certainly the same effect on `usb_txbuf` accesses, but which cells absorb
it is unverified. That audit is outstanding and should be done before either
configuration is called finished.
