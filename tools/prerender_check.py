#!/usr/bin/env python3
"""prerender_check.py - cross-check tools/prerender.py against the ASSEMBLED
usb_in_render, by running it.

design_b_in.md S12 records the lesson this exists to obey: a model that shares
a source with the artifact cannot validate it.  engine16_tx.md S6 verified the
transmit chain against an encoder written from the same table and missed a
defect that was in both (design_b_in.md S11).  So neither side of the
comparison below is a re-reading of the assembly by hand:

  left   tools/prerender.py, the host renderer whose output ships in flash
  right  usb_in_render, ASSEMBLED by arm-none-eabi-gcc from
         doc/py32/engine16_merged.S and EXECUTED instruction by instruction
         on a Cortex-M0+ emulator, with its record read back out of the
         emulated RAM

They must agree on every byte of the record that a pre-rendered one replaces:
pid, groups, the tail target, and the whole stuffed stream including the
sentinel bit .Lir_flush leaves in the final partial byte.

Requires the `unicorn` python package (pip install unicorn) and
arm-none-eabi-gcc/ld.  Both are build-time only; nothing here is on the device.

  usage: prerender_check.py [--engine doc/py32/engine16_merged.S]
                           [--tx doc/py32/engine16_tx.S] [--cases N]
"""

import argparse
import os
import random
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prerender import render, PID_DATA0, PID_DATA1, MAXPAY   # noqa: E402
from engine16_cyc import cost as cyc_cost                    # noqa: E402

FLASH_BASE = 0x08000000
RAM_BASE = 0x20000000
FLASH_SIZE = 0x10000
RAM_SIZE = 0x4000
STOP = 0x08FFFFF0           # the magic return address, outside any section

STUBS = r"""
	.syntax unified
	.cpu cortex-m0plus
	.thumb
	.text
	.thumb_func
	.global _start
_start:	b _start
	.macro STUB n
	.thumb_func
	.global \n
\n:	bx lr
	.endm
	STUB usb_pid_handle_ack
	STUB usb_pid_handle_data
	STUB usb_pid_handle_in
	STUB usb_pid_handle_out
	STUB usb_pid_handle_setup
	STUB py32_hsical_event
	.section .bss.rv003usb_internal_data, "aw", %nobits
	.balign 4
	.global rv003usb_internal_data
rv003usb_internal_data:
	.space 256
"""

LDS = """
ENTRY(_start)
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
  RAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 16K
}
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > FLASH
  .data : { *(.data*) } > RAM
  .bss  : { *(.bss*) *(COMMON) } > RAM
}
"""


