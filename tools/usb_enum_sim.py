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
        self.calls = {}                 # symbol -> times entered
        self.watch = {}
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
        nm = self.watch.get(address)
        if nm:
            self.calls[nm] = self.calls.get(nm, 0) + 1

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
        self.drive_from = None      # cycle the pins became outputs
        self.drive_to = None        # cycle they were released
        self.host_eop = None        # cycle the host's EOP SE0 began


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
        # the host's EOP: the first of the two SE0 bit times at the end
        for i in range(len(levels) - 1):
            if levels[i] == SE0:
                r.host_eop = t0 + CELL * i
                break
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
            r.drive_from = t_oe
            r.drive_to = dev_end
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
# turnaround.md S2 derives the legal window for the device's first response
# bit, in cycles after tau (the SE0-detecting IDR read): [tau+60, tau+124].
# The floor is USB 2.0 S7.1.18's 2-bit-time minimum inter-packet delay and
# the deadline its 6.5-bit-time maximum, both referred to SE0->J.
FLOOR, DEADLINE = 60, 124


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

    # ---- the shape of an aborted drive ----------------------------------
    def hold(self, r):
        """How long the device holds the bus past the point the host is
        entitled to start its next packet.  S7.1.18: the host may begin
        transmitting two bit times after the end of the previous packet's
        EOP, so anything the device is still driving then is a collision."""
        if not r.drove or r.host_eop is None:
            return None
        host_free = r.host_eop + 3 * CELL + 2 * CELL   # EOP is SE0,SE0,J
        return r.drive_to - host_free

    def describe_abort(self, r):
        h = self.hold(r)
        return ("held the bus for %d cycles = %.1f bit times past the "
                "earliest legal start of the host's next packet"
                % (h, h / 16.0)) if h and h > 0 else \
               ("released it %d cycles before the host's next packet may "
                "start" % (-h if h else 0))

    def check_wrong_address_in(self, r):
        label = "IN token addressed to another device"
        self.record(label, r, "abort")
        if not r.drove:
            return
        self.bad(label,
                 "device drove the bus in reply to an IN for address 7",
                 "the Design B IN path enables the pin drivers "
                 "(engine16_merged.S TBARM) and emits eight SYNC bit times "
                 "BEFORE the gate that compares the token halfword, so the "
                 "address is not consulted until the device is already "
                 "driving.  It then " + self.describe_abort(r) +
                 " - on top of whatever the device the token was addressed "
                 "to is driving at the same moment.")

    def check_bad_endpoint_in(self, r):
        label = "IN token for endpoint 3 (>= ENDPOINTS)"
        self.record(label, r, "abort")
        if not r.drove:
            return
        self.bad(label, "device drove the bus for an endpoint it does not "
                        "have", self.describe_abort(r))

    def check_bad_crc(self, r):
        label = "DATA with a wrong CRC16"
        self.record(label, r, "abort")
        if not r.drove:
            self.notes.append("a DATA with a bad CRC16 got no response - "
                              "correct, and the host will retry")
            return
        p = r.pkt or {}
        if p.get("type") == "handshake" and p.get("pid") == PID_ACK:
            self.bad(label, "device ACKED a packet whose CRC16 is wrong")
            return
        self.notes.append(
            "a DATA with a bad CRC16 is answered with a deliberately corrupt "
            "frame, not an ACK (turnaround.md S6.4) - the analyser reads it "
            "as %s.  The device %s."
            % (p.get("why", p.get("type")), self.describe_abort(r)))

    def check_ack_response(self, r):
        label = "the host's ACK at the end of an IN transaction"
        self.record(label, r, "abort")
        if not r.drove:
            self.notes.append("the host's ACK draws no response - correct")
            return
        self.bad(label, "device drove the bus in reply to the host's ACK",
                 "an ACK is SYNC + PID + EOP, so at EOP the PID byte has "
                 "not yet been committed to usb_rxbuf: the pipeline commits "
                 "byte N during byte N+1's cells and there is no byte N+1.  "
                 "EOPSTUB's `ldrb r2,[r9,#1]` therefore reads the PREVIOUS "
                 "packet's PID, which for the ACK that closes an IN "
                 "transaction is the IN token's 0x69, and the IN response "
                 "path is entered for a packet that is not a token.  The "
                 "gate rejects it, but only after TBARM has enabled the "
                 "drivers: the device " + self.describe_abort(r) + ".")

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
    """The descriptor table AS LINKED, read out of the image.

    Not a transcription of usb_config.h: descriptor_list is the array
    rv003usb.c:461 itself searches, so reading it out of flash gives exactly
    the (lIndexValue, address, length) triples the device will answer with -
    including the two cases where the length is NOT the descriptor's own
    first byte (the configuration descriptor, whose wTotalLength is 0x22
    while bLength is 9, and the HID report descriptor, which has no length
    byte at all).  Reading bLength instead silently asks for the wrong number
    of bytes and the comparison then passes on a prefix.

    struct descriptor_list_struct is { uint32_t lIndexValue; const uint8_t
    *addr; uint8_t length; } - 12 bytes with the tail padding."""
    import struct as _s
    secs = sections(elf)

    def at(addr, n):
        for vma, blob in secs:
            if vma <= addr < vma + len(blob):
                return blob[addr - vma:addr - vma + n]
        return None
    base = syms["descriptor_list"]
    end = min((v for v in syms.values() if v > base), default=base + 96)
    out = []
    for off in range(0, end - base, 12):
        rec = at(base + off, 12)
        if rec is None or len(rec) < 12:
            break
        idx, adr, ln = _s.unpack("<IIB", rec[:9])
        if adr == 0:
            break
        out.append((idx, adr, ln, at(adr, ln)))
    return out


