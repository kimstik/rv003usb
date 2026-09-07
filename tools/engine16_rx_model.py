#!/usr/bin/env python3
"""engine16_rx_model.py - bit-exact model of engine16_merged.S's RECEIVE
pipeline, driven by a host-side low-speed encoder.

The companion to design_b_in_model.py, which does the same job for the IN
response.  This one checks the half a cycle counter cannot see: that the
SEG0..SEGA chain still decodes packets.  It transliterates the `.S` one
instruction at a time - same registers, same masks, same order - and reads
T_UT and T_CRC16 out of the ASSEMBLED OBJECT, so the table the model uses is
the table the engine uses.

It carries two implementations of the byte-completion step:

  seg34_old   `subs r0,#8` / `asrs`+`mvns` / `movs`+`bics`+`adds`
  seg34_new   `lsls r0,#28`+`asrs` for the mask, `r0 &= 7` for the count
              (audit_discarded.md A15)

and runs both over every packet, asserting that every architectural register,
the buffer and the emitted count agree after every single segment.  That is
what makes the two forms interchangeable rather than merely plausible.

Usage:
  arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb \
      -c doc/py32/engine16_tx.S -o /tmp/tx.o
  tools/engine16_rx_model.py /tmp/tx.o
"""
import random
import struct
import subprocess
import sys

M32 = 0xFFFFFFFF
KSEEN = set()
T_UT = 512
BUFLEN = 32
BYTE_LIMIT = 24


# ------------------------------------------------------------------ tables

def load_tables(obj):
    """T_UT (128 words) and T_CRC16 (256 halfwords) out of the object."""
    nm = subprocess.run(['arm-none-eabi-nm', obj],
                        capture_output=True, text=True).stdout
    base = None
    for line in nm.splitlines():
        f = line.split()
        if len(f) == 3 and f[2] == 'usb_tables':
            base = int(f[0], 16)
    if base is None:
        sys.exit('usb_tables not found in ' + obj)
    sec = subprocess.run(['arm-none-eabi-objdump', '-h', obj],
                         capture_output=True, text=True).stdout
    name = '.text.engine16' if '.text.engine16' in sec else '.datacode'
    subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary',
                    '--only-section=' + name, obj, '/tmp/_tabs.bin'], check=True)
    blob = open('/tmp/_tabs.bin', 'rb').read()
    ut = [struct.unpack_from('<I', blob, base + T_UT + 4 * i)[0]
          for i in range(128)]
    crc = [struct.unpack_from('<H', blob, base + 2 * i)[0] for i in range(256)]
    return ut, crc


# --------------------------------------------------------------- reference

def crc16(data, init=0xFFFF):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c


def encode(pid, payload):
    """Host-side low-speed encoder.  Returns the D+ sample per bit time, from
    the first PID cell onward; SYNC is consumed by the phase lock and the
    pipeline is primed with it, so the stream starts after SYNC's last K."""
    c = crc16(payload) ^ 0xFFFF
    field = bytes([pid]) + bytes(payload) + bytes([c & 0xFF, (c >> 8) & 0xFF])
    bits = [(b >> i) & 1 for b in field for i in range(8)]
    # SYNC is 00000001; its trailing 1 is the first of the run the stuffing
    # rule counts.
    ones, stuffed = 1, []
    for b in bits:
        stuffed.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            stuffed.append(0)
            ones = 0
    level = 1                       # SYNC's last cell is K, i.e. D+ high
    out = []
    for b in stuffed:
        if not b:
            level ^= 1              # NRZI: a 0 toggles
        out.append(level)
    return out, field


# ------------------------------------------------------------- the engine

