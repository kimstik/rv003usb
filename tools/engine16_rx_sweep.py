#!/usr/bin/env python3
"""Sweep the receive engine's PHASE LOCK - poll-loop shape and fixed delay -
and measure, for each candidate, where the locked sample lands in the 16-cycle
bit cell and how much device-clock error the longest packet survives.

tools/engine16_rx_bus.py answers those two questions for the engine as it is
committed.  This one rewrites the two things that set them, assembles each
rewrite, and runs the same measurement on it, so the choice of constant is made
from numbers rather than from the arithmetic in a comment:

  * POLL SHAPE - the granularity with which .Lwait_k resolves the SYNC J->K
    edge.  Everything downstream is a fixed delay, so the width of the sample
    distribution is exactly the poll period.
  * FIXED DELAY - .Lk_edge's nop run (K) and .Lprime's tail padding (P).  These
    slide the whole distribution inside the cell without changing its width.

The figure of merit is the SYMMETRIC clock tolerance, worst case over every
entry latency and every sub-cycle packet phase - not the tolerance at one
arbitrary entry, which is what a single point of the distribution reports.

  usage: engine16_rx_sweep.py [--quick] [--jobs N] [--dribble C]
         engine16_rx_sweep.py --verify           # full grid, committed source
"""
import argparse
import multiprocessing
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from prerender_check import build                                   # noqa: E402
import engine16_rx_bus as rxbus                                     # noqa: E402
from engine16_rx_bus import Bus, wire, crc16, CELL                  # noqa: E402

ENGINE = os.path.join(ROOT, "doc/py32/engine16_merged.S")
TX = os.path.join(ROOT, "doc/py32/engine16_tx.S")

PID = 0xC3                              # DATA0
PAY = bytes(range(1, 9))                # the longest low-speed payload
_C = crc16(PAY) ^ 0xFFFF
# SYNC byte, PID, payload AND THE TWO CRC BYTES.  Checking only the ten bytes
# up to the payload calls a receiver correct when a whole bit has been
# duplicated into the CRC, which is exactly what a clock 1% fast does here: the
# payload still reads 01..08 and the residue is wrong.  engine16_rx_bus.py's
# "+1.00%" is that leniency, not margin.
WANT = bytes([0x80, PID]) + PAY + bytes([_C & 0xFF, _C >> 8])

# USB 2.0 S7.1.9: the transmitter may hold the last data bit up to 260 ns into
# the EOP.  At 24 MHz that is 6.24 cycles, and a sample earlier than that in
# the first SE0 cell reads the dribble as a data bit and loses the frame.
DRIBBLE = 6.24

# The grid every candidate is judged on: the entry latencies the COMMITTED
# engine accepts (6..37, tools/engine16_rx_bus.py gate G6), less the bottom
# one, because a non-zero sub-cycle packet phase is the same thing as half a
# cycle less entry latency and would push it out of the window.  Fixed rather
# than per-candidate: a candidate with a wider window would otherwise be judged
# at its own marginal edges, where the tolerance is zero by construction and
# says nothing about the lock.
TOL_ENTRIES = list(range(7, 38))


# ------------------------------------------------------------- source rewrite
POLL_RE = re.compile(r"^\.Lsync_hunt:\n.*?^\tbmi     \.Lwait_k[^\n]*\n",
                     re.S | re.M)

KEDGE_RE = re.compile(r"(^\.Lk_edge:\n\t\.rept   )(\d+)(\n)", re.M)

PRIME_RE = re.compile(r"(^\tmov     r11, r2\t\t\t/\* 1  /  base[^\n]*\n)"
                      r"((?:\tnop[^\n]*\n)*)", re.M)

ASSERT_RE = re.compile(r"(\t\.if     \(usb_rx_chain - \.Lprime\) != )(\d+)")


ENTRY_ANCHOR = ("\tldr     r2, =(usb_rx_chain + 1)\t/* chain head, bit0 set for bx"
                "       */\n\tmov     r14, r2\n")
DEL_FROM = ".Lsync_hunt:\n"
DEL_TO = "\t.balign 4\t\t\t/* align HERE, not at the chain head,"
CHAIN = "usb_rx_chain:\n"
INS_BEFORE = ("/* EOP stubs for cells 0..3 sit in front of the chain so that a"
              " backward\n")


