#!/usr/bin/env python3
"""hsical_sim.py - run the clock servo against a clock that is actually wrong.

`rv003usb/py32/py32_hsical.c` is the only thing that stands between a
crystal-less PY32 and the +-0.203 % receive-clock window `doc/py32/
SAMPLE_POINT.md` measures.  Until this file existed not one instruction of it
had ever been executed: `tools/usb_enum_sim.py` links a stub
(`py32_hsical_event: bx lr`).  `doc/py32/COVERAGE.md` records what that costs.

WHAT IS REAL HERE, AND WHAT IS MODELLED.

  REAL.  The device is the compiled `rv003usb/py32/py32_hsical.c` - unmodified
  source, arm-none-eabi-gcc -Os for cortex-m0plus - executed instruction by
  instruction on a Unicorn Cortex-M0+ with the same cycle-cost table
  `tools/usb_enum_sim.py` uses (`tools/engine16_cyc.py`).  Nothing about the
  servo's arithmetic, its dead band, its acceptance window, its saturation
  escape or its state machine is reimplemented in Python.

  MODELLED, from citations, in `Plant` below.  (a) SysTick: a 24-bit
  down-counter clocked from the core clock, mapped at 0xE000E010.  (b)
  RCC->ICSCR at 0x40021004: writes change the emulated core frequency, which
  is what closes the loop.  The frequency the trim word produces comes from
  Xiamatsu's measurements on live silicon - see Plant's docstring for the
  line numbers.  An open-loop run would prove nothing, so every experiment
  below has the servo's own ICSCR writes feeding back into the rate at which
  SysTick advances.

  MODELLED, from USB 2.0.  The reference: s7.1.7.6's low-speed keep-alive
  EOP, one per frame, and s7.1.11's 1.000 ms +-0.05 % frame interval.  Bus
  traffic is placed on the same time line, because the seam contract
  (py32_hsical.c "THE SEAM") says the ISR calls the servo on EVERY interrupt.

Usage:
    python3 tools/hsical_sim.py              # every experiment, tables
    python3 tools/hsical_sim.py --selftest   # the plant/hookup checks only
    python3 tools/hsical_sim.py E3           # one experiment by name
"""
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from engine16_cyc import cost as cyc_cost                          # noqa: E402

try:
    from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE,
                         UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ,
                         UC_HOOK_MEM_WRITE)
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0
except ImportError:
    sys.exit("hsical_sim: needs the `unicorn` package (pip install unicorn)")


# ==========================================================================
# 1.  THE PLANT: what an ICSCR write actually does to the clock.
# ==========================================================================
class Plant:
    """HSI frequency as a function of RCC->ICSCR.

    EVERY number here is a measurement on live silicon by Xiamatsu, quoted in
    doc/py32/CHIP_FACTS_XIAMATSU.md s2 and reproduced from the upstream
    READMEs.  None of it is chosen to make the servo look good; where the
    documents are silent (the shape of the curve between the two measured
    endpoints) the harness says so and `--sweep-shape` tests the alternative.

    HSI_TRIM[12:0] is one flat field in CMSIS (py32f002bx5.h:2241-2242).  The
    split it is USED as is a measurement:

        TRIM_H = bits 12:9, TRIM_L = bits 8:0            xm_002b.md:224-227
        TRIM_L = 0x000 with TRIM_H = 0 gives the band minimum,
        TRIM_L = 0x1FF with TRIM_H = 0 gives "max1"      xm_002b.md:228-232
        TRIM_H adds a percentage on top of that whole span:
            0b0001 +4 %   0b0010 +9 %   0b0100 +19 %  0b0101 +25 %
            0b0110 +33 %  0b0111 +41 %  0b1000 +50 %  0b1001 +60 %
            0b1010 +70 %  0b1100 +100 % 0b1101 +119 % 0b1110 +137 %
            0b1111 +165 %                               xm_002b.md:233-247
        (F003/F030's table, xm_030.md:404-418, differs only in filling the
        gaps: 0b0011 +14 %, 0b0100 +20 %, 0b0101 +26 %, 0b1011 +84 %,
        0b1101 +118 %, 0b1110 +140 %, 0b1111 +166 %.)

    Band endpoints, in MHz:
        F002B   HSI_FS=100 (24 MHz base): min 12.9, max1 20.6
                HSI_FS=101 (48 MHz base): min 21.7, max1 33.4
                                                    xm_002b.md:255-263
        F003    HSI_FS=100 (24 MHz base): min 10.7, max1 17.1
                (one part, PY32F003L16S6)           xm_030.md:428-433

    LINEARITY IS AN ASSUMPTION, and it is the harness's, not the datasheet's.
    Two endpoints do not describe a curve.  xm_002b.md:228-232 says only that
    TRIM_L "sets the offset from the minimum" over 0x000..0x1FF; the servo's
    own comment calls the sweep "monotone", and doc/py32/rework/
    target_clock.md OQ3 lists monotonicity and LSB weight as UNVERIFIED bench
    items.  So: the default curve is linear between the two measured points,
    and Plant(shape=...) also offers 'sqrt' and 'quad' curves through the SAME
    two endpoints, to show what the servo does when the LSB weight is 1.4x or
    0.7x what it assumed.  A servo whose answer depends on that choice is a
    servo that has not been shown to work.
    """

    # xm_002b.md:233-247 / xm_030.md:404-418.  Index = TRIM_H.
    TRIM_H_PCT_002B = [0, 4, 9, 14, 19, 25, 33, 41, 50, 60, 70, 84,
                       100, 119, 137, 165]
    TRIM_H_PCT_030 = [0, 4, 9, 14, 20, 26, 33, 41, 50, 60, 70, 84,
                      100, 118, 140, 166]
    # (0b0011 and 0b1011 are absent from the F002B list; the F003 values are
    # used to fill them, and no experiment below relies on either.)

    BANDS = {
        # name          FS   min    max1   TRIM_H table
        "F002B_FS100": (4, 12.9e6, 20.6e6, TRIM_H_PCT_002B),
        "F002B_FS101": (5, 21.7e6, 33.4e6, TRIM_H_PCT_002B),
        "F003_FS100":  (4, 10.7e6, 17.1e6, TRIM_H_PCT_030),
    }

    def __init__(self, band="F002B_FS100", shape="linear", spread=1.0):
        self.name = band
        fs, self.fmin, self.fmax1, self.pct = self.BANDS[band]
        self.fs = fs
        self.shape = shape
        self.spread = spread          # part-to-part scale on the whole band

    def _curve(self, l):
        x = l / 511.0
        if self.shape == "linear":
            return x
        if self.shape == "sqrt":      # LSB weight 1.4x nominal at the bottom
            return x ** 0.5
        if self.shape == "quad":      # LSB weight 0.7x nominal at the bottom
            return x ** 2.0
        raise ValueError(self.shape)

    def freq(self, icscr):
        trim = icscr & 0x1FFF
        l = trim & 0x1FF
        h = (trim >> 9) & 0xF
        base = self.fmin + (self.fmax1 - self.fmin) * self._curve(l)
        return base * (1.0 + self.pct[h] / 100.0) * self.spread

    def step_hz(self, icscr):
        """Local LSB weight in Hz, i.e. the cycles-per-millisecond the servo's
        PY32_HSICAL_CYC_PER_STEP is trying to name."""
        a = self.freq(icscr)
        b = self.freq((icscr & ~0x1FF) | min(0x1FF, (icscr & 0x1FF) + 1))
        if (icscr & 0x1FF) == 0x1FF:
            b = self.freq(icscr)
            a = self.freq((icscr & ~0x1FF) | 0x1FE)
        return b - a

    def icscr_for(self, f_target, h=None):
        """The ICSCR word closest to f_target.  Used only to SET UP an
        experiment (a part that starts x % off); the servo never sees it."""
        best, bicscr = None, None
        hs = range(16) if h is None else [h]
        for hh in hs:
            for l in range(512):
                icscr = (self.fs << 13) | (hh << 9) | l
                e = abs(self.freq(icscr) - f_target)
                if best is None or e < best:
                    best, bicscr = e, icscr
        return bicscr