class Engine:
    def __init__(self, ut, crc, newform):
        self.UT, self.CRC, self.new = ut, crc, newform
        self.r0 = 0                 # bit count
        self.r1 = 0                 # SYNC high nibble
        self.r3 = 1                 # accumulator, sentinel at bit 0
        self.r5 = 1                 # wire packer: SYNC's last sample was K
        self.r8 = 4                 # SYNC low nibble * 4
        self.r10 = 0xFFFF           # CRC16 init
        self.r11 = T_UT             # state 0, biased with T_UT
        self.r12 = 0                # emitted bytes
        self.buf = [0] * BUFLEN
        self.ovf = False

    # ---- the segments, instruction for instruction
    def SEG0(self):
        self.r1 = (self.r1 << 2) | self.r11
        self.r1 = self.UT[(self.r1 - T_UT) >> 2]
        self.r11 = self.r1 & 0xFFFF

    def _append(self):
        n = (self.r1 >> 24) & 7
        m = self.r1 >> 27
        self.r3 = (self.r3 + ((m << self.r0) & M32)) & M32
        self.r0 += n

    def SEG1(self):
        self._append()
        self.r1 = self.r8 | self.r11
        self.r1 = self.UT[(self.r1 - T_UT) >> 2]

    def SEG2(self):
        self.r11 = self.r1 & 0xFFFF
        self._append()

    def SEG3(self):
        idx = self.r12 & (BUFLEN - 1)
        self.buf[idx] = self.r3 & 0xFF
        if self.new:
            # lsls r2,r0,#28 / asrs r1,r2,#31
            self.r1 = M32 if (self.r0 >> 3) & 1 else 0
        else:
            self.r0 = (self.r0 - 8) & M32
            self.r1 = M32 if (self.r0 >> 31) & 1 else 0
            self.r1 ^= M32

    def SEG3_TAIL(self):
        if self.new:
            self.r0 &= 7
        else:
            self.r0 = (self.r0 + (8 & ~self.r1 & M32)) & M32

    def SEG4(self):
        self.SEG3_TAIL()
        self.r8 = self.CRC[(self.r10 ^ self.r3) & 0xFF]

    def SEG5(self):
        s = 8 & self.r1
        self.r3 >>= s
        self.r12 = (self.r12 - (self.r1 if self.r1 < 0x80000000
                                else self.r1 - 0x100000000)) & M32
        if self.r12 >= BYTE_LIMIT:
            self.ovf = True
        self.r1 &= ~(M32 if self.r12 < 3 else 0) & M32

    def SEG6(self):
        p = self.r8 & self.r1
        s = 8 & self.r1
        self.r10 = (self.r10 >> s) ^ p

    def SEGA(self):
        r1 = (~((self.r5 >> 1) ^ self.r5)) & M32
        r1 &= 0xFF
        self.r8 = (r1 & 0xF) * 4
        self.r1 = r1 >> 4

    def HEAD(self, unsampled):
        """.Ltb_head / .Lti_head / rx_flush7, after A17 removed the first
        uxtb: the left shift is by 1..7 and the uxtb below clears what it
        pushes up."""
        r1 = (~((self.r5 >> 1) ^ self.r5)) & M32
        if not self.new:
            r1 &= 0xFF              # the uxtb A17 removed
        r1 = (r1 << unsampled) & M32
        r1 &= 0xFF
        self.r8 = (r1 & 0xF) * 4
        self.r1 = r1 >> 4

    SEGS = [SEG0, SEG1, SEG2, SEG3, SEG4, SEG5, SEG6]

    def state(self, with_r0=True):
        # Between SEG3 and SEG3_TAIL the two forms hold r0 differently on
        # purpose - that IS the change - so the caller drops it for that one
        # boundary and compares it again the instant SEG3_TAIL has run.
        return ((self.r0 if with_r0 else None), self.r1, self.r3, self.r5,
                self.r8, self.r10, self.r11, self.r12, tuple(self.buf),
                self.ovf)

    def sample(self, dplus):
        self.r5 = ((self.r5 << 1) | dplus) & M32