def enumerate_device(h, elf, syms, e):
    tbl = descriptors_from_image(elf, syms)
    e.notes.append("descriptor_list as linked: " +
                   ", ".join("%08x/%dB" % (i, n) for i, _, n, _ in tbl))
    by_idx = {i: (n, b) for i, _, n, b in tbl}
    ADDR = 3

    print("\n--- 0. a low-speed keep-alive EOP (S7.1.7.6) ---")
    e.expect_silence("keep-alive EOP", h.keepalive())

    print("\n--- 1. GET_DESCRIPTOR(device, 8) at the default address ---")
    e.control_in("dev8", 0, setup_packet(0x80, 6, 0x0100, 0, 8), 8,
                 expect=by_idx[0x100][1][:8])

    print("\n--- 2. SET_ADDRESS(%d) ---" % ADDR)
    e.control_out_nodata("setaddr", 0, setup_packet(0x00, 5, ADDR, 0, 0))

    print("\n--- 3. the whole descriptor set at the new address ---")
    plan = [("dev18", 0x0100, 0, 18),
            ("cfg9", 0x0200, 0, 9),
            ("cfgN", 0x0200, 0, by_idx[0x200][0]),
            ("str0", 0x0300, 0, by_idx[0x300][0])]
    for i in (1, 2, 3):
        k = 0x04090300 + i
        plan.append(("str%d" % i, 0x0300 + i, 0x0409, by_idx[k][0]))
    plan.append(("hidrep", 0x2200, 0, by_idx[0x2200][0]))
    for label, wv, wi, n in plan:
        key = wv | (wi << 16)
        want = by_idx[key][1][:n]
        e.control_in(label, ADDR,
                     setup_packet(0x81 if wv == 0x2200 else 0x80, 6, wv, wi, n),
                     n, expect=want)

    print("\n--- 4. SET_CONFIGURATION(1) ---")
    e.control_out_nodata("setcfg", ADDR, setup_packet(0x00, 9, 1, 0, 0))

    print("\n--- 5. the interrupt endpoint ---")
    seen = []
    for i in range(4):
        p, r = e.in_transaction("ep1#%d" % i, ADDR, 1)
        if p is not None:
            seen.append((p["pid"], p["payload"]))
            h.handshake(PID_ACK, "host ACK")
    if seen:
        pids = [x[0] for x in seen]
        if pids[0] != PID_DATA0:
            e.bad("endpoint 1", "first IN answered %s; an endpoint that has "
                  "not been given a SET_INTERFACE or a ClearFeature(HALT) "
                  "starts at DATA0 (S8.6.1)"
                  % PID_NAME.get(pids[0], "%02X" % pids[0]))
        if not all(pids[i] != pids[i + 1] for i in range(len(pids) - 1)):
            e.bad("endpoint 1 toggle", "PIDs were %s - S8.6 requires "
                  "alternation"
                  % " ".join(PID_NAME.get(x, "%02X" % x) for x in pids))
        for pid, pay in seen:
            if len(pay) != 3:
                e.bad("endpoint 1 payload", "%d bytes; the demo's handler "
                      "sends 3" % len(pay))
        e.notes.append("endpoint 1 delivered: " +
                       " ".join(x[1].hex() for x in seen) +
                       "  (the demo increments byte 0 once per render)")
    return tbl