def build(engine, tx, workdir, endpoints=2, extra=None, tag=""):
    """Assemble and link the two engines exactly as INTEGRATION_BUILD.md does
    (.datacode -> .text.engine16, i.e. flash-resident), plus stubs for the C
    seam.  Returns (path to the ELF, {symbol: address})."""
    def fixup(src, dst):
        with open(src) as f:
            t = f.read()
        with open(dst, "w") as f:
            f.write(t.replace(".section .datacode", ".section .text.engine16"))

    m, t = os.path.join(workdir, "m%s.S" % tag), os.path.join(workdir, "t%s.S" % tag)
    s = os.path.join(workdir, "stubs%s.S" % tag)
    ld = os.path.join(workdir, "h%s.ld" % tag)
    elf = os.path.join(workdir, "h%s.elf" % tag)
    fixup(engine, m)
    fixup(tx, t)
    with open(s, "w") as f:
        f.write(STUBS)
    with open(ld, "w") as f:
        f.write(LDS)
    cf = ["arm-none-eabi-gcc", "-x", "assembler-with-cpp", "-mcpu=cortex-m0plus",
          "-mthumb", "-DENDPOINTS=%d" % endpoints, "-DUSB_RX_CHECK=2",
          "-DUSB_ENGINE16_FLASH=1", "-c"]
    objs = []
    srcs = [m, t, s] + ([extra] if extra else [])
    for f in srcs:
        o = f[:-2] + "%s.o" % tag
        subprocess.run(cf + [f, "-o", o], check=True)
        objs.append(o)
    subprocess.run(["arm-none-eabi-ld", "-T", ld] + objs + ["-o", elf],
                   check=True)
    syms = {}
    out = subprocess.run(["arm-none-eabi-nm", elf], check=True,
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return elf, syms


def load(elf):
    """Section image: [(addr, bytes)] for every allocated PROGBITS section."""
    out = subprocess.run(["arm-none-eabi-objcopy", "-O", "binary",
                          "--only-section=.text", elf, elf + ".bin"],
                         check=True, capture_output=True, text=True)
    del out
    with open(elf + ".bin", "rb") as f:
        blob = f.read()
    hdr = subprocess.run(["arm-none-eabi-objdump", "-h", elf], check=True,
                         capture_output=True, text=True).stdout
    base = None
    for line in hdr.splitlines():
        p = line.split()
        if len(p) >= 7 and p[1] == ".text":
            base = int(p[3], 16)
    assert base is not None, "no .text in the linked image"
    return base, blob


class Engine:
    def __init__(self, elf, syms):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
        from unicorn.arm_const import UC_CPU_ARM_CORTEX_M0
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        try:
            self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M0)
        except Exception:
            pass
        self.uc.mem_map(FLASH_BASE, FLASH_SIZE)
        self.uc.mem_map(RAM_BASE, RAM_SIZE)
        base, blob = load(elf)
        self.uc.mem_write(base, blob)
        self.syms = syms
        self.flash = (base, blob)

    def render(self, pid, payload, endp=0, pat=0x1234, src=None):
        """Run the assembled usb_in_render and read its record back out.
        `src` overrides the payload pointer, which is the table's key: pass a
        flash address to exercise the pre-rendered path."""
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                       UC_ARM_REG_R2, UC_ARM_REG_R3,
                                       UC_ARM_REG_SP, UC_ARM_REG_LR)
        uc = self.uc
        s = self.syms
        scratch = RAM_BASE + 0x2000
        uc.mem_write(RAM_BASE + 0x1000, b"\x00" * 0x1000)   # wipe bss window
        # every record byte poisoned, so anything the engine does not write is
        # visible as 0xA5 rather than as a lucky zero
        uc.mem_write(s["usb_in_arm"], b"\xA5" * (32 * 2))
        uc.mem_write(scratch, bytes(payload) + b"\x00" * 8)
        uc.mem_write(s["usb_in_pend"],
                     struct.pack("<HBB", pat, 1, endp))
        uc.reg_write(UC_ARM_REG_SP, RAM_BASE + RAM_SIZE - 0x100)
        uc.reg_write(UC_ARM_REG_R0, scratch if src is None else src)
        uc.reg_write(UC_ARM_REG_R1, len(payload))
        uc.reg_write(UC_ARM_REG_R2, 0)          # poly_function: a DATA packet
        uc.reg_write(UC_ARM_REG_R3, pid)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        uc.emu_start(s["usb_in_render"] | 1, STOP, 0, 200000)
        rec = bytes(uc.mem_read(s["usb_in_arm"] + endp * 32, 32))
        return rec

    def stream(self, src, n):
        """n bytes at the record's +24 src word, wherever it points."""
        if RAM_BASE <= src < RAM_BASE + RAM_SIZE:
            return bytes(self.uc.mem_read(src, n))
        base, blob = self.flash
        return blob[src - base:src - base + n]

    def scratch(self):
        return RAM_BASE + 0x2000

    def tails(self):
        """usb_ti_tails[], the eight code pointers the record's tail field is
        chosen from, read out of the linked image."""
        base, blob = self.flash
        a = self.syms["usb_ti_tails"] - base
        return list(struct.unpack("<8I", blob[a:a + 32]))




PAY_STRIDE = 16         # every synthetic payload gets its own slot, so the
                        # key array is ascending by construction


def in_flash(x):
    """RAM is at 0x20000000 and flash at 0x08000000, so "is this record's
    stream pre-rendered" is a range test, not a comparison."""
    return FLASH_BASE <= x < FLASH_BASE + FLASH_SIZE