def prime_body(text):
    """The eleven setup instructions of .Lprime, taken from the source rather
    than retyped, so a candidate cannot differ from the engine by a typo."""
    a = text.index(".Lprime:\n") + len(".Lprime:\n")
    b = text.index("\tnop", a)
    return text[a:b]


def prime(text, p, reloc):
    if not reloc:
        return ""
    return (".Lprime:\n" + prime_body(text) + "\tnop\n" * p +
            ".Lprime_end:\n\tbx      r14\n")


def hunt(shape, m, reloc):
    """The J->K hunt.  Every exit branches to the SAME .Lk_edge, so the delay
    from the sample that DETECTED the edge to the confirming sample is one
    constant and the spread of the lock is the poll period alone.  (Per-exit
    padding would equalize the delay from the START OF THE BLOCK, which is
    unrelated to the edge: that spreads the lock over the whole block.)

      orig  subs / beq / ldr / lsls / taken b<cond>            7 cycles
      out   ldr / lsls / untaken b<cond>, m of them in a row   3 cycles
      back  ldr / lsls / taken b<cond> over an untaken b       5 cycles

    .Lwait_j falls STRAIGHT into the first sample - no counter between them.
    The counter costs 2 cycles there, and those 2 cycles are a blind window
    between the last J seen and the first K looked for: they widen the lock by
    exactly as much as they take, whatever the poll period is.
    """
    s = [".Lsync_hunt:", ".Lwait_j:",
         "\tsubs    r1, #1",
         "\tbeq     .Lgiveup"]
    s += ["\tldr     r0, [r7, #USB_IDR_OFS]",
         "\tlsls    r0, r0, #(31 - USB_DM_BIT)",
         "\tbpl     .Lwait_j",
         ".Lwait_k:"]
    if shape == "orig":
        s += ["\tsubs    r1, #1", "\tbeq     .Lgiveup",
              "\tldr     r0, [r7, #USB_IDR_OFS]",
              "\tlsls    r0, r0, #(31 - USB_DM_BIT)",
              "\tbmi     .Lwait_k"]
        return "\n".join(s) + "\n"
    if shape == "out":
        for _i in range(m):
            s += ["\tldr     r0, [r7, #USB_IDR_OFS]",
                  "\tlsls    r0, r0, #(31 - USB_DM_BIT)",
                  "\tbpl     .Lk_edge"]
    elif shape == "back":
        for i in range(m):
            s += ["\tldr     r0, [r7, #USB_IDR_OFS]",
                  "\tlsls    r0, r0, #(31 - USB_DM_BIT)",
                  "\tbmi     9%02df" % i,
                  "\tb       .Lk_edge",
                  "9%02d:" % i]
    else:
        raise SystemExit("unknown poll shape " + shape)
    s.append("\tb       .Lsync_hunt")
    return "\n".join(s) + "\n"


def kedge(k, reloc):
    s = [".Lk_edge:"] + ["\tnop"] * k + [
        "\tldr     r0, [r7, #USB_IDR_OFS]",
        "\tlsls    r0, r0, #(31 - USB_DM_BIT)",
        "\tbmi     .Lsync_hunt"]
    return "\n".join(s) + "\n"


SHAPE_RE = re.compile(r"^(orig|out|back)(\d*)(r?)$")


def variant(text, poll, k, p):
    """Rewrite the committed source into one candidate.  A trailing "r" on the
    poll name moves the whole lock - hunt, confirm AND the priming block - in
    front of the EOP stubs and enters the chain through `bx r14`, which already
    holds usb_rx_chain+1 as the chain's own back edge.  Two reasons: nothing
    else fits (usb_rx_cell0's `beq rx_eop0` spends 232 of a Thumb B<cond>'s
    256 bytes, leaving 24), and moving the priming out as well SHORTENS that
    branch instead of lengthening it."""
    mo = SHAPE_RE.match(poll)
    if not mo:
        raise SystemExit("unknown poll " + poll)
    shape, m, reloc = mo.group(1), int(mo.group(2) or 0), bool(mo.group(3))
    a, b = text.index(DEL_FROM), text.index(DEL_TO)
    body = hunt(shape, m, reloc) + kedge(k, reloc) + prime(text, p, reloc)
    if reloc:
        c = text.index(CHAIN)
        t = text[:a] + text[b:c].split(DEL_TO)[0] + "\t.balign 4\n" + text[c:]
        t = t.replace(INS_BEFORE, body + "\n" + INS_BEFORE, 1)
        t = ASSERT_RE.sub(
            lambda mm: "\t.if     (.Lprime_end - .Lprime) != " + str(22 + 2 * p),
            t, count=1)
    else:
        t = text[:a] + body + text[b:]
        t2 = PRIME_RE.sub(lambda mm: mm.group(1) + "\tnop\n" * p, t, count=1)
        if t2 == t:
            raise SystemExit("engine16_rx_sweep: .Lprime anchor not found")
        t = ASSERT_RE.sub(lambda mm: mm.group(1) + str(22 + 2 * p), t2, count=1)
    return t


