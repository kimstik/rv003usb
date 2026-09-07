#!/usr/bin/env python3
"""prerender_gen.py - emit the pre-rendered IN records for a firmware's
static descriptor set.

Input is a LINKED elf of the firmware, from which this reads `descriptor_list`
(usb_config.h) and the bytes every entry points at.  Output is a C fragment to
be #included in the translation unit that instantiates the descriptors, so the
table's keys are the descriptor SYMBOLS and the linker resolves them.  Nothing
here bakes in an address: the emitted file survives the relayout that adding it
to the image causes, which is the whole reason it is written this way and not
as a table of absolute addresses.

Chunking follows rv003usb.c exactly.  usb_pid_handle_in sends
    sendnow = e->opaque + (e->count << 3),  tosend = min(8, max_len - offset)
and usb_pid_handle_setup sets toggle_in = 1 with count = 0, so chunk i of a
descriptor read in full is at byte 8*i, is 8 bytes long except possibly the
last, and carries DATA1 when i is even and DATA0 when it is odd.  A host that
asks for a length that truncates a chunk mid-descriptor produces a packet that
is a PREFIX of one of these and is not in the table; that transaction falls
back to usb_in_render, which is why the fallback is not optional.

  usage: prerender_gen.py FIRMWARE.elf [-o out.inc]
"""

import argparse
import struct
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prerender import render, PID_DATA0, PID_DATA1                # noqa: E402


def readelf_symbols(elf):
    out = subprocess.run(["arm-none-eabi-nm", "-S", elf], check=True,
                         capture_output=True, text=True).stdout
    syms = []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 4:
            syms.append((int(p[0], 16), int(p[1], 16), p[3]))
        elif len(p) == 3:
            syms.append((int(p[0], 16), 0, p[2]))
    return syms


def image(elf):
    """[(addr, bytes)] for every allocated section that has contents."""
    hdr = subprocess.run(["arm-none-eabi-objdump", "-h", elf], check=True,
                         capture_output=True, text=True).stdout
    secs = []
    for line in hdr.splitlines():
        p = line.split()
        if len(p) >= 7 and p[0].isdigit() and "LOAD" not in p:
            pass
        if len(p) >= 7 and p[0].isdigit():
            name, size, vma, lma, off = p[1], int(p[2], 16), int(p[3], 16), \
                int(p[4], 16), int(p[5], 16)
            del lma
            if size and name in (".text", ".rodata", ".data"):
                secs.append((name, vma, size, off))
    with open(elf, "rb") as f:
        blob = f.read()
    return [(vma, blob[off:off + size]) for (_, vma, size, off) in secs]


def peek(img, addr, n):
    for base, data in img:
        if base <= addr and addr + n <= base + len(data):
            return data[addr - base:addr - base + n]
    raise KeyError("address %#x not in any loaded section" % addr)


def name_of(syms, addr):
    """(symbol, offset) for a data address.  Sized symbols win; among equal
    candidates the closest below wins."""
    best = None
    for a, size, nm in syms:
        if size and a <= addr < a + size:
            if best is None or a > best[0]:
                best = (a, nm)
    if best is None:                    # no sized symbol: nearest below, but
        for a, size, nm in syms:        # never a boundary marker like _etext
            if a <= addr and not nm.startswith("_e"):
                if best is None or a > best[0]:
                    best = (a, nm)
    if best is None:
        raise KeyError("no symbol covers %#x" % addr)
    return best[1], addr - best[0]