# ==========================================================================
# 2.  THE DEVICE: build and run the real py32_hsical.c.
# ==========================================================================
FLASH_BASE, FLASH_SIZE = 0x08000000, 0x8000
RAM_BASE, RAM_SIZE = 0x20000000, 0x2000
RCC_ICSCR = 0x40021004
SYSTICK_BASE = 0xE000E010

LDS = """
ENTRY(_start)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 32K
         RAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 8K }
SECTIONS { .text : { *(.text*) *(.rodata*) } > FLASH
           .data : { *(.data*) } > RAM
           .bss  : { *(.bss*) *(COMMON) } > RAM }
"""

STUB = """
\t.syntax unified
\t.cpu cortex-m0plus
\t.thumb
\t.text
\t.thumb_func
\t.global _start
_start:\tb _start
"""


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write(" ".join(cmd) + "\n" + p.stdout + p.stderr)
        raise SystemExit("build step failed")
    return p.stdout


def build(workdir, defines=()):
    os.makedirs(workdir, exist_ok=True)
    objs = []
    s = os.path.join(workdir, "stub.S")
    with open(s, "w") as f:
        f.write(STUB)
    common = ["-mcpu=cortex-m0plus", "-mthumb",
              "-I" + os.path.join(ROOT, "tools/sim_shim"),
              "-I" + os.path.join(ROOT, "rv003usb/py32")] + list(defines)
    o = s[:-2] + ".o"
    run(["arm-none-eabi-gcc", "-x", "assembler-with-cpp"] + common +
        ["-c", s, "-o", o])
    objs.append(o)
    c = os.path.join(ROOT, "rv003usb/py32/py32_hsical.c")
    o = os.path.join(workdir, "py32_hsical.o")
    run(["arm-none-eabi-gcc", "-Os", "-ffreestanding", "-fno-builtin"] +
        common + ["-c", c, "-o", o])
    objs.append(o)
    ld = os.path.join(workdir, "h.ld")
    with open(ld, "w") as f:
        f.write(LDS)
    elf = os.path.join(workdir, "h.elf")
    run(["arm-none-eabi-ld", "-T", ld] + objs + ["-o", elf])
    syms = {}
    for line in run(["arm-none-eabi-nm", elf]).splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return elf, syms


def sections(elf):
    out = []
    lines = run(["arm-none-eabi-objdump", "-h", elf]).splitlines()
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


