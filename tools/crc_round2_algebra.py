#!/usr/bin/env python3
"""GF(2) facts for CRC_ROUND2: inverted suffixes, periodic functionals,
order of x mod the degree-15 factor, byte-XOR."""
import random

def pmod(a, m):
    dm = m.bit_length() - 1
    while a.bit_length() - 1 >= dm:
        a ^= m << (a.bit_length() - 1 - dm)
    return a
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
def pgcd(a, b):
    while b: a, b = b, pmod(a, b)
    return a

G16 = 0x18005                      # x^16+x^15+x^2+1
P15 = (1 << 15) | 0b11             # x^15+x+1
assert pmul(0b11, P15) == G16

# order of x modulo P15
def order_x(m):
    n = 1; v = 2
    while v != 1:
        v = pmod(v << 1, m); n += 1
    return n
o = order_x(P15)
print("order of x mod (x^15+x+1) =", o, "(2^15-1 =", 2**15 - 1, ")  primitive:", o == 2**15 - 1)

# Inverted suffix / burst of length L: error polynomial ones(L) = sum x^i, i<L.
# Undetected by CRC16 iff G16 | ones(L).  Check L = 1..100 exhaustively, and
# report the smallest L that is undetected.
def ones(L): return (1 << L) - 1
und = [L for L in range(1, 101) if pmod(ones(L), G16) == 0]
print("inverted run lengths 1..100 undetected by CRC16:", und)
# parity view: undetected by parity iff L even
print("... undetected by parity: every even L")
# find the smallest L at all
r = 0; L = 0
while True:
    r = pmod((r << 1) | 1, G16); L += 1      # r = ones(L) mod g16, incrementally
    if r == 0 or L > 200000: break
print("smallest inverted run undetected by CRC16: L =", L, "= 2 * order" if L == 2*o else "")

# Data-domain: is inverting a suffix of the (message||CRC) bit string, of any
# length 1..88, ever a codeword?  Direct check via the reference CRC.
def crc16(data, init=0xFFFF):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c
rng = random.Random(3)
bad = 0; tot = 0
for _ in range(2000):
    pl = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
    c = crc16(pl) ^ 0xFFFF
    field = pl + bytes([c & 0xFF, c >> 8])
    n = len(field) * 8
    v = int.from_bytes(field, 'little')
    for L in range(1, n + 1):
        mask = ((1 << L) - 1) << (n - L)     # suffix in transmission order = high bits
        f2 = (v ^ mask).to_bytes(len(field), 'little')
        tot += 1
        if crc16(f2) == 0xB001: bad += 1
print(f"inverted suffixes over 2000 packets, all lengths: {tot} cases, residue still valid in {bad}")

# Periodic functionals.  A linear functional on the code with byte-periodic
# mask (period p) exists other than parity iff (x^15+x+1) | (x^p + 1).
for p in (8, 16, 32, 64):
    print(f"period {p}: (x^15+x+1) divides x^{p}+1 ?", pmod((1 << p) | 1, P15) == 0)
print("smallest p with (x^15+x+1) | x^p+1 is the order:", o)

# Byte-XOR (x^8+1 = (x+1)^8): gcd with g16
print("gcd(x^8+1, g16) =", hex(pgcd((1 << 8) | 1, G16)), "(0x3 = x+1 -> parity only)")
# Numerical: XOR of all bytes of payload||crc over valid packets - how many distinct?
vals = set()
for _ in range(5000):
    pl = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
    c = crc16(pl) ^ 0xFFFF
    field = pl + bytes([c & 0xFF, c >> 8])
    x = 0
    for b in field: x ^= b
    vals.add(x)
print("distinct byte-XOR values over 5000 valid packets:", len(vals), "of 256; all even parity:", all(bin(v).count('1') % 2 == 0 for v in vals))

# Single-glitch pattern x^k (1+x): detected by CRC16 always? g16 | x^k(1+x) impossible since deg... check
print("adjacent pair x^k(1+x) mod g16 == 0 for some k<100?", any(pmod(0b11 << k, G16) == 0 for k in range(100)))