# --------------------------------------------------------------- measurement
# The C convention (F-4): usb_pid_handle_data is reached only past the CRC16
# residue test, and gets r3 = emitted bytes - 1 = payload + 3.
WANT_R3 = len(PAY) + 3


def decodes(bus, entry, t0, ppm, dribble, syms):
    """ACCEPTED, not merely "the buffer looks right".  The engine writes the
    buffer as it goes and only then tests the residue, so a frame that gained
    or lost a bit still leaves 80 c3 01..08 in RAM; the verdict exists only as
    the call to the C dispatch, and its r3 carries the length."""
    bus.configure(wire(PID, PAY), t0, CELL * (1.0 + ppm / 1e6), dribble)
    bus.run(entry)
    if bytes(bus.uc.mem_read(syms["usb_rxbuf"] + 2, len(WANT))) != WANT:
        return False
    return len(bus.calls) == 1 and bus.calls[0][4] == WANT_R3


def entry_window(bus, syms, dribble):
    """The LONGEST CONTIGUOUS run of entry latencies that decodes.  Taking
    min..max instead would report an isolated outlier (entry 2 decodes; 4 and 5
    do not) as the bottom of the window."""
    ok = [e for e in range(0, 90) if decodes(bus, e, 0.0, 0.0, dribble, syms)]
    best = cur = []
    for e in ok:
        cur = cur + [e] if cur and e == cur[-1] + 1 else [e]
        if len(cur) > len(best):
            best = cur
    return (best[0], best[-1]) if best else (None, None)


def offsets(bus, syms, entries, phases, dribble):
    """Where the locked sample sits in the cell, and at which (entry, phase)
    the extremes occur - those two points are where the clock tolerance is
    worst in each direction."""
    lo = (99.0, None)
    hi = (-1.0, None)
    fails = 0
    hist = {}
    for e in entries:
        for k in range(phases):
            t0 = k / float(phases)
            if not decodes(bus, e, t0, 0.0, dribble, syms):
                fails += 1
                continue
            oo = [o for (_t, i, o) in bus.samples if 8 <= i < 14]
            if not oo:
                fails += 1
                continue
            hist[round(min(oo), 1)] = hist.get(round(min(oo), 1), 0) + 1
            if min(oo) < lo[0]:
                lo = (min(oo), (e, t0))
            if max(oo) > hi[0]:
                hi = (max(oo), (e, t0))
    return lo, hi, fails, hist


def edge_ppm(bus, syms, entry, t0, dribble, sign, res=25, limit=40000):
    """Largest clock error of the given sign that still decodes the longest
    packet from this entry and phase, to `res` ppm.  Doubling then bisecting:
    the pass region is an interval around nominal, so the first failure bounds
    it, and scanning past a failure would report the far side of a hole as
    margin."""
    if decodes(bus, entry, t0, sign * limit, dribble, syms):
        return sign * limit
    good, bad = 0, res
    while bad <= limit and decodes(bus, entry, t0, sign * bad, dribble, syms):
        good, bad = bad, bad * 2
    while bad - good > res:
        mid = (good + bad) // 2
        if decodes(bus, entry, t0, sign * mid, dribble, syms):
            good = mid
        else:
            bad = mid
    return sign * good