class Servo:
    """The compiled servo, its two MMIO peripherals, and a device cycle
    counter that advances at whatever frequency the servo has trimmed the
    clock to.  Time is kept in HOST seconds; device cycles are the integral
    of the (servo-controlled) frequency over that."""

    def __init__(self, elf, syms, plant, icscr0):
        self.syms = syms
        self.plant = plant
        self.ins = disassemble(elf)
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(FLASH_BASE, FLASH_SIZE)
        uc.mem_map(RAM_BASE, RAM_SIZE)
        uc.mem_write(RAM_BASE, b"\x00" * RAM_SIZE)
        for vma, blob in sections(elf):
            uc.mem_write(vma, blob)
        uc.mmio_map(0x40021000, 0x1000, self._rcc_rd, None, self._rcc_wr, None)
        uc.mmio_map(0xE000E000, 0x1000, self._scs_rd, None, self._scs_wr, None)
        uc.hook_add(UC_HOOK_CODE, self._code)
        uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._mem)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)

        # ---- plant state
        self.icscr = icscr0
        self.f = plant.freq(icscr0)
        self.cyc = 0                 # device cycles since t=0
        self.t = 0.0                 # host seconds
        # ---- SysTick state
        self.st_ctrl = 0
        self.st_load = 0xFFFFFF
        self.st_ref_cyc = 0           # cycle count at which VAL was 0
        self.st_running = False
        # ---- instrumentation
        self.icscr_writes = 0
        self.exec_cycles = 0
        self.pending = None
        self.touched = set()
        self.pc_trace = set()

    # ------------------------------------------------------------- clock
    def advance_to(self, t_host):
        """Move host time forward, integrating device cycles at the current
        frequency.  The frequency only ever changes inside a servo call, so
        piecewise-constant integration is exact."""
        if t_host < self.t:
            raise ValueError("time went backwards")
        self.cyc += (t_host - self.t) * self.f
        self.t = t_host

    def systick_val(self):
        if not self.st_running:
            return 0
        period = self.st_load + 1
        e = int(self.cyc - self.st_ref_cyc) % period
        return (period - e) % period

    # -------------------------------------------------------------- MMIO
    def _scs_rd(self, uc, offset, size, ud):
        a = 0xE000E000 + (offset & 0xFFF)
        self.touched.add(a)
        if a == SYSTICK_BASE + 0x00:
            return self.st_ctrl
        if a == SYSTICK_BASE + 0x04:
            return self.st_load
        if a == SYSTICK_BASE + 0x08:
            return self.systick_val()
        return 0

    def _scs_wr(self, uc, offset, size, value, ud):
        a = 0xE000E000 + (offset & 0xFFF)
        self.touched.add(a)
        if a == SYSTICK_BASE + 0x00:
            self.st_ctrl = value & 7
            self.st_running = bool(value & 1)
        elif a == SYSTICK_BASE + 0x04:
            self.st_load = value & 0xFFFFFF
        elif a == SYSTICK_BASE + 0x08:
            # ARMv6-M: a write of any value clears the counter to 0.
            self.st_ref_cyc = self.cyc

    def _rcc_rd(self, uc, offset, size, ud):
        a = 0x40021000 + (offset & 0xFFF)
        self.touched.add(a)
        return self.icscr if a == RCC_ICSCR else 0

    def _rcc_wr(self, uc, offset, size, value, ud):
        a = 0x40021000 + (offset & 0xFFF)
        self.touched.add(a)
        if a == RCC_ICSCR:
            self.icscr = value & 0xFFFF
            self.f = self.plant.freq(self.icscr)
            self.icscr_writes += 1

    def _unmapped(self, uc, access, address, size, value, ud):
        try:
            uc.mem_map(address & ~0xFFF, 0x1000)
        except Exception:
            pass
        return True

    # ------------------------------------------------- the cycle counter
    def _code(self, uc, address, size, ud):
        if self.pending is not None:
            paddr, psz, mnem, ops, region = self.pending
            n = self._price(mnem, ops, region, address != paddr + psz)
            self.cyc += n
            self.exec_cycles += n
            self.t += n / self.f
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

    # ------------------------------------------------------------- calls
    def call(self, sym, r0=0, limit=200000):
        stop = self.syms["_start"] & ~1
        self.uc.reg_write(UC_ARM_REG_SP, RAM_BASE + RAM_SIZE - 0x100)
        self.uc.reg_write(UC_ARM_REG_LR, stop | 1)
        self.uc.reg_write(UC_ARM_REG_R0, r0 & 0xFFFFFFFF)
        self.pending = None
        c0 = self.cyc
        self.uc.emu_start(self.syms[sym] | 1, stop, 0, limit)
        if self.pending is not None:                    # charge the last one
            paddr, psz, mnem, ops, region = self.pending
            n = self._price(mnem, ops, region, False)
            self.cyc += n
            self.exec_cycles += n
            self.t += n / self.f
            self.pending = None
        return self.cyc - c0

    def ret(self):
        return self.uc.reg_read(UC_ARM_REG_R0)

    # ------------------------------------------------------- convenience
    def init(self):
        self.call("py32_hsical_init")

    def event(self, stamp):
        self.call("py32_hsical_event", stamp)

    def state(self):
        self.call("py32_hsical_state")
        return self.ret()

    def err_pct(self, target=24e6):
        return 100.0 * (self.f - target) / target


# ==========================================================================
# 3.  THE HOST: the reference the servo is designed to measure.
# ==========================================================================
# USB 2.0 s7.1.7.6, "Low-speed Keep-alive": "All hubs must generate a
# low-speed keep-alive strobe, generated at the beginning of the frame, which
# consists of a valid low-speed EOP.  The strobe must be generated at least
# once in each frame in which no other low-speed traffic exists."  That is the
# only periodic event a low-speed device sees, and it is what the servo
# measures.  s7.1.11 fixes the frame interval at 1.000 ms +-0.05 %.
FRAME = 1.0e-3

# Cycles from the D- edge to the SysTick load, on the merged engine: NVIC
# exception entry (fixed on M0+) plus `ldr r2,=ADDR; ldr r2,[r2]` as the first
# two instructions of the handler (engine16_merged.S:830-831).  The CONSTANT
# part cancels in the interval difference; only its variation matters, which
# is why the engine puts the load first (engine16_merged.S:813-828).
ISR_ENTRY_CYC = 18


def deliver(s, t_edge, jitter_cyc=0.0):
    """One USB interrupt at host time t_edge: stamp at ISR entry, servo call
    at ISR exit, exactly as the seam contract specifies."""
    s.advance_to(max(t_edge, s.t))
    d = ISR_ENTRY_CYC + jitter_cyc
    s.cyc += d
    s.t += d / s.f
    stamp = s.systick_val()
    s.event(stamp)
    return stamp


class Trace:
    def __init__(self):
        self.err = []          # frequency error in %, sampled per keep-alive
        self.icscr = []
        self.state = []
        self.writes = []


def make(plant, f0, target=24e6, defines=(), band_h=None, elfcache={}):
    key = tuple(defines)
    if key not in elfcache:
        wd = os.path.join(os.environ.get(
            "TMPDIR", "/tmp"), "hsical_sim_%d" % abs(hash(key)))
        elfcache[key] = build(wd, defines)
    elf, syms = elfcache[key]
    icscr0 = plant.icscr_for(f0, h=band_h)
    s = Servo(elf, syms, plant, icscr0)
    s.init()
    # Re-zero the time base after init so that host time 0 is the first bus
    # edge, not the moment py32_hsical_init() returned.  Without this the
    # first measured interval is short by the cost of init() and the servo
    # takes a spurious kick on frame 2 - a harness artefact, not a defect.
    s.st_ref_cyc -= s.cyc
    s.cyc = 0.0
    s.t = 0.0
    s.exec_cycles = 0
    s.target = target
    return s


def runsim(s, frames, traffic=None, jitter=0.0, frame_tol=0.0, rng=None,
           dropout=(), target=None):
    """Deliver `frames` frames of host events.  `traffic` is a list of offsets
    (seconds) inside each frame at which an extra packet lands - one interrupt
    per host packet, which is what the D- EXTI gives.  `dropout` is a set of
    frame indices in which the hub emits NO keep-alive."""
    tr = Trace()
    target = target or s.target
    t = 0.0
    rng = rng or random.Random(1)
    for n in range(frames):
        if n not in dropout:
            j = rng.uniform(-jitter, jitter) if jitter else 0.0
            deliver(s, t, j)
        for off in (traffic or ()):
            j = rng.uniform(-jitter, jitter) if jitter else 0.0
            deliver(s, t + off, j)
        tr.err.append(100.0 * (s.f - target) / target)
        tr.icscr.append(s.icscr)
        tr.state.append(s.state())
        tr.writes.append(s.icscr_writes)
        t += FRAME * (1.0 + (rng.uniform(-frame_tol, frame_tol)
                             if frame_tol else 0.0))
    return tr


