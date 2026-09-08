# Rendering the IN response at build time

> **Status: built, executed and measured.** `usb_in_render` costs **964
> cycles = 40.2 µs** for an 8-byte payload, measured by executing it, which
> confirms `design_b_in.md` §10's 956 arrived at on paper. For a payload
> known at compile time the same record is produced by
> `tools/prerender_gen.py` on the host and read out of flash in **144..208
> cycles = 6.0..8.7 µs**, a figure that does not depend on the payload
> length. On the gamepad demo's descriptor set that is **27 records, 557 B
> of flash, no RAM at all**, and reading the whole descriptor set — 25
> packets — drops from **21 534 cycles (897 µs) to at most 5 200 (217 µs)**. §7 is the ledger,
> including the 640 B of flash it costs; §8 is what it does and does not do
> about `engine16_tx.S`'s missing stuff bit.

`design_b_in.md` §10 is honest about the price of Design B's IN direction:
the response leaves the wire faster, and the render that produces it lands on
the ISR after the host's ACK, taking it from ~2 µs to ~44 µs. §10's closing
line is *"this is the item most likely to need work next"*.

This note takes the part of that work that needs no cleverness at all.

---

## 1. What is actually static

The record `usb_in_render` writes is, apart from one field, **a pure function
of `(pid, payload bytes)`**:

* the CRC16 is deterministic;
* the bit stuffing is deterministic, `T_TX` being a table;
* NRZI is **not in the record** — it is the payload cell's own
  `sbcs`/`ands`/`eors` (`design_b_in.md` §8).

And the overwhelming majority of IN traffic during enumeration is descriptors:
`const` arrays in flash, whose bytes the compiler had in front of it. So those
records are being recomputed, at 40 µs a transaction, from inputs that never
change.

## 2. The one field that is not static, and what it forces

`+0 tokpat` is the legal `(byte2,byte3)` token halfword — address, endpoint
and CRC5 in one 16-bit value (`design_b_in.md` §6, G5). **The device address
is assigned by the host at run time**, so no build-time table can carry it,
and the record cannot simply move to flash entire.

What moves instead is the part that can: the record gains

```
   +24  u32 src    WHERE the payload chain streams from
```

and the chain follows that word rather than assuming `record + 8`. A dynamic
payload sets it to the record's own `bits[]`; a static one sets it to a
build-time stream in flash. `tokpat`, `pid`, `groups` and `tail` are still
written into the RAM record, because `tokpat` has to be and the other three
are one store each.

## 3. The descriptor set: one record per packet, not per descriptor

A control IN splits a descriptor into 8-byte DATA packets with an alternating
toggle. `rv003usb.c` makes the split explicit:

```
  usb_pid_handle_in:    sendnow = e->opaque + (e->count << 3)
                        tosend  = min(8, e->max_len - offset)
  usb_pid_handle_setup: toggle_in = 1, count = 0
```

so chunk *i* starts at byte 8*i*, is 8 bytes except possibly the last, and
carries **DATA1 when *i* is even, DATA0 when it is odd**. Each chunk is a
different packet with a different CRC16 and a different stuff state, so it is
**one record per packet**. The gamepad demo:

| descriptor | bytes | packets |
|---|---|---|
| `device_descriptor` | 18 | 3 |
| `config_descriptor` | 34 | 5 |
| `gamepad_hid_desc` | 39 | 5 |
| `config_descriptor + 18` (the 0x2100 entry) | 9 | 2 |
| `string0` | 4 | 1 |
| `string1` | 14 | 2 |
| `string2` | 44 | 6 |
| `string3` | 8 | 1 |
| | **170** | **25** |
| the two zero-length status stages | 0 | 2 |
| | | **27** |

**Truncated reads are not covered and must not be.** A host that asks for
`wLength = 9` of the configuration descriptor gets chunk 0 whole and then a
**one-byte prefix** of chunk 1 — different bytes, different CRC16, a
different packet. The table is keyed by `(pointer, length, PID)` and that
lookup misses, so the transaction renders at run time exactly as before. Every
standard enumeration does this at least once, which is why the fallback is not
optional and why `usb_in_render` is not replaced.

