#!/usr/bin/env python3
"""Drive the ASSEMBLED receive engine with a synthesized low-speed USB bus.

Everything else in this tree checks the engine against a model of itself.  This
runs the linked image on an emulator with a clock, and answers the two
questions no source-level model can:

  * where in the 16-cycle bit cell does the engine's sample actually land, over
    the whole range of edge phases the phase lock can see?  (PLAN.md F5/G7:
    a sample too early in the cell after the last data bit reads USB 2.0
    S7.1.9's 260 ns of dribble as a spurious 1 and aborts the frame.)
  * how much clock error does it survive, over the longest packet?

The GPIO is memory-mapped with a read callback, so IDR returns the bus level at
the emulated CYCLE COUNT - which is accumulated per instruction out of
engine16_cyc.py's own measured cost table, the same pricing prerender_check.py
phase 3 uses.  Nothing here reads a cycle count out of a comment.

Usage:  python3 tools/engine16_rx_bus.py [--sweep] [--phases N]
"""
import os
import sys
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from prerender_check import (build, load, disassemble,               # noqa: E402
                             FLASH_BASE, RAM_BASE)
from engine16_cyc import cost as cyc_cost                            # noqa: E402

try:
    from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE,
                         UC_HOOK_MEM_UNMAPPED)
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR
except ImportError:
    sys.exit("engine16_rx_bus: needs the `unicorn` package")

GPIO_BASE = 0x50000400
EXTI_BASE = 0x40021800
IDR_OFS, MODER_OFS, BSRR_OFS = 0x10, 0x00, 0x18
DP_BIT, DM_BIT = 3, 4
CELL = 16                      # device cycles per bit at 24 MHz

J  = 1 << DM_BIT               # low speed: J = D- high, D+ low
K  = 1 << DP_BIT
SE0 = 0

# --------------------------------------------------------------- the wire
def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c

def packet_bits(pid, payload, crc=True):
    """SYNC + PID + payload + CRC16, LSB first, bit-stuffed."""
    body = [pid] + list(payload)
    if crc:
        c = crc16(payload) ^ 0xFFFF
        body += [c & 0xFF, c >> 8]
    raw = [0] * 7 + [1]                     # SYNC, first bit first
    for byte in body:
        raw += [(byte >> i) & 1 for i in range(8)]
    out, ones = [], 0
    for i, b in enumerate(raw):
        if i >= 8 and ones == 6:            # stuffing starts after SYNC
            out.append(0)
            ones = 0
        out.append(b)
        ones = ones + 1 if b else 0
    if ones == 6:
        out.append(0)
    return out

def wire(pid, payload, crc=True):
    """The driven bus level per bit time: NRZI from J, then EOP."""
    lvl, out = 0, []
    for b in packet_bits(pid, payload, crc):
        if b == 0:
            lvl ^= 1
        out.append(K if lvl else J)
    return out + [SE0, SE0, J, J, J, J]