def first_locked(tr):
    for i, st in enumerate(tr.state):
        if st == 2:
            return i + 1        # 1-based frame count
    return None


def settled(tr, win=0.203, tail=50):
    e = tr.err[-tail:]
    return max(abs(x) for x in e) <= win


# ==========================================================================
# 4.  THE EXPERIMENTS.
# ==========================================================================
WIN_FAST, WIN_SLOW = 0.203, -0.287     # doc/py32/SAMPLE_POINT.md:55, :169
EXPERIMENTS = {}


def experiment(name, title):
    def deco(fn):
        EXPERIMENTS[name] = (title, fn)
        return fn
    return deco


def table(rows, head):
    w = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    print("  " + " | ".join(str(head[i]).ljust(w[i]) for i in range(len(head))))
    print("  " + "-+-".join("-" * x for x in w))
    for r in rows:
        print("  " + " | ".join(str(r[i]).ljust(w[i]) for i in range(len(r))))


def fmt(x, n=3):
    return ("%+." + str(n) + "f") % x


@experiment("E0", "self-check: the plant, the hookup, and the build switch")
def e0():
    ok = True
    p = Plant("F002B_FS100")
    # The factory 24 MHz word, xm_002b.md:265-267: [0x1FFF0100] = 0x00008BA7,
    # HSI_FS=0b100, HSI_TRIM=0x0BA7.  The author's own formula calls it
    # 24.68 MHz; the band model here says 24.09 MHz.  Both are "about 24", and
    # the 2.4 % disagreement between them is a MEASURED-DATA gap, recorded
    # here rather than tuned away: it is exactly the uncertainty in the LSB
    # weight that E8 sweeps.
    f = p.freq(0x8BA7)
    print("    factory 24 MHz word 0x8BA7 -> %.3f MHz (author: 24.68)" % (f / 1e6))
    # Xiamatsu's second, "closer to 24 MHz" word: [0x1FFF01A0] = 0x8F11.
    print("    OTP word 0x8F11            -> %.3f MHz (author: 24.42)"
          % (p.freq(0x8F11) / 1e6))
    # Band endpoints must reproduce the two measured points exactly.
    assert abs(p.freq((4 << 13) | 0) - 12.9e6) < 1, "band min"
    assert abs(p.freq((4 << 13) | 0x1FF) - 20.6e6) < 1, "band max1"
    # The LSB weight the servo assumes, against the one the band gives.
    i24 = p.icscr_for(24e6, h=5)
    st = p.step_hz(i24)
    print("    ICSCR for 24.000 MHz, TRIM_H=5: 0x%04X -> %.4f MHz"
          % (i24, p.freq(i24) / 1e6))
    print("    LSB weight there: %.2f cyc/ms;  PY32_HSICAL_CYC_PER_STEP = 20"
          "  =>  loop gain %.3f" % (st / 1e3, st / 1e3 / 20.0))
    # The hookup: after one init and one event the servo must have touched
    # SysTick VAL and RCC->ICSCR and nothing else outside them.
    s = make(p, 24e6 * 1.05, band_h=5)
    deliver(s, 0.0)
    deliver(s, FRAME)
    if RCC_ICSCR not in s.touched:
        print("    FAIL: the servo never touched RCC->ICSCR (0x%08X)" % RCC_ICSCR)
        ok = False
    if (SYSTICK_BASE + 0x08) not in s.touched:
        print("    FAIL: the servo never touched SysTick->VAL")
        ok = False
    print("    MMIO actually touched: %s"
          % ", ".join("0x%08X" % a for a in sorted(s.touched)))
    print("    instructions executed at least once: %d" % len(s.pc_trace))
    # BUILD_FACTS.md: PY32_HSICAL_ENABLE=0 must still build to 0 bytes.
    wd = os.path.join(os.environ.get("TMPDIR", "/tmp"), "hsical_sim_off")
    os.makedirs(wd, exist_ok=True)
    o = os.path.join(wd, "off.o")
    run(["arm-none-eabi-gcc", "-Os", "-ffreestanding", "-fno-builtin",
         "-mcpu=cortex-m0plus", "-mthumb", "-DPY32_HSICAL_ENABLE=0",
         "-I" + os.path.join(ROOT, "tools/sim_shim"),
         "-I" + os.path.join(ROOT, "rv003usb/py32"), "-c",
         os.path.join(ROOT, "rv003usb/py32/py32_hsical.c"), "-o", o])
    sz = run(["arm-none-eabi-size", o]).splitlines()[1].split()
    print("    PY32_HSICAL_ENABLE=0: text=%s data=%s bss=%s" % (sz[0], sz[1], sz[2]))
    if (sz[0], sz[1], sz[2]) != ("0", "0", "0"):
        print("    FAIL: BUILD_FACTS.md says this must be 0 bytes")
        ok = False
    return ok


@experiment("E1", "convergence from a clock that is wrong, idle bus")
def e1():
    p = Plant("F002B_FS100")
    rows = []
    for e0pct in (+0.5, +1, +2, -1, -2, +5, -5, +10, -10):
        s = make(p, 24e6 * (1 + e0pct / 100.0), band_h=5)
        start = s.err_pct()
        tr = runsim(s, 60)
        lk = first_locked(tr)
        rows.append((fmt(e0pct, 1), fmt(start), lk if lk else "NEVER",
                     fmt(tr.err[-1]), fmt(max(tr.err[-20:])),
                     fmt(min(tr.err[-20:])), tr.writes[-1],
                     "yes" if settled(tr) else "NO"))
    table(rows, ["asked", "actual start %", "frames to LOCK",
                 "final err %", "max last20", "min last20", "ICSCR writes",
                 "in +-0.203%"])
    print("    (frames counted in keep-alives delivered; the first is always"
          " discarded)")
    return True


@experiment("E2", "steady state: does it stay locked, hunt, or walk?")
def e2():
    p = Plant("F002B_FS100")
    rows = []
    for e0pct, label in ((+2, "from +2 %"), (-2, "from -2 %"),
                         (0.0, "from dead on")):
        s = make(p, 24e6 * (1 + e0pct / 100.0), band_h=5)
        tr = runsim(s, 2000)
        tail = tr.err[200:]
        w = tr.writes[-1] - tr.writes[200]
        rows.append((label, fmt(min(tail)), fmt(max(tail)),
                     fmt(sum(tail) / len(tail)),
                     "%d in 1800 frames" % w,
                     len(set(tr.icscr[200:]))))
    table(rows, ["run", "min err %", "max err %", "mean err %",
                 "trim writes after settling", "distinct ICSCR values"])
    return True


