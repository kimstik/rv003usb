#!/usr/bin/env python3
"""Execution coverage of the assembled engines.

The premise, paid for on 2026-09-08: the receive engine's SYNC phase lock had
an inverted branch and could not decode a single packet, and it survived every
model in this tree because every model starts DOWNSTREAM of it.  The lock had
never been executed by anything.

So the useful question is not "what else might be wrong" but "what has never
run".  This links the engines, drives them with a battery of stimuli through
tools/engine16_rx_bus.py's emulator, records every instruction address that
actually executes, and prints what is left over - grouped by the symbol it
belongs to, because a whole cold symbol is a different kind of news from a
cold arm of a branch.

Usage:  python3 tools/engine16_coverage.py [--list]
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import engine16_rx_bus as B                                          # noqa: E402
from prerender_check import disassemble                              # noqa: E402
from unicorn import UC_HOOK_CODE                                     # noqa: E402


# ---------------------------------------------------------------- stimuli
def crc5(v, bits=11):
    """USB token CRC5, poly 0x05, init 0x1F, reflected, inverted."""
    c = 0x1F
    for i in range(bits):
        b = (v >> i) & 1
        if (c & 1) ^ b:
            c = (c >> 1) ^ 0x14
        else:
            c >>= 1
    return c ^ 0x1F


def token(pid, addr, endp):
    v = (addr & 0x7F) | ((endp & 0xF) << 7)
    c = crc5(v)
    return bytes([v & 0xFF, ((v >> 8) & 0x07) | (c << 3)])


def wire_raw(pid, payload, crc=True):
    return B.wire(pid, payload, crc)


def wire_token(pid, addr, endp):
    """A token packet: SYNC + PID + 11 bits + CRC5, no CRC16."""
    return B.wire(pid, token(pid, addr, endp), crc=False)


def wire_badcrc(pid, payload):
    lv = B.wire(pid, payload)
    # flip the last data bit's level onward: a CRC that cannot check out
    return lv[:-8] + [B.J if x == B.K else B.K for x in lv[-8:-6]] + lv[-6:]


def wire_stuffviolation(pid, payload):
    """Seven consecutive 1s on the wire: level held for 7 bit times."""
    lv = B.wire(pid, payload)
    i = len(lv) // 2
    return lv[:i] + [lv[i]] * 8 + lv[i + 8:]


def wire_se0():
    return [B.SE0] * 4 + [B.J] * 8


def battery():
    """(name, levels, entry) tuples.  Wide on purpose: the point is coverage,
    so every PID the stack can meet, every payload length, and every
    malformed shape that the engine has an arm for."""
    out = []
    pay8 = bytes(range(1, 9))
    for entry in (6, 12, 20, 30, 37):
        out.append(("DATA0 8B entry=%d" % entry, wire_raw(0xC3, pay8), entry))
    for n in range(0, 9):
        out.append(("DATA0 %dB" % n, wire_raw(0xC3, bytes(range(n))), 16))
        out.append(("DATA1 %dB" % n, wire_raw(0x4B, bytes(range(n))), 16))
    for name, pid in (("SETUP", 0x2D), ("IN", 0x69), ("OUT", 0xE1),
                      ("SOF", 0xA5)):
        for addr in (0, 1, 3):
            for endp in (0, 1, 2):
                out.append(("%s a=%d e=%d" % (name, addr, endp),
                            wire_token(pid, addr, endp), 16))
    for name, pid in (("ACK", 0xD2), ("NAK", 0x5A), ("STALL", 0x1E)):
        out.append((name, B.wire(pid, b"", crc=False), 16))
    out.append(("DATA0 bad CRC16", wire_badcrc(0xC3, pay8), 16))
    out.append(("stuffing violation", wire_stuffviolation(0xC3, pay8), 16))
    out.append(("SE0 at entry (keepalive)", wire_se0(), 4))
    out.append(("aliased PID 0xC4", wire_raw(0xC4, pay8), 16))
    out.append(("PID 0x00", wire_raw(0x00, pay8), 16))
    return out


# ---------------------------------------------------------------- coverage
def main():
    wd = tempfile.mkdtemp(prefix="e16cov.")
    elf, syms = B.make(wd)
    ins = disassemble(elf)
    seen = set()
    runs = 0

    def go(lv, entry, ph=0.0, owed=False):
        """Bus.run() wipes RAM on entry, so anything preset for the run has to
        be written from a hook on the first instruction, not before."""
        nonlocal runs
        b = B.Bus(elf, syms, lv, ph, float(B.CELL))
        state = {"armed": not owed}

        def h(u, a, sz, d):
            seen.add(a)
            if not state["armed"]:
                state["armed"] = True
                # TB_OWED lives at usb_rxbuf+28, i.e. +26 from the engine's
                # own base of usb_rxbuf+2 (engine16_merged.S:236,250)
                u.mem_write(syms["usb_rxbuf"] + 28, bytes([1, 0xD2]))
        b.uc.hook_add(UC_HOOK_CODE, h)
        b.run(entry)
        runs += 1

    for name, lv, entry in battery():
        for ph in (0.0, 0.5):
            go(lv, entry, ph)
    # a second pass with TB_OWED set, so the Design B response arms can run
    for name, lv, entry in battery():
        go(lv, entry, 0.0, owed=True)

    # The table block is data that objdump disassembles as instructions; it is
    # not code and must not be counted as cold.
    data_from = min((v & ~1) for k, v in syms.items()
                    if k in ("usb_tx_tables", "usb_tables"))
    ins = {a: v for a, v in ins.items() if a < data_from}
    lo = min(ins)
    hi = max(ins)
    rev = sorted((v & ~1, k) for k, v in syms.items() if lo <= (v & ~1) <= hi)
    import bisect
    addrs = [a for a, _ in rev]

    def owner(a):
        i = bisect.bisect_right(addrs, a) - 1
        return rev[i][1] if i >= 0 else "?"

    cold = {}
    warm = {}
    for a in sorted(ins):
        o = owner(a)
        if a in seen:
            warm[o] = warm.get(o, 0) + 1
        else:
            cold[o] = cold.get(o, 0) + 1

    tot = len(ins)
    hot = len(seen & set(ins))
    print("battery: %d runs, %d distinct stimuli" % (runs, len(battery())))
    print("instructions in the linked image: %d   executed: %d (%.1f%%)"
          % (tot, hot, 100.0 * hot / tot))
    print()
    print("%-28s %6s %6s  %s" % ("symbol", "cold", "warm", ""))
    for o in sorted(set(cold) | set(warm),
                    key=lambda x: -cold.get(x, 0)):
        c, w = cold.get(o, 0), warm.get(o, 0)
        if not c:
            continue
        flag = "NEVER EXECUTED" if not w else ""
        print("%-28s %6d %6d  %s" % (o, c, w, flag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
