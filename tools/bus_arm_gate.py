#!/usr/bin/env python3
"""bus_arm_gate.py - does the device DRIVE, and was it owed anything?

One question, asked of the assembled engine from outside it: for a packet the
device owes no response to, does a single write reach BSRR?  BSRR is the only
way this engine can put a level on the wire, so counting those writes is a
complete answer and it needs nothing from the engine's own state.

`tools/usb_bus_fuzz.py` asks the same question over random traffic with the
real C layer behind it.  This asks it of the six specific shapes
`doc/py32/BUS_COLLISIONS.md` is about, one shape per line, with the arm state
set up by hand so that each line isolates ONE reason to refuse:

  1  a handshake, with the ACK arm already owed and usb_rxbuf+3 preloaded with
     every PID the stale-read defect could find there
  2  a SOF and an OUT token arriving while the ACK arm is owed
  3  an IN token for another device's address
  4  an IN token for an endpoint this build does not have
  5  an IN token that IS ours and IS armed - the control, which must drive

The IN arm record is set up from the SPECIFICATION side: the token's wire
pattern is built here by NRZI and bit stuffing out of engine16_rx_bus.wire(),
not read out of the device.  That is also the check that the r5 key is
canonical - a token has exactly one wire form, so an exact compare is exact.

  python3 tools/bus_arm_gate.py [--source doc/py32/engine16_merged.S]
"""
import argparse, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine16_rx_bus import (Bus, build, wire, CELL, ROOT, RAM_BASE, J, K)
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR

PID_IN, PID_OUT, PID_SETUP, PID_SOF = 0x69, 0xE1, 0x2D, 0xA5
PID_ACK, PID_DATA0 = 0xD2, 0xC3
TB_OWED_OFS, TI_PAT_OFS, TI_R5_OFS, TI_REC_SIZE = 28, 0, 28, 32


def crc5(v11):
    """USB 2.0 S8.3.5.1, verified there against addr 0x15 / endp 0x0E -> 0x1D."""
    crc = 0x1F
    for i in range(11):
        if ((crc >> 4) ^ ((v11 >> i) & 1)) & 1:
            crc = ((crc << 1) & 0x1F) ^ 0x05
        else:
            crc = (crc << 1) & 0x1F
    return crc ^ 0x1F


def token(addr, endp):
    v = (addr & 0x7F) | ((endp & 0xF) << 7)
    v |= crc5(v) << 11
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def r5_of(pid, payload):
    """The value the receive engine's wire packer holds at the EOP stub.

    .Lprime seeds it with 1 - the last SYNC sample, which is a K, so D+ high -
    and CELL shifts one D+ sample in per bit time from the first PID bit on.
    So it is a sentinel followed by every wire bit of the packet after SYNC,
    NRZI and bit stuffing included, oldest at the top."""
    lv = wire(pid, payload, crc=False)
    r5 = 1
    for level in lv[8:]:                       # SYNC is never stuffed: 8 bits
        if level in (0,):                      # SE0 ends it; EOP is not sampled
            break
        r5 = (r5 << 1) | (1 if level == K else 0)
    assert r5 < (1 << 32), "a token cannot overflow r5"
    return r5


def run(b, entry=20):
    uc = b.uc
    b.cyc, b.pending = entry, None
    uc.reg_write(UC_ARM_REG_SP, RAM_BASE + 16 * 1024 - 0x100)
    stop = b.syms["_start"] & ~1
    uc.reg_write(UC_ARM_REG_LR, stop | 1)
    try:
        uc.emu_start(b.syms["usb_rx_engine16"] | 1, stop, 0, 200000)
    except Exception as exc:
        return exc
    return None


def probe(elf, syms, pid, payload, *, crc, owed=False, stale=None,
          arm_ep=None, arm_tok=None, entry=20):
    b = Bus(elf, syms, wire(pid, payload, crc=crc), 0.0, CELL)
    b.uc.mem_write(RAM_BASE, b"\x00" * (4 * 1024))
    rx = syms["usb_rxbuf"]
    if owed:
        b.uc.mem_write(rx + TB_OWED_OFS, b"\x01")
        b.uc.mem_write(rx + TB_OWED_OFS + 1, bytes([PID_ACK]))
    if stale is not None:
        b.uc.mem_write(rx + 3, bytes([stale]))
    if arm_ep is not None:
        rec = syms["usb_in_arm"] + arm_ep * TI_REC_SIZE
        b.uc.mem_write(rec + TI_PAT_OFS, arm_tok)
        if "usb_rx_wire" in syms:              # the arm gate's key
            v = r5_of(PID_IN, arm_tok)
            b.uc.mem_write(rec + TI_R5_OFS, v.to_bytes(4, "little"))
        b.uc.mem_write(rec + 3, b"\x01")       # groups
        b.uc.mem_write(rec + 4, (syms["usb_ti_eop"] | 1).to_bytes(4, "little"))
        b.uc.mem_write(rec + 2, bytes([PID_DATA0]))
        b.uc.mem_write(rec + 24, (rec + 8).to_bytes(4, "little"))
    run(b, entry)
    return len(b.driven)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source",
                    default=os.path.join(ROOT, "doc/py32/engine16_merged.S"))
    ap.add_argument("--endpoints", type=int, default=2)
    a = ap.parse_args()
    wd = tempfile.mkdtemp()
    elf, syms = build(a.source, os.path.join(ROOT, "doc/py32/engine16_tx.S"),
                      wd, tag="armgate")
    print("engine: %s" % a.source)
    print("%-58s %s" % ("packet, and the state it arrives in", "BSRR writes"))
    rows = []
    for name, byte in (("IN", PID_IN), ("SETUP", PID_SETUP),
                       ("DATA0", PID_DATA0), ("ACK", PID_ACK)):
        rows.append(("ACK handshake, ACK arm owed, usb_rxbuf+3 = 0x%02X (%s)"
                     % (byte, name),
                     probe(elf, syms, PID_ACK, b"", crc=False,
                           owed=True, stale=byte), 0))
    rows.append(("SOF token, ACK arm owed",
                 probe(elf, syms, PID_SOF, token(1, 0), crc=False, owed=True), 0))
    rows.append(("OUT token a0 e15, ACK arm owed",
                 probe(elf, syms, PID_OUT, token(0, 15), crc=False, owed=True), 0))
    ours = token(0, 0)
    rows.append(("IN token a17 e1, endpoint 0 armed for a0 e0",
                 probe(elf, syms, PID_IN, token(17, 1), crc=False,
                       arm_ep=0, arm_tok=ours), 0))
    rows.append(("IN token a0 e%d (out of range), endpoint 0 armed"
                 % a.endpoints,
                 probe(elf, syms, PID_IN, token(0, a.endpoints), crc=False,
                       arm_ep=0, arm_tok=ours), 0))
    rows.append(("IN token a0 e0, endpoint 0 armed for it  (THE CONTROL)",
                 probe(elf, syms, PID_IN, ours, crc=False,
                       arm_ep=0, arm_tok=ours), None))
    bad = 0
    for name, got, want in rows:
        if want is None:
            verdict = "answered" if got else "SILENT - the control did not fire"
            bad += 0 if got else 1
        else:
            verdict = "silent" if got == want else "DROVE THE BUS"
            bad += 0 if got == want else 1
        print("%-58s %5d   %s" % (name, got, verdict))
    print()
    print("shapes that must be silent and were not, plus a control that did "
          "not fire: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