@experiment("E3", "capture range: the largest initial error it survives")
def e3():
    p = Plant("F002B_FS100")
    rows = []
    lo_ok = hi_ok = None
    fails = []
    for e0pct in [x / 2.0 for x in range(-80, 121, 1)]:
        f0 = 24e6 * (1 + e0pct / 100.0)
        if not (p.freq((4 << 13) | 0) <= f0 <= p.freq((4 << 13) | 0x1FFF)):
            continue
        s = make(p, f0)
        tr = runsim(s, 200)
        good = settled(tr, WIN_FAST, 30)
        if good:
            lo_ok = e0pct if lo_ok is None else min(lo_ok, e0pct)
            hi_ok = e0pct if hi_ok is None else max(hi_ok, e0pct)
        else:
            fails.append((e0pct, tr.err[-1], tr.state[-1], tr.writes[-1]))
    print("    swept initial error in 0.5 % steps over every value the band"
          " can reach")
    print("    converged to inside +-0.203 %% for initial error in "
          "[%s %%, %s %%]" % (fmt(lo_ok, 1), fmt(hi_ok, 1)))
    if fails:
        print("    DID NOT converge at %d of the swept points:" % len(fails))
        table([(fmt(a, 1), fmt(b), c, d) for a, b, c, d in fails],
              ["initial err %", "err after 200 frames %", "state", "writes"])
    else:
        print("    no failures inside the swept range")
    # the acceptance window the servo itself declares
    print("    servo's own acceptance window: interval in [%d, %d] cycles"
          " = clock in [%.1f, %.1f] MHz = [%.1f %%, %.1f %%]"
          % (24000 - 8000, 24000 + 12000, 16.0, 36.0,
             100 * (16e6 - 24e6) / 24e6, 100 * (36e6 - 24e6) / 24e6))
    # what the part actually spreads by
    print("    for comparison: PY32F002B factory 48 MHz word measures -10.2 %"
          " (CHIP_FACTS_XIAMATSU.md s2); HSI drift over -40..85 C is -4/+2 %"
          " (DS030 Table 5-15, PLAN.md)")
    rows = []
    for e0pct in (-30, -20, -10, -5, 0, 5, 10, 20, 30, 40, 50):
        f0 = 24e6 * (1 + e0pct / 100.0)
        if not (p.freq((4 << 13) | 0) <= f0 <= p.freq((4 << 13) | 0x1FFF)):
            rows.append((fmt(e0pct, 0), "unreachable in this band",
                         "-", "-", "-"))
            continue
        s = make(p, f0)
        tr = runsim(s, 200)
        lk = first_locked(tr)
        rows.append((fmt(e0pct, 0), fmt(s.err_pct() if False else tr.err[0]),
                     lk if lk else "NEVER", fmt(tr.err[-1]),
                     "yes" if settled(tr, WIN_FAST, 30) else "NO"))
    table(rows, ["initial err %", "err after frame 1 %", "frames to LOCK",
                 "final err %", "in +-0.203%"])
    return True


@experiment("E4", "does it ever make things worse?")
def e4():
    p = Plant("F002B_FS100")
    rows = []
    worst = None
    for e0pct in [x / 100.0 for x in range(-20, 21, 2)]:
        s = make(p, 24e6 * (1 + e0pct / 100.0), band_h=5)
        start = s.err_pct()
        tr = runsim(s, 40)
        end = tr.err[-1]
        peak = max(tr.err[:8], key=abs)
        bad = abs(end) > abs(start) + 1e-9
        if bad and (worst is None or abs(end) - abs(start) > worst[0]):
            worst = (abs(end) - abs(start), start, end)
        rows.append((fmt(start), fmt(end), fmt(peak),
                     "WORSE" if bad else "",
                     "out" if not (WIN_SLOW <= end <= WIN_FAST) else ""))
    table(rows, ["start err %", "end err %", "worst excursion %",
                 "degraded?", "outside window?"])
    if worst:
        print("    largest degradation: %s %% -> %s %% (%s %% worse)"
              % (fmt(worst[1]), fmt(worst[2]), fmt(worst[0])))
    print("    dead band is +-%d cycles of %d = +-%.3f %%, so anything inside"
          " +-0.067 %% is by construction left alone" % (16, 24000, 100 * 16 / 24000.0))
    return True


@experiment("E5", "a missing or irregular reference")
def e5():
    p = Plant("F002B_FS100")
    print("    (a) 20 frames of keep-alives, then a 30-frame gap with no"
          " keep-alive at all, then keep-alives resume")
    s = make(p, 24e6 * 1.02, band_h=5)
    tr = runsim(s, 80, dropout=set(range(20, 50)))
    print("        err at frame 20 (just before the gap): %s %%" % fmt(tr.err[19]))
    print("        err at frame 50 (first after the gap):  %s %%" % fmt(tr.err[49]))
    print("        err at frame 52:                        %s %%" % fmt(tr.err[51]))
    print("        err at frame 80:                        %s %%" % fmt(tr.err[79]))
    print("        state across the gap: %s" % "".join(str(x) for x in tr.state[15:60]))
    print("        trim writes during the gap: %d"
          % (tr.writes[49] - tr.writes[19]))
    print("    (b) the burst: the gap ends and the hub emits five EOPs 20 us"
          " apart before resuming its 1 ms cadence")
    s = make(p, 24e6 * 1.02, band_h=5)
    tr1 = runsim(s, 20)
    before = s.icscr
    t = 20 * FRAME
    for k in range(5):
        deliver(s, t + k * 20e-6)
    burst_icscr = s.icscr
    print("        ICSCR before the burst 0x%04X, after 0x%04X, err %s %% ->"
          " %s %%" % (before, burst_icscr, fmt(tr1.err[-1]), fmt(s.err_pct())))
    print("    (c) the frame interval is 1.000 ms +-0.05 % (USB 2.0 s7.1.11)"
          " and the ISR stamp jitters")
    rows = []
    for jit, tol in ((0, 0.0), (4, 0.0), (16, 0.0), (0, 5e-4), (16, 5e-4)):
        s = make(p, 24e6 * 1.01, band_h=5)
        tr = runsim(s, 500, jitter=jit, frame_tol=tol,
                    rng=random.Random(7))
        tail = tr.err[100:]
        rows.append((jit, "%.2f %%" % (tol * 100), fmt(min(tail)),
                     fmt(max(tail)),
                     tr.writes[-1] - tr.writes[100],
                     "yes" if max(abs(x) for x in tail) <= WIN_FAST else "NO"))
    table(rows, ["stamp jitter +-cyc", "frame tol", "min err %", "max err %",
                 "trim writes in 400 frames", "stays in +-0.203%"])
    print()
    print("    (d) a MISSED keep-alive is only rejected while the clock is")
    print("    fast enough.  The acceptance ceiling is 36000 cycles, so a")
    print("    two-frame gap is inside the window for any clock below 18 MHz")
    print("    - and 18 MHz is inside the servo's own capture range.")
    rows = []
    for e0 in (-5, -15, -25, -30):
        for miss in (False, True):
            s = make(p, 24e6 * (1 + e0 / 100.0))
            drop = set(range(1, 400, 2)) if miss else set()
            tr = runsim(s, 400, dropout=drop)
            rows.append((fmt(e0, 0), "every 2nd" if miss else "none",
                         "%.2f" % (24e6 * (1 + e0 / 100.0) / 1e6),
                         fmt(tr.err[-1]), tr.state[-1],
                         "yes" if settled(tr, WIN_FAST, 30) else "NO"))
    table(rows, ["initial err %", "keep-alives dropped", "start MHz",
                 "err after 400 frames %", "state", "in +-0.203%"])
    return True


