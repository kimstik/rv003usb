#!/usr/bin/env python3
"""
wg015vcd.py -- hardware-in-the-loop VCD analyzer for the rv003usb WG015 port.

Decodes low-speed USB (1.5 Mbit/s) traffic from a logic-analyzer VCD export
(D+ / D- channels) and analyzes the zero-intrusiveness marker channel (DBG0,
PLAN.md P10): grid toggles right after each RX sample / at TX cell boundaries,
first toggle after the D- falling edge = ISR entry.

Modes:
  decode  bus traffic only: packets (PID, bytes, CRC5/16), keepalives, EOP
  rx      sample-position analysis: ISR entry latency, per-bit sample offset
          inside its reconstructed bit cell, drift slope, cumulative excursion
  tx      TX cell periods from marker toggles, turnaround, SE0/EOP widths,
          coarse D+/D- edge-skew estimate
  bench   generic single-channel marker edge-interval statistics + histogram

All numbers are reported in ns AND 48 MHz cycles (1 cyc = 20.833 ns).
Exit codes: 0 ok, 1 gate violated, 2 usage/parse/decode failure.

Python 3 stdlib only. See tools/wg015_vcd/README.md.
"""

import argparse
import json
import re
import statistics
import sys

F_CPU_HZ = 48_000_000.0
CYCLE_NS = 1e9 / F_CPU_HZ            # 20.8333... ns
BIT_NS = 1e9 / 1_500_000.0           # 666.6666... ns (LS bit)
CYC_PER_BIT = 32
BIT_TOL = 0.015                      # +-1.5% nominal tolerance (warn beyond)
BIT_HARD_TOL = 0.025                 # beyond this the packet is marked invalid

PID_NAMES = {0xD: 'SETUP', 0x1: 'OUT', 0x9: 'IN', 0x5: 'SOF',
             0x3: 'DATA0', 0xB: 'DATA1',
             0x2: 'ACK', 0xA: 'NAK', 0xE: 'STALL', 0xC: 'PRE'}
TOKEN_PIDS = ('SETUP', 'OUT', 'IN', 'SOF')
DATA_PIDS = ('DATA0', 'DATA1')
HS_PIDS = ('ACK', 'NAK', 'STALL')


def fail(msg):
    sys.stderr.write("wg015vcd: error: %s\n" % msg)
    sys.exit(2)


# --------------------------------------------------------------------------
# CRC (USB wire convention: bits LSB-first, reflected shift registers)
# CRC-5/USB : poly 0x05 refl 0x14, init 0x1F, xorout 0x1F, check("123456789")=0x19
# CRC-16/USB: poly 0x8005 refl 0xA001, init 0xFFFF, xorout 0xFFFF, check=0xB4C8
# --------------------------------------------------------------------------

def crc5_bits(bits):
    r = 0x1F
    for b in bits:
        if (r ^ b) & 1:
            r = (r >> 1) ^ 0x14
        else:
            r >>= 1
    return r ^ 0x1F


def crc16_bits(bits):
    r = 0xFFFF
    for b in bits:
        if (r ^ b) & 1:
            r = (r >> 1) ^ 0xA001
        else:
            r >>= 1
    return r ^ 0xFFFF


def _crc_selfcheck():
    bs = [(byte >> i) & 1 for byte in b"123456789" for i in range(8)]
    assert crc5_bits(bs) == 0x19, "CRC5 self-check failed"
    assert crc16_bits(bs) == 0xB4C8, "CRC16 self-check failed"


_crc_selfcheck()


# --------------------------------------------------------------------------
# VCD parsing
# --------------------------------------------------------------------------

_TS_RE = re.compile(r'^\s*(\d+)\s*(s|ms|us|ns|ps|fs)\s*$')
_TS_FACTOR = {'s': 1e9, 'ms': 1e6, 'us': 1e3, 'ns': 1.0, 'ps': 1e-3, 'fs': 1e-6}


def parse_timescale(txt):
    m = _TS_RE.match(txt)
    if not m:
        fail("cannot parse $timescale %r (expected e.g. '1ns', '10 ps')" % txt)
    return int(m.group(1)) * _TS_FACTOR[m.group(2)]


def parse_vcd_header(f, path):
    """Consume the header up to $enddefinitions. Returns (timescale_ns, vars).
    vars: list of dicts {id, size, name, full}."""
    header_tokens = []
    found = False
    for line in f:
        header_tokens.extend(line.split())
        if '$enddefinitions' in line:
            found = True
            break
    if not found:
        fail("%s: no $enddefinitions found -- not a VCD file?" % path)

    ts_ns = None
    vars_ = []
    scope = []
    it = iter(header_tokens)

    def until_end(it):
        acc = []
        for t in it:
            if t == '$end':
                break
            acc.append(t)
        return acc

    for tok in it:
        if tok == '$timescale':
            ts_ns = parse_timescale(''.join(until_end(it)))
        elif tok == '$scope':
            acc = until_end(it)
            if len(acc) >= 2:
                scope.append(acc[1])
        elif tok == '$upscope':
            until_end(it)
            if scope:
                scope.pop()
        elif tok == '$var':
            acc = until_end(it)
            if len(acc) >= 4:
                try:
                    size = int(acc[1])
                except ValueError:
                    size = 0
                name = ' '.join(acc[3:])
                # strip a trailing bit-range token like [7:0]
                name = re.sub(r'\s*\[[^\]]*\]$', '', name)
                vars_.append({'id': acc[2], 'size': size, 'name': name,
                              'full': '.'.join(scope + [name])})
    if ts_ns is None:
        fail("%s: VCD has no $timescale" % path)
    if not vars_:
        fail("%s: VCD declares no variables" % path)
    return ts_ns, vars_