def table_source(cases, path):
    """A linked-in table over synthetic payloads, laid out the way
    prerender_gen.py lays out a real one.  This is what proves the ENGINE
    consumes the format the generator emits - the byte-for-byte comparison in
    phase 1 only proves the two renderers agree."""
    blob = bytearray()
    offs = []
    for pid, pay in cases:
        offs.append(len(blob))
        blob += render(pid, pay).stream
    blob.append(0)
    w = []
    w.append("\t.syntax unified\n\t.cpu cortex-m0plus\n\t.thumb\n")
    w.append("\t.section .rodata.pr, \"a\"\n\t.balign 4\n")
    for i, (pid, pay) in enumerate(cases):
        w.append("pr_pay%d:\n" % i)
        w.append("\t.byte " + ",".join(str(b) for b in pay) + "\n"
                 if pay else "")
        w.append("\t.space %d\n" % (PAY_STRIDE - len(pay)))
    w.append("\t.balign 4\n\t.global usb_pr_bits\nusb_pr_bits:\n")
    w.append("\t.byte " + ",".join(str(b) for b in blob) + "\n")
    w.append("\t.balign 4\n\t.global usb_pr_keys\nusb_pr_keys:\n")
    for i in range(len(cases)):
        w.append("\t.word pr_pay%d\n" % i)
    w.append("\t.balign 4\n\t.global usb_pr_recs\nusb_pr_recs:\n")
    for i, (pid, pay) in enumerate(cases):
        r = render(pid, pay)
        w.append("\t.word usb_pr_bits + %d\n\t.byte %d,%d,%d,%d\n"
                 % (offs[i], len(pay), pid, r.groups, r.t))
    w.append("\t.balign 4\n\t.global usb_pr_count\nusb_pr_count:\n")
    w.append("\t.byte %d\n" % len(cases))
    with open(path, "w") as f:
        f.write("".join(w))
    return offs


def phase2(a, wd):
    """The pre-rendered path, executed.  A hit must produce a record whose
    stream word points into FLASH at the generated bytes; a lookup that must
    not hit - wrong length, wrong PID, a pointer the table does not name -
    must fall through to usb_in_render and produce a RAM record."""
    rnd = random.Random(6072026)
    cases = []
    for _ in range(a.table):
        pid = rnd.choice((PID_DATA0, PID_DATA1))
        n = rnd.randrange(0, MAXPAY + 1)
        cases.append((pid, bytes(rnd.randrange(256) for _ in range(n))))
    src = os.path.join(wd, "prtab.S")
    table_source(cases, src)
    elf, syms = build(a.engine, a.tx, wd, extra=src, tag="2")
    eng = Engine(elf, syms)
    tails = eng.tails()
    bad = hits = misses = 0
    for i, (pid, pay) in enumerate(cases):
        key = syms["pr_pay%d" % i] if ("pr_pay%d" % i) in syms else None
        if key is None:       # local labels are not in nm's default output
            key = syms["usb_pr_keys"]
            base, blob = eng.flash
            key = struct.unpack("<I", blob[key - base + 4 * i:
                                           key - base + 4 * i + 4])[0]
        r = render(pid, pay)
        rec = eng.render(pid, pay, src=key)
        stream_p = struct.unpack("<I", rec[24:28])[0]
        got = eng.stream(stream_p, len(r.stream))
        if not in_flash(stream_p):
            print("MISS where a hit was required: case %d, pid %02X, pay %s"
                  % (i, pid, pay.hex()))
            bad += 1
            continue
        hits += 1
        if (rec[2], rec[3], struct.unpack("<I", rec[4:8])[0], got) != \
           (r.pid, r.groups, tails[r.t], r.stream):
            print("HIT RECORD WRONG: case %d pid %02X pay %s" % (i, pid, pay.hex()))
            print("  want pid=%02X groups=%d tail=%08x stream=%s"
                  % (r.pid, r.groups, tails[r.t], r.stream.hex()))
            print("  got  pid=%02X groups=%d tail=%08x stream=%s"
                  % (rec[2], rec[3], struct.unpack("<I", rec[4:8])[0], got.hex()))
            bad += 1

        # the two guards, and a key the table does not hold
        for wpid, wpay, why in ((pid ^ (PID_DATA0 ^ PID_DATA1), pay, "wrong PID"),
                                (pid, pay[:-1] if pay else None, "short read"),
                                (pid, pay, "unlisted pointer")):
            if wpay is None:
                continue
            k = key + 1 if why == "unlisted pointer" else key
            rec2 = eng.render(wpid, wpay, src=k)
            sp = struct.unpack("<I", rec2[24:28])[0]
            if in_flash(sp):
                print("FALSE HIT (%s): case %d pid %02X pay %s"
                      % (why, i, pid, pay.hex()))
                bad += 1
            else:
                misses += 1
                # ...and the dynamic record it fell through to must still be
                # right, for the bytes the emulator actually staged in RAM
                if why != "unlisted pointer":
                    rr = render(wpid, wpay)
                    gg = eng.stream(sp, len(rr.stream))
                    if (rec2[2], rec2[3], struct.unpack("<I", rec2[4:8])[0], gg) \
                       != (rr.pid, rr.groups, tails[rr.t], rr.stream):
                        print("FALLBACK RECORD WRONG (%s): case %d" % (why, i))
                        bad += 1
    print("table entries        %d" % len(cases))
    print("pre-rendered hits    %d" % hits)
    print("required misses      %d" % misses)
    print("phase 2 problems     %d" % bad)
    return bad