@experiment("E6", "the reference with real bus traffic on the same wire")
def e6():
    """The seam contract says: call on EVERY USB interrupt, do not filter.
    So every host packet on the low-speed segment updates hsical_prev.  The
    interval the servo then measures across a frame boundary is not one
    frame; it is the gap from the LAST packet of frame n to the keep-alive of
    frame n+1.  This experiment puts one transaction per frame on the wire
    and asks what the servo converges to."""
    p = Plant("F002B_FS100")
    # A low-speed interrupt-IN transaction is IN token + DATA1 + ACK.  At
    # 1.5 Mbit/s a token is 35 bit times = 23.3 us, a 9-byte DATA is
    # 91 bits = 60.7 us, an ACK 27 bits = 18 us, with 2-bit-time gaps: the
    # three host-driven edges land at the offsets below (the device's own
    # reply does not raise a second interrupt - the engine acks EXTI_PR after
    # it transmits, engine16_merged.S:2245-2250).
    TOK, DAT, ACK = 0.0, 25e-6, 90e-6
    rows = []
    for start_us in (10, 50, 100, 200, 400, 600, 800, 900):
        off = start_us * 1e-6
        traffic = [off + TOK, off + DAT, off + ACK]
        s = make(p, 24e6, band_h=5)
        tr = runsim(s, 300, traffic=traffic)
        gap_ms = 1.0 - (off + ACK) * 1e3
        rows.append(("%d us" % start_us, "%.3f ms" % gap_ms,
                     fmt(tr.err[0]), fmt(tr.err[-1]),
                     tr.state[-1], tr.writes[-1],
                     "yes" if settled(tr, WIN_FAST, 30) else "NO"))
    table(rows, ["transaction starts at", "gap to next keep-alive",
                 "err frame 1 %", "err frame 300 %", "final state",
                 "trim writes", "in +-0.203%"])
    print("    A frame whose last packet ends more than 2/3 of a frame before")
    print("    the next keep-alive produces an interval inside the servo's")
    print("    acceptance window [16000, 36000] cycles, and is taken as a")
    print("    frame measurement.  py32_hsical.c's comment says data traffic")
    print("    'lands far short of the window'; that is true of the gaps")
    print("    BETWEEN packets and false of the gap from the last packet of a")
    print("    frame to the next keep-alive.")
    print()
    print("    (b) the same, but the hub omits the keep-alive in frames that")
    print("    carry traffic, which is all s7.1.7.6 actually requires")
    rows = []
    for start_us in (10, 100, 400, 800):
        off = start_us * 1e-6
        s = make(p, 24e6, band_h=5)
        tr = runsim(s, 300, traffic=[off + TOK, off + DAT, off + ACK],
                    dropout=set(range(300)))
        rows.append(("%d us" % start_us, fmt(tr.err[0]), fmt(tr.err[-1]),
                     tr.state[-1], tr.writes[-1]))
    table(rows, ["transaction at", "err frame 1 %", "err frame 300 %",
                 "final state", "trim writes"])
    print()
    print("    (c) enumeration traffic: the host's SETUP..DATA..IN..ACK burst")
    print("    early in the frame, which is where a control transfer lands")
    s = make(p, 24e6, band_h=5)
    burst = [10e-6, 35e-6, 100e-6, 125e-6, 190e-6]
    tr = runsim(s, 300, traffic=burst)
    print("        err frame 1 %s %% -> frame 300 %s %%, state %d, %d writes"
          % (fmt(tr.err[0]), fmt(tr.err[-1]), tr.state[-1], tr.writes[-1]))
    print("        clock ends at %.3f MHz" % (s.f / 1e6))
    return True


@experiment("E7", "the saturation escape, and the absorbing state below it")
def e7():
    p = Plant("F002B_FS100")
    print("    PY32_HSICAL_COARSE=1: on TRIM_L saturation the servo steps")
    print("    TRIM_H by one and re-centres TRIM_L at 0x100.  py32_hsical.c")
    print("    calls that 'roughly continuous'.  Measured against the band:")
    rows = []
    for h in range(0, 9):
        top = p.freq((4 << 13) | (h << 9) | 0x1FF)
        nxt = p.freq((4 << 13) | ((h + 1) << 9) | 0x100)
        bot = p.freq((4 << 13) | (h << 9) | 0x000)
        prv = p.freq((4 << 13) | ((h - 1) << 9) | 0x100) if h else float("nan")
        rows.append((h, "%.2f" % (top / 1e6), "%.2f" % (nxt / 1e6),
                     fmt(100 * (nxt - top) / top, 1),
                     "%.2f" % (bot / 1e6),
                     "%.2f" % (prv / 1e6) if h else "-",
                     fmt(100 * (prv - bot) / bot, 1) if h else "-"))
    table(rows, ["TRIM_H", "top of band MHz", "after up-escape MHz",
                 "up-escape jump %", "bottom MHz", "after down-escape MHz",
                 "down-escape jump %"])
    print()
    print("    (a) does the servo recover from an up-escape?  Start one step")
    print("    below TRIM_L saturation in the band below the target.")
    for h in (4, 5):
        s = make(p, 0, band_h=None)
        s.icscr = (4 << 13) | (h << 9) | 0x1F0
        s.f = p.freq(s.icscr)
        tr = runsim(s, 200)
        print("        TRIM_H=%d TRIM_L=0x1F0 (%.2f MHz, %s %%): after 200"
              " frames %.3f MHz (%s %%), state %d, %d writes"
              % (h, p.freq((4 << 13) | (h << 9) | 0x1F0) / 1e6,
                 fmt(100 * (p.freq((4 << 13) | (h << 9) | 0x1F0) - 24e6) / 24e6, 1),
                 s.f / 1e6, fmt(tr.err[-1]), tr.state[-1], tr.writes[-1]))
    print()
    print("    (b) the floor.  TRIM_H=0, TRIM_L=0 is %.2f MHz, which is BELOW"
          % (p.freq(4 << 13) / 1e6))
    print("    the servo's own acceptance floor of 16.0 MHz: every interval it")
    print("    then measures is rejected, so it can never climb out.")
    s = make(p, 0, band_h=None)
    s.icscr = (4 << 13) | 0
    s.f = p.freq(s.icscr)
    tr = runsim(s, 500)
    print("        from 0x%04X (%.2f MHz): after 500 frames %.2f MHz, state %d,"
          " %d writes" % ((4 << 13), p.freq(4 << 13) / 1e6, s.f / 1e6,
                          tr.state[-1], tr.writes[-1]))
    print("        (an F002B whose SystemInit leaves TRIM_H=0 - or a servo")
    print("        pushed there by a bad measurement - is stuck for good)")
    return True