def probes(h, elf, syms, e, tbl):
    """The things only an end-to-end run can ask, each one isolated."""
    ADDR = 3
    by_idx = {i: (n, b) for i, _, n, b in tbl}

    print("\n--- P1. a token addressed to another device ---")
    e.expect_silence("SETUP to address 7", h.token(PID_SETUP, 7, 0))
    r = h.token(PID_IN, 7, 0)
    e.check_wrong_address_in(r)
    e.expect_silence("OUT to address 7", h.token(PID_OUT, 7, 0))

    print("\n--- P2. a token for an endpoint the device does not have ---")
    r = h.token(PID_IN, ADDR, 3)
    e.check_bad_endpoint_in(r)

    print("\n--- P3. a DATA whose CRC16 is wrong ---")
    h.token(PID_SETUP, ADDR, 0, "SETUP a%d e0" % ADDR)
    lv = corrupt_crc(data_packet(PID_DATA0,
                                 setup_packet(0x80, 6, 0x0100, 0, 8)))
    e.check_bad_crc(h.send(lv, "DATA0 with a wrong CRC16"))
    # ...and the transfer the corrupted SETUP started must not have taken:
    # the device is still armed with whatever it had.  Re-run a good SETUP so
    # the following probes start from a known state.
    e.control_in("resync", ADDR, setup_packet(0x80, 6, 0x0100, 0, 8), 8,
                 expect=by_idx[0x100][1][:8])

    print("\n--- P4. does the device still answer the DEFAULT address? ---")
    # S9.4.6: after SET_ADDRESS the device responds only to the new address.
    h.token(PID_SETUP, 0, 0, "SETUP a0 e0")
    r = h.data(PID_DATA0, setup_packet(0x80, 6, 0x0100, 0, 8),
               "DATA0(setup) at address 0")
    if r.drove and r.pkt and r.pkt.get("pid") == PID_ACK:
        e.bad("default address after SET_ADDRESS",
              "device ACKed a SETUP addressed to 0 while its address is %d"
              % ADDR,
              "S9.4.6: a device that has been assigned an address responds "
              "only to that address.  On a bus with a second, unaddressed "
              "device both would answer the enumerating token.")
    e.record("SETUP a0 DATA0 after SET_ADDRESS", r, "DATA->handshake")

    print("\n--- P5. a request the device cannot satisfy ---")
    # S9.4.3: a GET_DESCRIPTOR for a descriptor that does not exist is a
    # Request Error and the device must return STALL.
    h.token(PID_SETUP, ADDR, 0)
    r = h.data(PID_DATA0, setup_packet(0x80, 6, 0x0309, 0x0409, 8))
    e.expect_handshake("unknown-descriptor SETUP ACK", r, PID_ACK)
    p, r = e.in_transaction("unknown descriptor", ADDR, 0)
    if p is not None:
        e.bad("unsupported GET_DESCRIPTOR",
              "device answered %s with %d bytes; S9.4.3 requires STALL"
              % (PID_NAME.get(p["pid"], "%02X" % p["pid"]),
                 len(p["payload"])),
              "the C layer leaves e->max_len at 0 and usb_pid_handle_in "
              "sends a zero-length packet, which a host reads as a valid "
              "short transfer rather than as a request error.")
    if p is not None:
        h.handshake(PID_ACK)

    print("\n--- P6. an OUT to the interrupt endpoint, which is IN-only ---")
    h.token(PID_OUT, ADDR, 1)
    r = h.data(PID_DATA0, b"\x01\x02\x03", "DATA0 to endpoint 1 OUT")
    if r.drove and r.pkt and r.pkt.get("pid") == PID_ACK:
        e.bad("OUT to an IN-only endpoint",
              "device ACKed data for endpoint 1, which the configuration "
              "descriptor declares as 0x81 (IN) only",
              "S8.4.5: a transaction to an endpoint that does not exist in "
              "the current configuration must be ignored or STALLed.")
    e.record("OUT ep1 DATA0", r, "DATA->handshake")

    print("\n--- P8. a foreign packet between a SETUP token and its DATA ---")
    # Design B decides "a handshake is owed" from the PREVIOUS packet
    # (turnaround.md S7.2) and the flag is consumed by whatever packet ends
    # next.  On a bus with more than one device that need not be our DATA.
    h.token(PID_SETUP, ADDR, 0, "SETUP a%d e0" % ADDR)
    h.token(PID_SOF, 0, 0, "a token for someone else, in between")
    r = h.data(PID_DATA0, setup_packet(0x80, 6, 0x0100, 0, 8),
               "DATA0(setup), one packet late")
    e.record("DATA->ACK with TB_OWED already spent", r, "DATA->handshake/slow")
    if r.drove and r.pkt and r.pkt.get("pid") == PID_ACK \
       and r.tau is not None and r.first_edge is not None:
        d = r.first_edge - r.tau
        if d > DEADLINE:
            e.bad("DATA->ACK after an intervening packet",
                  "the ACK's first edge is at tau+%d, %d cycles past the "
                  "tau+%d deadline (%.1f bit times after SE0->J against the "
                  "6.5 the specification allows)"
                  % (d, d - DEADLINE, DEADLINE, (d - 24) / 16.0),
                  "TB_OWED is armed by the SETUP token and consumed by the "
                  "END of the next packet, whatever it is.  One foreign "
                  "packet in between spends it, the Design B path does not "
                  "run, and the ACK falls back to usb_send_data - which is "
                  "the ordinary transmit engine, entered after the whole C "
                  "dispatch.  The host times out and retries the transfer.")
    # put the device back in a known state
    e.control_in("resync2", ADDR, setup_packet(0x80, 6, 0x0100, 0, 8), 8)

    print("\n--- P7. what the device does with the host's ACK ---")
    # The host ACK ends every IN transaction.  S8.5.1: the host may begin the
    # next transaction one inter-packet delay (2 bit times) later.
    p, r = e.in_transaction("ackprobe", ADDR, 1)
    if p is not None:
        r2 = h.handshake(PID_ACK, "host ACK after an IN")
        e.check_ack_response(r2)