# ---------------------------------------------------------------- phase 3
# What the ISR after the host's ACK actually costs.  design_b_in.md S10 puts
# usb_in_render at ~956 cycles by walking the loops on paper; this walks the
# instructions the emulator really executed and prices each one out of
# tools/engine16_cyc.py's table - the SAME table the cell ledgers use, so the
# two numbers are comparable.  Nothing is guessed: a load's cost follows the
# address it actually touched, flash 2 and RAM 4 for flash-resident code.

def disassemble(elf):
    """{addr: (size, mnemonic, operands)} for the whole image."""
    out = subprocess.run(["arm-none-eabi-objdump", "-d", elf], check=True,
                         capture_output=True, text=True).stdout
    ins = {}
    for line in out.splitlines():
        mo = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{4} ?)+)\s+(\S+)\s*(.*)",
                      line)
        if mo:
            addr = int(mo.group(1), 16)
            size = len(mo.group(2).replace(" ", "")) // 2
            ins[addr] = (size, mo.group(3), mo.group(4).split(";")[0].strip())
    return ins


class Tracer:
    """Executes usb_in_render under the emulator and totals its cycles."""

    def __init__(self, eng, elf):
        self.eng = eng
        self.ins = disassemble(elf)

    def run(self, pid, payload, src=None, endp=0):
        from unicorn import (UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE)
        uc = self.eng.uc
        trace = []
        region = [None]

        def on_code(u, address, size, ud):
            trace.append([address, None])

        def on_mem(u, access, address, size, value, ud):
            if trace:
                trace[-1][1] = "flash" if in_flash(address) else "ram"

        h1 = uc.hook_add(UC_HOOK_CODE, on_code)
        h2 = uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_mem)
        try:
            self.eng.render(pid, payload, endp=endp, src=src)
        finally:
            uc.hook_del(h1)
            uc.hook_del(h2)
        del region
        return self.price(trace)

    def price(self, trace):
        total = 0
        for i, (pc, reg) in enumerate(trace):
            ent = self.ins.get(pc)
            if ent is None:
                continue
            size, mnem, ops = ent
            flash_regs = ()
            if reg == "flash":
                mo = re.search(r"\[(\w+)", ops)
                if mo:
                    flash_regs = (mo.group(1).lower(),)
            lo, hi = cyc_cost(mnem, ops, "flash", (), flash_regs)
            if lo != hi:                    # a conditional branch: the trace
                nxt = trace[i + 1][0] if i + 1 < len(trace) else None
                taken = nxt is not None and nxt != pc + size
                total += hi if taken else lo
            else:
                total += lo
        return total, len(trace)


def phase3(a, wd):
    """The headline number: what an IN transaction's render costs, on the
    dynamic path and on the pre-rendered one, for the packet sizes an
    enumeration actually sends."""
    rnd = random.Random(11)
    # 25 entries because that is what the gamepad demo's descriptor set comes
    # to (prerender.md S3), and the search depth is what the hit costs
    cases = [(PID_DATA1, bytes(rnd.randrange(256) for _ in range(8)))
             for _ in range(25)]
    # the same engine linked twice: once with no table at all (the weak
    # symbols are zero and the lookup is one compare), once with one
    elf0, sym0 = build(a.engine, a.tx, wd, tag="3a")
    tr0 = Tracer(Engine(elf0, sym0), elf0)
    src = os.path.join(wd, "prtab3.S")
    table_source(cases, src)
    elf, syms = build(a.engine, a.tx, wd, extra=src, tag="3b")
    eng = Engine(elf, syms)
    tr = Tracer(eng, elf)
    base, blob = eng.flash
    keys = struct.unpack("<%dI" % len(cases),
                         blob[syms["usb_pr_keys"] - base:
                              syms["usb_pr_keys"] - base + 4 * len(cases)])

    rows = [tr.run(pid, pay, src=keys[i])[0]
            for i, (pid, pay) in enumerate(cases)]
    base8 = tr0.run(PID_DATA1, cases[0][1])[0]
    base0 = tr0.run(PID_DATA1, b"")[0]
    miss8 = tr.run(PID_DATA1, cases[0][1], src=keys[0] + 1)[0]

    print("%-52s %7s %8s" % ("what the ISR after the host's ACK spends",
                             "cycles", "us@24MHz"))
    for label, v in (
            ("no table linked: 8-byte payload, full render", base8),
            ("no table linked: zero-length, full render", base0),
            ("table linked, HIT: worst of %d" % len(cases), max(rows)),
            ("table linked, HIT: best", min(rows)),
            ("table linked, MISS: %d-entry search then full render"
             % len(cases), miss8)):
        print("%-52s %7d %8.1f" % (label, v, v / 24.0))
    print("the search costs a miss %d cycles = %.1f us over the render it "
          "still has to do" % (miss8 - base8, (miss8 - base8) / 24.0))
    return 0

