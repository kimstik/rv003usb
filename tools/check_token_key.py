#!/usr/bin/env python3
"""Is the IN gate's one-word token key actually injective?

`BUS_COLLISIONS.md` arms the IN response by comparing r5 - which `CELL`'s
`adcs r5, r5` has filled with one sampled D+ level per wire bit - against a
stored pattern.  One 32-bit compare is claimed to settle the PID, the address,
the endpoint, the CRC5 and the length at once.  Everything about the IN arm's
correctness rests on that claim: if two different packets share a key, the
gate answers a token that is not ours.

So check it independently.  The keys here are built from the SPECIFICATION's
wire encoding (`usb_enum_sim.py`'s codec, which self-checks against USB 2.0
S8.3.5.1's worked CRC5 example), never from the engine or its tables.

    python3 tools/check_token_key.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from usb_enum_sim import (token_packet, data_packet, handshake_packet,   # noqa
                          pid_byte, K, SE0, selfcheck)

TOKEN_PIDS = (("OUT", 0x1), ("IN", 0x9), ("SOF", 0x5), ("SETUP", 0xD))
DATA_PIDS = ((0x3, "DATA0"), (0xB, "DATA1"))
HS_PIDS = ((0x2, "ACK"), (0xA, "NAK"), (0xE, "STALL"))


def key(levels):
    """What r5 holds at the EOP stub: a sentinel from the last SYNC sample,
    then one bit per wire bit of everything after SYNC."""
    r5 = 1
    for lv in levels[8:]:
        if lv == SE0:
            break
        r5 = (r5 << 1) | (1 if lv == K else 0)
    return r5


def main():
    selfcheck()
    seen, coll = {}, []
    for name, p4 in TOKEN_PIDS:
        for addr in range(128):
            for endp in range(16):
                k = key(token_packet(pid_byte(p4), addr, endp))
                tag = "%s a=%d e=%d" % (name, addr, endp)
                if k in seen:
                    coll.append((seen[k], tag, k))
                seen[k] = tag
    print("tokens hashed        %d" % (len(TOKEN_PIDS) * 128 * 16))
    print("distinct keys        %d" % len(seen))
    print("collisions           %d" % len(coll))
    for a, b, k in coll[:5]:
        print("   0x%08x  %s == %s" % (k, a, b))
    w = max(k.bit_length() for k in seen)
    print("widest key           %d bits, sentinel included (r5 holds 32)" % w)

    bad = 0
    for pl in (b"", b"\x00", b"\xff", bytes(range(8)), b"\x7f\xff\xff\xff",
               b"\xff" * 8, bytes([0xAA] * 8)):
        for p4, nm in DATA_PIDS:
            k = key(data_packet(pid_byte(p4), pl))
            if k in seen:
                bad += 1
                print("   %s %s collides with %s" % (nm, pl.hex(), seen[k]))
    for p4, nm in HS_PIDS:
        k = key(handshake_packet(pid_byte(p4)))
        if k in seen:
            bad += 1
            print("   %s collides with %s" % (nm, seen[k]))
    print("non-token packets colliding with a token key: %d" % bad)
    return 1 if (coll or bad or w > 32) else 0


if __name__ == "__main__":
    sys.exit(main())