@experiment("E8", "gain sensitivity: what the LSB weight really is")
def e8():
    print("    PY32_HSICAL_CYC_PER_STEP is a compile-time constant (20).  The")
    print("    loop gain is the real LSB weight divided by it; a proportional")
    print("    loop is stable for gain in (0, 2) and rings above ~1.")
    rows = []
    cases = [
        ("F002B_FS100", 5, "linear", 24e6, "F002B 24 MHz, TRIM_H=5"),
        ("F002B_FS100", 6, "linear", 24e6, "F002B 24 MHz, TRIM_H=6"),
        ("F002B_FS101", 0, "linear", 24e6, "F002B 24 MHz in the 48 MHz band"),
        ("F002B_FS101", 8, "linear", 48e6, "F002B 48 MHz build, TRIM_H=8"),
        ("F003_FS100", 7, "linear", 24e6, "F003 24 MHz, TRIM_H=7"),
        ("F003_FS100", 8, "linear", 24e6, "F003 24 MHz, TRIM_H=8"),
        ("F002B_FS100", 5, "sqrt", 24e6, "F002B, sqrt trim curve"),
        ("F002B_FS100", 5, "quad", 24e6, "F002B, quadratic trim curve"),
    ]
    for band, h, shape, targ, label in cases:
        p = Plant(band, shape=shape)
        i = p.icscr_for(targ, h=h)
        if abs(p.freq(i) - targ) / targ > 0.02:
            rows.append((label, "-", "-", "unreachable in this band",
                         "-", "-"))
            continue
        st = p.step_hz(i) / 1e3          # kHz per step = cycles per ms
        cyc_per_step = 20
        gain = st / cyc_per_step * (24e6 / targ if targ != 24e6 else 1.0)
        # For a 48 MHz build PY32_HSICAL_FCPU is 48000000, so TARGET is
        # 48000 cycles and one step still moves `st` cycles per frame; the
        # gain is st/CYC_PER_STEP either way.
        gain = st / cyc_per_step
        defs = ["-DPY32_HSICAL_FCPU=%d" % int(targ)] if targ != 24e6 else []
        s = make(p, targ, target=targ, defines=tuple(defs), band_h=h)
        s.icscr = p.icscr_for(targ * 1.02, h=h)
        s.f = p.freq(s.icscr)
        tr = runsim(s, 300, target=targ)
        tail = tr.err[100:]
        rows.append((label, "%.1f" % st, "%.2f" % gain,
                     first_locked(tr) or "NEVER", fmt(min(tail)),
                     fmt(max(tail))))
    table(rows, ["configuration", "cyc/ms per step", "loop gain",
                 "frames to LOCK", "min err %", "max err %"])
    print()
    print("    A dead band narrower than half a trim step is a limit cycle:")
    print("    the loop leaves the band on every correction.  Sweep 60 start")
    print("    points per configuration and count the ones that never stop")
    print("    writing.")
    rows = []
    for band, h, targ, label in (
            ("F002B_FS100", 5, 24e6, "F002B 24 MHz, TRIM_H=5"),
            ("F002B_FS101", 8, 48e6, "F002B 48 MHz build, TRIM_H=8"),
            ("F003_FS100", 8, 24e6, "F003 24 MHz, TRIM_H=8")):
        st = Plant(band).step_hz(Plant(band).icscr_for(targ, h=h)) / 1e3
        hunt, worst = 0, 0.0
        defs = ("-DPY32_HSICAL_FCPU=%d" % int(targ),) if targ != 24e6 else ()
        for k in range(60):
            # The offset goes in the PLANT, as a part-to-part / temperature
            # scale on the whole band: a real drift moves the frequency
            # between trim steps, so the servo cannot land exactly on target.
            p = Plant(band, spread=1.0 + (k - 30) * 0.0004)
            i = p.icscr_for(targ, h=h)
            s = make(p, targ, target=targ, defines=defs, band_h=h)
            s.icscr = i
            s.f = p.freq(i)
            tr = runsim(s, 400, target=targ)
            w = tr.writes[-1] - tr.writes[100]
            if w > 4:
                hunt += 1
            worst = max(worst, max(abs(x) for x in tr.err[100:]))
        rows.append((label, "%.1f" % st, "%.1f" % (16.0 / (st / 2.0)),
                     "%d/60" % hunt, fmt(worst)))
    table(rows, ["configuration", "cyc/ms per step",
                 "dead band / half step", "start points that hunt",
                 "worst |err| after settling %"])
    return True