def measure(src, workdir, tag, entries=None, phases=8, dribble=DRIBBLE):
    eng = os.path.join(workdir, "eng%s.S" % tag)
    with open(eng, "w") as f:
        f.write(src)
    elf, syms = build(eng, TX, workdir, tag=tag)
    bus = Bus(elf, syms, wire(PID, PAY), 0.0, float(CELL), dribble)
    bus.watch(syms["usb_pid_handle_data"])
    lo, hi = entry_window(bus, syms, dribble)
    if lo is None:
        return {"entry": None}
    ents = list(TOL_ENTRIES if entries is None else entries)
    omin, omax, fails, hist = offsets(bus, syms, ents, phases, dribble)
    r = {"entry": (lo, hi), "omin": omin[0], "omax": omax[0],
         "fails": fails, "hist": hist}
    if omin[1] is None:
        return r
    plus, minus = 10 ** 9, -10 ** 9
    pw = pl = None
    for e in ents:
        for k in range(phases):
            t = k / float(phases)
            v = edge_ppm(bus, syms, e, t, dribble, +1)
            if v < plus:
                plus, pw = v, (e, t)
            v = edge_ppm(bus, syms, e, t, dribble, -1)
            if v > minus:
                minus, pl = v, (e, t)
    r["plus"], r["minus"] = plus, minus
    r["worst"] = (pw, pl)
    r["sym"] = min(plus, -minus)
    return r


def job(a):
    poll, k, p, quick, dribble = a
    wd = tempfile.mkdtemp(prefix="e16sw.")
    try:
        text = open(ENGINE).read()
        src = variant(text, poll, k, p)
        ents = TOL_ENTRIES[::3] if quick else None
        r = measure(src, wd, "s", entries=ents,
                    phases=4 if quick else 8, dribble=dribble)
    except Exception as exc:                       # a variant that will not
        r = {"entry": None, "err": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    return (poll, k, p), r


def fmt(key, r):
    poll, k, p = key
    if r.get("entry") is None:
        return "  %-8s K=%-3d P=%-2d  DOES NOT DECODE   %s" % (
            poll, k, p, r.get("err", ""))
    return ("  %-8s K=%-3d P=%-2d  entry %2d..%-2d  offset %5.2f..%-5.2f "
            "(width %4.2f)  tol %+.3f%% .. %+.3f%%  symmetric %.3f%%  %s"
            % (poll, k, p, r["entry"][0], r["entry"][1], r["omin"], r["omax"],
               r["omax"] - r["omin"], r.get("minus", 0) / 1e4,
               r.get("plus", 0) / 1e4, r.get("sym", 0) / 1e4,
               "worst +@%s -@%s fails %d" % (r.get("worst", ("", ""))[0],
                                             r.get("worst", ("", ""))[1],
                                             r.get("fails", -1))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="coarse entry/phase grid (screening)")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--polls", default="orig,out4,out6,out8")
    ap.add_argument("--k", default="20")
    ap.add_argument("--p", default="3")
    ap.add_argument("--dribble", type=float, default=DRIBBLE,
                    help="EOP dribble held in the first SE0 cell, in cycles")
    ap.add_argument("--verify", action="store_true",
                    help="measure the committed source on the full grid")
    args = ap.parse_args()

    if args.verify:
        wd = tempfile.mkdtemp(prefix="e16sw.")
        r = measure(open(ENGINE).read(), wd, "v", dribble=args.dribble)
        print("committed source, full grid:")
        print(fmt(("committed", -1, -1), r))
        for o in sorted(r["hist"]):
            print("      offset %5.1f   %5.2f%%"
                  % (o, 100.0 * r["hist"][o] / sum(r["hist"].values())))
        shutil.rmtree(wd, ignore_errors=True)
        return 0

    ks = [int(x) for x in args.k.split(",")]
    ps = [int(x) for x in args.p.split(",")]
    todo = [(poll, k, p, args.quick, args.dribble)
            for poll in args.polls.split(",") for k in ks for p in ps]
    print("engine16_rx_sweep: %d candidates, dribble floor %.2f cycles, "
          "entries %d..%d x %d phases, %s grid"
          % (len(todo), args.dribble, TOL_ENTRIES[0], TOL_ENTRIES[-1],
             4 if args.quick else 8, "quick" if args.quick else "full"))
    with multiprocessing.Pool(args.jobs) as pool:
        out = []
        for key, r in pool.imap(job, todo):
            print(fmt(key, r))
            sys.stdout.flush()
            out.append((key, r))
    good = [(k, r) for k, r in out if r.get("sym")]
    if good:
        best = max(good, key=lambda kr: kr[1]["sym"])
        print("\nbest symmetric tolerance:")
        print(fmt(*best))
    return 0


if __name__ == "__main__":
    sys.exit(main())
