# wg015bflash — host CLI for the WG015 (K1921VG015) USB HID bootloader

Flashes applications through `bootloader_wg015/` over HID feature reports
(VID:PID 1209:B003, bcdDevice **0x0200** — the CLI refuses V003-family
loaders, which report 0x0000 and speak the WCH blob ISA).

## Build

```sh
sudo apt install libhidapi-dev        # hidapi-hidraw
make PREFIX=riscv64-unknown-elf-      # also assembles ../blobs -> blobs.h
```

## Use

```sh
./wg015bflash info                 # identity + SECRET integrity check
./wg015bflash write app.bin        # erase+program+verify @0x80001000
./wg015bflash verify app.bin
./wg015bflash erase 0x80001000 0x4000
./wg015bflash run                  # reset into the app
./wg015bflash -u 64 write app.bin  # 64-byte program unit (R6 hardware test)
```

Addresses below `0x80001000` (the loader's own page) are refused both here
and inside the device-side blobs.

## udev rule (Linux, run without root)

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="b003", MODE="0660", TAG+="uaccess"
```

Save as `/etc/udev/rules.d/70-wg015boot.rules`, then
`sudo udevadm control --reload && sudo udevadm trigger`.

## Pacing

Erase/program blobs run with interrupts off while flash is busy — the device
falls silent for milliseconds (a 4K page erase is the worst case).  The CLI
never keeps a transfer in flight during a blob: it sends the report, sleeps
the expected time (adapted from the rdcycle measurement each blob returns),
then polls with retries that tolerate failed control transfers.

Note: blob images and payload chunks must not contain the magic trailer
0x1234abcd in the last 4 bytes of any 8-byte position — the device would arm
execution early.  (Same property as the V003 loader.)