@experiment("E9", "cost: what one call actually takes")
def e9():
    p_ = Plant("F002B_FS100")
    s = make(p_, 24e6 * 1.02, band_h=5)
    costs = {}
    t = 0.0
    for n in range(12):
        c0 = s.exec_cycles
        deliver(s, t)
        costs.setdefault(n, s.exec_cycles - c0)
        t += FRAME
    print("    py32_hsical_event, cycles per call, first 12 frames:")
    print("      " + "  ".join("%d:%d" % (k, v) for k, v in costs.items()))
    # a rejected interval: a second edge 20 us after a keep-alive
    deliver(s, t)
    c0 = s.exec_cycles
    deliver(s, t + 20e-6)
    print("    rejected (out-of-window) interval: %d cycles"
          % (s.exec_cycles - c0))
    # the path that writes ICSCR, timed on its own
    s2 = make(p_, 24e6 * 1.02, band_h=5)
    deliver(s2, 0.0)
    c0 = s2.exec_cycles
    deliver(s2, FRAME)
    print("    the path that writes ICSCR:        %d cycles"
          % (s2.exec_cycles - c0))
    print("    (the .c comment claims 12 instructions on the reject path,"
          " ~20 in the dead band, ~40 when it writes ICSCR)")
    print("    instructions in py32_hsical.c executed at least once: %d"
          % len(s.pc_trace))
    return True


@experiment("EB", "the one existing build switch that removes a trap")
def eb():
    """PY32_HSICAL_COARSE is already a build switch (py32_hsical.h:52).  This
    measures what setting it to 0 does to the capture range and to the +32 %
    runaway E3 found.  It changes no algorithm - it selects the branch the
    header already offers."""
    p = Plant("F002B_FS100")
    for coarse in (1, 0):
        defs = ("-DPY32_HSICAL_COARSE=%d" % coarse,)
        lo = hi = None
        runaway = []
        for e0pct in [x / 2.0 for x in range(-70, 121, 1)]:
            f0 = 24e6 * (1 + e0pct / 100.0)
            if not (p.freq(4 << 13) <= f0 <= p.freq((4 << 13) | 0x1FFF)):
                continue
            s = make(p, f0, defines=defs)
            tr = runsim(s, 200)
            if settled(tr, WIN_FAST, 30):
                lo = e0pct if lo is None else min(lo, e0pct)
                hi = e0pct if hi is None else max(hi, e0pct)
            elif abs(tr.err[-1]) > abs(tr.err[0]) + 1.0:
                runaway.append(e0pct)
        print("    PY32_HSICAL_COARSE=%d: converges over [%s %%, %s %%];"
              " %d start points end further off than they began%s"
              % (coarse, fmt(lo, 1), fmt(hi, 1), len(runaway),
                 (" (" + ", ".join(fmt(x, 1) for x in runaway[:12])
                  + ("..." if len(runaway) > 12 else "") + ")")
                 if runaway else ""))
    print("    with COARSE=0 the reachable range is one TRIM_L band, which is")
    print("    the band SystemInit left; the servo can no longer leave it, and")
    print("    can no longer be thrown out of the acceptance window by an")
    print("    escape that moves the clock the wrong way (E7).")
    return True


@experiment("EA", "the seam: the real engine ISR driving the real servo")
def ea():
    """Everything above calls py32_hsical_event() directly.  This one goes
    through doc/py32/engine16_merged.S: the D- interrupt is taken, the two
    entry instructions load SysTick->VAL, the SE0 test forks to
    usb_rx_keepalive, EXTI_PR is acked, and `bl py32_hsical_event` is reached
    across the `push {r1, lr}` / `pop {r1, pc}` frame the engine sets up for
    it.  The loop is still closed: the ICSCR the servo writes sets the rate at
    which the next keep-alive's cycle position is computed."""
    import tempfile
    import usb_enum_sim as U

    wd = tempfile.mkdtemp(prefix="hsical_seam.")
    elf, syms = U.build(wd, hsical=True)
    for want in ("py32_hsical_event", "py32_hsical_init", "usb_rx_keepalive",
                 "usb_rx_engine16"):
        if want not in syms:
            print("    FAIL: %s did not link" % want)
            return False
    print("    linked the real servo into the real engine image:"
          " py32_hsical_event @ %08x" % syms["py32_hsical_event"])

    p = Plant("F002B_FS100")
    m = U.Machine(elf, syms)
    m.icscr = p.icscr_for(24e6 * 1.02, h=5)

    def call(sym):
        m.uc.reg_write(U.UC_ARM_REG_SP, U.RAM_BASE + U.RAM_SIZE - 0x100)
        m.uc.reg_write(U.UC_ARM_REG_LR, (syms["_start"] & ~1) | 1)
        m.pending = None
        m.uc.emu_start(syms[sym] | 1, syms["_start"] & ~1, 0, 100000)

    call("py32_hsical_init")
    if not m.st_running:
        print("    FAIL: py32_hsical_init did not start SysTick")
        return False
    print("    py32_hsical_init started SysTick: CTRL=0x%X LOAD=0x%06X"
          % (m.st_ctrl, m.st_load))

    sp0 = m.uc.reg_read(U.UC_ARM_REG_SP)
    at = 100000.0
    rows = []
    keepalive = [U.SE0, U.SE0, U.J]
    for n in range(10):
        f = p.freq(m.icscr)
        m.host_seg = (at, keepalive)
        before = len(m.icscr_writes)
        err, spent = m.isr(at + 16)
        if err is not None:
            print("    FAIL: the ISR faulted on a keep-alive: %r" % err)
            return False
        sp = m.uc.reg_read(U.UC_ARM_REG_SP)
        rows.append((n + 1, "0x%04X" % m.icscr, "%.4f" % (p.freq(m.icscr) / 1e6),
                     fmt(100 * (p.freq(m.icscr) - 24e6) / 24e6),
                     len(m.icscr_writes) - before, spent,
                     "ok" if sp == sp0 else "LEAK %+d" % (sp - sp0)))
        at += p.freq(m.icscr) * 1e-3
    table(rows, ["keep-alive", "ICSCR", "MHz", "err %", "ICSCR writes",
                 "ISR cycles", "SP balance"])
    hit = sum(1 for a in m.pc_trace if a == (syms["usb_rx_keepalive"] & ~1))
    print("    the keep-alive path was the one taken: usb_rx_keepalive %s"
          % ("entered" if hit else "NEVER ENTERED - the SE0 test forked wrong"))
    print("    total ICSCR writes through the engine: %d" % len(m.icscr_writes))
    return bool(m.icscr_writes) and hit > 0


def main(argv):
    sel = [a for a in argv[1:] if not a.startswith("-")]
    only_self = "--selftest" in argv
    names = ["E0"] if only_self else (sel or sorted(EXPERIMENTS))
    ok = True
    for n in names:
        if n not in EXPERIMENTS:
            sys.exit("no such experiment: %s (have %s)"
                     % (n, " ".join(sorted(EXPERIMENTS))))
        title, fn = EXPERIMENTS[n]
        print("\n=== %s  %s" % (n, title))
        ok = fn() and ok
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