The two zero-length entries are static in a stronger sense than the rest: for
`len == 0` the record is a function of the PID alone, so those two records are
correct whatever pointer the caller passed. They are *found* because
`usb_send_empty` is `mov r3, r0` falling into `usb_send_data`
(`engine16_tx.S`:326-330), so `r0` — the key the search runs on — still holds
the token, 0x4B or 0xC3. That is read out of the source, not assumed, and if
it ever stops being true the entries stop being found and a status stage costs
its 297 cycles again.

## 4. The cells, and the question of where the stream lives

The brief for this work asked whether a flash-resident stream is what lets the
streaming cells hold 16 where a RAM-resident one could not, the way
`engine16_tx.S` sits at 18 on seven of ten cells. **It is not, and the
arithmetic says so plainly.** `TIBIT` is 5 cycles and a cell is 16, so there
are 11 free; a data read is 4 from RAM and 2 from flash for flash-resident
code (`ENGINE16_SPEC.md` §2).

```
  Q1, before                            Q1, after
    TIBIT                     5           TIBIT                     5
    movs r3,#TI_BITS_OFS      1           ldr  r1,[r1,#TI_SRC_OFS]  4  RAM
    adds r1,r1,r3             1           ldrb r2,[r1,#0]           4  RAM
    ldrb r2,[r1,#0]           4  RAM                                2  flash
    adds r1,r1,#1             1           adds r1,r1,#1             1
    mov  r8,r2                1           mov  r8,r2                1
                             --                                    --
                             13 of 16                     RAM      15 of 16
                                                          flash    13 of 16

  D0, the one-group prefetch, unchanged in form
    TIBIT 5 | ldrb r2,[r1,#0] 4 RAM / 2 flash | adds 1 | mov 1
                                              RAM      11 of 16
                                              flash     9 of 16
```

**The RAM-resident stream already held 16, with five cycles to spare in `D0`
and three in `Q1`.** Flash residency returns two more in each and nothing
else. It cannot be otherwise: what makes `engine16_tx.S`'s cells cost 18 is
that they carry the stuffing pipeline, not that their data is in the wrong
memory, and `design_b_in.md` §8 already removed that pipeline by
pre-rendering. Moving the bytes is a second-order effect on top of a
first-order one that was already taken.

The cell must still be budgeted at 15, not 13, because a **dynamic** payload
streams from the record in RAM and the cell is one piece of code. So the
indirection spends 2 of `Q1`'s 3 spare cycles and leaves 1.

Every timed cell is still exactly 16. That is asserted by the `TIBIT` and
`TBPADTO` macros at assembly time, and the file assembles in all twelve
combinations of `USB_RX_CHECK` (0/1/2) × `USB_ENGINE16_FLASH` (0/1) ×
`USB_PRERENDER` (0/1). `tools/engine16_cyc.py --exec flash --ioport r7
--flashdata r4 --budget 16` on the linked image reports 16 for
`usb_ti_C0..C7`, `E0..E3`, `Q0..Q7`, `D0..D7`, `T1..T6`; the cells it flags
are the ones that end in a branch out of the chain, which the tool does not
resolve, and they are the same set `design_b_in.md` §7 flagged.

**Nothing on either turnaround path moved.** `tb_flush1..6` still measure
61 and `ti_flush1..5` still 44, and the head is still 35, so DATA→ACK is
τ+115 and IN → DATA0/DATA1 is τ+109, unchanged.

## 5. The lookup

`usb_send_data`'s seam carries a pointer, a length and a PID and nothing else,
and `rv003usb.c` is not edited, so the table is keyed on the pointer:

```
  usb_pr_keys[]   const uint8_t *   ascending
  usb_pr_recs[]   { const uint8_t *bits; u8 len, pid, groups, t; }   8 B
  usb_pr_count    u8
```