def stuff_count(pidb, payload):
    """How many zeros S7.1.9 inserts into this packet.  The wire byte
    boundary the receive chain counts from is SYNC + 8k, so the cell K in
    which SE0 lands is exactly this count mod 8 - which is why a real
    enumeration, whose packets are all the same lengths, only ever exercises
    two of the eight EOP stubs."""
    c = crc16(bytes(payload))
    body = [pidb] + list(payload) + [c & 0xFF, (c >> 8) & 0xFF]
    bits = []
    for b in body:
        bits += lsb_bits(b)
    return len(stuff(bits)) - len(bits)


def corrupt_crc(levels):
    """Flip one payload bit of an already-encoded DATA packet and re-encode
    it, leaving the CRC16 as it was.  The frame stays legal NRZI with legal
    bit stuffing; only S8.3.5.2's check fails, which is exactly the error a
    receiver must not acknowledge."""
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


def timing_sweep(elf, syms, latency, per_k=4):
    """The DATA->ACK turnaround at every K.

    turnaround.md S7 gives a different first-edge cycle for each of the eight
    EOP stubs and audit_discarded.md F.6 revises four of them; neither has
    ever been executed.  Payloads are SEARCHED for by stuff count so that all
    eight are reached, not waited for."""
    import random
    rnd = random.Random(20260908)
    want = {k: [] for k in range(8)}
    tries = 0
    while any(len(v) < per_k for v in want.values()) and tries < 60000:
        tries += 1
        n = rnd.randrange(1, 9)
        pay = bytes(rnd.choice((0, 0xFF, 0x7F, 0xFE, 0xF0, 0x3F, 0xFC, 0xCF,
                                rnd.randrange(256))) for _ in range(n))
        k = stuff_count(PID_DATA0, pay) & 7
        if len(want[k]) < per_k and pay not in want[k]:
            want[k].append(pay)
    m = Machine(elf, syms)
    h = Host(m, latency=latency)
    got = {}
    for k in range(8):
        for pay in want[k]:
            h.token(PID_SETUP, 0, 0)
            r = h.data(PID_DATA0, pay)
            if not r.drove or r.tau is None or r.first_edge is None:
                continue
            if r.pkt is None or r.pkt.get("pid") != PID_ACK:
                continue
            got.setdefault(r.K, []).append(r.first_edge - r.tau)
    return ({k: (min(v), max(v), len(v)) for k, v in got.items()},
            {k: len(v) for k, v in want.items()})


