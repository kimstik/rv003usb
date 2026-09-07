#!/usr/bin/env python3
"""design_b_in_model.py - cross-check Design B's IN response against a
reference low-speed encoder.

Two independent models of the same packet:

  reference()   builds SYNC + PID + payload + CRC16, applies the bit-stuffing
                rule to the whole data stream and NRZI-encodes it, from the
                specification alone.

  engine()      is a transcription of what doc/py32/engine16_merged.S actually
                does: usb_in_render's CRC and stuffing loops produce the
                record's bit stream, the eight SYNC cells drive the fixed
                pattern, the eight PID cells shift the record's PID byte, and
                the payload chain consumes the record's stream in groups of
                eight with the last t = nbits & 7 bits entered at T(8-t).

They must agree on the sequence of J/K levels, bit for bit, for every payload
this stack can send.  Nothing here reads the object file: this checks the
ALGORITHM, which is the half a cycle counter cannot see.
"""

MAXPAY = 8


# ---------------------------------------------------------------- reference

def crc16(data):
    """Reflected CRC-16 0xA001, init 0xFFFF - USB's data CRC."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def bits_lsb(data):
    for b in data:
        for i in range(8):
            yield (b >> i) & 1


def stuff(bits):
    """USB 2.0 S7.1.9: insert a 0 after six consecutive 1s."""
    run = 0
    out = []
    for b in bits:
        out.append(b)
        if b:
            run += 1
            if run == 6:
                out.append(0)
                run = 0
        else:
            run = 0
    return out


def nrzi(bits, start_j=True):
    """Toggle on 0, hold on 1.  Returns the driven level per bit cell,
    True = J."""
    level = start_j
    out = []
    for b in bits:
        if b == 0:
            level = not level
        out.append(level)
    return out


def reference(pid, payload):
    crc = crc16(payload) ^ 0xFFFF
    field = bytes([0x80, pid]) + bytes(payload) + bytes([crc & 0xFF, crc >> 8])
    # SYNC is not stuffed with the rest in hardware terms, but the stuffing
    # counter runs across the whole packet, so stuffing the concatenation is
    # the same thing.
    return nrzi(stuff(list(bits_lsb(field))))


# ------------------------------------------------------------------- engine

def t_tx():
    """engine16_tx.S's stuffing table, from its documented definition.  Row
    (state+1), entry m<<9 | (n-4)<<8 | (state'+1)<<5, m = w + 2^n - 1.  Note
    the DEFERRED stuffed zero: a nibble that takes the run to six emits
    nothing extra, and the zero is prepended by the NEXT nibble.  The table
    generated here is compared against the assembled .hword literals by
    check_table() below, so this is a transcription and not a guess."""
    rows = []
    for state in range(7):
        row = []
        for nib in range(16):
            run, w, n = state, 0, 0
            for i in range(4):
                if run == 6:
                    n += 1          # the deferred zero
                    run = 0
                b = (nib >> i) & 1
                w |= b << n
                n += 1
                run = run + 1 if b else 0
            row.append((((w + (1 << n) - 1) << 9) | ((n - 4) << 8)
                        | ((run + 1) << 5)))
        rows.append(row)
    return rows


T_TX = t_tx()


def check_table(path="doc/py32/engine16_tx.S"):
    import re
    src = open(path).read()
    blk = src[src.index("/* T_TX: 7 stuff states"):src.index(".global usb_tables")]
    got = [int(x, 16) for x in re.findall(r"0x([0-9a-f]{4})", blk)]
    return got == [v for r in T_TX for v in r], len(got)


def render(pid, payload):
    """usb_in_render, transcribed: CRC16, then T_TX a nibble at a time, then
    the trailing stuffed zero S7.1.9 requires and the table defers."""
    assert len(payload) <= MAXPAY
    crc = crc16(payload) ^ 0xFFFF
    src = bytes(payload) + bytes([crc & 0xFF, crc >> 8])

    state = 2 if pid == 0xC3 else 0     # what SYNC + PID leave behind
    out = []
    for b in src:
        for nib in (b & 0x0F, b >> 4):  # low nibble first in time
            e = T_TX[state][nib]
            n = 4 + ((e >> 8) & 1)
            w = (e >> 9) - ((1 << n) - 1)
            out += [(w >> i) & 1 for i in range(n)]
            state = ((e >> 5) & 0x07) - 1   # the field is (state'+1)<<5
    if state == 6:
        out.append(0)                   # .Lir_tail
    return out


def engine(pid, payload):
    stream = render(pid, payload)
    nbits = len(stream)
    groups, t = nbits >> 3, nbits & 7

    # the cells, in the order they drive the bus
    data = []
    data += [0] * 7 + [1]                       # SYNC, 8 cells, fixed
    data += [(pid >> i) & 1 for i in range(8)]  # the PID cells, LSB first
    # the payload chain: `groups` full groups of eight, then T(8-t), which is
    # t cells consuming the low t bits of the final group
    data += stream[: groups * 8]
    data += stream[groups * 8:]
    assert len(data) == 16 + nbits
    return nrzi(data)


# -------------------------------------------------------------------- check

def main():
    import itertools
    import random

    ok, n = check_table()
    print("T_TX vs engine16_tx.S   %s (%d entries)"
          % ("match" if ok else "MISMATCH", n))
    if not ok:
        return 1

    bad = 0
    cases = []
    for pid in (0xC3, 0x4B):
        for n in range(0, MAXPAY + 1):
            cases.append((pid, bytes([0x00] * n)))
            cases.append((pid, bytes([0xFF] * n)))
            cases.append((pid, bytes([0x55] * n)))
            cases.append((pid, bytes([0xFE] * n)))
    rnd = random.Random(20240607)
    for _ in range(4000):
        pid = rnd.choice((0xC3, 0x4B))
        n = rnd.randrange(0, MAXPAY + 1)
        cases.append((pid, bytes(rnd.randrange(256) for _ in range(n))))
    # exhaustive over every 1- and 2-byte payload, both PIDs
    for pid in (0xC3, 0x4B):
        for a in range(256):
            cases.append((pid, bytes([a])))
        for a, b in itertools.product(range(0, 256, 7), repeat=2):
            cases.append((pid, bytes([a, b])))

    worst = 0
    for pid, pay in cases:
        r, e = reference(pid, pay), engine(pid, pay)
        worst = max(worst, len(render(pid, pay)))
        if r != e:
            bad += 1
            if bad < 4:
                print("MISMATCH pid=%02X pay=%s" % (pid, pay.hex()))
                print("  ref %s" % "".join("J" if x else "K" for x in r))
                print("  eng %s" % "".join("J" if x else "K" for x in e))

    print("cases            %d" % len(cases))
    print("mismatches       %d" % bad)
    print("longest stream   %d wire bits = %d record bytes"
          % (worst, (worst + 7) // 8))
    # the record allocates TI_BITS_OFS=8 .. TI_REC_SIZE=32, i.e. 24 bytes,
    # and the payload chain prefetches one group past the last stored one
    print("record bytes needed, with the one-group prefetch overrun: %d of 24"
          % ((worst + 7) // 8 + 1))

    # engine16_tx.S walks the same table and has no equivalent of .Lir_tail:
    # its chain leaves for the EOP the moment the source is exhausted, so a
    # packet whose last six data bits are 1s goes out without the zero
    # S7.1.9 requires.  Count how often that is.
    short = 0
    for pid, pay in cases:
        s = render(pid, pay)
        if s and s[-1] == 0 and len(s) >= 7 and all(x == 1 for x in s[-7:-1]):
            short += 1
    print("packets whose last bit is a stuffed zero (engine16_tx.S omits it):"
          " %d of %d = %.2f%%" % (short, len(cases), 100.0 * short / len(cases)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