`usb_in_render` binary-searches `usb_pr_keys`, then requires the length and
the PID to match as well — a truncated read of the same bytes is a different
packet and must not hit. On a hit it does four loads and five stores and
returns; the last store is `strh` of the pattern, which is what arms the
endpoint, exactly as on the dynamic path.

The three symbols are **weak**. A build with no generated table links them at
address zero, the first compare fails and every packet takes the dynamic
path — the behaviour before this existed.

A miss costs the search it lost: **69 cycles = 2.9 µs** over a 25-entry table,
paid by every dynamic HID report. Against the 964 that report's render costs
anyway, that is 7 %.

## 6. How it is verified, and why it is verified this way

`design_b_in.md` §12's standing lesson: *a model that shares a source with the
artifact cannot validate it.* `engine16_tx.md` §6 checked the transmit chain
against an encoder written from the same table and did not catch the defect
that was in both (§8 below). So no side of any comparison here is a hand
transcription of the assembly.

`tools/prerender_check.py` runs three phases. It assembles
`engine16_merged.S` and `engine16_tx.S` with `arm-none-eabi-gcc` exactly as
`INTEGRATION_BUILD.md` does (flash-resident), links them with stubs for the C
seam, and **executes them on a Cortex-M0+ emulator**.

| phase | what it compares | result |
|---|---|---|
| 1 | `tools/prerender.py`'s record against the record the **assembled `usb_in_render` actually writes into emulated RAM**, with the record poisoned to `0xA5` first so an unwritten byte cannot pass as a lucky zero | 2632 cases, **0 mismatches** |
| 2 | a generated table linked into the image, then the engine run against it: a hit must arm from a **flash** stream matching the generator byte for byte; a wrong length, a wrong PID and a pointer the table does not name must each fall through and produce a correct **RAM** record | 120 entries, 120 hits, 346 required misses, **0 problems** |
| 3 | the cycles of the instructions the emulator really executed, priced out of `tools/engine16_cyc.py`'s own cost table with each load charged by the address it actually touched | see §7 |

`tools/prerender_gen.py --verify` closes the remaining hole, which is the
two-pass build. The table's keys are addresses the linker has not assigned
when the table is generated, so it is generated against pass 1's image and
linked into pass 2's, and pass 2 moves everything pass 1 measured. That
cannot corrupt a packet — a key that no longer names a chunk simply misses,
and a miss renders — but it can silently cost the cycles this exists to save.
So `--verify` re-renders the descriptors **out of the final image** and checks
the linked table against them: the count, that `usb_pr_keys` is still
strictly ascending, that every key is the address of the chunk it names, and
that every record's `len`, `pid`, `groups`, `t` and stream bytes match. On the
gamepad demo it reports `verified 27 records`.

The build is therefore two passes, and it is **`tools/prerender_build.sh`**,
not a procedure to remember:

```
  tools/prerender_build.sh demo_gamepad MCU_TYPE=PY32F003x4
```

It refuses to start if `usb_config.h` does not contain
`#include "usb_prerender.inc"` — the one line of integration, which has to be
inside the `INSTANCE_DESCRIPTORS` block because that is the only place the
descriptor symbols are in scope. It seeds an empty table so pass 1 links,
generates from pass 1's image, rebuilds, and **runs `--verify` as the last
step of the build**, so a stale or unlinked table is a build failure rather
than a silent loss of the cycles this exists to save. Both failure modes were
tested rather than asserted:

```
  no #include            prerender_build: usb_config.h does not include
                         usb_prerender.inc  ...                    exit 1
  #include, no table     prerender_gen: usb_pr_count is not in
                         Build/demo_gamepad.elf - the generated table
                         was not linked in                          exit 1
  correct build          prerender_gen: verified 27 records         exit 0
```

