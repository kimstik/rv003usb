#!/usr/bin/env python3
"""
make_test_vcd.py -- synthetic LS-USB VCD generator for wg015vcd.py selftest.

Emits, on dp/dn/dbg0 1-bit channels (timescale 1 ps):
  - N keepalives (SE0, 2 bit-times) from idle J,
  - N transactions: SETUP token + DATA0(8-byte GET_DESCRIPTOR) from the host
    at nominal 1.5 Mbit/s, then a device ACK at the device clock
    (48 MHz * (1 + ppm/1e6)) after a configurable turnaround,
  - P10-style markers on dbg0:
      host packets : 1 toggle at ISR entry (D- fall + entry latency), then one
                     toggle per bit cell at offset phase0 + slope*k + jitter,
                     where slope = 32*ppm/1e6 + extra drift (cycles/bit)
      device ACK   : 1 toggle per TX cell boundary (device clock)
      keepalives   : 1 stray entry-style toggle (robustness fodder)

Ground truth is written as JSON next to the VCD (--truth) for the selftest.
Python 3 stdlib only; deterministic for a given --seed.
"""

import argparse
import json
import random
import sys

F_CPU = 48e6
CYC_NOM_PS = 1e12 / F_CPU                 # 20833.33 ps
HOST_BIT_PS = 1e12 / 1.5e6                # 666666.67 ps


# ---- CRC (USB wire convention, reflected; check values from the CRC catalog)

def crc5_bits(bits):
    r = 0x1F
    for b in bits:
        r = (r >> 1) ^ 0x14 if (r ^ b) & 1 else r >> 1
    return r ^ 0x1F


def crc16_bits(bits):
    r = 0xFFFF
    for b in bits:
        r = (r >> 1) ^ 0xA001 if (r ^ b) & 1 else r >> 1
    return r ^ 0xFFFF


_check = [(byte >> i) & 1 for byte in b"123456789" for i in range(8)]
assert crc5_bits(_check) == 0x19
assert crc16_bits(_check) == 0xB4C8


# ---- packet bit construction (LSB first on the wire)

def byte_bits(bs):
    return [(b >> i) & 1 for b in bs for i in range(8)]


def pid_byte(pid):
    return pid | ((pid ^ 0xF) << 4)


def token_bits(pid, addr, ep):
    field = (addr & 0x7F) | ((ep & 0xF) << 7)
    fb = [(field >> i) & 1 for i in range(11)]
    c = crc5_bits(fb)
    return byte_bits([pid_byte(pid)]) + fb + [(c >> i) & 1 for i in range(5)]


def data_bits(pid, payload):
    db = byte_bits(payload)
    c = crc16_bits(db)
    return byte_bits([pid_byte(pid)]) + db + [(c >> i) & 1 for i in range(16)]


def hs_bits(pid):
    return byte_bits([pid_byte(pid)])


def stuff(bits):
    out, ones = [], 0
    for b in bits:
        out.append(b)
        if b:
            ones += 1
            if ones == 6:
                out.append(0)
                ones = 0
        else:
            ones = 0
    return out


def nrzi_cells(bits):
    """bits (incl. sync) -> per-cell line state 'J'/'K', starting from idle J."""
    lvl = 'J'
    cells = []
    for b in bits:
        if b == 0:
            lvl = 'K' if lvl == 'J' else 'J'
        cells.append(lvl)
    return cells


SYNC = [0] * 7 + [1]

PIDS = {'SETUP': 0xD, 'OUT': 0x1, 'IN': 0x9, 'DATA0': 0x3, 'DATA1': 0xB,
        'ACK': 0x2, 'NAK': 0xA, 'STALL': 0xE}


# ---- emitter