def probe_address_timing(elf, syms, latency, e):
    """S9.2.6.3: the new device address takes effect AFTER the status stage
    of SET_ADDRESS completes.  A fresh machine, because this deliberately
    interrupts a control transfer."""
    m = Machine(elf, syms)
    h = Host(m, latency=latency)
    ADDR = 5
    # warm the arm pattern for (address 0, endpoint 0) so a silent answer
    # here means "filtered", not "not yet rendered"
    h.token(PID_SETUP, 0, 0)
    h.data(PID_DATA0, setup_packet(0x80, 6, 0x0100, 0, 8))
    for _ in range(2):
        h.token(PID_IN, 0, 0)
    h.handshake(PID_ACK)
    # SET_ADDRESS, setup stage only
    h.token(PID_SETUP, 0, 0)
    r = h.data(PID_DATA0, setup_packet(0x00, 5, ADDR, 0, 0))
    if not (r.pkt and r.pkt.get("pid") == PID_ACK):
        e.notes.append("address-timing probe: the SET_ADDRESS data stage was "
                       "not ACKed, so the probe proves nothing")
        return
    # the status stage has NOT run yet.  A token for the new address must be
    # ignored until it has.
    r2 = h.token(PID_IN, ADDR, 0)
    early = r2.drove
    # and now the status stage
    h.token(PID_IN, 0, 0)
    if early:
        e.bad("SET_ADDRESS takes effect too early",
              "a token for address %d was answered before the status stage "
              "of SET_ADDRESS had run" % ADDR,
              "rv003usb.c:478 writes ist->my_address inside "
              "usb_pid_handle_data, i.e. during the DATA stage.  S9.2.6.3 "
              "requires the device to keep responding at its old address "
              "until the status stage completes.  It is benign here only "
              "because the engine also accepts address 0 unconditionally, "
              "so the status IN still reaches it.")
    else:
        e.notes.append("SET_ADDRESS: a token for the new address before the "
                       "status stage drew no response")


def report(e, h, ksweep=None, kwant=None):
    print("\n" + "=" * 74)
    print("TURNAROUND, MEASURED")
    print("  cycles from tau - the SE0-detecting IDR read, turnaround.md S2's")
    print("  reference point - to the first driven J->K edge.  The legal")
    print("  window derived there is [tau+%d, tau+%d]." % (FLOOR, DEADLINE))
    print("=" * 74)
    byk = {}
    for label, K, d, kind in e.turnarounds:
        byk.setdefault((kind, K), []).append((d, label))
    print("  %-18s %3s %9s %9s %5s   %s"
          % ("path", "K", "min", "max", "n", "verdict"))
    worst = {}
    for kk in sorted(byk, key=lambda x: (x[0], -1 if x[1] is None else x[1])):
        kind, K = kk
        v = [d for d, _ in byk[kk]]
        lo, hi = min(v), max(v)
        verdict = "OVER THE DEADLINE" if hi > DEADLINE else (
            "under the floor" if lo < FLOOR else "conformant")
        print("  %-18s %3s %9s %9s %5d   %s"
              % (kind, K, "tau+%d" % lo, "tau+%d" % hi, len(v), verdict))
        w = worst.setdefault(kind, [10 ** 9, -10 ** 9])
        w[0], w[1] = min(w[0], lo), max(w[1], hi)
    print()
    for kind, (lo, hi) in sorted(worst.items()):
        print("  %-18s tau+%d .. tau+%d  = %.2f .. %.2f bit times after "
              "SE0->J" % (kind, lo, hi, (lo - 24) / 16.0, (hi - 24) / 16.0))
    if ksweep:
        print("\n  DATA->ACK at every K, forced by choosing payloads whose bit")
        print("  stuffing moves the wire-byte boundary (a real enumeration")
        print("  only ever produces two of the eight):")
        print("    %3s %9s %9s %5s   %-12s %s"
              % ("K", "min", "max", "n", "S7 predicts", "F.6 predicts"))
        pred7 = {1: 111, 2: 100, 3: 91, 4: 81, 5: 71, 6: 60, 7: 63, 0: 103}
        pred6 = {1: 108, 2: 97, 3: 88, 0: 95}
        for K in sorted(ksweep):
            lo, hi, n = ksweep[K]
            print("    %3d %9s %9s %5d   %-12s %s"
                  % (K, "tau+%d" % lo, "tau+%d" % hi, n,
                     "tau+%d" % pred7[K] if K in pred7 else "-",
                     "tau+%d" % pred6[K] if K in pred6 else "-"))
        allv = [v for K in ksweep for v in ksweep[K][:2]]
        print("    worst over every K: tau+%d  (deadline tau+%d), best "
              "tau+%d (floor tau+%d)"
              % (max(allv), DEADLINE, min(allv), FLOOR))
        missing = [k for k in range(8) if k not in ksweep]
        if missing:
            print("    NOT REACHED: K = %s  (payloads found for each: %s)"
                  % (", ".join(str(k) for k in missing),
                     kwant and ", ".join("K%d:%d" % (k, kwant[k])
                                         for k in missing)))

    print("\n" + "=" * 74)
    print("WHAT THE RUN FOUND")
    print("=" * 74)
    if not e.problems:
        print("  no problem detected by the checks this run performs.")
    for i, pr in enumerate(e.problems):
        print("  %d. %s" % (i + 1, pr))
    if e.notes:
        print("\nOBSERVED, NOT A DEFECT")
        for n in e.notes:
            print("  - " + _wrap(n))
    print("\n  packets sent by the host      %d" % len(h.log))
    print("  responses the device drove    %d"
          % sum(1 for _, r in h.log if r.drove))
    print("  host retries forced           %d" % e.retries)
    return 1 if e.problems else 0