Run end to end from a clean tree on `demo_gamepad`, `PY32F003x4`: 27 records,
`usb_pr_keys` / `usb_pr_recs` / `usb_pr_count` linked in flash at
`0x08001f98` / `0x08001ec0` / `0x08001ebc` — not the weak zero — and RAM
416 B, FLASH 8636 B. Removing only the `#include` gives 8072 B, so **the table
itself is 564 B** on this descriptor set with the current engines. (§7.2's
668 B is a different number: the whole feature, lookup code included, at the
commit that introduced it.)

One thing this section previously described but the branch could not do:
`tools/prerender.py`, the renderer both `prerender_gen.py` and
`prerender_check.py` import, **was never committed**. Every command above
failed with `ModuleNotFoundError` on a fresh checkout. It is committed now.
The lesson is the one this project keeps re-learning in a new costume: a
procedure that has only ever been described is not a procedure that works, and
running it once from a clean tree is what tells the difference.

`prerender_check.py` needs the `unicorn` python package (`pip install
unicorn`) and `arm-none-eabi-gcc`. Both are build-time only; nothing here is
on the device.

## 7. The ledger

### 7.1 Cycles, measured by execution

`tools/prerender_check.py` phase 3, flash-resident, `USB_RX_CHECK=CRC16`:

| the ISR after the host's ACK spends | cycles | µs @ 24 MHz |
|---|---|---|
| `usb_in_render`, 8-byte payload, no table | **964** | **40.2** |
| `usb_in_render`, zero-length, no table | 297 | 12.4 |
| pre-rendered **hit**, worst over 25 entries | **208** | **8.7** |
| pre-rendered hit, best | 144 | 6.0 |
| a **miss**: the search, then the full render | 1033 | 43.0 |

964 against `design_b_in.md` §10's 956, arrived at independently and on paper,
is a 0.8 % agreement and both stand.

**The hit does not depend on the payload length**, because there is no CRC16
and no stuffing left to do; the 144..208 spread is entirely the binary
search's depth, about 16 cycles a probe. The dynamic path is the opposite —
it is almost all length:

```
  payload bytes  0    1    2    3    4    5    6    7    8
  cycles       297  383  466  546  629  712  795  878  961     (DATA1)
```

Reading the gamepad demo's whole descriptor set — the 25 packets of §3, at
their real lengths and toggles:

```
  rendered at run time    21 534 cycles   897 us
  pre-rendered, worst      5 200 cycles   217 us     (25 x 208)
                          --------------  --------
  saved                   16 334 cycles   681 us
```

and each control transfer's status stage goes from 297 to at most 208 on top
of that.

### 7.2 Flash and RAM

`INTEGRATION_BUILD.md`'s recipe, PY32F003x4, gamepad demo, calibration on,
`find .. -name '*.o' -delete` before every build:

| | RAM | FLASH |
|---|---|---|
| `design_b_in.md` §12 baseline | 416 B | 7928 B |
| the `+24 src` indirection only (`USB_PRERENDER=0`) | 416 B | **7924 B** |
| the lookup compiled in, no table generated | 416 B | 8032 B |
| **the gamepad demo's 27 records linked** | **416 B** | **8596 B** |

* **RAM: zero.** The `src` word fits in the arm record's existing slack — the
  record is 32 B because the index is a shift, and `bits[13]` at +8 leaves
  +21..+31 unused. `TI_REC_SIZE` does not move, so neither does the 64 B of
  `usb_in_arm`.
* **The indirection is 4 B smaller than what it replaced**, because `Q1` lost
  two instructions and gained one.
* **The lookup is 108 B** of flash.
* **The table is 564 B** as linked: 233 B of stream, 324 B of key and record
  arrays, 7 B of alignment. The arrays are bigger than the data they index,
  which is what a 12-byte-per-entry table over 10-byte streams looks like.
* **Total +668 B of flash for −681 µs of enumeration and −32 µs on every
  static IN transaction.**

### 7.3 Is that a good trade? The arithmetic, without a thumb on it