def collect(elf):
    syms = readelf_symbols(elf)
    img = image(elf)
    dl = [s for s in syms if s[2] == "descriptor_list"]
    if not dl:
        raise SystemExit("prerender_gen: no descriptor_list in %s" % elf)
    base, size, _ = dl[0]
    if size % 12:
        raise SystemExit("descriptor_list size %d is not a multiple of 12"
                         % size)
    out = []
    for i in range(size // 12):
        ent = peek(img, base + 12 * i, 12)
        idxval, addr, length = struct.unpack("<IIB", ent[:9])
        data = peek(img, addr, length)
        sym, off = name_of(syms, addr)
        out.append((idxval, sym, off, addr, bytes(data)))
    return out


def chunks(entries):
    """One record per packet of a full-length read, SORTED BY ADDRESS - the
    binary search in usb_in_render requires it.  The addresses are this
    image's, and the image the table is linked into is a different one, so
    --verify re-checks the ordering there rather than trusting it.

    Duplicate keys are dropped: two descriptor_list entries can name the same
    bytes at different lengths, and a binary search cannot see past the first
    of them.  The dropped packet renders at run time."""
    seen = {}
    recs = []
    for idxval, sym, off, base, data in entries:
        n = len(data)
        i = 0
        while True:
            lo = 8 * i
            if lo >= n and i:
                break
            pay = data[lo:lo + 8]
            pid = PID_DATA1 if (i % 2) == 0 else PID_DATA0
            # the cast is not decoration: a descriptor may be declared as a
            # struct (the string descriptors are), and only the byte address
            # of it is a key
            expr = ("(const uint8_t *)&%s + %d" % (sym, off + lo)
                    if off + lo else "(const uint8_t *)&%s" % sym)
            if base + lo not in seen:
                seen[base + lo] = True
                recs.append((expr, len(pay), pid, render(pid, pay), idxval, i,
                             base + lo))
            i += 1
            if lo + 8 >= n:
                break
    recs += zlp()
    recs.sort(key=lambda r: r[6])
    return recs


def zlp():
    """The two zero-length responses, which are static in the strongest
    possible sense: for len == 0 the record is a function of the PID alone,
    so these two entries are correct whatever the source pointer holds.

    Every control transfer ends in one, and rv003usb.c reaches them through
    usb_send_empty(sendtok), which is `mov r3, r0` falling into usb_send_data
    (engine16_tx.S:326-330) - so r0, the key usb_in_render searches on, still
    holds the token, 0x4B or 0xC3.  That is read out of the source, not
    assumed; and if it ever stops being true these two entries simply stop
    being found, because a zero-length miss renders in 297 cycles as before."""
    out = []
    for pid in (PID_DATA1, PID_DATA0):
        out.append(("(const uint8_t *)0x%02X" % pid, 0, pid, render(pid, b""),
                    0, 0, pid))
    return out


HEADER = """\
/* usb_prerender.inc - GENERATED by tools/prerender_gen.py.  Do not edit.
 *
 * The IN arm records for this firmware's static descriptors, rendered at
 * build time: bit-stuffed, CRC16 appended, LSB first, exactly as
 * usb_in_render would have written them at ~956 cycles a packet
 * (doc/py32/design_b_in.md S10).  NRZI is not here - it is the payload
 * cell's own sbcs/ands/eors.
 *
 * tools/prerender_check.py verifies this renderer against the ASSEMBLED
 * usb_in_render by executing it; see doc/py32/prerender.md.
 *
 * Include this ONCE, in the translation unit that instantiates the
 * descriptors (the one that defines INSTANCE_DESCRIPTORS), after
 * usb_config.h.  The keys are descriptor symbols, so the linker resolves
 * them and this file is immune to the relayout it causes.
 */
#include <stdint.h>

struct usb_prerender_rec {
	const uint8_t *bits;	/* the rendered wire-bit stream, in flash   */
	uint8_t len;		/* payload bytes this record answers        */
	uint8_t pid;		/* 0xC3 DATA0 / 0x4B DATA1                  */
	uint8_t groups;		/* whole 8-bit groups of wire bits          */
	uint8_t t;		/* wire bits & 7: the tail cell index       */
};

"""


def emit(recs, out):
    blob = bytearray()
    offs = []
    for e in recs:
        offs.append(len(blob))
        blob += e[3].stream
    blob.append(0x00)       # the payload chain prefetches one group past the
                            # last stored one; this is that group

    w = out.write
    w(HEADER)
    w("static const uint8_t usb_pr_bits[] = {")
    for i, b in enumerate(blob):
        w(("\n\t" if i % 12 == 0 else " ") + "0x%02x," % b)
    w("\n};\n\n")

    w("/* Ascending by address, which is what makes the lookup a binary\n"
      " * search - five probes over %d packets.  A separate u32 array\n"
      " * because a u32 array is indexed by a shift. */\n" % len(recs))
    w("const uint8_t * const usb_pr_keys[] = {\n")
    for expr, ln, pid, r, idxval, ci, _ in recs:
        what = ("zero-length status stage" if ln == 0
                else "%08x chunk %d, %d B" % (idxval, ci, ln))
        w("\t%-44s /* %-28s %s */\n"
          % (expr + ",", what, "DATA1" if pid == PID_DATA1 else "DATA0"))
    w("};\n\n")
    w("const struct usb_prerender_rec usb_pr_recs[] = {\n")
    for i, (expr, ln, pid, r, idxval, ci, _) in enumerate(recs):
        w("\t{ usb_pr_bits + %3d, %d, 0x%02X, %2d, %d },\n"
          % (offs[i], ln, pid, r.groups, r.t))
    w("};\n\n")
    w("const uint8_t usb_pr_count = %d;\n" % len(recs))
    return len(blob)


VERIFY_DOC = """\
verify: the table in the LINKED image still describes the LINKED image.

Two passes are unavoidable - the table's keys are addresses the linker has
not assigned yet when it is generated - and the second pass moves everything
the first pass measured.  That relayout cannot corrupt a packet (a key that
no longer names a descriptor simply misses, and a miss renders at run time),
but it can silently cost the cycles this whole exercise is about, so it is
checked rather than argued.  What is checked, against a FRESH render of the
descriptor bytes as they are in the final image:

  * usb_pr_count matches the number of packets
  * usb_pr_keys is strictly ascending - the binary search's precondition
  * every key is the address of the chunk it is supposed to name
  * every record's len, pid, groups and t match the fresh render
  * every record's stream bytes match the fresh render, byte for byte
"""


def verify(elf):
    syms = readelf_symbols(elf)
    img = image(elf)
    sym = {n: (a, sz) for a, sz, n in syms}
    for need in ("usb_pr_count", "usb_pr_keys", "usb_pr_recs"):
        if need not in sym:
            raise SystemExit("prerender_gen: %s is not in %s - the generated "
                             "table was not linked in" % (need, elf))
    n = peek(img, sym["usb_pr_count"][0], 1)[0]
    keys_a, recs_a = sym["usb_pr_keys"][0], sym["usb_pr_recs"][0]
    keys = list(struct.unpack("<%dI" % n, peek(img, keys_a, 4 * n)))

    want = chunks(collect(elf))
    if len(want) != n:
        raise SystemExit("prerender_gen: image has %d records, this "
                         "descriptor set needs %d" % (n, len(want)))

    # the address every emitted key is supposed to hold, in THIS image
    addr = {}
    for a, size, nm in syms:
        addr.setdefault(nm, a)
    bad = 0
    for i in range(1, n):
        if keys[i] <= keys[i - 1]:
            print("NOT SORTED: usb_pr_keys[%d]=%08x <= [%d]=%08x"
                  % (i, keys[i], i - 1, keys[i - 1]))
            bad += 1
    byaddr = {}
    for expr, ln, pid, r, idxval, ci, key in want:
        if "&" not in expr:                 # the two zero-length entries,
            byaddr[key] = (ln, pid, r)      # whose key is the PID itself
            continue
        nm, _, off = expr.partition(" + ")
        nm = nm.strip().rsplit("&", 1)[1]
        byaddr[addr[nm] + (int(off) if off else 0)] = (ln, pid, r)
    for i in range(n):
        ent = peek(img, recs_a + 8 * i, 8)
        bits_p, ln, pid, groups, t = struct.unpack("<IBBBB", ent)
        exp = byaddr.get(keys[i])
        if exp is None:
            print("STALE KEY: usb_pr_keys[%d]=%08x names no chunk" % (i, keys[i]))
            bad += 1
            continue
        eln, epid, r = exp
        stream = bytes(peek(img, bits_p, len(r.stream)))
        if (ln, pid, groups, t, stream) != (eln, epid, r.groups, r.t, r.stream):
            print("RECORD %d (%08x) disagrees with a fresh render:" % (i, keys[i]))
            print("  image len=%d pid=%02X groups=%d t=%d stream=%s"
                  % (ln, pid, groups, t, stream.hex()))
            print("  fresh len=%d pid=%02X groups=%d t=%d stream=%s"
                  % (eln, epid, r.groups, r.t, r.stream.hex()))
            bad += 1
    if bad:
        raise SystemExit("prerender_gen: %d problems - regenerate against "
                         "this image and rebuild" % bad)
    print("prerender_gen: verified %d records against %s" % (n, elf))
    return 0


def main():
    ap = argparse.ArgumentParser(epilog=VERIFY_DOC,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("elf")
    ap.add_argument("-o", "--out", default="-")
    ap.add_argument("--verify", action="store_true",
                    help="check the table already in this image instead of "
                         "emitting one")
    a = ap.parse_args()
    if a.verify:
        return verify(a.elf)
    entries = collect(a.elf)
    recs = chunks(entries)
    f = sys.stdout if a.out == "-" else open(a.out, "w")
    nblob = emit(recs, f)
    if f is not sys.stdout:
        f.close()
    tbl = 4 * len(recs) + 8 * len(recs)
    sys.stderr.write(
        "prerender_gen: %d descriptors, %d packets (2 of them the "
        "zero-length status stages), %d B of stream + %d B of table = "
        "%d B of flash\n"
        % (len(entries), len(recs), nblob, tbl, nblob + tbl))
    for idxval, sym, off, base, data in entries:
        sys.stderr.write("  %08x  %-20s +%-3d %3d B -> %d packets\n"
                         % (idxval, sym, off, len(data), (len(data) + 7) // 8 or 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
