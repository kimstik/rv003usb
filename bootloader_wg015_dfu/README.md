# bootloader_wg015_dfu — USB DFU bootloader for the K1921VG015 (WG015)

A 4 KB USB DFU 1.1 bootloader on the rv003usb bitbang low-speed USB stack,
modeled on the samdx1-usb-dfu-bootloader concept. Works with **stock
`dfu-util`** — no custom host tool is needed to flash, only
`tools/wg015mkdfu.py` to prepare the image.

* Loader: flash page 0, `0x8000_0000`, budget one 4 K page.
* App slot: `0x8000_1000` (`APP_BASE`) — same as `bootloader_wg015` (the HID
  blob loader); the two loaders are interchangeable on a board.
* USB identity: VID:PID `1209:B003`, `bcdDevice 0x0201` (HID loader reports
  `0x0200`, V003-family loaders `0x0000`). Serial string `W15D`.

## Usage

```sh
# build the loader
make PREFIX=riscv64-unknown-elf-      # or riscv-none-elf-

# prepare an app image (app linked at 0x80001000, reserving a word at +0x10)
python3 ../tools/wg015mkdfu.py app.bin          # -> app.dfu

# flash it
dfu-util -D app.dfu

# read back the app region (optional)
dfu-util -U readback.bin
```

`wg015mkdfu.py` pads the .bin to a word multiple, patches the **total image
length** (padded size + 4) into offset `0x10`, appends a CRC32, and appends
the standard 16-byte DFU suffix so dfu-util target-matches the file
(`--selfcheck` runs a built-in round-trip test; `--force` overwrites a
non-empty word at 0x10).

## How entry works

The loader decides at reset, before touching USB:

1. `RTC_REG[0]` is read **one-shot** (always cleared) and honored only when
   `RCU->RSTSTAT` reports a system reset (stale flags after POR are ignored):
   * `WG015_BOOT_FLAG_APP` (0x0AFF10AD) — jump to the app immediately
     (written by the loader itself after a successful manifest).
   * `WG015_BOOT_FLAG_STAY` (0xB00710AD) — stay in DFU (an app writes this
     plus `RCU->RSTSYS = RCU_RSTSYS_MAGIC` to request an update).
2. Otherwise the app is CRC-checked: the word at `APP_BASE+0x10` is the
   total image length (including the trailing 4-byte CRC32, the samd11
   convention); a software CRC32 over `length-4` bytes must match the
   trailing word. Valid → run the app; invalid/erased → stay in DFU.

So a device with a healthy app boots straight into it; DFU mode is entered
by the app's own request (reboot-with-STAY-flag) or automatically when the
app is missing/corrupt (e.g. an interrupted update — the length word's page
is written before the tail pages, but the CRC only passes on a complete
image, so a torn download safely lands back in DFU).

Note: the CRC check runs at every boot; the bitwise (table-less) CRC costs
roughly 160 cycles/byte at 48 MHz — ~0.1 s per 32 KB of app.

## Download internals (why it looks like the samd11 loader)

DFU state is answered from a premade 6-byte GETSTATUS buffer. A 64-byte
`DFU_DNLOAD` block is captured over EP0 (control-OUT) into RAM; the
following `DFU_GETSTATUS` answers **dfuDNBUSY** with `bwPollTimeout` = 50 ms
when the block starts a 4 K page (erase + program) or 8 ms (program only),
and arms the deferred flash op. The main loop then waits ~3 ms of quiet bus,
masks interrupts and runs the flash routine **from TCM-B RAM** — the
K1921VG015 returns garbage on any flash read while program/erase is busy, so
nothing may fetch from flash until `STAT.BUSY` clears. Writes go as 4 x
16-byte program units per РП А.4 (`ADDR`, `DATA0..3`, `CMD=0xC0DE0002`,
≥5 NOPs, poll BUSY). Addresses below `APP_BASE` or past flash end are
refused with `errADDRESS` — the loader can never overwrite itself.

`DFU_UPLOAD` streams flash contents back directly (legal outside flash
ops). `DFU_DETACH` is accepted and ignored; `DFU_CLRSTATUS`/`DFU_ABORT`
reset to dfuIDLE.

The zero-length `DFU_DNLOAD` (manifest) makes the main loop re-verify the
app CRC: on success it writes `WG015_BOOT_FLAG_APP` and issues a system
reset (the DPU pull-up drops, the host re-enumerates the app); on failure it
parks in `dfuERROR`/`errVERIFY`. The loader does not claim manifestation
tolerance, so dfu-util's complaint about the device vanishing after
download is expected and harmless.

## Differences vs the HID loader (`bootloader_wg015`)

| | `bootloader_wg015` (HID) | `bootloader_wg015_dfu` |
|---|---|---|
| Host tool | custom `wg015hostcli` (hidapi) | stock `dfu-util` |
| Protocol | HID feature reports carrying executable blobs | DFU 1.1 over EP0 |
| Flash code | host-supplied blobs run in TCM-A scratchpad | fixed routine in loader, TCM-B |
| Entry | 5 s timeout, then boot app if present | no timeout: CRC-valid app boots at once |
| App validity | first-word sanity only | CRC32 (length word @+0x10) |
| SECRET word / scratchpad ABI | yes | none |
| bcdDevice | 0x0200 | 0x0201 |
| Endpoints | EP0 + dummy interrupt IN | EP0 only |

Both use the same `APP_BASE` and the same `RTC_REG[0]` boot-flag contract,
so apps built for one work under the other — but only the DFU loader
*requires* the length word at `+0x10` (under the HID loader the patched
word is simply harmless).

## udev note (Linux)

dfu-util needs write access to the device. Either run it with sudo, or
install a rule, e.g. `/etc/udev/rules.d/70-wg015-dfu.rules`:

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="b003", MODE="0664", GROUP="plugdev", TAG+="uaccess"
```

then `sudo udevadm control --reload && sudo udevadm trigger`.

## Verification hooks

* `make` reports the flash budget (`--print-memory-usage`); the loader must
  stay ≤ 4096 B.
* `bootloader.lst`: `flash_write_block` must be at a `0x4002xxxx` (TCM-B)
  address and contain no calls/loads outside TCM-B + the flash controller.
* `python3 ../tools/wg015mkdfu.py --selfcheck` round-trips the image and
  suffix math.
