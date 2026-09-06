#!/usr/bin/env python3
"""CRC_ROUND2 simulator: sample-level NRZI channel, engine-faithful decoder,
reference CRC16, disturbance classes.  Self-tests first."""
import random, sys
from collections import Counter

# ---------------------------------------------------------------- reference
def crc16_bitwise(data, init=0xFFFF):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c
assert crc16_bitwise(b"123456789") == 0x4B37

def usb_data_field(payload):
    c = crc16_bitwise(payload) ^ 0xFFFF
    return bytes(payload) + bytes([c & 0xFF, (c >> 8) & 0xFF])

def residue_ok(field):            # field = payload || crc as received
    return crc16_bitwise(field) == 0xB001

def parity_ok(field):
    return bin(int.from_bytes(field, 'little')).count('1') % 2 == 0

PID_DATA0, PID_DATA1 = 0xC3, 0x4B

# ---------------------------------------------------------------- wire model
def bits_lsb_first(bs):
    return [(b >> i) & 1 for b in bs for i in range(8)]

def stuff(bits):
    out, run = [], 0
    for b in bits:
        out.append(b)
        run = run + 1 if b else 0
        if run == 6:
            out.append(0); run = 0
    return out

def nrzi_encode(bits, start=1):   # returns D+ level per cell; J = D+ low? we
    # model only the level that the engine captures (D+).  Idle J on LS has
    # D- high, D+ low; K is D+ high.  SYNC begins with K.  Data 1 = no change.
    lvl, out = start, []
    for b in bits:
        if b == 0:
            lvl ^= 1
        out.append(lvl)
    return out

def encode_packet(pid, payload):
    """Samples of D+ from the first SYNC cell to the last data cell.
    Idle is J (D+ = 0); SYNC's first bit is a 0 -> transition to K."""
    bits = bits_lsb_first(bytes([0x80, pid]) + usb_data_field(payload))
    return nrzi_encode(stuff(bits), start=0)

# ---------------------------------------------------------------- decoder
# Faithful to engine16_merged.S: one sample per cell, d = NOT(s ^ s_prev),
# phase lock on SYNC's last edge means the engine starts decoding at the
# first PID cell with prev = last SYNC sample; but since we corrupt samples
# inside SYNC too, decode everything from the idle level.
def decode(samples, prev=0):
    d = []
    for s in samples:
        d.append(1 if s == prev else 0)
        prev = s
    return d

def unstuff(d):
    """Returns (data_bits, violation).  Seven ones -> sticky violation."""
    out, run, viol = [], 0, False
    for b in d:
        if run == 6:
            run = 0
            if b == 1:
                viol = True     # seventh 1: stuffing violation (sticky)
            continue            # the stuffed 0 is dropped
        out.append(b)
        run = run + 1 if b else 0
    return out, viol

def to_bytes(bits):
    n = len(bits) // 8
    return bytes(sum(bits[8*i+j] << j for j in range(8)) for i in range(n))

def receive(samples):
    """Engine verdicts on a sample stream.  Returns dict."""
    d = decode(samples)
    data, viol = unstuff(d)
    bs = to_bytes(data)
    r = {'bytes': bs, 'viol': viol, 'struct': True, 'crc': None, 'par': None}
    # structural checks of engine16_merged.S .Lrx_tail / .Ldata
    if viol or not (2 <= len(bs) <= 12) or bs[0] != 0x80:
        r['struct'] = False
    elif ((bs[1] >> 4) ^ (bs[1] & 0xF)) != 0xF:
        r['struct'] = False
    elif (bs[1] & 3) == 3 and len(bs) < 4:
        r['struct'] = False
    if r['struct']:
        field = bs[2:]
        r['crc'] = residue_ok(field)
        r['par'] = parity_ok(field)
    return r

# ---------------------------------------------------------------- self-test
def selftest():
    rng = random.Random(1)
    for _ in range(3000):
        pl = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
        pid = rng.choice([PID_DATA0, PID_DATA1])
        r = receive(encode_packet(pid, pl))
        assert r['struct'] and r['crc'] and r['par'], (pl, r)
        assert r['bytes'] == bytes([0x80, pid]) + usb_data_field(pl)
    for _ in range(500):
        pl = bytes([0xFF] * rng.randrange(1, 9))
        s = encode_packet(PID_DATA0, pl)
        assert receive(s)['crc']
    print("selftest ok")

# ---------------------------------------------------------------- disturbances
def flip_one(s, k):
    s = list(s); s[k] ^= 1; return s
def invert_from(s, k):
    return s[:k] + [x ^ 1 for x in s[k:]]
def alternate_from(s, k):            # inverts every decoded bit from k on
    return s[:k] + [x ^ ((i + 1) & 1) for i, x in enumerate(s[k:])]
def dup_sample(s, k):                # receiver sampled cell k twice (slip late)
    return s[:k+1] + s[k:]
def drop_sample(s, k):               # receiver skipped cell k (slip early)
    return s[:k] + s[k+1:]
def burst(s, k, n):
    s = list(s)
    for i in range(k, min(k+n, len(s))): s[i] ^= 1
    return s

def weight_of(orig_samples, bad_samples):
    """Hamming weight of the decoded-bit error before unstuffing, and
    whether lengths differ."""
    a, b = decode(orig_samples), decode(bad_samples)
    if len(a) != len(b):
        return None
    return sum(x != y for x, y in zip(a, b))

def run(classname, mutate, trials=40000, seed=11, kmin_from_pid=True):
    rng = random.Random(seed)
    n = 0; det_struct = det_crc = det_par = 0; w = Counter(); undetected_crc_examples = 0
    while n < trials:
        pl = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 9)))
        pid = rng.choice([PID_DATA0, PID_DATA1])
        s = encode_packet(pid, pl)
        good = receive(s)['bytes']
        k = rng.randrange(8 if kmin_from_pid else 0, len(s))
        bad = mutate(s, k, rng)
        r = receive(bad)
        if r['bytes'] == good:
            continue                     # not a corruption of what we accept
        n += 1
        w[weight_of(s, bad)] += 1
        if not r['struct']:
            det_struct += 1; det_crc += 1; det_par += 1
        else:
            if not r['crc']: det_crc += 1
            if not r['par']: det_par += 1
    tot = sum(w.values())
    wdist = {k: round(v/tot, 4) for k, v in sorted(w.items(), key=lambda kv: (kv[0] is None, kv[0]))}
    print(f"{classname:34s} n={n:6d} free={det_struct/n:.4f} crc16={det_crc/n:.4f} "
          f"parity={det_par/n:.4f}  weight={wdist}")
    return det_crc / n

if __name__ == '__main__':
    selftest()
    print("--- detection by class; 'free' = structural checks only; weight = "
          "decoded-bit error weight pre-unstuff (None = length changed)")
    run("1 sample flipped", lambda s, k, r: flip_one(s, k))
    run("level inverted k..EOP", lambda s, k, r: invert_from(s, k))
    run("alternating flip k..EOP (decoded inv)", lambda s, k, r: alternate_from(s, k))
    run("duplicate sample k (slip late)", lambda s, k, r: dup_sample(s, k))
    run("drop sample k (slip early)", lambda s, k, r: drop_sample(s, k))
    run("burst 2 samples", lambda s, k, r: burst(s, k, 2))
    run("burst 8 samples", lambda s, k, r: burst(s, k, 8))
    run("2 random samples", lambda s, k, r: flip_one(flip_one(s, k), r.randrange(8, len(s))))
