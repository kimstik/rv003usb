#!/usr/bin/env python3
"""usb_enum_sim.py - a whole USB enumeration, executed.

Everything else in this tree checks a PIECE.  engine16_rx_bus.py decodes one
DATA0 on the assembled receiver; prerender_check.py runs usb_in_render;
engine16_coverage.py measures which instructions have ever executed.  None of
them has ever run the STACK: the real rv003usb.c control-transfer state
machine, driven by real tokens, answering onto a real wire.

On 2026-09-08 an inverted branch in the receiver's SYNC phase lock (b729cff)
was found to make the engine unable to decode a single packet.  It survived
because every model in the tree started downstream of it.  The generalisation
is that a check which shares a source with the artifact proves nothing about
the artifact, so:

  * the HOST side and the REFERENCE DECODER in section 1 are written from
    USB 2.0 (chapters 7 and 8), citing clause numbers, and were written
    without reading the engine's tables.  Section 1 imports nothing.
  * the DEVICE is the assembled, linked image of doc/py32/engine16_merged.S,
    doc/py32/engine16_tx.S and the UNMODIFIED rv003usb/rv003usb.c plus the
    demo_gamepad descriptors and IN handler, executed instruction by
    instruction on a Cortex-M0+ emulator with a cycle clock.
  * nothing about the device's answer is read out of its RAM.  Every verdict
    below is taken off the WIRE - reconstructed from the GPIO writes the
    emulator recorded, decoded by section 1's receiver.

Usage:
    python3 tools/usb_enum_sim.py            # the enumeration, and the report
    python3 tools/usb_enum_sim.py --trace    # every packet, both directions
    python3 tools/usb_enum_sim.py --latency N
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


# ==========================================================================
# 1.  THE REFERENCE: USB 2.0 low speed, from the specification.
#
# Nothing in this section reads anything in doc/py32 or rv003usb.  It is the
# host and it is the analyser; if it agreed with the engine because it was
# copied from the engine it would be worthless.  Clause numbers are USB 2.0.
# ==========================================================================

# S8.3.1: a packet identifier is four bits PID<3:0> followed by their ones
# complement, transmitted LSB first, so the byte on the wire is
# PID | (~PID & 0xF) << 4.
def pid_byte(p4):
    return (p4 & 0xF) | ((~p4 & 0xF) << 4)


PID_OUT   = pid_byte(0b0001)    # 0xE1   S8.3.1 Table 8-1
PID_IN    = pid_byte(0b1001)    # 0x69
PID_SOF   = pid_byte(0b0101)    # 0xA5
PID_SETUP = pid_byte(0b1101)    # 0x2D
PID_DATA0 = pid_byte(0b0011)    # 0xC3
PID_DATA1 = pid_byte(0b1011)    # 0x4B
PID_ACK   = pid_byte(0b0010)    # 0xD2
PID_NAK   = pid_byte(0b1010)    # 0x5A
PID_STALL = pid_byte(0b1110)    # 0x1E

PID_NAME = {PID_OUT: "OUT", PID_IN: "IN", PID_SOF: "SOF", PID_SETUP: "SETUP",
            PID_DATA0: "DATA0", PID_DATA1: "DATA1", PID_ACK: "ACK",
            PID_NAK: "NAK", PID_STALL: "STALL"}


# S8.3.5.1: token CRC5, generator polynomial x^5 + x^2 + 1, the remainder is
# seeded all ones and complemented before transmission.  Data is sent LSB
# first (S8.1), so the shift register is run LSB first as well and the
# generator is used in its reflected form: x^5+x^2+1 = 0b00101 -> reflected
# over five bits = 0b10100 = 0x14.
def crc5(value, nbits=11):
    c = 0x1F
    for i in range(nbits):
        bit = (value >> i) & 1
        if (c & 1) ^ bit:
            c = (c >> 1) ^ 0x14
        else:
            c >>= 1
    return c ^ 0x1F


# S8.3.5.2: data CRC16, generator x^16 + x^15 + x^2 + 1 = 0x8005, seeded all
# ones, complemented before transmission, sent LSB first.  Reflected form of
# 0x8005 is 0xA001.
def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c ^ 0xFFFF


# S7.1.9: a zero is inserted after every six consecutive ones in the data,
# counted across the whole packet after the SYNC pattern.
def stuff(bits):
    out, ones = [], 0
    for b in bits:
        if ones == 6:
            out.append(0)
            ones = 0
        out.append(b)
        ones = ones + 1 if b else 0
    if ones == 6:
        out.append(0)
    return out


def unstuff(bits):
    """Inverse of stuff().  Returns (bits, error) - error is set when the
    bit following six ones is not a zero, which S7.1.9 makes a bit stuffing
    violation the receiver must flag."""
    out, ones, err = [], 0, None
    i = 0
    while i < len(bits):
        b = bits[i]
        if ones == 6:
            if b != 0:
                err = "bit stuffing violation at bit %d" % i
                break
            ones = 0
            i += 1
            continue
        out.append(b)
        ones = ones + 1 if b else 0
        i += 1
    return out, err


def lsb_bits(byte):
    return [(byte >> i) & 1 for i in range(8)]        # S8.1, LSB first


SYNC_BITS = [0, 0, 0, 0, 0, 0, 0, 1]                  # S8.2: KJKJKJKK in NRZI

# S7.1.7.1 low speed signalling: J is D- high / D+ low, K is D+ high / D-
# low, SE0 is both low.
J   = (0, 1)
K   = (1, 0)
SE0 = (0, 0)


def nrzi(bits, start=J):
    """S7.1.7.1: a zero is encoded as a transition, a one as no transition."""
    lvl, out = start, []
    for b in bits:
        if b == 0:
            lvl = K if lvl == J else J
        out.append(lvl)
    return out


def packet_levels(pidb, body, with_crc16):
    """A complete packet as a list of bus levels, one per bit time:
    SYNC, PID, body, CRC (when the packet type carries one), then the EOP of
    S7.1.13.2 - SE0 for two bit times, then J for one."""
    payload = list(body)
    if with_crc16:
        c = crc16(bytes(payload))
        payload += [c & 0xFF, (c >> 8) & 0xFF]
    data = []
    for b in [pidb] + payload:
        data += lsb_bits(b)
    return nrzi(SYNC_BITS + stuff(data)) + [SE0, SE0, J]


def token_packet(pidb, addr, endp):
    """S8.4.1: seven address bits then four endpoint bits, LSB first, then
    the CRC5 over those eleven bits."""
    v = (addr & 0x7F) | ((endp & 0xF) << 7)
    c = crc5(v)
    return packet_levels(pidb, [v & 0xFF, ((v >> 8) & 0x07) | (c << 3)], False)


def data_packet(pidb, payload):
    return packet_levels(pidb, list(payload), True)


def handshake_packet(pidb):
    return packet_levels(pidb, [], False)


class DecodeError(Exception):
    pass


def decode(sample):
    """The analyser.  `sample` is a list of bus levels, one per bit time,
    starting with the first bit of SYNC.  Returns a dict describing the
    packet, from S7.1.9, S8.1, S8.2, S8.3.1 and S8.3.5 - and it is the only
    thing in this file allowed to say what the device sent."""
    # find the EOP: two consecutive SE0 bit times (S7.1.13.2)
    n = None
    for i in range(len(sample) - 1):
        if sample[i] == SE0 and sample[i + 1] == SE0:
            n = i
            break
    if n is None:
        raise DecodeError("no EOP: never saw two SE0 bit times")
    body = sample[:n]
    if len(body) < 8:
        raise DecodeError("packet shorter than SYNC (%d bit times)" % len(body))
    # NRZI decode: no transition = 1
    prev = J
    bits = []
    for lvl in body:
        if lvl not in (J, K):
            raise DecodeError("SE1 (both lines high) inside the packet")
        bits.append(1 if lvl == prev else 0)
        prev = lvl
    if bits[:8] != SYNC_BITS:
        raise DecodeError("SYNC is %s, not KJKJKJKK"
                          % "".join(str(b) for b in bits[:8]))
    data, err = unstuff(bits[8:])
    r = {"stuff_error": err, "trailing_bits": len(data) % 8, "eop_at": n}
    by = [sum(data[i + k] << k for k in range(8))
          for i in range(0, len(data) - 7, 8)]
    if not by:
        raise DecodeError("no PID after SYNC")
    r["pid"] = by[0]
    r["pid_ok"] = ((by[0] >> 4) ^ (by[0] & 0xF)) == 0xF
    r["bytes"] = bytes(by)
    kind = by[0] & 3
    if kind == 3 and r["pid_ok"]:                     # DATA
        r["type"] = "data"
        if len(by) < 3:
            r["crc_ok"] = False
            r["payload"] = b""
        else:
            pay = bytes(by[1:-2])
            got = by[-2] | (by[-1] << 8)
            r["payload"] = pay
            r["crc_ok"] = (crc16(pay) == got)
    elif kind == 2 and r["pid_ok"]:
        r["type"] = "handshake"
        r["ok"] = (len(by) == 1)
    elif kind == 1 and r["pid_ok"]:
        r["type"] = "token"
    else:
        r["type"] = "invalid"
    return r


# ==========================================================================
# 2.  THE DEVICE: build the real image.
# ==========================================================================

FLASH_BASE, FLASH_SIZE = 0x08000000, 0x10000
RAM_BASE, RAM_SIZE = 0x20000000, 0x4000
GPIO_BASE = 0x50000400
EXTI_BASE = 0x40021800
IDR_OFS, MODER_OFS, BSRR_OFS = 0x10, 0x00, 0x18
DP_BIT, DM_BIT = 3, 4
MODER_MASK = (3 << (2 * DP_BIT)) | (3 << (2 * DM_BIT))
MODER_OUT = (1 << (2 * DP_BIT)) | (1 << (2 * DM_BIT))
CELL = 16                       # device cycles per bit time at 24 MHz

LDS = """
ENTRY(_start)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
         RAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 16K }
