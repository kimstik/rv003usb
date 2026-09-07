#!/usr/bin/env python3
"""Transmit-chain model for doc/py32/engine16_tx.S.

Transliterates the timed chain instruction for instruction and drives it with
the tables read out of the ASSEMBLED object, not out of a generator.  That is
the point: a model that shares a source with the thing it checks proves
nothing about that source - engine16_tx.md S6.1 records a live defect that
only a direct comparison found.

The emitted NRZI level sequence is compared against an independent encoder
that applies USB 2.0 S7.1.9 in full, including the sentence the chain used to
miss: "If required by the bit stuffing rules, a zero bit will be inserted even
if it is the last bit before the end-of-packet signal."

Usage:  python3 tools/engine16_tx_model.py [engine16_tx.S ...]
"""
import subprocess, sys, os, re, random, struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def assemble(src):
    obj = "/tmp/engine16_tx_model.o"
    subprocess.run(["arm-none-eabi-gcc", "-x", "assembler-with-cpp",
                    "-mcpu=cortex-m0plus", "-mthumb", "-c", src, "-o", obj],
                   check=True)
    return obj

def tables_from_object(obj):
    """Return (bytes, {symbol: offset}) for the block based at usb_tx_tables."""
    nm = subprocess.run(["arm-none-eabi-nm", obj], capture_output=True,
                        text=True, check=True).stdout
    sym = {}
    for line in nm.splitlines():
        f = line.split()
        if len(f) == 3:
            sym[f[2]] = int(f[0], 16)
    base = sym["usb_tx_tables"]
    # the tables live in the engine's own section; dump it and slice
    od = subprocess.run(["arm-none-eabi-objcopy", "-O", "binary",
                         "--only-section", ".datacode", obj, "/tmp/e16tx.bin"],
                        capture_output=True, text=True)
    if od.returncode != 0 or not os.path.getsize("/tmp/e16tx.bin"):
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary",
                        "--only-section", ".text.engine16", obj,
                        "/tmp/e16tx.bin"], check=True)
    blob = open("/tmp/e16tx.bin", "rb").read()
    return blob[base:], sym, base

def relocs(obj):
    """section offset -> symbol, for the R_ARM_ABS32 words of the dispatch
    tables.  gas leaves them unresolved in a .o, which is exactly what makes
    this a check OF the .S: the model reads the same words the linker will."""
    txt = subprocess.run(["arm-none-eabi-objdump", "-r", obj],
                         capture_output=True, text=True, check=True).stdout
    out, sec = {}, None
    for line in txt.splitlines():
        m = re.match(r"RELOCATION RECORDS FOR \[(\S+)\]", line)
        if m:
            sec = m.group(1); continue
        m = re.match(r"([0-9a-f]{8})\s+(\S+)\s+(\S+)", line)
        if m and sec in (".datacode", ".text.engine16"):
            out[int(m.group(1), 16)] = m.group(3).split("+")[0]
    return out

# --------------------------------------------------------------------------
# the reference encoder: SYNC, then bytes LSB-first, stuffed, then NRZI
# --------------------------------------------------------------------------
def crc16_table():
    T = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
        T.append(c)
    return T
T_CRC16_REF = crc16_table()

def reference(pid, payload, want_crc):
    """Return the list of NRZI levels the bus should carry, one per bit time,
    starting from J=0, for everything after the bus turnaround."""
    sync = [0, 0, 0, 0, 0, 0, 0, 1]          # first bit first
    body = [pid] + list(payload)
    if want_crc:
        c = 0xFFFF
        for b in payload:
            c = (c >> 8) ^ T_CRC16_REF[(c ^ b) & 0xFF]
        c = (~c) & 0xFFFF
        body += [c & 0xFF, c >> 8]
    bits = []
    ones = 0
    for b in sync:                            # SYNC is not stuffed, but it
        bits.append(b)                        # does prime the run counter
        ones = ones + 1 if b else 0
    for byte in body:
        for k in range(8):
            if ones == 6:
                bits.append(0)
                ones = 0
            b = (byte >> k) & 1
            bits.append(b)
            ones = ones + 1 if b else 0
    if ones == 6:                             # USB 2.0 S7.1.9, last sentence
        bits.append(0)
    lvl, out = 0, []
    for b in bits:
        if b == 0:
            lvl ^= 1
        out.append(lvl)
    return out

# --------------------------------------------------------------------------
# the model: usb_send_data, transliterated
# --------------------------------------------------------------------------
M32 = 0xFFFFFFFF
def asr31(x):  return M32 if (x >> 31) & 1 else 0
def s32(x):    return x - (1 << 32) if x & 0x80000000 else x