def _wrap(t, w=68, ind=" " * 4):
    out, line = [], ""
    for word in t.split():
        if len(line) + len(word) + 1 > w:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    out.append(line)
    return ("\n" + ind).join(out)


def main():
    import argparse
    import tempfile
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true",
                    help="print every packet in both directions")
    ap.add_argument("--latency", type=int, default=16,
                    help="cycles from the D- edge to the first ISR "
                         "instruction (M0+ exception entry)")
    ap.add_argument("--sweep-latency", action="store_true",
                    help="re-run the whole enumeration at every entry latency")
    ap.add_argument("--no-ksweep", action="store_true")
    ap.add_argument("--keep", default=None, help="keep the build here")
    a = ap.parse_args()

    wd = a.keep or tempfile.mkdtemp(prefix="usbenum.")
    elf, syms = build(wd)
    print("linked %s" % elf)
    for sym in ("usb_rx_engine16", "usb_send_data", "usb_in_render",
                "usb_pid_handle_setup", "usb_pid_handle_data",
                "usb_pid_handle_in", "usb_handle_user_in_request",
                "rv003usb_internal_data", "descriptor_list"):
        if sym not in syms:
            raise SystemExit("the C layer did not link: %s is missing" % sym)
    print("  the REAL C layer is in the image: usb_pid_handle_data @ %08x, "
          "descriptor_list @ %08x, usb_handle_user_in_request @ %08x"
          % (syms["usb_pid_handle_data"], syms["descriptor_list"],
             syms["usb_handle_user_in_request"]))

    if a.sweep_latency:
        import io
        import contextlib
        for lat in range(6, 49, 2):
            m = Machine(elf, syms)
            h = Host(m, latency=lat)
            e = Enum(h)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    tbl = enumerate_device(h, elf, syms, e)
                    probes(h, elf, syms, e, tbl)
                bad = len(e.problems)
            except Exception as ex:
                bad = "crashed: %s" % ex
            print("  entry latency %2d cycles: %s problems, %d retries"
                  % (lat, bad, e.retries))
        return 0

    m = Machine(elf, syms)
    h = Host(m, latency=a.latency, trace=a.trace)
    e = Enum(h)
    for nm in ("usb_handle_user_in_request", "usb_pid_handle_in",
               "usb_in_render", "usb_send_data", "usb_pid_handle_data"):
        m.watch[syms[nm] & ~1] = nm
    tbl = enumerate_device(h, elf, syms, e)
    ep1_calls = dict(m.calls)
    probes(h, elf, syms, e, tbl)
    probe_address_timing(elf, syms, a.latency, e)
    e.notes.append("C entry points executed during the enumeration: " +
                   ", ".join("%s x%d" % (k, v)
                             for k, v in sorted(ep1_calls.items())))
    ks = kw = None
    if not a.no_ksweep:
        ks, kw = timing_sweep(elf, syms, a.latency)
    return report(e, h, ks, kw)


if __name__ == "__main__":
    sys.exit(main())
