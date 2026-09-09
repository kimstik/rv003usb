#!/usr/bin/env python3
"""Adversarial bus traffic against one invariant.

Three collisions were found yesterday by the scripted enumeration, each by a
single hand-injected error.  Finding them one at a time is not a method.  The
general statement of all three is one property:

    THE DEVICE MAY DRIVE THE BUS ONLY WHEN IT OWES A RESPONSE TO A PACKET
    THAT WAS ADDRESSED TO IT AND THAT IT ACCEPTED.

That is checkable mechanically over arbitrary traffic, and the entitlement is
computed HERE, from the specification and from what the host sent - never by
asking the engine.  A checker that shares a source with the artifact proves
nothing about it, which is how the SYNC defect survived four models.

The machinery - the emulator, the wire codec, the host - is `usb_enum_sim.py`.
This file is the fuzzer and the oracle.

Usage:  python3 tools/usb_bus_fuzz.py [--seqs N] [--len N] [--seed N]
"""
import argparse
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from usb_enum_sim import (build, Machine, Host, selfcheck,            # noqa: E402
                          pid_byte, PID_NAME)

# USB 2.0 Table 8-1
PID_OUT, PID_IN, PID_SOF, PID_SETUP = 0x1, 0x9, 0x5, 0xD
PID_DATA0, PID_DATA1 = 0x3, 0xB
PID_ACK, PID_NAK, PID_STALL = 0x2, 0xA, 0xE

OUR_ADDR = 0            # the fuzzer never sends SET_ADDRESS, so the device
                        # stays at the default address (S9.4.6)
ENDPOINTS = (0, 1)      # the demo builds with ENDPOINTS=2


class Oracle:
    """What the specification entitles the device to do, tracked from the
    host side alone."""

    def __init__(self):
        self.owed_by = None     # the token that armed a data stage, if any

    def entitled(self, kind, pidb, addr, endp, crc_ok):
        if kind == "token":
            p = pidb & 0xF
            if p == PID_IN:
                # S8.4.1: only an IN to this device and a real endpoint
                ok = (addr == OUR_ADDR and endp in ENDPOINTS)
                self.owed_by = None
                return ok
            if p in (PID_SETUP, PID_OUT):
                self.owed_by = (addr, endp) if \
                    (addr == OUR_ADDR and endp in ENDPOINTS) else None
                return False        # a token is never answered by itself
            self.owed_by = None
            return False            # SOF: S8.4.3, no response ever
        if kind == "data":
            # S8.5.3: the data stage of a transfer this device was addressed
            # for, and only if the packet checks out
            ok = self.owed_by is not None and crc_ok
            self.owed_by = None
            return ok
        # S8.4.4: a handshake is never answered by anything
        self.owed_by = None
        return False