class Chain:
    def __init__(self, blob, sym, base, legacy, reloc):
        self.reloc = reloc
        self.tab = blob
        self.sym = sym
        self.base = base
        self.legacy = legacy          # True: no T_EXH, no over-fetch zeroing
        self.T_TX_BIAS = 0 if legacy else 32
        self.CRC_OFF = 256 if legacy else 288

    def h(self, off):  return struct.unpack_from("<H", self.tab, off)[0]
    def w(self, off):  return struct.unpack_from("<I", self.tab, off)[0]

    def target(self, off):
        """The dispatch words are unresolved relocations in the object, so the
        target is read from the relocation table, not from the word."""
        name = self.reloc.get(self.base + off)
        assert name is not None, "no relocation at table offset %d" % off
        return name

    def run(self, pid, payload, want_crc):
        st = bytearray(16)
        st[0] = 0x80
        st[1] = pid
        for i, b in enumerate(payload):
            st[2 + i] = b
        r8 = len(payload) + (2 if want_crc else 0)   # staging + total - 2
        if not self.legacy:
            st[r8 + 2] = 0            # the fix's one setup instruction
        else:
            st[r8 + 2] = 0            # legacy left it whatever it was; zero is
                                      # the same buffer state, so the only
                                      # difference measured is the dispatch
        r = [0] * 8
        r[3] = 1
        r[4] = 0x80
        r9, r10, r11, r12 = 3, 0xFFFF, (2 << 5), 0
        lvl = 0
        out = []
        cell = "usb_tx_cellS0"
        order = ["usb_tx_cellP0", "usb_tx_cellP1"] + \
                ["usb_tx_cellS%d" % i for i in range(8)]
        guard = 0
        while True:
            guard += 1
            assert guard < 4096, "chain does not terminate"
            if cell == "usb_tx_eop":
                return out
            if cell == "usb_tx_stuff0":
                lvl ^= 1
                out.append(lvl)
                cell = "usb_tx_eop"
                continue
            i = order.index(cell)
            # ---- TXCELL: shift the queue, NRZI, drive ----
            bit = r[4] & 1
            r[4] >>= 1
            if bit == 0:
                lvl ^= 1
            out.append(lvl)
            # ---- the segment this cell carries ----
            if cell.startswith("usb_tx_cellS"):
                k = int(cell[-1])
                if k == 0:
                    r[0] = st[r[3]]; r[3] += 1
                    r[1] = asr31((r[3] - r9) & M32)
                    r2  = asr31((r8 - r[3]) & M32)
                    r[1] = (~(r[1] | r2)) & M32
                elif k == 1:
                    r12 = r[0]
                    r2 = ((r10 ^ r[0]) & 0xFF) << 1
                    r2 += self.CRC_OFF
                    r[0] = self.h(r2) & r[1]
                elif k == 2:
                    r2 = 8 & r[1]
                    r[1] = (r10 >> r2) ^ r[0]
                    r10 = r[1] & M32
                    r[1] = (~r[1]) & M32
                    st[r8] = r[1] & 0xFF
                elif k == 3:
                    r[1] >>= 8
                    st[r8 + 1] = r[1] & 0xFF
                    r[0] = r12
                    r[1] = ((r[0] & 0x0F) << 1) | r11
                    r[1] += self.T_TX_BIAS
                elif k == 4:
                    r[1] = self.h(r[1])
                    r11 = r[1] & 0xFF
                    r[0] = (r[1] >> 9) + 1
                    r[1] = (((r[1] << 23) & M32) >> 31) + 4
                elif k == 5:
                    r2 = (r12 >> 4) << 1
                    r12 = r[1]
                    r[1] = (r11 | r2) + self.T_TX_BIAS
                    r[1] = self.h(r[1])
                elif k == 6:
                    r11 = r[1] & 0xFF
                    r[1] = (r[1] >> 9) << r12
                    r[0] = (r[0] + r[1]) & M32
                    r[1] = r[0] >> 8
                    r12 = r[0]
                    r[0] = (r[3] - r8) & M32
                    if not self.legacy:
                        r[0] = (r[0] - 3) & M32
                elif k == 7:
                    if self.legacy:
                        r[0] = (r[0] - 3) & M32
                        r[1] &= asr31(r[0])
                        r[1] <<= 2
                    else:
                        r2 = (1 if (r[0] >> 31) & 1 else 0) << 3
                        r[1] = (r[1] + r2) << 2
                    cell = self.target(r[1])
                    r[4] = r12
                    continue
            cell = order[i + 1]

# --------------------------------------------------------------------------
def cases():
    out = []
    for pid in (0xC3, 0x4B, 0xD2, 0x2D, 0x69, 0xE1, 0xA5, 0x5A):
        out.append((pid, b"", True))
    for n in range(9):
        out.append((0xC3, bytes(n), True))
        out.append((0xC3, b"\xff" * n, True))
    out.append((0xC3, b"\x7f\xff\xff\xff", True))
    rnd = random.Random(20260907)
    for _ in range(3000):
        n = rnd.randrange(0, 9)
        out.append((rnd.choice([0xC3, 0x4B]),
                    bytes(rnd.randrange(256) for _ in range(n)), True))
    # 1-heavy payloads, where a run of six at the very end is common
    for _ in range(3000):
        n = rnd.randrange(1, 9)
        out.append((0xC3, bytes(rnd.choice([0xFF, 0xFE, 0x7F, 0xFD, 0xBF,
                                            0xF7, 0xFB, 0xEF, 0xDF])
                                for _ in range(n)), True))
    for pid in (0xD2, 0x5A, 0x96):
        out.append((pid, b"", False))
    return out

def main():
    srcs = sys.argv[1:] or [os.path.join(ROOT, "doc/py32/engine16_tx.S")]
    src = srcs[0]
    obj = assemble(src)
    blob, sym, base = tables_from_object(obj)
    legacy = "usb_tx_stuff0" not in sym
    ch = Chain(blob, sym, base, legacy, relocs(obj))
    print("engine: %s%s" % (os.path.basename(src),
                            "   [legacy: no trailing-stuff cell]" if legacy else ""))
    bad = tail = 0
    n = 0
    for pid, pay, crc in cases():
        n += 1
        got = ch.run(pid, pay, crc)
        want = reference(pid, pay, crc)
        if got != want:
            bad += 1
            if len(got) == len(want) - 1 and got == want[:-1]:
                tail += 1
            elif bad - tail <= 3:
                print("  MISMATCH pid=%02x pay=%s crc=%d  len %d vs %d" %
                      (pid, pay.hex(), crc, len(got), len(want)))
    print("cases            %d" % n)
    print("mismatches       %d" % bad)
    print("  of which the missing trailing stuffed zero: %d = %.2f%%" %
          (tail, 100.0 * tail / n))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