# ------------------------------------------------------------- the harness
class Bus:
    def __init__(self, elf, syms, levels, t0, period, dribble=0.0):
        self.syms, self.levels = syms, levels
        self.t0, self.period, self.dribble = t0, period, dribble
        self.cost = {}
        for addr, (sz, mnem, ops) in disassemble(elf).items():
            lo, hi = cyc_cost(mnem, ops, "flash", ("r7",), ("r4",))
            self.cost[addr] = (sz, lo, hi)
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(FLASH_BASE, 64 * 1024)
        uc.mem_map(RAM_BASE, 16 * 1024)
        base, blob = load(elf)
        uc.mem_write(base, blob)
        uc.mmio_map(GPIO_BASE & ~0xFFF, 0x1000, self._rd, None, self._wr, None)
        uc.mmio_map(EXTI_BASE & ~0xFFF, 0x1000,
                    lambda u, o, s, d: 0, None, lambda *a: None, None)
        self.cyc = 0
        self.samples = []          # (cycle, bit index, offset within the cell)
        self.driven = []           # (cycle, BSRR word) - what WE put on the bus
        self.pending = None
        uc.hook_add(UC_HOOK_CODE, self._code)
        # Any peripheral the engine touches that this harness does not model -
        # the calibration stamp's counter, for instance - is backed on demand
        # with a zero page rather than left to fault.  Reads of it are not
        # part of anything measured here.
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)

    # The clock.  Charge the PREVIOUS instruction before the current one runs,
    # so self.cyc is the time at the START of the instruction now executing -
    # and charge a branch its TAKEN cost only when it was actually taken,
    # which is exactly what "the next address is not the fall-through" says.
    # Pricing every branch at its fall-through cost smears the sample across
    # the whole cell; that was this harness's first wrong answer.
    def _code(self, uc, address, size, ud):
        if self.pending is not None:
            paddr, psz, plo, phi = self.pending
            self.cyc += plo if address == paddr + psz else phi
        sz, lo, hi = self.cost.get(address, (size, 1, 1))
        self.pending = (address, sz, lo, hi)

    def _unmapped(self, uc, access, address, size, value, ud):
        try:
            uc.mem_map(address & ~0xFFF, 0x1000)
        except Exception:
            pass
        return True

    def level_at(self, t):
        if t < self.t0:
            return J
        i = (t - self.t0) / self.period
        idx = int(i)
        if idx >= len(self.levels):
            return J
        # dribble: the last driven bit may be held past its cell boundary
        if self.dribble and idx == len(self.levels) - 6 and \
           (i - idx) * self.period < self.dribble:
            return self.levels[idx - 1]
        return self.levels[idx]

    def _rd(self, uc, offset, size, ud):
        if offset == (GPIO_BASE & 0xFFF) + IDR_OFS:
            t = self.cyc
            i = (t - self.t0) / self.period
            self.samples.append((t, int(i), (i - int(i)) * CELL))
            return self.level_at(t)
        return 0

    def _wr(self, uc, offset, size, value, ud):
        if offset == (GPIO_BASE & 0xFFF) + BSRR_OFS:
            self.driven.append((self.cyc, value))

    def run(self, entry_cyc, limit=200000):
        uc = self.uc
        self.cyc = entry_cyc
        self.pending = None
        uc.mem_write(RAM_BASE, b"\x00" * (4 * 1024))
        uc.reg_write(UC_ARM_REG_SP, RAM_BASE + 16 * 1024 - 0x100)
        stop = self.syms["_start"] & ~1
        uc.reg_write(UC_ARM_REG_LR, stop | 1)
        try:
            uc.emu_start(self.syms["usb_rx_engine16"] | 1, stop, 0, limit)
        except Exception as e:
            return e
        return None

    def decoded(self, n):
        return bytes(self.uc.mem_read(self.syms["usb_rxbuf"] + 2, n))


# ------------------------------------------------------------------- driver
def make(workdir):
    return build(os.path.join(ROOT, "doc/py32/engine16_merged.S"),
                 os.path.join(ROOT, "doc/py32/engine16_tx.S"),
                 workdir, tag="bus")


def one(elf, syms, pid, payload, entry, t0=0.0, ppm=0.0, dribble=0.0):
    lv = wire(pid, payload)
    period = CELL * (1.0 + ppm / 1e6)
    b = Bus(elf, syms, lv, t0, period, dribble)
    err = b.run(entry)
    return b, err, lv


def sweep_offset(elf, syms, entries, phases):
    """The F5/G7 number: where the locked sample sits in the 16-cycle cell,
    over every entry latency and sub-cycle packet phase the lock can see."""
    hist, fails = {}, 0
    pay = bytes(range(1, 9))
    for entry in entries:
        for k in range(phases):
            t0 = k / float(phases)
            lv = wire(0xC3, pay)
            b = Bus(elf, syms, lv, t0, float(CELL))
            b.run(entry)
            buf = bytes(b.uc.mem_read(syms["usb_rxbuf"] + 2, 10))
            if not (buf[0] == 0x80 and buf[1] == 0xC3 and buf[2:] == pay):
                fails += 1
                continue
            for _t, i, o in b.samples:
                if i >= 14:
                    hist[round(o, 1)] = hist.get(round(o, 1), 0) + 1
    return hist, fails


