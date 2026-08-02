#!/usr/bin/env python3
"""wg015mkdfu.py — prepare a .dfu image for the WG015 (K1921VG015) rv003usb
DFU bootloader (bootloader_wg015_dfu/), python3 stdlib only.

Pipeline (samd11 dfu-bootloader convention, adapted):
  1. pad app.bin to a 4-byte multiple (the loader reads the trailing CRC as
     an aligned word),
  2. patch the 32-bit little-endian TOTAL image length (= padded size + 4
     CRC bytes) into offset 0x10 — the word must be 0 or 0xFFFFFFFF in the
     input (reserved slot), unless --force,
  3. append CRC32 (standard reflected poly 0xEDB88320 == zlib.crc32) over
     the patched image — the loader recomputes this at boot to decide
     "valid app: run it" vs "stay in DFU",
  4. append the standard 16-byte DFU 1.1 suffix (bcdDevice 0x0201,
     idProduct 0xB003, idVendor 0x1209, bcdDFU 0x0100, 'UFD', suffix CRC)
     so stock dfu-util accepts and target-matches the file.

Usage:
  python3 wg015mkdfu.py app.bin [-o app.dfu] [--force]
  python3 wg015mkdfu.py --selfcheck
"""

import argparse
import struct
import sys
import zlib

LENGTH_OFFSET = 0x10
DFU_VID = 0x1209
DFU_PID = 0xB003
DFU_BCDDEVICE = 0x0201  # WG015 DFU loader protocol rev (HID loader = 0x0200)
DFU_BCDDFU = 0x0100
SUFFIX_LEN = 16


def make_image(binary: bytes, force: bool = False) -> bytes:
    """bin -> image with patched length word + appended CRC32 (no suffix)."""
    if len(binary) <= LENGTH_OFFSET + 4:
        raise ValueError("input too small to carry a length word at 0x10")
    # pad to word multiple so the trailing CRC lands word-aligned in flash
    binary = binary + b"\xff" * (-len(binary) % 4)
    (cur,) = struct.unpack_from("<I", binary, LENGTH_OFFSET)
    if cur not in (0, 0xFFFFFFFF) and not force:
        raise ValueError(
            "word at offset 0x10 is 0x%08X (expected 0 or 0xFFFFFFFF); the "
            "app must reserve it for the image length — use --force to "
            "overwrite anyway" % cur
        )
    total = len(binary) + 4  # length INCLUDES the appended 4-byte CRC32
    image = bytearray(binary)
    struct.pack_into("<I", image, LENGTH_OFFSET, total)
    image += struct.pack("<I", zlib.crc32(bytes(image)) & 0xFFFFFFFF)
    return bytes(image)


def add_dfu_suffix(image: bytes) -> bytes:
    """Append the 16-byte DFU 1.1 suffix (spec section 6.2)."""
    suffix = struct.pack(
        "<HHHH3sB",
        DFU_BCDDEVICE,
        DFU_PID,
        DFU_VID,
        DFU_BCDDFU,
        b"UFD",  # ucDfuSignature, reads 'DFU' end-to-start
        SUFFIX_LEN,
    )
    data = image + suffix
    # DFU suffix CRC: same CRC32 process but stored WITHOUT the final
    # complement (dfu-util convention) => zlib.crc32 ^ 0xFFFFFFFF.
    crc = (zlib.crc32(data) ^ 0xFFFFFFFF) & 0xFFFFFFFF
    return data + struct.pack("<I", crc)


def make_dfu(binary: bytes, force: bool = False) -> bytes:
    return add_dfu_suffix(make_image(binary, force))


# ---------------------------------------------------------------------------