It buys **no RAM at all**, and it was worth checking whether it could. It
cannot: `bits[13]` in the RAM record is needed by any endpoint that renders
dynamically, and both of the gamepad demo's do — EP1 for HID reports, EP0 for
truncated descriptor reads. The two records cannot share one staging buffer
either, because both endpoints can be armed at the same time. So the RAM line
of this ledger is 0 B, and any claim that pre-rendering saves RAM would be
wrong.

It costs **668 B, 4.2 % of a 16 KB part**, taking the image from 48.4 % to
52.5 % full. On the F003x4 that is affordable and on a smaller part it is not:
a descriptor set is not small, and `string2` alone — a 21-character product
string — is 6 of the 25 packets and 60 B of the stream.

What it buys is **the ISR after the host's ACK, which is the whole of
`design_b_in.md` §10's open risk**: 44 µs of work in the gap between two
transactions, against ~73 µs of wire time for an 8-byte DATA packet at low
speed. 8.7 µs is comfortably inside any inter-transaction gap; 44 µs was the
number nobody had measured a host against. That is the reason to spend the
flash, and it is a different reason from the cycle total.

## 8. The trailing stuffed zero, and what pre-rendering does about it

USB 2.0 §7.1.9: *"If required by the bit stuffing rules, a zero bit will be
inserted even if it is the last bit before the end-of-packet signal."*

`T_TX` **defers** a stuffed zero to the head of the next nibble — that is what
its seventh row, state 6, is for — and at the end of a packet there is no next
nibble. `design_b_in.md` §11 measured 0.61 % of packets ending that way;
`prerender_check.py` measures **0.68 %** over its 2632 cases, about one in
150.

**A build-time renderer has no "next nibble" problem, because it sees the
whole stream.** `tools/prerender.py` emits the deferred zero at end of
stream, and phase 1 confirms the assembled `usb_in_render` emits exactly the
same bit at `.Lir_tail`. So:

* **the pre-rendered records are correct**, and
* **so were the runtime-rendered ones.** Design B's IN path never had this
  defect; `usb_in_render` handles it explicitly and always did.

Pre-rendering therefore **does not fix anything that Design B's IN direction
had wrong**, and it is worth being exact about that rather than claiming a
win. What had the defect was **`engine16_tx.S`'s own transmit chain**, which
is a different chain: its dispatch row 0 went to `usb_tx_eop` the moment the
source was exhausted and nothing anywhere in it tested the stuff state. That
chain is untouched *here*; it is fixed separately, in `engine16_tx.md` §6.3,
for zero cycles and 68 B.

One measurement worth having: **none of the gamepad demo's 27 packets ends in
a stuffed zero.** The defect is real at 1 in 150 over arbitrary payloads, but
this descriptor set does not contain a case, so it would never have shown up
in enumeration — only in HID reports, which is exactly the traffic that is
*not* pre-rendered.

## 9. What is not built

* **Anything on hardware.** No part of this has been on a bus. The emulator
  executes the instructions; it does not model the flash controller's timing,
  which is why the cycle figures are priced out of `engine16_cyc.py`'s
  measured table rather than counted by the emulator.
* **Truncated descriptor reads.** By design; §3.
* **A cheaper miss.** The 69-cycle search is paid by every dynamic HID
  report. A range test on the key would cut it to about 8 for a RAM pointer,
  at the price of two more generated symbols.
* **A cheaper hit.** 208 is 144 for the shallowest lookup — prologue, the
  five checks, one probe and the stores — plus 64 for the four further
  probes, i.e. 16 a probe, both measured. Remembering the last hit's index
  would find the next
  chunk of a multi-packet descriptor in one probe rather than five, since
  consecutive chunks are consecutive keys — about 55 cycles, for 4 B of RAM
  and a cache that has to be invalidated correctly. Not built.
* **`engine16_tx.S`'s trailing stuff bit.** §8. Unchanged, and this does not
  route around it either: it is a different chain.
* **Anything that needs `rv003usb.c` edited.** The seam of
  `ENGINE16_SPEC.md` §4 is untouched, `usb_handle_user_in_request` is
  untouched, and the table is keyed on the pointer precisely so that it can
  stay that way.