def sweep_ppm(elf, syms, entry, lo, hi, step):
    """How far the device clock may sit from nominal and still decode the
    longest packet.  Positive ppm = the device's cell is longer than the
    host's bit, i.e. the device clock is SLOW."""
    pay = bytes(range(1, 9))
    ok = []
    v = lo
    while v <= hi:
        lv = wire(0xC3, pay)
        b = Bus(elf, syms, lv, 0.0, CELL * (1.0 + v / 1e6))
        b.run(entry)
        buf = bytes(b.uc.mem_read(syms["usb_rxbuf"] + 2, 10))
        ok.append((v, buf[0] == 0x80 and buf[1] == 0xC3 and buf[2:] == pay))
        v += step
    return ok


def main():
    import argparse
    import tempfile
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-lo", type=int, default=12)
    ap.add_argument("--entry-hi", type=int, default=44)
    ap.add_argument("--payload", default="0102030405060708")
    args = ap.parse_args()

    wd = tempfile.mkdtemp(prefix="e16bus.")
    elf, syms = make(wd)
    pay = bytes.fromhex(args.payload)
    print("linked %s   usb_rx_engine16 @ %08x" % (os.path.basename(elf),
                                                  syms["usb_rx_engine16"]))
    ok = 0
    offs = []
    for entry in []:
        b, err, lv = one(elf, syms, 0xC3, pay, entry)
        got = b.decoded(1 + len(pay))
        good = got[0] == 0xC3 and got[1:] == pay
        ok += good
        cell_samples = [o for (_, i, o) in b.samples if 8 <= i < len(lv) - 6]
        if cell_samples:
            offs.append((entry, min(cell_samples), max(cell_samples)))
        print("  entry=%2d  err=%-28s decoded=%s %s"
              % (entry, type(err).__name__ if err else "-",
                 got.hex(), "OK" if good else "**"))
    # 1. the entry-latency window (PLAN.md gate G6)
    lo = hi = None
    for entry in range(4, 80):
        lv = wire(0xC3, bytes(range(1, 9)))
        b = Bus(elf, syms, lv, 0.0, float(CELL))
        b.run(entry)
        buf = bytes(b.uc.mem_read(syms["usb_rxbuf"] + 2, 10))
        good = buf[0] == 0x80 and buf[1] == 0xC3 and buf[2:] == bytes(range(1, 9))
        if good and lo is None:
            lo = entry
        if good:
            hi = entry
    print("G6  entry latency that still decodes: %d..%d cycles "
          "= %.2f..%.2f bit times" % (lo, hi, lo / 16.0, hi / 16.0))

    # 2. where the locked sample lands (PLAN.md F5 / gate G7)
    hist, fails = sweep_offset(elf, syms, range(lo, hi + 1), 8)
    tot = sum(hist.values())
    print("G7  locked sample offset in the 16-cycle cell, over %d entry "
          "latencies x 8 sub-cycle phases:" % (hi - lo + 1))
    for o in sorted(hist):
        print("      offset %5.1f of 16   %6.2f%%" % (o, 100.0 * hist[o] / tot))
    print("      min %.1f  max %.1f   (dribble floor is 7: USB 2.0 S7.1.9's "
          "260 ns = 6.24 cycles at 24 MHz)" % (min(hist), max(hist)))

    # 3. clock tolerance over the longest packet
    for entry in (16,):
        res = sweep_ppm(elf, syms, entry, -40000, 40000, 1000)
        good = [v for v, k in res if k]
        print("clock error that still decodes an 8-byte DATA0: "
              "%+.2f%% .. %+.2f%%" % (min(good) / 1e4, max(good) / 1e4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