def _crc32_bitwise(data: bytes) -> int:
    """Reference implementation of the loader's crc32_range (bitwise,
    reflected 0xEDB88320, init/xorout 0xFFFFFFFF) — used by --selfcheck to
    prove device-side equivalence with zlib.crc32."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1) & 0xFFFFFFFF)
    return crc ^ 0xFFFFFFFF


def selfcheck() -> int:
    failures = []

    def check(name, cond):
        print("  %-52s %s" % (name, "ok" if cond else "FAIL"))
        if not cond:
            failures.append(name)

    for rawlen in (300, 301, 302, 303):  # exercise all pad remainders
        fake = bytearray(range(256)) * 2
        fake = bytearray(fake[:rawlen])
        struct.pack_into("<I", fake, LENGTH_OFFSET, 0)
        dfu = make_dfu(bytes(fake))

        # --- DFU suffix round-trip (what dfu-util validates) ---
        suffix = dfu[-SUFFIX_LEN:]
        bcddev, pid, vid, bcddfu, sig, slen = struct.unpack(
            "<HHHH3sB", suffix[:12]
        )
        (dwcrc,) = struct.unpack("<I", suffix[12:])
        check(
            "suffix ids (len=%d)" % rawlen,
            (bcddev, pid, vid, bcddfu, sig, slen)
            == (DFU_BCDDEVICE, DFU_PID, DFU_VID, DFU_BCDDFU, b"UFD", 16),
        )
        check(
            "suffix CRC (len=%d)" % rawlen,
            dwcrc == (zlib.crc32(dfu[:-4]) ^ 0xFFFFFFFF) & 0xFFFFFFFF,
        )

        # --- device-side view: what bootloader.c app_crc_ok() computes ---
        image = dfu[:-SUFFIX_LEN]
        (total,) = struct.unpack_from("<I", image, LENGTH_OFFSET)
        check("length word == image size (len=%d)" % rawlen, total == len(image))
        check("length word 4-aligned (len=%d)" % rawlen, total % 4 == 0)
        (trailing,) = struct.unpack("<I", image[-4:])
        check(
            "trailing CRC over total-4 bytes (len=%d)" % rawlen,
            trailing == zlib.crc32(image[:-4]) & 0xFFFFFFFF,
        )
        check(
            "bitwise CRC (loader algo) == zlib (len=%d)" % rawlen,
            _crc32_bitwise(image[:-4]) == zlib.crc32(image[:-4]) & 0xFFFFFFFF,
        )

    # --- occupied length slot must be refused without --force ---
    bad = bytearray(64)
    struct.pack_into("<I", bad, LENGTH_OFFSET, 0x12345678)
    try:
        make_dfu(bytes(bad))
        check("nonzero 0x10 word refused", False)
    except ValueError:
        check("nonzero 0x10 word refused", True)
    try:
        make_dfu(bytes(bad), force=True)
        check("--force overrides the refusal", True)
    except ValueError:
        check("--force overrides the refusal", False)

    if failures:
        print("selfcheck FAILED: %d check(s)" % len(failures))
        return 1
    print("selfcheck passed")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build a dfu-util-ready .dfu image for the WG015 "
        "rv003usb DFU bootloader (VID:PID 1209:B003, bcdDevice 0x0201)."
    )
    p.add_argument("input", nargs="?", help="application .bin (linked at 0x80001000)")
    p.add_argument("-o", "--output", help="output .dfu path (default: input with .dfu)")
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite a non-empty length word at offset 0x10",
    )
    p.add_argument(
        "--selfcheck",
        action="store_true",
        help="run the built-in round-trip test and exit",
    )
    args = p.parse_args()

    if args.selfcheck:
        return selfcheck()

    if not args.input:
        p.error("input .bin required (or --selfcheck)")

    with open(args.input, "rb") as f:
        binary = f.read()

    try:
        dfu = make_dfu(binary, args.force)
    except ValueError as err:
        print("error: %s" % err, file=sys.stderr)
        return 1

    out = args.output
    if not out:
        out = (
            args.input[: -len(".bin")] if args.input.endswith(".bin") else args.input
        ) + ".dfu"
    with open(out, "wb") as f:
        f.write(dfu)
    print(
        "%s: %d bytes (app %d + CRC32 + DFU suffix); flash with: dfu-util -D %s"
        % (out, len(dfu), len(binary), out)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