def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--engine", default=os.path.join(here, "doc/py32/engine16_merged.S"))
    ap.add_argument("--tx", default=os.path.join(here, "doc/py32/engine16_tx.S"))
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--table", type=int, default=150,
                    help="synthetic pre-rendered table entries for phase 2")
    a = ap.parse_args()

    try:
        import unicorn                                  # noqa: F401
    except ImportError:
        print("prerender_check: the `unicorn` package is required "
              "(pip install unicorn).  Without it the generated records are "
              "UNVERIFIED and must not be shipped.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as wd:
        elf, syms = build(a.engine, a.tx, wd)
        eng = Engine(elf, syms)
        tails = eng.tails()

        cases = []
        for pid in (PID_DATA0, PID_DATA1):
            for n in range(0, MAXPAY + 1):
                for fill in (0x00, 0xFF, 0x55, 0xFE, 0x7E, 0xF8):
                    cases.append((pid, bytes([fill] * n)))
            for x in range(256):
                cases.append((pid, bytes([x])))
                cases.append((pid, bytes([x, x ^ 0xFF])))
        rnd = random.Random(20260907)
        for _ in range(a.cases):
            pid = rnd.choice((PID_DATA0, PID_DATA1))
            n = rnd.randrange(0, MAXPAY + 1)
            cases.append((pid, bytes(rnd.randrange(256) for _ in range(n))))

        bad = 0
        for pid, pay in cases:
            r = render(pid, pay)
            got = eng.render(pid, pay)
            g_pid, g_groups = got[2], got[3]
            g_tail = struct.unpack("<I", got[4:8])[0]
            g_stream = got[8:8 + len(r.stream)]
            want_tail = tails[r.t]
            if (g_pid, g_groups, g_tail, bytes(g_stream)) != \
               (r.pid, r.groups, want_tail, r.stream):
                bad += 1
                if bad <= 5:
                    print("MISMATCH pid=%02X pay=%s" % (pid, pay.hex()))
                    print("  gen pid=%02X groups=%d t=%d tail=%08x stream=%s"
                          % (r.pid, r.groups, r.t, want_tail, r.stream.hex()))
                    print("  asm pid=%02X groups=%d          tail=%08x stream=%s"
                          % (g_pid, g_groups, g_tail, bytes(g_stream).hex()))

        print("cases                %d" % len(cases))
        print("record mismatches    %d" % bad)
        if bad:
            return 1

        # The one thing the generator can do that usb_in_render's own caller
        # cannot: it sees the whole stream, so USB 2.0 S7.1.9's trailing
        # stuffed zero is never deferred past an end of packet.  Count the
        # packets where that bit exists, i.e. where engine16_tx.S's chain -
        # which leaves for usb_tx_eop the moment the source is exhausted and
        # has no state-6 test - would put out a packet one bit short.
        short = 0
        for pid, pay in cases:
            r = render(pid, pay)
            if r.nbits and (r.nbits % 8 or True):
                bits = []
                b = r.stream
                for i in range(r.nbits):
                    bits.append((b[i >> 3] >> (i & 7)) & 1)
                if bits[-1] == 0 and len(bits) >= 7 and all(bits[-7:-1]):
                    short += 1
        print("packets ending in a stuffed zero (engine16_tx.S omits it): "
              "%d of %d = %.2f%%" % (short, len(cases), 100.0 * short / len(cases)))
        print("phase 1 PASS: every generated record is byte-identical to the "
              "one the assembled usb_in_render produces")

        print()
        if phase2(a, wd):
            return 1
        print("phase 2 PASS: the assembled engine reads the generated table, "
              "arms from flash on a hit, and falls back to its own renderer "
              "on every lookup that must not hit")
        print()
        return phase3(a, wd)


if __name__ == "__main__":
    raise SystemExit(main())