def parse_vcd_body(f, want_ids, path):
    """Stream value changes for the wanted ids. Returns dict id -> [(t,v)]."""
    data = {i: [] for i in want_ids}
    t = 0
    nonbin = 0
    seen_time = False
    for line in f:
        line = line.strip()
        if not line:
            continue
        c = line[0]
        if c == '#':
            try:
                t = int(line[1:].split()[0])
            except (ValueError, IndexError):
                fail("%s: malformed timestamp line: %r" % (path, line[:60]))
            seen_time = True
            continue
        if c == '$':
            continue  # $dumpvars / $end / $comment blocks: value lines handled below
        # possibly several tokens on one line
        toks = line.split() if (' ' in line or '\t' in line) else [line]
        skip_next = False
        for tok in toks:
            if skip_next:
                skip_next = False
                continue
            tc = tok[0]
            if tc in 'bBrR':
                # vector/real: value token followed by id token (multi-bit: ignored)
                if len(tok) > 1 and ' ' not in tok:
                    skip_next = True
                continue
            if tc in '01xXzZuUwWlLhH-':
                vid = tok[1:]
                if vid in want_ids:
                    if tc in '1hH':
                        v = 1
                    elif tc in '0lL':
                        v = 0
                    else:
                        nonbin += 1
                        v = data[vid][-1][1] if data[vid] else 0
                    data[vid].append((t, v))
    if not seen_time:
        fail("%s: VCD body contains no timestamps (#<time>)" % path)
    return data, nonbin


GUESS = {
    'dp': ['dp', 'd+', 'dplus', 'usb_dp', 'usbdp'],
    'dn': ['dn', 'd-', 'dm', 'dminus', 'usb_dn', 'usbdn', 'usb_dm'],
    'dbg': ['dbg0', 'dbg', 'marker', 'mark', 'b2', 'grid'],
}


def select_channel(vars1, spec, role, taken):
    """vars1: 1-bit vars. spec: user substring or None (auto-guess)."""
    if spec:
        cands = [v for v in vars1 if spec.lower() in v['full'].lower()]
        exact = [v for v in cands if v['name'].lower() == spec.lower()
                 or v['full'].lower() == spec.lower()]
        if exact:
            return exact[0]
        if len(cands) == 1:
            return cands[0]
        if not cands:
            fail("no 1-bit channel matches --%s %r; available: %s"
                 % (role, spec, ', '.join(v['full'] for v in vars1)))
        fail("--%s %r is ambiguous: %s (use a longer substring)"
             % (role, spec, ', '.join(v['full'] for v in cands)))
    # auto-guess
    avoid = {'dp': ['dpu'], 'dn': [], 'dbg': []}[role]
    for cand in GUESS[role]:
        for v in vars1:
            if v['id'] in taken:
                continue
            if v['name'].lower() == cand:
                return v
    for cand in GUESS[role]:
        for v in vars1:
            if v['id'] in taken:
                continue
            nm = v['name'].lower()
            if any(a in nm for a in avoid):
                continue
            if cand in nm:
                return v
    fail("cannot auto-detect the %s channel; available 1-bit channels: %s\n"
         "use --%s NAME" % (role, ', '.join(v['full'] for v in vars1), role))


# --------------------------------------------------------------------------
# Series helpers
# --------------------------------------------------------------------------

def dedupe(seq):
    out = []
    for t, v in seq:
        if not out or out[-1][1] != v:
            out.append((t, v))
        # redundant same-value writes dropped (first time kept)
    return out


def edge_times(seq):
    ded = dedupe(seq)
    return [t for t, _ in ded[1:]]


def merged_bus(dp, dn):
    """dp/dn deduped [(t_ns,v)] -> list of (t_ns, state) changes."""
    di = dict(dp)
    ni = dict(dn)
    times = sorted(set(di) | set(ni))
    dv = dp[0][1] if dp else 0
    nv = dn[0][1] if dn else 1
    out = []
    for t in times:
        if t in di:
            dv = di[t]
        if t in ni:
            nv = ni[t]
        st = ('SE0', 'J', 'K', 'SE1')[dv * 2 + nv] if True else None
        # (dp,dn): (0,0)=SE0 (0,1)=J (1,0)=K (1,1)=SE1
        if not out or out[-1][1] != st:
            out.append((t, st))
    return out