def sequences(rnd, n, length):
    """Random traffic, weighted towards the shapes that carry state across
    packets - a token then something that is not its data, a handshake after
    an IN, an address that is not ours."""
    for _ in range(n):
        seq = []
        for _ in range(length):
            r = rnd.random()
            if r < 0.32:
                p = rnd.choice([PID_IN, PID_OUT, PID_SETUP, PID_SOF])
                seq.append(("token", p,
                            rnd.choice([OUR_ADDR, OUR_ADDR, 3, 17, 127]),
                            rnd.choice([0, 1, 1, 2, 5, 15]), True))
            elif r < 0.60:
                p = rnd.choice([PID_DATA0, PID_DATA1])
                seq.append(("data", p, 0, 0, rnd.random() > 0.15))
            elif r < 0.88:
                p = rnd.choice([PID_ACK, PID_NAK, PID_STALL])
                seq.append(("handshake", p, 0, 0, True))
            else:
                seq.append(("keepalive", 0, 0, 0, True))
        yield seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seqs", type=int, default=120)
    ap.add_argument("--len", type=int, default=8, dest="ln")
    ap.add_argument("--seed", type=int, default=20260909)
    a = ap.parse_args()

    selfcheck()
    wd = tempfile.mkdtemp(prefix="usbfuzz.")
    elf, syms = build(wd)
    rnd = random.Random(a.seed)

    total = 0
    viol = []
    for seq in sequences(rnd, a.seqs, a.ln):
        m = Machine(elf, syms)
        h = Host(m)
        o = Oracle()
        hist = []
        for kind, pidb, addr, endp, crc_ok in seq:
            pb = pid_byte(pidb)
            if kind == "token":
                r = h.token(pb, addr, endp)
                what = "%s a=%d e=%d" % (PID_NAME.get(pb, "?"), addr, endp)
            elif kind == "data":
                pay = bytes(rnd.randrange(256) for _ in range(rnd.randrange(9)))
                lv = None
                if crc_ok:
                    r = h.data(pb, pay)
                else:
                    from usb_enum_sim import data_packet, J, K
                    lv = data_packet(pb, pay)
                    lv = lv[:-8] + [K if x == J else J for x in lv[-8:-6]] \
                        + lv[-6:]
                    r = h.send(lv, "DATA bad CRC")
                what = "%s %dB%s" % (PID_NAME.get(pb, "?"), len(pay),
                                     "" if crc_ok else " CRCBAD")
            elif kind == "handshake":
                r = h.handshake(pb)
                what = PID_NAME.get(pb, "?")
            else:
                r = h.keepalive()
                what = "keep-alive"
            may = o.entitled(kind, pb, addr, endp, crc_ok)
            total += 1
            hist.append(what)
            if r.drove and not may:
                tau = (r.first_edge - r.tau) if (r.tau and r.first_edge) else None
                viol.append((list(hist), what, tau))
            if len(viol) > 60:
                break
        if len(viol) > 60:
            break

    print()
    print("packets driven at the device: %d" % total)
    print("INVARIANT VIOLATIONS (device drove when it owed nothing): %d"
          % len(viol))
    # Classify by what the device answered, because that is what names the
    # rule it broke - a pair table names instances, a rule table names the
    # class.
    RULES = [
        ("a handshake",                 "S8.4.4: a handshake is never answered",
         lambda w: w.split()[0] in ("ACK", "NAK", "STALL")),
        ("a SOF token",                 "S8.4.3: SOF carries no response",
         lambda w: w.startswith("SOF")),
        ("a token for another address", "S8.3.2.1 / S9.4.6: not ours",
         lambda w: w[0] in "IOS" and " a=" in w and not w.split("a=")[1].startswith("0 ")),
        ("an IN for a dead endpoint",   "S8.4.1: endpoint out of range",
         lambda w: w.startswith("IN") and " e=" in w
                   and int(w.split("e=")[1]) not in ENDPOINTS),
        ("a DATA with no token",        "S8.5.3: no transfer was armed",
         lambda w: w.startswith("DATA") and "CRCBAD" not in w),
        ("a DATA that failed its CRC",  "S8.7.1: must not be acknowledged",
         lambda w: "CRCBAD" in w),
        ("a keep-alive EOP",            "S7.1.7.6: it is not a packet",
         lambda w: w.startswith("keep")),
    ]
    tally = {}
    for hist, what, tau in viol:
        for name, cite, test in RULES:
            try:
                hit = test(what)
            except Exception:
                hit = False
            if hit:
                e = tally.setdefault(name, [0, cite, hist, 0])
                e[0] += 1
                if tau is not None and tau > 124:
                    e[3] += 1
                break
        else:
            e = tally.setdefault("unclassified", [0, "-", hist, 0])
            e[0] += 1
            if tau is not None and tau > 124:
                e[3] += 1
    if tally:
        print()
        print("%-30s %6s %8s   %s" % ("the device answered", "times",
                                       "past tau+124", "the rule"))
        for name, (n, cite, hist, late) in sorted(tally.items(),
                                                  key=lambda x: -x[1][0]):
            print("%-30s %6d %8d   %s" % (name, n, late, cite))
            print("%-30s %6s %8s   e.g. %s" % ("", "", "", " -> ".join(hist[-3:])))
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