class Emitter:
    def __init__(self):
        self.dp = [(0, 0)]
        self.dn = [(0, 1)]
        self.dbg_times = []

    def set_bus(self, t_ps, dpv, dnv):
        t = int(round(t_ps))
        self.dp.append((t, dpv))
        self.dn.append((t, dnv))

    def mark(self, t_ps):
        self.dbg_times.append(int(round(t_ps)))

    def emit_packet(self, t0, cells, bit_ps):
        """Wire a packet whose cell 0 starts at t0. Returns (se0_start, j_start)."""
        lvl = 'J'
        for k, c in enumerate(cells):
            if c != lvl:
                t = t0 + k * bit_ps
                if c == 'K':
                    self.set_bus(t, 1, 0)
                else:
                    self.set_bus(t, 0, 1)
                lvl = c
        t_end = t0 + len(cells) * bit_ps
        self.set_bus(t_end, 0, 0)                    # EOP SE0
        j = t_end + 2 * bit_ps
        self.set_bus(j, 0, 1)                        # back to J
        return t_end, j

    def emit_keepalive(self, t0, bit_ps):
        self.set_bus(t0, 0, 0)
        self.set_bus(t0 + 2 * bit_ps, 0, 1)

    def write(self, path):
        events = []
        for t, v in self._clean(self.dp):
            events.append((t, '!', str(v)))
        for t, v in self._clean(self.dn):
            events.append((t, '"', str(v)))
        lvl = 0
        last_t = -1
        for t in sorted(self.dbg_times):
            if t <= last_t:
                t = last_t + 1                      # keep toggles strictly ordered
            lvl ^= 1
            events.append((t, '#', str(lvl)))
            last_t = t
        events.sort(key=lambda e: e[0])
        with open(path, 'w') as f:
            f.write("$date synthetic $end\n$version make_test_vcd.py $end\n")
            f.write("$timescale 1ps $end\n")
            f.write("$scope module tb $end\n")
            f.write("$var wire 1 ! dp $end\n")
            f.write("$var wire 1 \" dn $end\n")
            f.write("$var wire 1 # dbg0 $end\n")
            f.write("$upscope $end\n$enddefinitions $end\n")
            f.write("#0\n$dumpvars\n0!\n1\"\n0#\n$end\n")
            cur = 0
            for t, ch, v in events:
                if t == 0:
                    continue                        # initials already dumped
                if t != cur:
                    f.write("#%d\n" % t)
                    cur = t
                f.write("%s%s\n" % (v, ch))
            f.write("#%d\n" % (cur + 5_000_000))     # 5 us idle tail

    @staticmethod
    def _clean(seq):
        out = []
        for t, v in sorted(seq, key=lambda e: e[0]):
            if not out or out[-1][1] != v:
                out.append((t, v))
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--out', required=True, help="output VCD path")
    ap.add_argument('--truth', help="write injected ground truth JSON here")
    ap.add_argument('--ppm', type=float, default=0.0,
                    help="device clock offset in ppm (default 0)")
    ap.add_argument('--entry', type=float, default=50.0,
                    help="ISR entry latency, device cycles (default 50)")
    ap.add_argument('--entry-jitter', type=float, default=1.0,
                    help="uniform +- jitter on entry latency, cycles")
    ap.add_argument('--phase0', type=float, default=10.0,
                    help="initial sample offset inside bit cell, cycles")
    ap.add_argument('--drift', type=float, default=0.0,
                    help="extra sample drift, cycles/bit (on top of ppm-induced)")
    ap.add_argument('--sample-jitter', type=float, default=0.3,
                    help="uniform +- jitter on sample position, cycles")
    ap.add_argument('--turnaround', type=float, default=4.0,
                    help="device response turnaround, host bit-times")
    ap.add_argument('--transactions', type=int, default=4)
    ap.add_argument('--keepalives', type=int, default=3)
    ap.add_argument('--seed', type=int, default=1)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    cyc_dev = CYC_NOM_PS * (1 + args.ppm / 1e6)
    slope = 32.0 * args.ppm / 1e6 + args.drift      # cycles(nominal)/host bit
    em = Emitter()

    def host_markers(t0, ncells):
        entry_t = t0 + (args.entry +
                        rnd.uniform(-args.entry_jitter, args.entry_jitter)) * cyc_dev
        em.mark(entry_t)
        for k in range(ncells):
            off = (args.phase0 + slope * k +
                   rnd.uniform(-args.sample_jitter, args.sample_jitter))
            ts = t0 + k * HOST_BIT_PS + off * CYC_NOM_PS
            if ts > entry_t + 2 * CYC_NOM_PS:
                em.mark(ts)

    t = 5e6  # 5 us of idle J first
    for _ in range(args.keepalives):
        em.emit_keepalive(t, HOST_BIT_PS)
        em.mark(t + args.entry * cyc_dev)           # stray ISR-entry toggle
        t += 200e6                                  # 200 us spacing

    setup = token_bits(PIDS['SETUP'], addr=0, ep=0)
    get_desc = data_bits(PIDS['DATA0'],
                         [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])
    ack = hs_bits(PIDS['ACK'])

    for _ in range(args.transactions):
        # SETUP (host)
        cells = nrzi_cells(SYNC + stuff(setup))
        se0, j = em.emit_packet(t, cells, HOST_BIT_PS)
        host_markers(t, len(cells))
        # DATA0 (host), normal inter-packet gap
        t = j + 3 * HOST_BIT_PS
        cells = nrzi_cells(SYNC + stuff(get_desc))
        se0, j = em.emit_packet(t, cells, HOST_BIT_PS)
        host_markers(t, len(cells))
        # ACK (device clock) after the injected turnaround
        t = j + args.turnaround * HOST_BIT_PS
        bit_dev = 32 * cyc_dev
        cells = nrzi_cells(SYNC + stuff(ack))
        se0, j = em.emit_packet(t, cells, bit_dev)
        for k in range(len(cells) + 1):             # TX cell-boundary markers
            em.mark(t + k * bit_dev)
        t = j + 50e6                                # 50 us to next transaction

    em.write(args.out)

    truth = {
        'ppm': args.ppm,
        'entry_cyc': args.entry,
        'entry_jitter_cyc': args.entry_jitter,
        'phase0_cyc': args.phase0,
        'slope_cyc_per_bit': slope,
        'sample_jitter_cyc': args.sample_jitter,
        'turnaround_bt': args.turnaround,
        'tx_cell_cyc': 32.0 * (1 + args.ppm / 1e6),
        'transactions': args.transactions,
        'keepalives': args.keepalives,
        'seed': args.seed,
    }
    if args.truth:
        with open(args.truth, 'w') as f:
            json.dump(truth, f, indent=1)
    else:
        json.dump(truth, sys.stdout, indent=1)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
