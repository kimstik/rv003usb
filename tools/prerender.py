#!/usr/bin/env python3
"""prerender.py - build-time renderer for Design B's IN arm records.

`usb_in_render` (doc/py32/engine16_merged.S) costs ~956 cycles = 40 us per IN
transaction and lands on the ISR that follows the host's ACK
(doc/py32/design_b_in.md S10).  For a static payload - a descriptor, a
zero-length status stage - the whole of that is a pure function of bytes known
at compile time: the CRC16 is deterministic, the bit stuffing is deterministic,
and NRZI is not in the record at all (it is the payload cell's own sbcs/eors).

This module renders those records on the host.  What it emits is byte-for-byte
what usb_in_render would have written, which is not a claim: tools/
prerender_check.py runs the ASSEMBLED usb_in_render under an emulator and
compares.  design_b_in.md S12's standing lesson is that a model sharing a
source with the artifact cannot validate it, so the check does not use this
file's arithmetic on either side of its comparison - it uses the engine's.

Record layout (TI_* in engine16_merged.S):

    +0  u16 pat      the legal (byte2,byte3) token halfword - RUNTIME ONLY,
                     a function of the device address the host assigns, so a
                     pre-rendered record cannot carry it and does not
    +2  u8  pid      0xC3 DATA0 / 0x4B DATA1
    +3  u8  groups   whole 8-bit groups of rendered wire bits
    +4  u32 tail     usb_ti_tails[nbits & 7], a link-time code pointer
    +8  u8  bits[]   the stuffed stream, LSB first, payload then CRC16
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from design_b_in_model import T_TX, crc16, MAXPAY   # noqa: E402

PID_DATA0 = 0xC3
PID_DATA1 = 0x4B


def render_bits(pid, payload):
    """usb_in_render's stuffing loop: T_TX a nibble at a time over
    payload+CRC16, then the trailing zero .Lir_tail emits when the table's
    deferred state 6 is left standing at end of stream."""
    if len(payload) > MAXPAY:
        raise ValueError("payload %d > %d" % (len(payload), MAXPAY))
    if pid not in (PID_DATA0, PID_DATA1):
        raise ValueError("pid %#x is not DATA0/DATA1" % pid)
    crc = crc16(payload) ^ 0xFFFF
    src = bytes(payload) + bytes([crc & 0xFF, crc >> 8])

    # what SYNC + the PID leave the stuffing run at.  SYNC 0x80 is
    # 0,0,0,0,0,0,0,1 LSB first and ends on one 1; DATA0 0xC3 is
    # 1,1,0,0,0,0,1,1 and ends on two; DATA1 0x4B is 1,1,0,1,0,0,1,0 and
    # ends on none.  Neither field can stuff on its own.
    state = 2 if pid == PID_DATA0 else 0
    out = []
    for b in src:
        for nib in (b & 0x0F, b >> 4):      # low nibble is first in time
            e = T_TX[state][nib]
            n = 4 + ((e >> 8) & 1)
            w = (e >> 9) - ((1 << n) - 1)
            out += [(w >> i) & 1 for i in range(n)]
            state = ((e >> 5) & 0x07) - 1
    if state == 6:
        out.append(0)                       # .Lir_tail, USB 2.0 S7.1.9
    return out


def pack_stream(bits):
    """The bytes .Lir_byte's TISTUFF and .Lir_flush actually store.

    The accumulator is `data + (1 << f)`: a sentinel bit, never any junk above
    it.  A whole byte is stored the moment f reaches 8, so full bytes are pure
    data; the final partial byte is stored by .Lir_flush WITH the sentinel
    still in it, at bit t.  When t == 0 .Lir_flush stores nothing at all and
    the byte at index `groups` is left as it was."""
    nbits = len(bits)
    groups, t = nbits >> 3, nbits & 7
    out = bytearray()
    for g in range(groups):
        v = 0
        for i in range(8):
            v |= bits[g * 8 + i] << i
        out.append(v)
    if t:
        v = 1 << t                          # the sentinel
        for i in range(t):
            v |= bits[groups * 8 + i] << i
        out.append(v)
    return bytes(out), groups, t


class Record:
    __slots__ = ("pid", "payload", "groups", "t", "stream", "nbits")

    def __init__(self, pid, payload):
        bits = render_bits(pid, payload)
        self.pid = pid
        self.payload = bytes(payload)
        self.nbits = len(bits)
        self.stream, self.groups, self.t = pack_stream(bits)

    def __repr__(self):
        return ("Record(pid=%02X len=%d nbits=%d groups=%d t=%d stream=%s)"
                % (self.pid, len(self.payload), self.nbits, self.groups,
                   self.t, self.stream.hex()))

    def key(self):
        return (self.pid, len(self.payload), self.groups, self.t, self.stream)


def render(pid, payload):
    return Record(pid, payload)


if __name__ == "__main__":
    for pid in (PID_DATA0, PID_DATA1):
        for pay in (b"", b"\x00", b"\xff" * 8, bytes(range(8))):
            print(render(pid, pay))