SECTIONS { .text : { *(.text*) *(.rodata*) } > FLASH
           .data : { *(.data*) } > RAM
           .bss  : { *(.bss*) *(COMMON) } > RAM }
"""

STUBS = """
\t.syntax unified
\t.cpu cortex-m0plus
\t.thumb
\t.text
\t.thumb_func
\t.global _start
_start:\tb _start
\t.thumb_func
\t.global py32_hsical_event
py32_hsical_event:\tbx lr
"""


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write(" ".join(cmd) + "\n" + p.stdout + p.stderr)
        raise SystemExit("build step failed")
    return p.stdout


def build(workdir, demo="demo_gamepad"):
    """Assemble both engines the way INTEGRATION_BUILD.md does (.datacode ->
    .text.engine16, flash-resident) and link them against the REAL C layer
    and the REAL descriptors.  The only thing stubbed is the CH32V003 vendor
    header, through tools/sim_shim - see that file."""
    os.makedirs(workdir, exist_ok=True)
    objs = []
    for name, src in (("m", "doc/py32/engine16_merged.S"),
                      ("t", "doc/py32/engine16_tx.S")):
        with open(os.path.join(ROOT, src)) as f:
            txt = f.read().replace(".section .datacode",
                                   ".section .text.engine16")
        p = os.path.join(workdir, name + ".S")
        with open(p, "w") as f:
            f.write(txt)
        objs.append(p)
    p = os.path.join(workdir, "stubs.S")
    with open(p, "w") as f:
        f.write(STUBS)
    objs.append(p)

    inc = ["-I" + os.path.join(ROOT, d)
           for d in ("tools/sim_shim", "lib", "rv003usb", demo)]
    common = ["-mcpu=cortex-m0plus", "-mthumb", "-DUSB_ENGINE16_FLASH=1"] + inc
    out = []
    for s in objs:
        o = s[:-2] + ".o"
        run(["arm-none-eabi-gcc", "-x", "assembler-with-cpp"] + common +
            ["-c", s, "-o", o])
        out.append(o)
    for s in (os.path.join(ROOT, "rv003usb/rv003usb.c"),
              os.path.join(ROOT, demo, demo + ".c")):
        o = os.path.join(workdir, os.path.basename(s)[:-2] + ".o")
        run(["arm-none-eabi-gcc", "-Os", "-ffreestanding", "-fno-builtin"] +
            common + ["-c", s, "-o", o])
        out.append(o)
    ld = os.path.join(workdir, "h.ld")
    with open(ld, "w") as f:
        f.write(LDS)
    elf = os.path.join(workdir, "h.elf")
    run(["arm-none-eabi-ld", "-T", ld] + out + ["-o", elf])
    syms = {}
    for line in run(["arm-none-eabi-nm", elf]).splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return elf, syms


def sections(elf):
    """Every allocated section with contents, at its VMA.  objcopy -O binary
    on .text alone drops .data, and .data is where the demo's IN payload
    lives."""
    out = []
    hdr = run(["arm-none-eabi-objdump", "-h", elf])
    lines = hdr.splitlines()
    for i, line in enumerate(lines):
        p = line.split()
        flags = lines[i + 1] if i + 1 < len(lines) else ""
        if len(p) >= 6 and p[0].isdigit() and "CONTENTS" in flags \
           and "ALLOC" in flags and int(p[2], 16):
            name, vma = p[1], int(p[3], 16)
            b = elf + name.replace("/", "_") + ".bin"
            run(["arm-none-eabi-objcopy", "-O", "binary",
                 "--only-section=" + name, elf, b])
            with open(b, "rb") as f:
                out.append((vma, f.read()))
    return out


def disassemble(elf):
    ins = {}
    for line in run(["arm-none-eabi-objdump", "-d", elf]).splitlines():
        mo = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{4} ?)+)\s+(\S+)\s*(.*)",
                      line)
        if mo:
            ins[int(mo.group(1), 16)] = (
                len(mo.group(2).replace(" ", "")) // 2, mo.group(3),
                mo.group(4).split(";")[0].strip())
    return ins


# ==========================================================================
# 3.  THE MACHINE: emulator, cycle clock, and the two-sided bus.
# ==========================================================================
from engine16_cyc import cost as cyc_cost                          # noqa: E402

try:
    from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE,
                         UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ,
                         UC_HOOK_MEM_WRITE)
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR
except ImportError:
    sys.exit("usb_enum_sim: needs the `unicorn` package (pip install unicorn)")


class Machine:
    def __init__(self, elf, syms):
        self.syms = syms
        self.ins = disassemble(elf)
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(FLASH_BASE, FLASH_SIZE)
        uc.mem_map(RAM_BASE, RAM_SIZE)
        uc.mem_write(RAM_BASE, b"\x00" * RAM_SIZE)      # .bss, once
        for vma, blob in sections(elf):
            uc.mem_write(vma, blob)
        uc.mmio_map(GPIO_BASE & ~0xFFF, 0x1000, self._rd, None, self._wr, None)
        uc.mmio_map(EXTI_BASE & ~0xFFF, 0x1000,
                    lambda *a: 0, None, lambda *a: None, None)
        uc.mmio_map(0xE000E000, 0x1000,
                    lambda *a: 0, None, lambda *a: None, None)
        uc.hook_add(UC_HOOK_CODE, self._code)
        uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._mem)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        uc.reg_write(UC_ARM_REG_SP, RAM_BASE + RAM_SIZE - 0x100)

        self.cyc = 0
        self.pending = None
        # the wire
        self.host_seg = None            # (t0, [levels])
        self.oe = False                 # device output enable (MODER)
        self.odr = [0, 0]               # D+, D- output data
        self.moder = 0
        self.dev = [(0, False, 0, 0)]   # (cycle, oe, dp, dm)
        self.samples = []               # (cycle, level) - every IDR read
        self.pc_trace = set()
        self.stray = []                 # writes to the shim peripheral page

    # ------------------------------------------------------------ the clock
    # Charge the PREVIOUS instruction before the current one runs, so self.cyc
    # is the time at the START of the instruction now executing.  A branch is
    # charged its taken cost only when the next address is not the fall-
    # through.  A load or store is priced by the region it actually touched,
    # not by guessing what its base register held: IOPORT 1, flash 2, RAM 4
    # for flash-resident code (ENGINE16_SPEC.md S2 / FLASH_TIMING.md).
    def _code(self, uc, address, size, ud):
        if self.pending is not None:
            paddr, psz, mnem, ops, region = self.pending
            self.cyc += self._price(mnem, ops, region,
                                    address != paddr + psz)
        sz, mnem, ops = self.ins.get(address, (size, "nop", ""))
        self.pending = (address, sz, mnem, ops, None)
        self.pc_trace.add(address)

    def _price(self, mnem, ops, region, taken):
        flash_regs, ioport_regs = (), ()
        if region is not None:
            mo = re.search(r"\[(\w+)", ops)
            base = mo.group(1).lower() if mo else "r0"
            if region == "flash":
                flash_regs = (base,)
            elif region == "ioport":
                ioport_regs = (base,)
        lo, hi = cyc_cost(mnem, ops, "flash", ioport_regs, flash_regs)
        return hi if taken else lo

    def _mem(self, uc, access, address, size, value, ud):
        if self.pending is not None:
            if FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
                r = "flash"
            elif RAM_BASE <= address < RAM_BASE + RAM_SIZE:
                r = "ram"
            else:
                r = "ioport"
            self.pending = self.pending[:4] + (r,)

    def _unmapped(self, uc, access, address, size, value, ud):
        try:
            uc.mem_map(address & ~0xFFF, 0x1000)
        except Exception:
            pass
        return True

    # -------------------------------------------------------------- the bus
    def host_level(self, t):
        if self.host_seg is None:
            return J
        t0, lv = self.host_seg
        if t < t0:
            return J
        i = int((t - t0) // CELL)
        return lv[i] if i < len(lv) else J

    def line(self, t):
        oe, dp, dm = False, 0, 0
        for c, o, a, b in self.dev:
            if c <= t:
                oe, dp, dm = o, a, b
            else:
                break
        return (dp, dm) if oe else self.host_level(t)

    def _rd(self, uc, offset, size, ud):
        off = offset & 0xFFF
        if off == (GPIO_BASE & 0xFFF) + IDR_OFS:
            dp, dm = self.line(self.cyc)
            self.samples.append((self.cyc, (dp, dm)))
            return (dp << DP_BIT) | (dm << DM_BIT)
        if off == (GPIO_BASE & 0xFFF) + MODER_OFS:
            return self.moder
        return 0

    def _wr(self, uc, offset, size, value, ud):
        off = offset & 0xFFF
        if off == (GPIO_BASE & 0xFFF) + BSRR_OFS:
            for bit, idx in ((DP_BIT, 0), (DM_BIT, 1)):
                if value & (1 << (bit + 16)):
                    self.odr[idx] = 0
                if value & (1 << bit):
                    self.odr[idx] = 1
            self._pin_event()
        elif off == (GPIO_BASE & 0xFFF) + MODER_OFS:
            self.moder = value
            self.oe = bool(value & MODER_MASK)
            self._pin_event()

    def _pin_event(self):
        e = (self.cyc, self.oe, self.odr[0], self.odr[1])
        if self.dev and self.dev[-1][1:] == e[1:]:
            return
        self.dev.append(e)

    # -------------------------------------------------------------- the ISR
    def isr(self, at, limit=400000):
        """Enter usb_rx_engine16 at cycle `at`, as the D- edge interrupt
        would.  Returns (error or None, cycles consumed)."""
        self.cyc = at
        self.pending = None
        stop = self.syms["_start"] & ~1
        self.uc.reg_write(UC_ARM_REG_SP, RAM_BASE + RAM_SIZE - 0x100)
        self.uc.reg_write(UC_ARM_REG_LR, stop | 1)
        try:
            self.uc.emu_start(self.syms["usb_rx_engine16"] | 1, stop, 0, limit)
        except Exception as e:
            return e, self.cyc - at
        return None, self.cyc - at


# ==========================================================================
# 4.  THE HOST.
# ==========================================================================
IPG = 8 * CELL          # inter-packet gap the host leaves, 8 bit times


class Response:
    def __init__(self):
        self.drove = False
        self.pkt = None
        self.err = None
        self.first_edge = None
        self.tau = None
        self.K = None
        self.released = True
        self.final = J
        self.collided = False


class Host:
    def __init__(self, m, latency=16, trace=False):
        self.m = m
        self.t = 1000
        self.latency = latency
        self.trace = trace
        self.log = []
        self.eopstub = {}
        for k in range(8):
            a = m.syms.get("rx_eop%d" % k)
            if a is not None:
                self.eopstub[a & ~1] = k

    def send(self, levels, label):
        """Put one packet on the wire and run the receive ISR over it."""
        m = self.m
        t0 = self.t
        m.host_seg = (t0, levels)
        mark = len(m.dev)
        m.samples = []
        m.pc_trace = set()
        err, spent = m.isr(t0 + self.latency)
        host_end = t0 + CELL * len(levels)

        r = Response()
        r.err = err
        # tau: the first IDR sample of this packet that read SE0
        for c, lv in m.samples:
            if lv == SE0:
                r.tau = c
                break
        for a in m.pc_trace:
            if a in self.eopstub:
                r.K = self.eopstub[a]
        # what the device drove
        ev = m.dev[mark:]
        drives = [e for e in ev if e[1]]
        if drives:
            r.drove = True
            t_oe = drives[0][0]
            if t_oe < host_end - 2 * CELL:
                r.collided = True
            # first edge away from J while driving
            for c, oe, dp, dm in ev:
                if oe and (dp, dm) != J:
                    r.first_edge = c
                    break
            if r.first_edge is not None:
                sample = []
                k = 0
                while k < 400:
                    t = r.first_edge + CELL * k + CELL // 2
                    sample.append(m.line(t))
                    if len(sample) >= 2 and sample[-1] == SE0 \
                       and sample[-2] == SE0:
                        break
                    k += 1
                try:
                    r.pkt = decode(sample)
                except DecodeError as e:
                    r.pkt = {"type": "undecodable", "why": str(e)}
            r.released = not m.dev[-1][1]
            r.final = m.line(m.dev[-1][0] + 1)
            dev_end = m.dev[-1][0]
        else:
            dev_end = 0
        self.t = max(host_end, dev_end) + IPG
        if self.trace:
            self._show(label, levels, r)
        self.log.append((label, r))
        return r

    def _show(self, label, levels, r):
        try:
            h = decode(levels)
            hs = "%s %s" % (PID_NAME.get(h["pid"], "%02X" % h["pid"]),
                            h.get("payload", h["bytes"][1:]).hex())
        except DecodeError as e:
            hs = "<%s>" % e
        line = "  HOST -> %-52s" % (label + ": " + hs)
        if not r.drove:
            print(line + "  device: SILENT")
            return
        p = r.pkt
        if p.get("type") == "undecodable":
            d = "UNDECODABLE (%s)" % p["why"]
        elif p["type"] == "handshake":
            d = PID_NAME.get(p["pid"], "pid %02X" % p["pid"])
        elif p["type"] == "data":
            d = "%s %s%s" % (PID_NAME.get(p["pid"], "pid %02X" % p["pid"]),
                             p["payload"].hex(),
                             "" if p["crc_ok"] else "  CRC BAD")
        else:
            d = "%s pid %02X" % (p["type"], p.get("pid", 0))
        ta = ("tau+%d" % (r.first_edge - r.tau)) if (r.tau and r.first_edge) \
             else "?"
        print(line + "  device: %-28s %s K=%s" % (d, ta, r.K))

    # ---- the four packet kinds ------------------------------------------
    def token(self, pidb, addr, endp, label=None):
        return self.send(token_packet(pidb, addr, endp),
                         label or "%s a%d e%d" % (PID_NAME[pidb], addr, endp))

    def data(self, pidb, payload, label=None):
        return self.send(data_packet(pidb, payload),
                         label or "%s" % PID_NAME[pidb])

    def handshake(self, pidb, label=None):
        return self.send(handshake_packet(pidb), label or PID_NAME[pidb])

    def keepalive(self):
        """S7.1.7.6: on a low-speed segment the host sends an EOP once per
        frame instead of a SOF."""
        return self.send([SE0, SE0, J], "keep-alive EOP")


# ==========================================================================
# 5.  THE ENUMERATION.
#
# The sequence a real host performs, USB 2.0 S9.1.2 and S9.4:
#   GET_DESCRIPTOR(device, 8) at address 0, SET_ADDRESS, GET_DESCRIPTOR
#   (device, 18), GET_DESCRIPTOR(config, 9), GET_DESCRIPTOR(config, full),
#   the string descriptors, SET_CONFIGURATION, then an interrupt IN.
# Every stage is a control transfer of S5.5 / S8.5.3: a SETUP stage, an
# optional data stage of one or more IN or OUT transactions, and a status
# stage in the opposite direction.
# ==========================================================================
class Problem:
    def __init__(self, where, what, detail=""):
        self.where, self.what, self.detail = where, what, detail

    def __str__(self):
        return "%-34s %s%s" % (self.where, self.what,
                               ("\n" + " " * 39 + self.detail)
                               if self.detail else "")


class Enum:
    """A host, plus the bookkeeping that turns 'what came back' into
    'was it right'."""

    def __init__(self, host):
        self.h = host
        self.problems = []
        self.turnarounds = []       # (label, K, tau_delta, kind)
        self.notes = []
        self.retries = 0

    def bad(self, where, what, detail=""):
        self.problems.append(Problem(where, what, detail))

    def record(self, label, r, kind):
        if r.tau is not None and r.first_edge is not None:
            self.turnarounds.append((label, r.K, r.first_edge - r.tau, kind))
        if r.drove:
            if not r.released:
                self.bad(label, "device left its pins driving after the "
                                "response")
            elif r.final != J:
                self.bad(label, "bus left at %s, not J" % ("SE0"
                         if r.final == SE0 else str(r.final)))
            if r.collided:
                self.bad(label, "device began driving while the host was "
                                "still driving the token")
        if r.err is not None:
            self.bad(label, "emulator fault: %r" % (r.err,))

    # ---- primitives -----------------------------------------------------
    def expect_handshake(self, label, r, pidb):
        self.record(label, r, "DATA->handshake")
        if not r.drove:
            self.bad(label, "no response at all (expected %s)"
                     % PID_NAME[pidb])
            return False
        p = r.pkt
        if p.get("type") != "handshake":
            self.bad(label, "expected a handshake, got %s"
                     % p.get("why", p.get("type")))
            return False
        if p["pid"] != pidb:
            self.bad(label, "expected %s, got %s" % (PID_NAME[pidb],
                     PID_NAME.get(p["pid"], "pid %02X" % p["pid"])))
            return False
        return True

    def expect_silence(self, label, r):
        self.record(label, r, "silence")
        if r.drove:
            what = "drove the bus"
            if r.pkt and r.pkt.get("type") == "undecodable":
                what += " (an undecodable packet: %s)" % r.pkt["why"]
            elif r.pkt:
                what += " (%s)" % PID_NAME.get(r.pkt.get("pid"),
                                               r.pkt.get("type"))
            self.bad(label, "expected silence, device " + what)
            return False
        return True

    def in_transaction(self, label, addr, endp, allow_retry=2):
        """One IN transaction, with the retries a real host performs on a
        packet it could not decode (S8.6.4 / S5.5.5 - three attempts)."""
        for attempt in range(allow_retry + 1):
            r = self.h.token(PID_IN, addr, endp,
                             "%s IN a%d e%d" % (label, addr, endp))
            self.record("%s IN a%d e%d" % (label, addr, endp), r, "IN->DATA")
            if not r.drove:
                return None, r          # timeout: host would retry too
            p = r.pkt
            if p.get("type") == "data" and p.get("crc_ok") and p["pid_ok"]:
                return p, r
            self.retries += 1
            if attempt == allow_retry:
                self.bad("%s IN a%d e%d" % (label, addr, endp),
                         "no decodable DATA after %d attempts"
                         % (allow_retry + 1),
                         "last was: %s" % (p.get("why") or
                                           PID_NAME.get(p.get("pid"),
                                                        p.get("type"))))
                return None, r
        return None, r

    # ---- control transfers ----------------------------------------------
    def setup_stage(self, label, addr, req):
        self.h.token(PID_SETUP, addr, 0, label + " SETUP")
        r = self.h.data(PID_DATA0, req, label + " DATA0(setup)")
        self.expect_handshake(label + " setup ACK", r, PID_ACK)
        return r

    def control_in(self, label, addr, req, wLength, expect=None):
        self.setup_stage(label, addr, req)
        got = b""
        toggles = []
        while len(got) < wLength:
            p, r = self.in_transaction(label, addr, 0)
            if p is None:
                break
            toggles.append(p["pid"])
            got += p["payload"]
            self.h.handshake(PID_ACK, label + " host ACK")
            if len(p["payload"]) < 8:
                break
        # S8.6: the data stage of a control read starts with DATA1 and
        # alternates.
        want = [PID_DATA1 if i % 2 == 0 else PID_DATA0
                for i in range(len(toggles))]
        if toggles != want:
            self.bad(label + " data toggle",
                     "sequence was %s" % " ".join(PID_NAME.get(t, "%02X" % t)
                                                  for t in toggles),
                     "S8.6 requires %s" % " ".join(PID_NAME[t] for t in want))
        # status stage: an OUT with a zero-length DATA1, ACKed by the device
        self.h.token(PID_OUT, addr, 0, label + " status OUT")
        r = self.h.data(PID_DATA1, b"", label + " status DATA1()")
        self.expect_handshake(label + " status ACK", r, PID_ACK)
        if expect is not None and got[:len(expect)] != expect:
            self.bad(label, "descriptor bytes differ from the linked image",
                     "wire %s\n%svs   %s" % (got.hex(), " " * 39,
                                             expect.hex()))
        return got

    def control_out_nodata(self, label, addr, req):
        self.setup_stage(label, addr, req)
        # status stage of a no-data control write is an IN returning a
        # zero-length DATA1 (S8.5.3)
        p, r = self.in_transaction(label + " status", addr, 0)
        if p is not None:
            if p["pid"] != PID_DATA1:
                self.bad(label + " status", "status stage returned %s, S8.6 "
                         "requires DATA1"
                         % PID_NAME.get(p["pid"], "%02X" % p["pid"]))
            if p["payload"]:
                self.bad(label + " status", "status stage carried %d bytes, "
                         "must be zero-length" % len(p["payload"]))
            self.h.handshake(PID_ACK, label + " host ACK")
        return p


def setup_packet(bmRequestType, bRequest, wValue, wIndex, wLength):
    return bytes([bmRequestType, bRequest, wValue & 0xFF, wValue >> 8,
                  wIndex & 0xFF, wIndex >> 8, wLength & 0xFF, wLength >> 8])


def descriptors_from_image(elf, syms):
    """The descriptor bytes AS LINKED, read out of the ELF - so the wire
    comparison is against the image, not against a second transcription of
    usb_config.h."""
    out = {}
    secs = sections(elf)

    def at(addr, n):
        for vma, blob in secs:
            if vma <= addr < vma + len(blob):
                return blob[addr - vma:addr - vma + n]
        return None
    for name in ("device_descriptor", "config_descriptor", "gamepad_hid_desc",
                 "string0", "string1", "string2", "string3"):
        a = syms.get(name)
        if a is None:
            continue
        first = at(a, 1)
        out[name] = at(a, first[0]) if first else None
    return out


def enumerate_device(h, elf, syms, e):
    D = descriptors_from_image(elf, syms)
    ADDR = 3

    print("\n--- 0. a low-speed keep-alive EOP (S7.1.7.6) ---")
    r = h.keepalive()
    e.expect_silence("keep-alive EOP", r)

    print("\n--- 1. GET_DESCRIPTOR(device, 8) at the default address ---")
    e.control_in("dev8", 0, setup_packet(0x80, 6, 0x0100, 0, 8), 8,
                 expect=D["device_descriptor"][:8])

    print("\n--- 2. a token for another device, and a bad CRC ---")
    e.expect_silence("SETUP to address 7",
                     h.token(PID_SETUP, 7, 0, "SETUP a7 e0"))
    e.expect_silence("IN to address 7", h.token(PID_IN, 7, 0, "IN a7 e0"))
    e.expect_silence("IN to endpoint 3 (>= ENDPOINTS)",
                     h.token(PID_IN, 0, 3, "IN a0 e3"))
    # a SETUP the device does accept, then a DATA whose CRC16 is wrong: the
    # device must not acknowledge it (S8.5.3.2 / S8.7.3).
    h.token(PID_SETUP, 0, 0, "SETUP a0 e0")
    lv = data_packet(PID_DATA0, setup_packet(0x80, 6, 0x0100, 0, 8))
    bad = corrupt_crc(lv)
    e.expect_silence("DATA0 with a wrong CRC16", h.send(bad, "DATA0 bad CRC"))

    print("\n--- 3. SET_ADDRESS(%d) ---" % ADDR)
    e.control_out_nodata("setaddr", 0, setup_packet(0x00, 5, ADDR, 0, 0))
    e.expect_silence("SETUP at the old address after SET_ADDRESS",
                     h.token(PID_SETUP, 0, 0, "SETUP a0 e0 (stale address)"))

    print("\n--- 4. GET_DESCRIPTOR(device, 18) at the new address ---")
    e.control_in("dev18", ADDR, setup_packet(0x80, 6, 0x0100, 0, 18), 18,
                 expect=D["device_descriptor"])

    print("\n--- 5. GET_DESCRIPTOR(config) ---")
    e.control_in("cfg9", ADDR, setup_packet(0x80, 6, 0x0200, 0, 9), 9,
                 expect=D["config_descriptor"][:9])
    full = len(D["config_descriptor"])
    e.control_in("cfgN", ADDR, setup_packet(0x80, 6, 0x0200, 0, full), full,
                 expect=D["config_descriptor"])

    print("\n--- 6. the string descriptors ---")
    e.control_in("str0", ADDR, setup_packet(0x80, 6, 0x0300, 0, 4), 4,
                 expect=D["string0"])
    for i, nm in ((1, "string1"), (2, "string2"), (3, "string3")):
        n = len(D[nm])
        e.control_in("str%d" % i, ADDR,
                     setup_packet(0x80, 6, 0x0300 + i, 0x0409, n), n,
                     expect=D[nm])

    print("\n--- 7. GET_DESCRIPTOR(HID report) ---")
    n = len(D["gamepad_hid_desc"])
    e.control_in("hidrep", ADDR, setup_packet(0x81, 6, 0x2200, 0, n), n,
                 expect=D["gamepad_hid_desc"])

    print("\n--- 8. SET_CONFIGURATION(1) ---")
    e.control_out_nodata("setcfg", ADDR, setup_packet(0x00, 9, 1, 0, 0))

    print("\n--- 9. the interrupt endpoint ---")
    seen = []
    for i in range(4):
        p, r = e.in_transaction("ep1#%d" % i, ADDR, 1)
        if p is not None:
            seen.append((p["pid"], p["payload"]))
            h.handshake(PID_ACK, "host ACK")
    if seen:
        pids = [s[0] for s in seen]
        want = [pids[0]] + [PID_DATA0 if pids[0] == PID_DATA1 else PID_DATA1,
                            pids[0]][:0]
        alt = all(pids[i] != pids[i + 1] for i in range(len(pids) - 1))
        if not alt:
            e.bad("endpoint 1 toggle",
                  "PIDs were %s - S8.6 requires alternation"
                  % " ".join(PID_NAME.get(p, "%02X" % p) for p in pids))
        for pid, pay in seen:
            if len(pay) != 3:
                e.bad("endpoint 1 payload",
                      "%d bytes, the demo's handler sends 3" % len(pay))
        del want
    return D


def corrupt_crc(levels):
    """Flip one payload bit of an already-encoded DATA packet, re-NRZI it,
    and leave the CRC16 as it was: what a receiver must reject."""
    # decode back to data bits, flip one, re-encode - the packet stays a
    # legal NRZI/stuffed frame whose CRC no longer matches.
    prev = J
    bits = []
    for lvl in levels:
        if lvl == SE0:
            break
        bits.append(1 if lvl == prev else 0)
        prev = lvl
    data, _ = unstuff(bits[8:])
    data[16] ^= 1                      # a bit of the first payload byte
    return nrzi(SYNC_BITS + stuff(data)) + [SE0, SE0, J]


# ==========================================================================
# 6.  THE REPORT.
#
# turnaround.md S2 derives the legal window for the device's first response
# bit, in cycles after tau (the SE0-detecting IDR read):  [tau+60, tau+124].
# S11 claims tau+115 for DATA->ACK, audit_discarded.md F.6 revises that to
# tau+108 and gives tau+95 for IN->DATA.  Nothing had executed either.
# ==========================================================================
FLOOR, DEADLINE = 60, 124


def report(e, h):
    print("\n" + "=" * 74)
    print("TURNAROUND, MEASURED  (cycles from tau, the SE0-detecting IDR "
          "read,\n                       to the first driven J->K edge)")
    print("=" * 74)
    byk = {}
    for label, K, d, kind in e.turnarounds:
        byk.setdefault((kind, K), []).append((d, label))
    if not byk:
        print("  no responses were measured")
    print("  %-16s %3s %8s %8s %5s   %s"
          % ("path", "K", "min", "max", "n", "verdict"))
    worst = {}
    for (kind, K) in sorted(byk, key=lambda x: (x[0], x[1] if x[1] is not None else -1)):
        v = [d for d, _ in byk[(kind, K)]]
        lo, hi = min(v), max(v)
        bad = "OVER DEADLINE" if hi > DEADLINE else (
            "under floor" if lo < FLOOR else "in [%d,%d]" % (FLOOR, DEADLINE))
        print("  %-16s %3s %8s %8s %5d   %s"
              % (kind, K, "tau+%d" % lo, "tau+%d" % hi, len(v), bad))
        w = worst.setdefault(kind, [999, -999])
        w[0] = min(w[0], lo)
        w[1] = max(w[1], hi)
    for kind, (lo, hi) in sorted(worst.items()):
        print("  %-16s over every K: tau+%d .. tau+%d   (%.2f .. %.2f bit "
              "times after SE0->J)" % (kind, lo, hi, (lo - 24) / 16.0,
                                       (hi - 24) / 16.0))

    print("\n" + "=" * 74)
    print("WHAT THE RUN FOUND")
    print("=" * 74)
    if not e.problems:
        print("  no problem detected by the checks this run performs.")
    for p in e.problems:
        print("  * " + str(p))
    print("\n  packets sent by the host      %d" % len(h.log))
    print("  responses the device drove    %d"
          % sum(1 for _, r in h.log if r.drove))
    print("  host retries forced           %d" % e.retries)
    return 1 if e.problems else 0


def main():
    import argparse
    import tempfile
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true",
                    help="print every packet in both directions")
    ap.add_argument("--latency", type=int, default=16,
                    help="cycles from the D- edge to the first ISR "
                         "instruction (M0+ exception entry)")
    ap.add_argument("--sweep-latency", action="store_true")
    ap.add_argument("--keep", default=None, help="keep the build here")
    a = ap.parse_args()

    wd = a.keep or tempfile.mkdtemp(prefix="usbenum.")
    elf, syms = build(wd)
    print("linked %s" % elf)
    for s in ("usb_rx_engine16", "usb_send_data", "usb_in_render",
              "usb_pid_handle_setup", "usb_pid_handle_data",
              "usb_pid_handle_in", "usb_handle_user_in_request",
              "rv003usb_internal_data", "descriptor_list"):
        if s not in syms:
            raise SystemExit("the C layer did not link: %s is missing" % s)
    print("  the REAL C layer is in the image: "
          "usb_pid_handle_data @ %08x, descriptor_list @ %08x"
          % (syms["usb_pid_handle_data"], syms["descriptor_list"]))

    if a.sweep_latency:
        for lat in range(8, 41, 2):
            m = Machine(elf, syms)
            h = Host(m, latency=lat)
            e = Enum(h)
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                enumerate_device(h, elf, syms, e)
            print("latency %2d: %d problems, %d retries"
                  % (lat, len(e.problems), e.retries))
        return 0

    m = Machine(elf, syms)
    h = Host(m, latency=a.latency, trace=a.trace)
    e = Enum(h)
    enumerate_device(h, elf, syms, e)
    return report(e, h)


if __name__ == "__main__":
    sys.exit(main())