def linfit(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    d = n * sxx - sx * sx
    if d == 0:
        return 0.0, sy / n
    a = (n * sxy - sx * sy) / d
    return a, (sy - a * sx) / n


def sdict(vals):
    if not vals:
        return None
    return {'n': len(vals), 'min': min(vals), 'med': statistics.median(vals),
            'mean': sum(vals) / len(vals), 'max': max(vals)}


def fmt_nc(ns):
    return "%9.1f ns  %7.2f cyc" % (ns, ns / CYCLE_NS)


def text_hist(vals, bin_w, unit, width=48):
    if not vals:
        return ["  (no data)"]
    lo = bin_w * (min(vals) // bin_w)
    import math
    nbins = int(math.floor((max(vals) - lo) / bin_w)) + 1
    nbins = min(nbins, 120)
    counts = [0] * nbins
    for v in vals:
        i = int((v - lo) / bin_w)
        counts[min(i, nbins - 1)] += 1
    peak = max(counts)
    lines = []
    for i, c in enumerate(counts):
        b0 = lo + i * bin_w
        bar = '#' * max(0, int(round(width * c / peak)))
        lines.append("  %8.2f..%-8.2f %-5s |%-*s %d" %
                     (b0, b0 + bin_w, unit, width, bar, c))
    return lines


# --------------------------------------------------------------------------
# Bus scan: segments -> packets / keepalives
# --------------------------------------------------------------------------

def scan_bus(changes, t_last, glitch_ns, notes):
    segs = []
    for a, b in zip(changes, changes[1:]):
        segs.append((a[0], b[0], a[1]))
    if changes:
        segs.append((changes[-1][0], t_last, changes[-1][1]))
    packets, keepalives = [], []
    if segs and segs[0][2] != 'J':
        notes.append("capture does not start in idle J (starts in %s at %.0f ns)"
                     % (segs[0][2], segs[0][0]))
    n = len(segs)
    i = 0
    while i < n:
        t0, t1, st = segs[i]
        dur = t1 - t0
        if st == 'J':
            i += 1
            continue
        if st == 'SE1':
            if dur > glitch_ns:
                notes.append("SE1 on bus at %.0f ns for %.0f ns" % (t0, dur))
            i += 1
            continue
        if st == 'SE0':
            if dur < glitch_ns:
                pass
            elif dur <= 3.5 * BIT_NS:
                keepalives.append({'t_ns': t0, 'width_ns': dur})
            else:
                notes.append("long SE0 (bus reset?) at %.0f ns, width %.1f us"
                             % (t0, dur / 1000.0))
            i += 1
            continue
        # st == 'K': packet start (D- falling edge)
        trans = [t0]
        skews = []
        cur = 'K'
        pend = None
        se0_start = se0_end = None
        j = i + 1
        ok = True
        while j < n:
            s0, s1, sst = segs[j]
            d = s1 - s0
            if sst in ('SE0', 'SE1') and d < glitch_ns:
                pend = (s0, s1)          # inter-edge transient (D+/D- skew)
                j += 1
                continue
            if sst == 'SE0':
                se0_start, se0_end = s0, s1
                break
            if sst in ('J', 'K'):
                if sst != cur:
                    if pend:
                        trans.append((pend[0] + pend[1]) / 2.0)
                        skews.append(pend[1] - pend[0])
                    else:
                        trans.append(s0)
                    cur = sst
                pend = None
                if len(trans) > 2000:
                    notes.append("packet at %.0f ns exceeds 2000 transitions "
                                 "-- abandoned (noise?)" % t0)
                    ok = False
                    break
                j += 1
                continue
            notes.append("SE1 inside packet at %.0f ns" % s0)
            j += 1
        if se0_start is None:
            if ok and j >= n:
                notes.append("capture ends mid-packet (packet at %.0f ns dropped)" % t0)
            ok = False
        if ok:
            packets.append({'t_start': trans[0], 'trans': trans,
                            'se0_start': se0_start, 'se0_end': se0_end,
                            'skews': skews})
        i = j + 1 if j > i else i + 1
    return packets, keepalives


# --------------------------------------------------------------------------
# Packet decode: NRZI + unstuff + PID + CRC + bit-cell reconstruction
# --------------------------------------------------------------------------

def unstuff(bits):
    out = []
    ones = 0
    errs = 0
    for b in bits:
        if ones == 6:
            if b != 0:
                errs += 1
            ones = 0
            continue            # drop the stuffed bit
        out.append(b)
        ones = ones + 1 if b else 0
    return out, errs


def decode_packet(pkt, notes):
    ts = pkt['trans']
    knots_t = ts + [pkt['se0_start']]
    ivals = [knots_t[k + 1] - knots_t[k] for k in range(len(knots_t) - 1)]
    period = BIT_NS
    counts = []
    for _ in range(4):
        counts = [max(1, int(round(d / period))) for d in ivals]
        total = sum(counts)
        period = (knots_t[-1] - knots_t[0]) / total
    dev = period / BIT_NS - 1.0
    pkt['bit_ns'] = period
    pkt['bit_ppm'] = dev * 1e6
    pkt['eop_se0_ns'] = pkt['se0_end'] - pkt['se0_start']
    pkt['valid'] = True
    pkt['errors'] = []

    if abs(dev) > BIT_HARD_TOL:
        pkt['valid'] = False
        pkt['errors'].append("bit rate %.1f%% off nominal 1.5 Mbit/s" % (dev * 100))
    elif abs(dev) > BIT_TOL:
        pkt['errors'].append("bit rate %.2f%% off nominal (beyond +-1.5%% spec window)"
                             % (dev * 100))

    bidx = [0]
    for c in counts:
        bidx.append(bidx[-1] + c)
    slope, intc = linfit(bidx, knots_t)
    pkt['cell_period'] = slope if slope > 0 else period
    pkt['cell_phase'] = intc
    pkt['nbits'] = bidx[-1]

    bits = []
    for c in counts:
        bits.extend([0] + [1] * (c - 1))
    pkt['raw_nbits'] = len(bits)

    pkt['sync_ok'] = bits[:8] == [0] * 7 + [1]
    if not pkt['sync_ok']:
        pkt['valid'] = False
        pkt['errors'].append("no SYNC (first 8 bits %s)" % ''.join(map(str, bits[:8])))
        pkt['pid_name'] = '?'
        return pkt

    pbits, stuff_errs = unstuff(bits[8:])
    if stuff_errs:
        pkt['valid'] = False
        pkt['errors'].append("%d bit-stuff violation(s)" % stuff_errs)
    nbytes = len(pbits) // 8
    leftover = len(pbits) % 8
    if leftover > 1:
        pkt['errors'].append("%d dangling bits after last byte" % leftover)
    bytes_ = []
    for k in range(nbytes):
        v = 0
        for i in range(8):
            v |= pbits[k * 8 + i] << i
        bytes_.append(v)
    pkt['bytes'] = bytes_
    if not bytes_:
        pkt['valid'] = False
        pkt['errors'].append("empty packet (no PID byte)")
        pkt['pid_name'] = '?'
        return pkt

    b0 = bytes_[0]
    pid = b0 & 0xF
    pid_ok = ((b0 >> 4) ^ 0xF) == pid
    nm = PID_NAMES.get(pid, 'PID%X' % pid)
    pkt['pid'] = pid
    pkt['pid_name'] = nm
    pkt['pid_ok'] = pid_ok
    if not pid_ok:
        pkt['valid'] = False
        pkt['errors'].append("PID check-nibble mismatch (byte 0x%02X)" % b0)

    pkt['crc_ok'] = None
    if nm in TOKEN_PIDS:
        if nbytes == 3:
            fbits = pbits[8:19]
            rx_crc = 0
            for i, b in enumerate(pbits[19:24]):
                rx_crc |= b << i
            pkt['crc_ok'] = (crc5_bits(fbits) == rx_crc)
            field = 0
            for i, b in enumerate(fbits):
                field |= b << i
            if nm == 'SOF':
                pkt['frame'] = field
            else:
                pkt['addr'] = field & 0x7F
                pkt['ep'] = (field >> 7) & 0xF
        else:
            pkt['errors'].append("token PID but %d bytes (expected 3)" % nbytes)
            pkt['valid'] = False
    elif nm in DATA_PIDS:
        if nbytes >= 3:
            dbits = pbits[8:len(pbits) - leftover - 16]
            rx_crc = 0
            for i, b in enumerate(pbits[len(pbits) - leftover - 16:len(pbits) - leftover]):
                rx_crc |= b << i
            pkt['crc_ok'] = (crc16_bits(dbits) == rx_crc)
            pkt['payload'] = bytes_[1:-2]
        else:
            pkt['errors'].append("data PID but %d bytes (expected >=3)" % nbytes)
            pkt['valid'] = False
    elif nm in HS_PIDS:
        if nbytes != 1:
            pkt['errors'].append("handshake PID but %d bytes" % nbytes)
    if pkt['crc_ok'] is False:
        pkt['errors'].append("CRC mismatch")
    return pkt


def classify_directions(pkts):
    expect = None
    for p in pkts:
        nm = p.get('pid_name')
        if not p.get('valid'):
            p['dir'] = 'unknown'
            expect = None
            continue
        if nm in TOKEN_PIDS:
            p['dir'] = 'host'
            expect = {'IN': 'dev_data', 'SETUP': 'host_data',
                      'OUT': 'host_data', 'SOF': None}[nm]
        elif nm in DATA_PIDS:
            if expect == 'dev_data':
                p['dir'] = 'device'; expect = 'host_hs'
            elif expect == 'host_data':
                p['dir'] = 'host'; expect = 'dev_hs'
            else:
                p['dir'] = 'unknown'; expect = None
        elif nm in HS_PIDS:
            if expect in ('dev_hs', 'dev_data'):
                p['dir'] = 'device'
            elif expect == 'host_hs':
                p['dir'] = 'host'
            else:
                p['dir'] = 'unknown'
            expect = None
        else:
            p['dir'] = 'unknown'
            expect = None


# --------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------

def gap_prev_bt(pkts, i):
    if i == 0:
        return None
    prev = pkts[i - 1]
    bit = prev.get('bit_ns') or BIT_NS
    return (pkts[i]['t_start'] - prev['se0_end']) / bit


def analyze_rx(pkts, dbg_edges, args, notes):
    per_pkt = []
    entries_ns = []
    all_offs = []
    slopes = []
    excursions = []
    for i, p in enumerate(pkts):
        if not p.get('valid'):
            continue
        if p['dir'] == 'device':
            continue
        if p['dir'] == 'unknown' and not args.include_unknown:
            continue
        w = [t for t in dbg_edges if p['t_start'] < t < p['se0_start']]
        if not w:
            continue
        entry_ns = w[0] - p['t_start']
        entries_ns.append(entry_ns)
        grid = w[1:]
        offs = []
        period = p['cell_period']
        phase = p['cell_phase']
        if grid:
            k = int((grid[0] - phase) // period)
            prev_t = grid[0]
            for t in grid:
                k += int(round((t - prev_t) / period))
                prev_t = t
                off = (t - (phase + k * period)) / CYCLE_NS
                if -CYC_PER_BIT <= off <= 2 * CYC_PER_BIT and 0 <= k < p['nbits'] + 2:
                    offs.append((k, off))
        rec = {'idx': i, 't_ms': p['t_start'] / 1e6, 'pid': p['pid_name'],
               'entry_ns': round(entry_ns, 2),
               'entry_cyc': round(entry_ns / CYCLE_NS, 3),
               'n_marks': len(offs)}
        if len(offs) >= 2:
            ks = [k for k, _ in offs]
            os_ = [o for _, o in offs]
            sl, _b = linfit(ks, os_)
            exc = max(os_) - min(os_)
            rec.update({'first_off_cyc': round(os_[0], 3),
                        'last_off_cyc': round(os_[-1], 3),
                        'slope_cyc_per_bit': round(sl, 5),
                        'excursion_cyc': round(exc, 3)})
            slopes.append(sl)
            excursions.append(exc)
            all_offs.extend(os_)
        rec['offsets'] = [[k, round(o, 3)] for k, o in offs]
        per_pkt.append(rec)
    if not per_pkt:
        notes.append("rx: no valid host packets with DBG toggles found")
    return {
        'entry_ns': sdict(entries_ns),
        'entry_cyc': sdict([e / CYCLE_NS for e in entries_ns]),
        'offset_cyc': sdict(all_offs),
        'slope_cyc_per_bit': sdict(slopes),
        'excursion_cyc': sdict(excursions),
        'packets': per_pkt,
        'all_offsets': all_offs,
    }


def analyze_tx(pkts, dbg_edges, args, notes):
    per_pkt = []
    all_cells = []
    turnarounds = []
    eop_ns = []
    skews = []
    for i, p in enumerate(pkts):
        if not p.get('valid'):
            continue
        if p['dir'] == 'host':
            continue
        if p['dir'] == 'unknown' and not args.include_unknown:
            continue
        w = [t for t in dbg_edges
             if p['t_start'] - 0.5 * BIT_NS <= t <= p['se0_start'] + 1.0]
        cells = []
        for a, b in zip(w, w[1:]):
            c = (b - a) / CYCLE_NS
            if 4 <= c <= 3 * CYC_PER_BIT:
                cells.append(c)
        gap = gap_prev_bt(pkts, i)
        rec = {'idx': i, 't_ms': p['t_start'] / 1e6, 'pid': p['pid_name'],
               'n_marks': len(w),
               'eop_se0_ns': round(p['eop_se0_ns'], 1),
               'eop_se0_cyc': round(p['eop_se0_ns'] / CYCLE_NS, 2),
               'bit_ppm': round(p['bit_ppm'], 1)}
        if cells:
            rec['cell_min_cyc'] = round(min(cells), 3)
            rec['cell_avg_cyc'] = round(sum(cells) / len(cells), 3)
            rec['cell_max_cyc'] = round(max(cells), 3)
            all_cells.extend(cells)
        if gap is not None and gap < args.tx_gap_max:
            rec['turnaround_bt'] = round(gap, 3)
            turnarounds.append(gap)
        eop_ns.append(p['eop_se0_ns'])
        skews.extend(p['skews'])
        per_pkt.append(rec)
    if not per_pkt:
        notes.append("tx: no device packets found (protocol direction heuristic; "
                     "see --include-unknown)")
    return {
        'cell_cyc': sdict(all_cells),
        'cell_ns': sdict([c * CYCLE_NS for c in all_cells]),
        'turnaround_bt': sdict(turnarounds),
        'turnaround_ns': sdict([t * BIT_NS for t in turnarounds]),
        'eop_se0_ns': sdict(eop_ns),
        'eop_se0_cyc': sdict([e / CYCLE_NS for e in eop_ns]),
        'edge_skew_ns': sdict(skews),
        'packets': per_pkt,
        'all_cells': all_cells,
    }


def analyze_bench(dbg_edges, notes):
    ivals = [b - a for a, b in zip(dbg_edges, dbg_edges[1:])]
    if not ivals:
        notes.append("bench: fewer than 2 edges on the marker channel")
    return {
        'n_edges': len(dbg_edges),
        'interval_ns': sdict(ivals),
        'interval_cyc': sdict([v / CYCLE_NS for v in ivals]),
        'all_intervals_ns': ivals,
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def print_sdict_nc(label, d_ns):
    if not d_ns:
        print("  %-28s (no data)" % label)
        return
    print("  %-28s n=%-5d min %s   med %s   max %s"
          % (label, d_ns['n'], fmt_nc(d_ns['min']), fmt_nc(d_ns['med']),
             fmt_nc(d_ns['max'])))


def report_decode(pkts, keepalives, notes, human):
    if not human:
        return
    print("== bus decode: %d packet(s), %d keepalive(s)" % (len(pkts), len(keepalives)))
    print("  %-4s %-10s %-7s %-6s %-5s %-5s %-9s %-8s  %s"
          % ("#", "t(ms)", "dir", "PID", "len", "CRC", "bit(ppm)", "EOP(cyc)", "bytes"))
    for i, p in enumerate(pkts):
        crc = {True: 'ok', False: 'BAD', None: '-'}[p.get('crc_ok')]
        by = ' '.join('%02X' % b for b in p.get('bytes', [])[:12])
        if len(p.get('bytes', [])) > 12:
            by += ' ...'
        flag = '' if p.get('valid') else '  INVALID: ' + '; '.join(p['errors'])
        extra = ''
        if 'addr' in p:
            extra = ' a%d.e%d' % (p['addr'], p['ep'])
        print("  %-4d %-10.4f %-7s %-6s %-5d %-5s %+-9.0f %-8.1f  %s%s%s"
              % (i, p['t_start'] / 1e6, p.get('dir', '?'), p.get('pid_name', '?'),
                 len(p.get('bytes', [])), crc, p.get('bit_ppm', 0),
                 p['eop_se0_ns'] / CYCLE_NS, by, extra, flag))
    if keepalives:
        w = [k['width_ns'] for k in keepalives]
        iv = [b['t_ns'] - a['t_ns'] for a, b in zip(keepalives, keepalives[1:])]
        print("  keepalives: n=%d  SE0 width med %s (%.2f bit)"
              % (len(w), fmt_nc(statistics.median(w)),
                 statistics.median(w) / BIT_NS))
        if iv:
            print("              interval med %.3f ms" % (statistics.median(iv) / 1e6))
    for n in notes:
        print("  note: %s" % n)


def report_rx(rx, human, verbose):
    if not human:
        return
    print("\n== rx: ISR entry latency (D- fall -> first DBG toggle)")
    print_sdict_nc("entry", rx['entry_ns'])
    ec = rx['entry_cyc']
    if ec:
        print("  entry cycles:                min %.2f  med %.2f  max %.2f"
              % (ec['min'], ec['med'], ec['max']))
    print("\n== rx: sample position inside reconstructed bit cell (cyc from cell start)")
    oc = rx['offset_cyc']
    if oc:
        print("  offsets: n=%d  min %.2f  med %.2f  max %.2f cyc  (cell = %d cyc)"
              % (oc['n'], oc['min'], oc['med'], oc['max'], CYC_PER_BIT))
        for ln in text_hist(rx['all_offsets'], 1.0, 'cyc'):
            print(ln)
    sl = rx['slope_cyc_per_bit']
    if sl:
        print("  drift slope (cyc/bit):       min %+.4f  med %+.4f  max %+.4f"
              % (sl['min'], sl['med'], sl['max']))
    exc = rx['excursion_cyc']
    if exc:
        print("  cumulative excursion (cyc):  min %.2f  med %.2f  worst %.2f"
              % (exc['min'], exc['med'], exc['max']))
    print("\n  per-packet:")
    print("  %-4s %-6s %-8s %-6s %-9s %-9s %-11s %-9s"
          % ("#", "PID", "entry", "marks", "first", "last", "slope", "excur"))
    for r in rx['packets']:
        print("  %-4d %-6s %-8.2f %-6d %-9s %-9s %-11s %-9s"
              % (r['idx'], r['pid'], r['entry_cyc'], r['n_marks'],
                 r.get('first_off_cyc', '-'), r.get('last_off_cyc', '-'),
                 r.get('slope_cyc_per_bit', '-'), r.get('excursion_cyc', '-')))
        if verbose:
            for k, o in r['offsets']:
                print("        bit %-4d off %8.3f cyc  (%.1f ns)" % (k, o, o * CYCLE_NS))


def report_tx(tx, human):
    if not human:
        return
    print("\n== tx: cell periods from marker toggles")
    cc = tx['cell_cyc']
    if cc:
        print("  cell period: n=%d  min %.3f  avg %.3f  max %.3f cyc"
              % (cc['n'], cc['min'], cc['mean'], cc['max']))
        print("               min %.1f  avg %.1f  max %.1f ns  (nominal %d cyc = %.1f ns)"
              % (cc['min'] * CYCLE_NS, cc['mean'] * CYCLE_NS, cc['max'] * CYCLE_NS,
                 CYC_PER_BIT, CYC_PER_BIT * CYCLE_NS))
        for ln in text_hist(tx['all_cells'], 0.25, 'cyc'):
            print(ln)
    else:
        print("  (no cell intervals)")
    ta = tx['turnaround_bt']
    print("\n== tx: turnaround (host EOP end -> device SYNC start)")
    if ta:
        print("  n=%d  min %.2f  med %.2f  max %.2f bit-times"
              % (ta['n'], ta['min'], ta['med'], ta['max']))
        print("  (med = %s)" % fmt_nc(ta['med'] * BIT_NS))
    else:
        print("  (none observed)")
    eo = tx['eop_se0_ns']
    print("\n== tx: device EOP SE0 width")
    print_sdict_nc("SE0", eo)
    sk = tx['edge_skew_ns']
    print("\n== tx: D+/D- edge-to-edge skew (coarse rise/fall estimate)")
    if sk:
        print_sdict_nc("skew", sk)
    else:
        print("  no inter-edge transients observed (edges simultaneous at capture resolution)")
    print("\n  per-packet:")
    print("  %-4s %-6s %-6s %-9s %-9s %-9s %-10s %-9s"
          % ("#", "PID", "marks", "cellmin", "cellavg", "cellmax", "turn(bt)", "EOP(cyc)"))
    for r in tx['packets']:
        print("  %-4d %-6s %-6d %-9s %-9s %-9s %-10s %-9.2f"
              % (r['idx'], r['pid'], r['n_marks'], r.get('cell_min_cyc', '-'),
                 r.get('cell_avg_cyc', '-'), r.get('cell_max_cyc', '-'),
                 r.get('turnaround_bt', '-'), r['eop_se0_cyc']))


def report_bench(be, human):
    if not human:
        return
    print("== bench: marker channel edge intervals")
    print("  edges: %d" % be['n_edges'])
    print_sdict_nc("interval", be['interval_ns'])
    ic = be['interval_cyc']
    if ic:
        print("  cycles:                      min %.2f  med %.2f  mean %.2f  max %.2f"
              % (ic['min'], ic['med'], ic['mean'], ic['max']))
        for ln in text_hist([v / CYCLE_NS for v in be['all_intervals_ns']], 1.0, 'cyc'):
            print(ln)


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def eval_gates(mode, rx, tx, args, human):
    gates = {}
    ok = True
    if mode == 'rx':
        if args.gate_entry and args.gate_entry > 0:
            ec = rx['entry_cyc']
            g = {'limit_cyc': args.gate_entry,
                 'max_cyc': round(ec['max'], 3) if ec else None,
                 'pass': bool(ec and ec['max'] <= args.gate_entry)}
            gates['entry'] = g
            ok &= g['pass']
        if args.gate_excursion is not None:
            exc = rx['excursion_cyc']
            g = {'limit_cyc': args.gate_excursion,
                 'worst_cyc': round(exc['max'], 3) if exc else None,
                 'pass': bool(exc and exc['max'] <= args.gate_excursion)}
            gates['excursion'] = g
            ok &= g['pass']
    if mode == 'tx' and args.gate_turnaround is not None:
        ta = tx['turnaround_bt']
        g = {'limit_bt': args.gate_turnaround,
             'max_bt': round(ta['max'], 3) if ta else None,
             'pass': bool(ta and ta['max'] <= args.gate_turnaround)}
        gates['turnaround'] = g
        ok &= g['pass']
    if human and gates:
        print("\n== gates")
        for name, g in gates.items():
            lim = g.get('limit_cyc', g.get('limit_bt'))
            got = g.get('max_cyc', g.get('worst_cyc', g.get('max_bt')))
            print("  GATE %-10s measured %s <= limit %s : %s"
                  % (name, got, lim, "PASS" if g['pass'] else "FAIL"))
    return gates, ok


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def round_floats(o, nd=4):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, dict):
        return {k: round_floats(v, nd) for k, v in o.items()}
    if isinstance(o, list):
        return [round_floats(v, nd) for v in o]
    return o


def main():
    ap = argparse.ArgumentParser(
        prog='wg015vcd.py',
        description="VCD analyzer for the rv003usb WG015 port (LS USB decode + "
                    "P10 marker analysis). All numbers in ns and 48 MHz cycles.")
    ap.add_argument('mode', choices=['decode', 'rx', 'tx', 'bench'])
    ap.add_argument('vcd', help="VCD file (logic-analyzer export)")
    ap.add_argument('--dp', help="D+ channel name substring (default: auto-guess)")
    ap.add_argument('--dn', help="D- channel name substring (default: auto-guess)")
    ap.add_argument('--dbg', help="marker (DBG0) channel name substring")
    ap.add_argument('--json', action='store_true', help="machine-readable JSON to stdout")
    ap.add_argument('--verbose', action='store_true',
                    help="rx: print full per-bit offset series")
    ap.add_argument('--list-channels', action='store_true',
                    help="list 1-bit channels in the VCD and exit")
    ap.add_argument('--gate-entry', type=float, default=55.0, metavar='CYC',
                    help="rx gate: max ISR entry latency in cycles (default 55; 0 disables)")
    ap.add_argument('--gate-excursion', type=float, default=None, metavar='CYC',
                    help="rx gate: max per-packet cumulative excursion in cycles")
    ap.add_argument('--gate-turnaround', type=float, default=None, metavar='BT',
                    help="tx gate: max turnaround in bit-times (e.g. 7.5)")
    ap.add_argument('--glitch-ns', type=float, default=120.0,
                    help="merge SE0/SE1 transients shorter than this into edges (default 120)")
    ap.add_argument('--tx-gap-max', type=float, default=16.0, metavar='BT',
                    help="max leading gap for a packet to count as a turnaround response")
    ap.add_argument('--include-unknown', action='store_true',
                    help="rx/tx: include packets whose direction could not be classified")
    args = ap.parse_args()

    need = {'decode': ('dp', 'dn'), 'rx': ('dp', 'dn', 'dbg'),
            'tx': ('dp', 'dn', 'dbg'), 'bench': ('dbg',)}[args.mode]

    try:
        f = open(args.vcd, 'r', errors='replace')
    except OSError as e:
        fail("cannot open %s: %s" % (args.vcd, e))
    with f:
        ts_ns, vars_ = parse_vcd_header(f, args.vcd)
        vars1 = [v for v in vars_ if v['size'] == 1]
        if args.list_channels:
            print("timescale: %g ns/unit" % ts_ns)
            for v in vars_:
                print("  %-3s %2d-bit  %s" % (v['id'], v['size'], v['full']))
            return 0
        if not vars1:
            fail("VCD has no 1-bit channels (only vectors); nothing to analyze")
        chan = {}
        taken = set()
        for role in need:
            v = select_channel(vars1, getattr(args, role), role, taken)
            chan[role] = v
            taken.add(v['id'])
        data, nonbin = parse_vcd_body(f, {v['id'] for v in chan.values()}, args.vcd)

    notes = []
    if nonbin:
        notes.append("%d non-0/1 samples (x/z) coerced to previous value" % nonbin)
    for role in need:
        if not data[chan[role]['id']]:
            fail("channel %s (%s) has no value changes in this capture"
                 % (chan[role]['full'], role))

    # ns timelines
    series = {r: [(t * ts_ns, v) for t, v in data[chan[r]['id']]] for r in need}
    out = {'mode': args.mode, 'file': args.vcd, 'timescale_ns': ts_ns,
           'cycle_ns': CYCLE_NS, 'bit_ns': BIT_NS,
           'channels': {r: chan[r]['full'] for r in need}}
    human = not args.json

    if human:
        print("wg015vcd %s: %s" % (args.mode, args.vcd))
        print("  timescale %g ns/unit; channels: %s"
              % (ts_ns, ', '.join("%s=%s" % (r, chan[r]['full']) for r in need)))

    dbg_edges = []
    if 'dbg' in need:
        dbg_edges = edge_times(series['dbg'])

    if args.mode == 'bench':
        be = analyze_bench(dbg_edges, notes)
        report_bench(be, human)
        out['bench'] = {k: v for k, v in be.items() if k != 'all_intervals_ns'}
        out['bench']['intervals_ns'] = round_floats(be['all_intervals_ns'], 2)
        out['notes'] = notes
        out['pass'] = True
        if args.json:
            print(json.dumps(round_floats(out), indent=1))
        if not dbg_edges:
            fail("no edges on marker channel %s" % chan['dbg']['full'])
        return 0

    dp = dedupe(series['dp'])
    dn = dedupe(series['dn'])
    changes = merged_bus(dp, dn)
    t_last = max(dp[-1][0], dn[-1][0]) + 5 * BIT_NS
    raw_pkts, keepalives = scan_bus(changes, t_last, args.glitch_ns, notes)
    for p in raw_pkts:
        decode_packet(p, notes)
    classify_directions(raw_pkts)

    out['packets'] = [{
        'idx': i, 't_ms': round(p['t_start'] / 1e6, 5), 'dir': p['dir'],
        'pid': p.get('pid_name'), 'bytes': p.get('bytes', []),
        'valid': p['valid'], 'crc_ok': p.get('crc_ok'),
        'errors': p['errors'], 'bit_ppm': round(p['bit_ppm'], 1),
        'nbits': p.get('raw_nbits'),
        'eop_se0_ns': round(p['eop_se0_ns'], 1),
        'eop_se0_cyc': round(p['eop_se0_ns'] / CYCLE_NS, 2),
        'addr': p.get('addr'), 'ep': p.get('ep'), 'frame': p.get('frame'),
        'gap_prev_bt': round(gap_prev_bt(raw_pkts, i), 3)
        if gap_prev_bt(raw_pkts, i) is not None else None,
    } for i, p in enumerate(raw_pkts)]
    out['keepalives'] = {'n': len(keepalives),
                         'width_ns': sdict([k['width_ns'] for k in keepalives])}
    out['crc_errors'] = sum(1 for p in raw_pkts if p.get('crc_ok') is False)
    out['invalid_packets'] = sum(1 for p in raw_pkts if not p['valid'])

    report_decode(raw_pkts, keepalives, notes, human)

    if not raw_pkts and args.mode != 'decode':
        fail("no USB packets found on %s/%s -- wrong channels, or capture "
             "contains no traffic" % (chan['dp']['full'], chan['dn']['full']))

    rx = tx = None
    if args.mode == 'rx':
        rx = analyze_rx(raw_pkts, dbg_edges, args, notes)
        report_rx(rx, human, args.verbose)
        out['rx'] = {k: v for k, v in rx.items() if k != 'all_offsets'}
    if args.mode == 'tx':
        tx = analyze_tx(raw_pkts, dbg_edges, args, notes)
        report_tx(tx, human)
        out['tx'] = {k: v for k, v in tx.items() if k != 'all_cells'}

    gates, gates_ok = eval_gates(args.mode, rx, tx, args, human)
    out['gates'] = gates
    out['notes'] = notes
    out['pass'] = gates_ok

    if args.json:
        print(json.dumps(round_floats(out), indent=1))
    elif notes:
        print("\n  notes:")
        for n in notes:
            print("  - %s" % n)

    if args.mode == 'rx' and rx is not None and not rx['packets']:
        fail("rx: no packets with marker toggles -- is --dbg the right channel, "
             "and are DBG masks enabled in firmware?")
    if args.mode == 'tx' and tx is not None and not tx['packets']:
        fail("tx: no device packets with markers found")

    return 0 if gates_ok else 1


if __name__ == '__main__':
    sys.exit(main())