def run(samples, ut, crc, newform, twin=None):
    """Drive the chain.  `twin` is the other implementation, stepped in
    lockstep; every segment boundary is compared."""
    e = Engine(ut, crc, newform)
    es = [e] + ([twin] if twin else [])

    def step(fn, *a):
        for x in es:
            fn(x, *a)
        if twin:
            r0 = fn is not Engine.SEG3
            assert e.state(r0) == twin.state(r0), fn.__name__

    # A CELL samples FIRST (ldr/ands/beq/lsrs/adcs) and then runs its
    # segment, which is why cell 7's SEGA sees all eight of the byte's
    # samples.  Getting this backwards shifts the whole stream by one bit.
    i, cell = 0, 0
    while i < len(samples):
        for x in es:
            x.sample(samples[i])
        i += 1
        if cell < 7:
            step(Engine.SEGS[cell])
        else:
            step(Engine.SEGA)
        cell = (cell + 1) % 8
    # EOP: SE0 seen in cell `cell`, before that cell's segment ran
    k = cell
    KSEEN.add(k)
    for seg in Engine.SEGS[k:]:
        step(seg)
    if k != 0:
        step(Engine.HEAD, 8 - k)
        for seg in Engine.SEGS:
            step(seg)
    return e


def main():
    obj = sys.argv[1] if len(sys.argv) > 1 else '/tmp/tx.o'
    ut, crc = load_tables(obj)
    random.seed(20260907)
    cases = [(0xC3, b''), (0x4B, b''),
             (0xC3, bytes(8)), (0xC3, b'\xff' * 8),
             (0xC3, bytes(range(8))), (0x4B, b'\x00\xff' * 4)]
    cases += [(random.choice((0xC3, 0x4B)),
               bytes(random.randrange(256) for _ in range(random.randrange(9))))
              for _ in range(400)]
    # tokens too: 4 emitted bytes, the length TIG2 demands
    cases += [(0x69, b'')]
    # The EOP cell K is (number of stuffed bits) mod 8 for a whole-byte data
    # field, so uniform random payloads only ever reach a few values of K.
    # Every K matters: it selects the flush entry AND the head's shift.  Fill
    # the gaps with 1-heavy payloads, which is where stuffing happens.
    have = set()
    extra = []
    for _ in range(200000):
        if len(have) == 8:
            break
        pay = bytes(random.choice((0xFF, 0xFF, 0xFE, 0x7F, 0xFC,
                                   random.randrange(256)))
                    for _ in range(random.randrange(1, 9)))
        pid = random.choice((0xC3, 0x4B))
        n = len(encode(pid, pay)[0]) % 8
        if n not in have:
            have.add(n)
            extra.append((pid, pay))
    cases += extra

    fails = 0
    for pid, pay in cases:
        samples, field = encode(pid, pay)
        e = run(samples, ut, crc, True,
                twin=Engine(ut, crc, False))
        want = [0x80, pid] + list(pay) + list(field[-2:])
        ok = (e.buf[:len(want)] == want
              and e.r12 == len(want)
              and e.r10 == 0xB001
              and (e.r11 >> 6) != (T_UT >> 6) + 7
              and not e.ovf)
        if not ok:
            fails += 1
            print('FAIL pid=%02x pay=%s  buf=%s count=%d crc=%04x state=%d' %
                  (pid, pay.hex(), [hex(b) for b in e.buf[:len(want) + 1]],
                   e.r12, e.r10, e.r11 >> 6))
    print('%d packets, %d failures  (old and new SEG3/SEG4 and the two '
          'head NRZI forms in lockstep)' % (len(cases), fails))
    print('EOP cell K exercised: %s' % sorted(KSEEN))

    # the equivalence itself, exhaustively over the invariant's range
    for r0 in range(16):
        a = Engine(ut, crc, False); a.r0 = r0; a.r3 = 0; a.r12 = 0
        b = Engine(ut, crc, True);  b.r0 = r0; b.r3 = 0; b.r12 = 0
        a.SEG3(); a.SEG3_TAIL()
        b.SEG3(); b.SEG3_TAIL()
        assert (a.r0, a.r1) == (b.r0, b.r1), r0
    print('SEG3/SEG3_TAIL identical for every r0 in 0..15 (the invariant)')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
